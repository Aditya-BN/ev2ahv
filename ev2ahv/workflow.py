"""The migration workflow. Every step is idempotent and records progress in SQLite.
The source is only ever read, stopped and started. It is never modified or deleted."""
import base64, fcntl, logging, os, re, shlex, socket, subprocess, time

from . import builder, cbt, engine, guest as guestlib, target as tgt
from .readers import make_reader
from .state import State
from .transport import BlockdTransport, BlockEndpoint, LocalTransport, SshTransport

log = logging.getLogger("ev2ahv.workflow")
DATA_DIR = os.path.expanduser(os.environ.get("EV2AHV_DATA", "~/.local/share/ev2ahv"))
STEPS = ("stopped", "synced", "quiesced", "created", "powered", "verified", "converted")


def plan_dir(name):
    return os.path.join(DATA_DIR, re.sub(r"[^\w.-]", "_", name))



def safe_staging_file(path, staging_dir=None):
    """Guard for every rm/truncate of a staging file on the writer (it runs as root there). The path must be a plain
    absolute *.raw path: no "..", not "/", and directly inside the plan's staging_dir when that is known."""
    if (not isinstance(path, str) or not path.startswith("/") or path != os.path.normpath(path)
            or not path.endswith(".raw") or os.path.basename(path) == ".raw" or path.count("/") < 2):
        raise RuntimeError(f"refusing to delete or overwrite {path!r}: not a staging *.raw file")
    if staging_dir and os.path.dirname(path) != os.path.normpath(staging_dir):
        raise RuntimeError(f"refusing to delete or overwrite {path!r}: not inside the staging directory {staging_dir!r}")
    return path


def image_space_needed(sizes):
    """Worst-case free bytes the writer needs for target.transfer: image: a raw staging file per disk (sparse, but
    worst case fully written) plus one qcow2 at a time while it is converted and uploaded (worst case the largest
    disk). Before 2026-11-26 the check asked for twice the total."""
    return sum(sizes) + max(sizes, default=0)


class Migration:
    def __init__(self, plan, prism, dry_run=False, progress=None):
        self.p, self.prism, self.dry_run, self.progress = plan, prism, dry_run, progress
        self.dir = plan_dir(plan["name"])
        os.makedirs(self.dir, exist_ok=True)
        self.state = State(os.path.join(self.dir, "state.db"))
        self.chunk = int(plan["sync"]["chunk_mib"]) * 1024 ** 2
        self.prism.extra_owned.add(plan["target"]["vm_name"])
        self._lock = None
        self._writer = None
        self._guest_facts = None

    # ---- plumbing ----------------------------------------------------------
    def lock(self):
        self._lock = open(os.path.join(self.dir, "lock"), "w")
        try:
            fcntl.flock(self._lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            raise RuntimeError(f"plan {self.p['name']} is already running") from None

    @property
    def writer(self):
        if self._writer is None:
            w = self.p["writer"]
            if w.get("local"):   # the appliance is its own writer
                self._writer = BlockdTransport()
            else:
                self._writer = tgt.writer_transport(w["host"], w.get("user", "nutanix"))
            self._writer.setup()
        return self._writer

    def writer_vm(self):
        if self.p["writer"].get("local"):
            vm_id = self.writer.uuid()
            vm = self.prism.vm(vm_id)
            self.prism.disk_only.add(vm["name"])   # it may add and remove disks on itself, nothing else (enforced in Prism)
            return vm_id
        vm = self.prism.vm_by_name(self.p["writer"]["vm"])
        if not vm:
            raise RuntimeError(f"writer VM {self.p['writer']['vm']} not found")
        return vm["extId"]

    def source_shell(self):
        s = self.p["source"]
        if s["type"] == "direct" or s.get("host"):
            return SshTransport(s["host"], s.get("user", "root"), s.get("key") or os.path.join(tgt.CONFIG, "id_ed25519"),
                                s.get("port", 22), s.get("sudo", False), os.path.join(tgt.CONFIG, "known_hosts"))
        return LocalTransport(sudo=s.get("sudo", False))

    def reader(self, disk):
        return make_reader(self.p["source"], disk, self.chunk, os.path.join(tgt.CONFIG, "id_ed25519"),
                           os.path.join(tgt.CONFIG, "known_hosts"))

    def staging(self):
        return self.state.get("staging", {})

    def step_done(self, step):
        return self.state.get("cutover_step") in STEPS and STEPS.index(self.state.get("cutover_step")) >= STEPS.index(step)

    def mark(self, step):
        self.state.set("cutover_step", step)
        self.state.event("cutover", step=step)
        log.info("cutover step done: %s", step)

    def timing(self, **kw):
        t = self.state.get("timings", {})
        t.update(kw)
        self.state.set("timings", t)

    # ---- staging disks -----------------------------------------------------
    def ensure_staging(self, disk, size):
        all_staging = self.staging()
        size = builder.align(size)
        entry = all_staging.get(disk["name"])
        if self.image_mode:
            if entry:
                return self.device_for(entry)
            path = safe_staging_file(self.staging_file(disk), self.p["target"].get("staging_dir"))
            q = shlex.quote(path)
            self.writer.run(f"mkdir -p {shlex.quote(os.path.dirname(path))}; rm -f {q}; truncate -s {size} {q}")
            all_staging[disk["name"]] = {"file": path, "path": path, "size": size}
            self.state.set("staging", all_staging)
            return path
        vm = self.writer_vm()
        if entry and any(d["extId"] == entry["disk_id"] for d in self.prism.vm(vm).get("disks") or []):
            return self.device_for(entry)
        if entry:
            raise RuntimeError(f"staging disk for {disk['name']} is gone from the writer. "
                               "Run cleanup, then seed again")
        cluster = self.prism.cluster_id(self.p["target"]["cluster"])
        container = self.prism.container_id(self.p["target"]["container"], cluster)
        disk_id, path = tgt.attach_staging(self.prism, self.writer, vm, size, container=container)
        serial = tgt.serial_of(self.writer, path)
        all_staging[disk["name"]] = {"disk_id": disk_id, "path": path, "serial": serial, "size": size}
        self.state.set("staging", all_staging)
        return path

    @property
    def image_mode(self):
        return self.p["target"]["transfer"] == "image"

    def staging_file(self, disk):
        return os.path.join(self.p["target"]["staging_dir"], f"{self.p['name']}-{disk['name']}.raw")

    def image_name(self, disk):
        return f"ev2ahv-img-{self.p['name']}-{disk['name']}"

    def upload_images(self):
        """Fallback path: raw staging file -> qcow2 on the writer -> streamed to Prism via the bastion.
        The API key stays on the bastion. Any image from an earlier attempt is replaced."""
        cluster = self.prism.cluster_id(self.p["target"]["cluster"])
        images = self.state.get("images", {})
        for disk in self.p["disks"]:
            name = self.image_name(disk)
            old = self.prism.image_by_name(name)
            if old:
                self.prism.delete_image(old["extId"])
            images.pop(disk["name"], None)
            self.state.set("images", images)
            raw = safe_staging_file(self.staging()[disk["name"]]["file"], self.p["target"].get("staging_dir"))
            qcow = raw[:-4] + ".qcow2"
            q_raw, q_qcow = shlex.quote(raw), shlex.quote(qcow)
            # one qcow2 at a time, removed after upload or failure, so the writer needs the raw files plus the largest
            # disk (image_space_needed); a leftover from an earlier failed attempt is removed first
            self.writer.run(f"rm -f {q_qcow} {q_qcow}.tmp; sync; "
                            f"qemu-img convert -f raw -O qcow2 {q_raw} {q_qcow}.tmp && mv -f {q_qcow}.tmp {q_qcow}",
                            timeout=14400)
            try:
                size = int(self.writer.run(f"stat -c %s {q_qcow}").strip())
                proc = subprocess.Popen(self.writer.argv(f"cat {q_qcow}"), stdout=subprocess.PIPE)
                try:
                    image = self.prism.upload_image(name, proc.stdout, size, cluster,
                                                    f"ev2ahv staging, plan {self.p['name']}")
                finally:
                    proc.stdout.close()
                    rc = proc.wait()
                if rc:
                    self.prism.delete_image(image)
                    raise RuntimeError(f"reading {qcow} on the writer failed (rc {rc})")
            finally:
                self.writer.run(f"rm -f {q_qcow} {q_qcow}.tmp")
            images[disk["name"]] = image
            self.state.set("images", images)

    def device_for(self, entry):
        """Find the staging device by serial and size, rescanning if it was offlined."""
        if entry.get("file"):
            have = int(self.writer.run(f"stat -c %s {shlex.quote(entry['file'])}").strip())
            if have != int(entry["size"]):
                raise RuntimeError(f"staging file {entry['file']} has the wrong size")
            return entry["file"]
        for attempt in range(2):
            for name, d in tgt.block_devices(self.writer).items():
                if d.get("serial") == entry["serial"]:
                    if int(d["size"]) != int(entry["size"]) or d.get("mountpoint"):
                        raise RuntimeError(f"device {name} does not match staging record")
                    return tgt.stable_path(self.writer, name)
            self.writer.rescan()
            time.sleep(2)
        raise RuntimeError(f"staging device with serial {entry['serial']} not found")

    # ---- steps -------------------------------------------------------------
    def prep_check(self):
        """Each item is PASS, WARN or FAIL. Cutover refuses on any FAIL unless forced."""
        checks = []
        def check(name, fn):
            try:
                detail = fn()
                checks.append({"name": name, "status": "PASS", "detail": detail or ""})
            except Exception as e:
                checks.append({"name": name, "status": "FAIL", "detail": str(e)[:300]})
        t = self.p["target"]
        check("Prism reachable", lambda: f"{len(self.prism.list_vms(flt=None, select='name'))} VMs visible")
        check("Cluster", lambda: self.prism.cluster_id(t["cluster"]))
        check("Storage container", lambda: self.prism.container_id(t["container"], self.prism.cluster_id(t["cluster"])))
        for nic in self.p.get("nics") or []:
            check(f"Subnet {nic['subnet']}", lambda n=nic: self.prism.subnet_id(n["subnet"], self.prism.cluster_id(t["cluster"])))
            if nic.get("mac"):
                def mac_free(n=nic):
                    owners = [o for o in self.prism.mac_owners(n["mac"]) if o != t["vm_name"]]
                    if owners:
                        raise RuntimeError(f"MAC {n['mac']} is already on {owners}")
                    return n["mac"]
                check(f"MAC {nic['mac']} free on AHV", mac_free)
        check("Writer VM", self.writer_vm)
        check("Writer ssh and helper", lambda: self.writer.caps()["python"])
        def target_free():
            vm = self.prism.vm_by_name(t["vm_name"])
            if vm and not self.step_done("created"):
                raise RuntimeError(f"VM {t['vm_name']} already exists")
            return "free" if not vm else "created by this plan"
        check("Target VM name", target_free)
        t_ = self.p["target"]
        if self.wants_conversion() and (self.p.get("guest") or {}).get("os") == "windows" and not self.direct_scsi_now():
            g_ = self.p["guest"]
            if g_.get("prep") != "auto" and not (g_.get("password_env") or g_.get("password_file")):
                checks.append({"name": "PCI to SCSI conversion", "status": "WARN",
                               "detail": "needs WinRM credentials in the guest section, so it will be skipped"})
            else:
                checks.append({"name": "PCI to SCSI conversion", "status": "PASS",
                               "detail": "runs after cutover. Set target.convert_to_scsi: false to stay on PCI"})
        if self.image_mode:
            def space():
                sizes = []
                for disk in self.p["disks"]:
                    r = self.reader(disk)
                    try:
                        sizes.append(builder.align(r.size()))
                    finally:
                        r.close()
                need = image_space_needed(sizes)
                base = self.p["target"]["staging_dir"]
                free = int(self.writer.run(f"mkdir -p {shlex.quote(base)}; df -B1 --output=avail {shlex.quote(base)} | tail -1").strip())
                if free < need:
                    raise RuntimeError(f"writer has {free} bytes free, worst case needs {need}")
                return f"{free} bytes free, worst case {need}"
            check("Writer staging space", space)
        for d in self.p["disks"]:
            def src(disk=d):
                r = self.reader(disk)
                try:
                    return f"{r.size()} bytes, helper {r.caps()['python']}"
                finally:
                    r.close()
            check(f"Source disk {d['name']}", src)
        checks += self.guest_checks()
        return checks

    # ---- guest prep (Move-like) ---------------------------------------------
    def guest(self):
        return guestlib.GuestPrep(self.p, self.dir)

    def guest_checks(self):
        g = self.p.get("guest")
        if not g:
            return [{"name": "Guest readiness", "status": "WARN", "detail": "plan has no guest section, so the guest was not checked"}]
        if g.get("prep") == "none":
            return [{"name": "Guest readiness", "status": "WARN", "detail": "guest.prep is none, so the guest was not checked"}]
        try:
            facts = self.guest().facts_for_check()
        except Exception as e:
            return [{"name": "Guest readiness", "status": "FAIL", "detail": f"cannot read the guest state: {str(e)[:250]}"}]
        if facts.get("on_ahv") and not self.step_done("created"):
            return [{"name": "Guest readiness", "status": "FAIL", "detail": "guest.address answers from AHV, not from the source"}]
        t = self.p["target"]
        self._guest_facts = facts
        return [dict(c, name="Guest: " + c["name"]) for c in guestlib.grade(g["os"], facts, t["disk_bus"], t["firmware"])]

    def guest_prep(self):
        gp = self.guest()
        if not self.p.get("guest"):
            raise RuntimeError("plan has no guest section")
        if gp.mode == "manual":
            return {"mode": "manual", "bundle": gp.bundle("prep")}
        if gp.mode == "none" or self.dry_run:
            return {"mode": gp.mode, "dry_run": self.dry_run}
        facts = gp.run("prep")
        self.state.set("guest_prep", {"time": facts.get("time"), "changes": facts.get("changes"), "problems": facts.get("problems")})
        self.state.event("guest-prep", changes=len(facts.get("changes") or []))
        t = self.p["target"]
        return {"mode": "auto", "changes": facts.get("changes"), "problems": facts.get("problems"),
                "checks": guestlib.grade(self.p["guest"]["os"], facts, t["disk_bus"], t["firmware"])}

    def guest_cleanup(self):
        gp = self.guest()
        if not self.p.get("guest"):
            raise RuntimeError("plan has no guest section")
        if gp.mode == "manual":
            return {"mode": "manual", "bundle": gp.bundle("cleanup")}   # files only, safe at any time
        if not self.step_done("verified"):
            raise RuntimeError("cleanup runs on the migrated VM, after a verified cutover")
        if gp.mode == "none" or self.dry_run:
            return {"mode": gp.mode, "dry_run": self.dry_run}
        facts = gp.run("cleanup", target=True)
        self.state.set("guest_cleanup", {"time": facts.get("time"), "changes": facts.get("changes"), "problems": facts.get("problems")})
        return {"mode": "auto", "changes": facts.get("changes"), "problems": facts.get("problems")}

    def _cbt_for(self, disk):
        """A dirty-bitmap tracker for this disk, or None (sync.cbt off, not a direct everRun source, no domain named)."""
        s = self.p["source"]
        if self.p["sync"].get("cbt", "off") != "auto" or s.get("type") != "direct" or not s.get("qemu_domain"):
            return None
        shell = self.source_shell()
        qmp = cbt.VirshQmp(shell, s["qemu_domain"])
        node = cbt.find_node(qmp, disk["path"])
        run = lambda argv: shell.run(" ".join(shlex.quote(a) for a in argv))
        return cbt.QemuBitmap(qmp, node, f"/var/tmp/ev2ahv-cbt-{self.p['name']}.sock", map_cmd=run, start_server=True)

    def _sync_disk(self, disk, reader, writer, algo, kw, verify, final):
        """One disk. Dirty-bitmap pass when tracking has been gap-free since the last full pass started, else a full
        pass (starting tracking first). Any doubt or error means a full pass: the bitmap is only an accelerator."""
        name, flag = disk["name"], "cbt:" + disk["name"]
        if final:
            rounds, stats = engine.final_sync(reader, writer, self.state, name, algo,
                                              verify=self.p["sync"].get("final_verify", "full"), **kw)
            return dict(stats, verified_rounds=rounds)
        bm = None
        if not verify:
            try:
                bm = self._cbt_for(disk)
            except Exception as e:
                log.warning("%s: changed-block tracking unavailable (%s), using a full pass", name, e)
        if bm is None and not verify:
            return dict(engine.sync(reader, writer, self.state, name, algo, verify_target=False, **kw))
        if bm is not None and self.state.get(flag) == "tracking":
            self.state.set(flag, "off")   # stays off unless this pass completes
            try:
                frozen = bm.rotate()
                chunks = cbt.ranges_to_chunks(bm.dirty(frozen), self.chunk)
                stats = engine.sync_dirty(reader, writer, self.state, name, algo, chunks, batch=kw["batch"],
                                          progress=kw.get("progress"))
                bm.drop(frozen)
                self.state.set(flag, "tracking")
                return dict(stats, cbt=True)
            except InterruptedError:
                raise
            except Exception as e:
                log.warning("%s: dirty-bitmap pass failed (%s), full pass instead", name, e)
        safe = False
        d = self.state.disk(name)
        if bm is not None and not (d and d["pass_active"]):   # a resumed pass has a gap, so it cannot start tracking
            try:
                bm.start()
                safe = True
            except Exception as e:
                log.warning("%s: cannot start tracking (%s)", name, e)
        stats = engine.sync(reader, writer, self.state, name, algo, verify_target=verify, **kw)
        if bm is not None:
            self.state.set(flag, "tracking" if safe else "off")
        elif verify:
            self.state.set(flag, "off")
        return dict(stats)

    def _sync_all(self, verify=False, final=False):
        results = {}
        for disk in self.p["disks"]:
            reader = self.reader(disk)
            try:
                size = reader.size()
                if self.dry_run:
                    log.info("[dry-run] would sync %s (%d bytes)", disk["name"], size)
                    results[disk["name"]] = {"size": size}
                    continue
                path = self.ensure_staging(disk, size)
                writer = BlockEndpoint(self.writer, path, chunk=self.chunk, writable=True)
                try:
                    algo = self.state.get("algo") or engine.negotiate(reader, writer, self.p["sync"]["algo"])
                    self.state.set("algo", algo)
                    kw = {"batch": int(self.p["sync"]["batch"]), "progress": self.progress}
                    stats = self._sync_disk(disk, reader, writer, algo, kw, verify, final)
                    results[disk["name"]] = dict(stats)
                finally:
                    writer.close()
            finally:
                reader.close()
        return results

    def seed(self):
        self.lock()
        began = time.time()
        res = self._sync_all()
        if not self.dry_run:
            self.timing(seed_seconds=round(time.time() - began, 1))
            self.state.set("phase", "seeded")
        return res

    def resync(self):
        self.lock()
        if not self.staging() and not self.dry_run:
            raise RuntimeError("nothing seeded yet. Run seed first")
        began = time.time()
        res = self._sync_all()
        if not self.dry_run:
            self.timing(last_resync_seconds=round(time.time() - began, 1))
        return res

    def _source_power(self, action):
        power = self.p["source"].get("power") or {}
        cmd = power.get(action)
        if not cmd:
            return None
        shell = LocalTransport() if power.get("local") else self.source_shell()
        return shell.run(cmd, timeout=int(power.get("timeout", 300))).strip()

    def source_stopped(self):
        out = self._source_power("status")
        if out is None:
            return None
        if not re.search(r"[a-z]{3}", out, re.I):
            raise RuntimeError(f"cannot read the source power state from {out!r}. Check source.power.status")
        return bool(re.search(r"\b(stopped|shut ?off|off)\b", out, re.I))

    def stop_source(self, assume_stopped=False):
        state = self.source_stopped()
        if state is None:
            if not assume_stopped:
                raise RuntimeError("plan has no source.power.status. Stop the source yourself, "
                                   "then pass --source-already-stopped")
            return
        if state:
            return
        self._source_power("stop")
        deadline = time.time() + int(self.p["source"].get("stop_timeout", 300))
        while time.time() < deadline:
            if self.source_stopped():
                return
            time.sleep(3)
        raise RuntimeError("source did not stop in time. Nothing on AHV was changed")

    def cutover(self, assume_stopped=False, force=False):
        self.lock()
        t = self.p["target"]
        if not self.step_done("stopped") and not self.dry_run:
            checks = self.prep_check()
            self.state.set("cutover_checks", checks)
            failed = [f"{c['name']}: {c['detail']}" for c in checks if c["status"] == "FAIL"]
            if failed and not force:
                raise RuntimeError("prep-check has FAIL items, so cutover did not start. The source was not touched:\n  "
                                   + "\n  ".join(failed) + "\nFix them, or pass --force to override")
            if failed:
                self.state.event("cutover-forced", failed=failed)
                log.warning("cutover forced past %d FAIL item(s)", len(failed))
            self.state.set("direct_scsi", self.direct_scsi_now())
        if self.dry_run:
            log.info("[dry-run] stop source, verified final sync, clone %d disk(s) into %s, power on, verify",
                     len(self.p["disks"]), t["vm_name"])
            return {"dry_run": True}
        if not self.staging():
            raise RuntimeError("nothing seeded yet. Run seed first")
        if not self.step_done("created") and self.prism.vm_by_name(t["vm_name"]):
            raise RuntimeError(f"VM {t['vm_name']} already exists and holds older data. "
                               "Run discard-target first. The source was not touched")
        began = self.state.get("downtime_start") or time.time()
        if not self.step_done("stopped"):
            began = time.time()     # downtime includes the guest shutdown itself
            self.state.set("downtime_start", began)
            try:
                self.stop_source(assume_stopped)
            except Exception:
                self.state.set("downtime_start", None)
                raise
            self.mark("stopped")
        elif self.source_stopped() is False:
            raise RuntimeError("source is running again. Run rollback before retrying the cutover")
        if not self.step_done("synced"):
            t0 = time.time()
            self.state.set("final_sync", self._sync_all(final=True))
            self.timing(final_sync_seconds=round(time.time() - t0, 1),
                        final_verify=self.p["sync"].get("final_verify", "full"))
            for d in self.p["disks"]:   # the guest is off, so its bitmaps are gone: a later resync starts tracking afresh
                if self.state.get("cbt:" + d["name"]) is not None:
                    self.state.set("cbt:" + d["name"], "off")
            self.mark("synced")
        staging = self.staging()
        writer_vm = self.writer_vm()
        if not self.step_done("quiesced"):
            if self.image_mode:
                t0 = time.time()
                self.upload_images()
                self.timing(image_upload_seconds=round(time.time() - t0, 1))
            else:
                for entry in staging.values():
                    tgt.quiesce_device(self.writer, self.device_for(entry))
            self.mark("quiesced")
        vm_id = self.state.get("target_vm")
        if not self.step_done("created"):
            cluster = self.prism.cluster_id(t["cluster"])
            subnets = {n["subnet"]: self.prism.subnet_id(n["subnet"], cluster) for n in self.p.get("nics") or []}
            images = self.state.get("images", {})
            sources = [(staging[d["name"]]["size"],
                        self.prism.image_ref(images[d["name"]]) if self.image_mode
                        else self.prism.disk_ref(writer_vm, staging[d["name"]]["disk_id"]))
                       for d in self.p["disks"]]
            vm_id = self.prism.create_vm(builder.vm_payload(self.create_plan(), cluster, sources, subnets))
            self.state.set("target_vm", vm_id)
            if t.get("cloud_init_file"):
                with open(os.path.expanduser(t["cloud_init_file"]), "rb") as fh:
                    self.prism.customize_cloud_init(vm_id, base64.b64encode(fh.read()).decode())
            if t.get("project"):
                self.prism.set_project(vm_id, self.prism.project_id(t["project"]))
            self.mark("created")
        if not self.step_done("powered"):
            # MAC and IP are kept, so the source must be off before the target starts.
            if self.source_stopped() is False:
                raise RuntimeError("source is running. Refusing to power on the AHV VM with the same MAC")
            if self.source_stopped() is None and not assume_stopped:
                raise RuntimeError("cannot confirm the source is off. Pass --source-already-stopped")
            self.prism.power(vm_id, "power-on")
            self.mark("powered")
        if not self.step_done("verified"):
            try:
                self.state.set("verify", self.verify())
            except Exception as e:
                if not self.direct_scsi():
                    raise
                self.state.set("verify", self.revert_direct_scsi(vm_id, e))
            self.timing(downtime_seconds=round(time.time() - began, 1))
            self.state.set("phase", "cutover-complete")
            self.mark("verified")
        if not self.step_done("converted"):
            if self.wants_conversion():
                t0 = time.time()
                self.state.set("conversion", self.convert_to_scsi(vm_id))
                self.timing(conversion_seconds=round(time.time() - t0, 1),
                            downtime_seconds=round(time.time() - began, 1))
            elif self.direct_scsi():
                self.state.set("conversion", {"result": "direct SCSI", "reason": "vioscsi was boot-ready, no conversion needed",
                                              "disks": len(self._bus_disks(vm_id, "SCSI"))})
            self.mark("converted")
        return {"vm": vm_id, "timings": self.state.get("timings"), "verify": self.state.get("verify"),
                "conversion": self.state.get("conversion")}

    # ---- PCI to SCSI conversion ---------------------------------------------
    def wants_conversion(self):
        t = self.p["target"]
        return t["disk_bus"] == "pci" and t.get("convert_to_scsi", True) and not self.direct_scsi()

    def direct_scsi_now(self):
        """True when a Windows plan on PCI (with convert_to_scsi) can boot straight from SCSI: the guest facts of the
        last prep-check say vioscsi is boot-ready. Anything else keeps the PCI boot and the conversion after cutover."""
        t, g = self.p["target"], self.p.get("guest") or {}
        return bool(g.get("os") == "windows" and t["disk_bus"] == "pci" and t.get("convert_to_scsi", True)
                    and self._guest_facts is not None and guestlib.scsi_boot_ready(self._guest_facts))

    def direct_scsi(self):
        """The decision recorded when the cutover started (stays the same if the cutover is resumed)."""
        return bool(self.state.get("direct_scsi"))

    def create_plan(self):
        """The plan the VM is created from: all disks on SCSI when the guest was ready to boot from it directly."""
        if not self.direct_scsi():
            return self.p
        q = dict(self.p, target=dict(self.p["target"], disk_bus="scsi"))
        log.info("vioscsi is boot-ready: creating %s on SCSI directly, no PCI to SCSI conversion", self.p["target"]["vm_name"])
        return q

    def revert_direct_scsi(self, vm_id, err):
        """The direct SCSI boot failed verification: move the disks to PCI (which the guest always boots from)."""
        log.warning("direct SCSI boot failed verification (%s). Moving %s to PCI", err, self.p["target"]["vm_name"])
        self.prism.power(vm_id, "power-off")
        self.prism.wait_power(vm_id, "OFF")
        scsi = self._bus_disks(vm_id, "SCSI")
        self._move_disks(vm_id, "SCSI", "PCI", 0)
        self.prism.set_boot_disk(vm_id, "PCI", 0)
        self.prism.power(vm_id, "power-on")
        v = self.verify()
        self.state.set("direct_scsi", False)
        self.state.set("conversion", {"result": "direct SCSI reverted to PCI", "reason": str(err)[:300], "disks": len(scsi)})
        return v

    def _bus_disks(self, vm_id, bus):
        return sorted((d for d in self.prism.vm(vm_id).get("disks") or [] if d["diskAddress"]["busType"] == bus),
                      key=lambda d: d["diskAddress"]["index"])

    def _wait_vioscsi(self, access, timeout=300):
        deadline = time.time() + timeout
        while time.time() < deadline:
            rc, out, _ = access.ps('$d = @(Get-CimInstance Win32_PnPEntity -Filter "Service = \'vioscsi\'"); '
                                   '$s = (Get-ItemProperty HKLM:\\SYSTEM\\CurrentControlSet\\Services\\vioscsi -ErrorAction SilentlyContinue).Start; '
                                   '"{0}|{1}|{2}" -f $d.Count, (@($d | ForEach-Object Status) -join ","), $s')
            parts = out.strip().split("|")
            if rc == 0 and len(parts) == 3 and parts[0] not in ("", "0") and "OK" in parts[1] and parts[2] == "0":
                return
            time.sleep(10)
        raise RuntimeError("Windows did not install vioscsi for the temporary SCSI disk")

    def _move_disks(self, vm_id, src_bus, dst_bus, boot_index):
        """Clone every disk on src_bus to dst_bus (VM is off), point boot at the clone, then drop the originals."""
        disks = self._bus_disks(vm_id, src_bus)
        new_boot = None
        for i, d in enumerate(disks):
            self.prism.add_disk(vm_id, d["backingInfo"]["diskSizeBytes"], bus=dst_bus, index=i,
                                clone_from=self.prism.disk_ref(vm_id, d["extId"]))
            if d["diskAddress"]["index"] == boot_index:
                new_boot = i
        self.prism.set_boot_disk(vm_id, dst_bus, new_boot if new_boot is not None else 0)
        for d in disks:
            self.prism.remove_vm_disk(vm_id, d["extId"])

    def convert_to_scsi(self, vm_id):
        """Moves a verified VM from virtio-blk (PCI) to virtio-scsi. Windows first gets a temporary SCSI disk so it
        installs vioscsi from the driver store. If the SCSI boot fails verification, the VM is moved back to PCI."""
        pci = self._bus_disks(vm_id, "PCI")
        if not pci:
            return {"result": "already SCSI"}
        boot = ((self.prism.vm(vm_id).get("bootConfig") or {}).get("bootDevice") or {}).get("diskAddress") or {}
        boot_index = boot.get("index", 0) if boot.get("busType") == "PCI" else 0
        g = self.p.get("guest") or {}
        if g.get("os") == "windows":
            try:
                access = self.guest().access(target=True)
            except Exception as e:
                return {"result": "skipped", "reason": f"needs WinRM access to the migrated VM: {e}"}
            cluster = self.prism.cluster_id(self.p["target"]["cluster"])
            container = self.prism.container_id(self.p["target"]["container"], cluster)
            dummy = self.prism.add_disk(vm_id, 1024 ** 3, container=container, bus="SCSI")
            try:
                self._wait_vioscsi(access)
            except Exception as e:
                self.prism.remove_vm_disk(vm_id, dummy)
                return {"result": "skipped", "reason": str(e)}
        else:
            dummy = None
        log.info("converting %s from PCI to SCSI", self.p["target"]["vm_name"])
        self.prism.power(vm_id, "shutdown")
        self.prism.wait_power(vm_id, "OFF", 900)
        if dummy:
            self.prism.remove_vm_disk(vm_id, dummy)
        self._move_disks(vm_id, "PCI", "SCSI", boot_index)
        self.prism.power(vm_id, "power-on")
        try:
            self.state.set("verify_scsi", self.verify())
            return {"result": "converted", "disks": len(pci)}
        except Exception as e:
            log.warning("SCSI boot failed verification (%s). Moving %s back to PCI", e, self.p["target"]["vm_name"])
            self.prism.power(vm_id, "power-off")
            self.prism.wait_power(vm_id, "OFF")
            self._move_disks(vm_id, "SCSI", "PCI", 0)
            self.prism.set_boot_disk(vm_id, "PCI", boot_index)
            self.prism.power(vm_id, "power-on")
            self.verify()
            return {"result": "reverted to PCI", "reason": str(e)[:300]}

    def convert(self):
        """CLI entry: convert a migrated VM later, for plans that kept PCI."""
        self.lock()
        if not self.step_done("verified"):
            raise RuntimeError("convert-to-scsi runs on a migrated, verified VM")
        vm_id = self.state.get("target_vm")
        t0 = time.time()
        r = self.convert_to_scsi(vm_id)
        self.state.set("conversion", r)
        self.timing(conversion_seconds=round(time.time() - t0, 1))
        return r

    def verify(self):
        v = self.p.get("verify") or {}
        out = {"power": self.prism.vm(self.state.get("target_vm")).get("powerState")}
        if out["power"] != "ON":
            raise RuntimeError("target VM is not powered on")
        if v.get("ip"):
            port = int(v.get("port", 22))
            deadline = time.time() + int(v.get("timeout", 420))
            while True:
                try:
                    socket.create_connection((v["ip"], port), 5).close()
                    break
                except OSError:
                    if time.time() > deadline:
                        raise RuntimeError(f"target did not answer on {v['ip']}:{port}. "
                                           "Check the console, or run rollback") from None
                    time.sleep(5)
            out["reachable"] = f"{v['ip']}:{port}"
        if v.get("ssh_user") and v.get("commands"):
            shell = SshTransport(v["ip"], v["ssh_user"], os.path.join(tgt.CONFIG, "id_ed25519"), port=int(v.get("port", 22)),
                                 sudo=v.get("sudo", True), known_hosts=os.path.join(self.dir, "known_hosts"))
            deadline = time.time() + 180
            while True:
                try:
                    shell.run("true", timeout=30)
                    break
                except Exception:
                    if time.time() > deadline:
                        raise
                    time.sleep(5)
            out["commands"] = {c: shell.run(c, timeout=600).strip() for c in v["commands"]}
        for cmd in v.get("local_commands") or []:   # e.g. a WinRM check for Windows guests
            out.setdefault("local", {})[cmd] = LocalTransport().run(cmd, timeout=900).strip()
        return out

    def rollback(self):
        """Power off the AHV VM, then power the source back on. Nothing is deleted."""
        self.lock()
        vm_id = self.state.get("target_vm")
        if self.dry_run:
            log.info("[dry-run] power off %s, start source", self.p["target"]["vm_name"])
            return {"dry_run": True}
        if vm_id:
            self.prism.power(vm_id, "power-off")
            # Same MAC and IP: never start the source while the AHV copy may still be on.
            deadline = time.time() + 300
            while self.prism.vm(vm_id).get("powerState") != "OFF":
                if time.time() > deadline:
                    raise RuntimeError("AHV VM did not power off. The source was NOT started")
                time.sleep(5)
        started = self._source_power("start")
        status = self._source_power("status")
        deadline = time.time() + 300
        while status is not None and self.source_stopped() and time.time() < deadline:
            time.sleep(5)       # the start command returns before the hypervisor reports the new state
            status = self._source_power("status")
        self.state.set("cutover_step", None)
        self.state.set("downtime_start", None)
        self.state.set("phase", "rolled-back")
        self.state.event("rollback", target=vm_id)
        return {"target_powered_off": bool(vm_id), "source_start": started, "source_status": status}

    def forget_target_host_key(self):
        """The pinned ssh key belonged to the VM just deleted. A rebuilt VM may legitimately differ."""
        path = os.path.join(self.dir, "known_hosts")
        if os.path.exists(path):
            os.remove(path)

    def discard_target(self):
        """Delete the migrated VM after a rollback, keeping staging disks so resync stays incremental."""
        self.lock()
        vm_id = self.state.get("target_vm")
        if self.step_done("verified"):
            raise RuntimeError("cutover is complete. Run rollback first if you really want to discard the VM")
        if vm_id and not self.dry_run:
            self.prism.delete_vm(vm_id)
            self.forget_target_host_key()
            self.state.set("target_vm", None)
            self.state.set("cutover_step", None)
        return {"discarded": vm_id, "dry_run": self.dry_run}

    def cleanup(self, delete_target=False):
        """Removes staging vdisks and, only on request, the migrated VM. Never touches the source."""
        self.lock()
        out = {"removed_staging": [], "deleted_target": False}
        if self.staging():
            vm = self.writer_vm()
            remaining = dict(self.staging())
            for name, entry in list(remaining.items()):
                if self.dry_run:
                    log.info("[dry-run] remove staging %s", entry.get("disk_id") or entry["file"])
                    continue
                if entry.get("file"):
                    raw = safe_staging_file(entry["file"], self.p["target"].get("staging_dir"))
                    self.writer.run(f"rm -f {shlex.quote(raw)} {shlex.quote(raw[:-4] + '.qcow2')}")
                else:
                    self.prism.remove_vm_disk(vm, entry["disk_id"])
                out["removed_staging"].append(name)
                del remaining[name]
                self.state.set("staging", remaining)
        for disk in self.p["disks"]:
            image = self.prism.image_by_name(self.image_name(disk))
            if image:
                self.prism.delete_image(image["extId"])
                out.setdefault("removed_images", []).append(image["name"])
        if not self.dry_run:
            self.state.set("images", {})
        if delete_target and self.state.get("target_vm"):
            if not self.dry_run:
                self.prism.delete_vm(self.state.get("target_vm"))
                self.forget_target_host_key()
                self.state.set("target_vm", None)
                self.state.set("cutover_step", None)
            out["deleted_target"] = not self.dry_run
        if not self.dry_run and not self.staging():
            with self.state.db:
                self.state.db.execute("DELETE FROM chunks")
                self.state.db.execute("DELETE FROM disks")
            self.state.set("algo", None)
            self.state.set("phase", "cleaned")
        return out

    def status(self):
        return {"plan": self.p["name"], "phase": self.state.get("phase", "new"),
                "cutover_step": self.state.get("cutover_step"), "target_vm": self.state.get("target_vm"),
                "staging": self.staging(), "timings": self.state.get("timings", {}),
                "disks": [self.state.disk(d["name"]) for d in self.p["disks"]]}

    def close(self):
        if self._writer:
            self._writer.close()
        self.state.close()
        if self._lock:
            self._lock.close()
