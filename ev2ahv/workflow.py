"""The migration workflow. Every step is idempotent and records progress in SQLite.
The source is only ever read, stopped and started. It is never modified or deleted."""
import base64, fcntl, logging, os, re, shlex, socket, subprocess, time

from . import builder, engine, target as tgt
from .readers import make_reader
from .state import State
from .transport import BlockEndpoint, LocalTransport, SshTransport

log = logging.getLogger("ev2ahv.workflow")
DATA_DIR = os.path.expanduser(os.environ.get("EV2AHV_DATA", "~/.local/share/ev2ahv"))
STEPS = ("stopped", "synced", "quiesced", "created", "powered", "verified")


def plan_dir(name):
    return os.path.join(DATA_DIR, re.sub(r"[^\w.-]", "_", name))


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
            self._writer = tgt.writer_transport(w["host"], w.get("user", "nutanix"))
            self._writer.setup()
        return self._writer

    def writer_vm(self):
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
            path = self.staging_file(disk)
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
        serial = path.rsplit("scsi-SNUTANIX_VDISK_", 1)[-1]
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
            raw = self.staging()[disk["name"]]["file"]
            qcow = raw[:-4] + ".qcow2"
            q_raw, q_qcow = shlex.quote(raw), shlex.quote(qcow)
            self.writer.run(f"sync; qemu-img convert -f raw -O qcow2 {q_raw} {q_qcow}.tmp && mv -f {q_qcow}.tmp {q_qcow}", timeout=14400)
            size = int(self.writer.run(f"stat -c %s {q_qcow}").strip())
            proc = subprocess.Popen(self.writer.argv(f"cat {q_qcow}"), stdout=subprocess.PIPE)
            try:
                image = self.prism.upload_image(name, proc.stdout, size, cluster, f"ev2ahv staging, plan {self.p['name']}")
            finally:
                proc.stdout.close()
                rc = proc.wait()
            if rc:
                self.prism.delete_image(image)
                raise RuntimeError(f"reading {qcow} on the writer failed (rc {rc})")
            images[disk["name"]] = image
            self.state.set("images", images)
            self.writer.run(f"rm -f {q_qcow}")

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
            self.writer.run("for h in /sys/class/scsi_host/host*/scan; do echo '- - -' > $h; done", check=False)
            time.sleep(2)
        raise RuntimeError(f"staging device with serial {entry['serial']} not found")

    # ---- steps -------------------------------------------------------------
    def prep_check(self):
        checks = []
        def check(name, fn):
            try:
                detail = fn()
                checks.append({"name": name, "ok": True, "detail": detail or ""})
            except Exception as e:
                checks.append({"name": name, "ok": False, "detail": str(e)[:300]})
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
        if self.image_mode:
            def space():
                need = 0
                for disk in self.p["disks"]:
                    r = self.reader(disk)
                    try:
                        need += 2 * builder.align(r.size())
                    finally:
                        r.close()
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
        return checks

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
                    if final:
                        rounds, stats = engine.final_sync(reader, writer, self.state, disk["name"], algo, **kw)
                        stats = dict(stats, verified_rounds=rounds)
                    else:
                        stats = engine.sync(reader, writer, self.state, disk["name"], algo, verify_target=verify, **kw)
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
        return shell.run(cmd, timeout=300).strip()

    def source_stopped(self):
        out = self._source_power("status")
        if out is None:
            return None
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

    def cutover(self, assume_stopped=False):
        self.lock()
        t = self.p["target"]
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
            self.stop_source(assume_stopped)
            began = time.time()
            self.state.set("downtime_start", began)
            self.mark("stopped")
        elif self.source_stopped() is False:
            raise RuntimeError("source is running again. Run rollback before retrying the cutover")
        if not self.step_done("synced"):
            t0 = time.time()
            self.state.set("final_sync", self._sync_all(final=True))
            self.timing(final_sync_seconds=round(time.time() - t0, 1))
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
            vm_id = self.prism.create_vm(builder.vm_payload(self.p, cluster, sources, subnets))
            self.state.set("target_vm", vm_id)
            if t.get("cloud_init_file"):
                with open(os.path.expanduser(t["cloud_init_file"]), "rb") as fh:
                    self.prism.customize_cloud_init(vm_id, base64.b64encode(fh.read()).decode())
            self.mark("created")
        if not self.step_done("powered"):
            self.prism.power(vm_id, "power-on")
            self.mark("powered")
        if not self.step_done("verified"):
            self.state.set("verify", self.verify())
            self.timing(downtime_seconds=round(time.time() - began, 1))
            self.state.set("phase", "cutover-complete")
            self.mark("verified")
        return {"vm": vm_id, "timings": self.state.get("timings"), "verify": self.state.get("verify")}

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
        started = self._source_power("start")
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
                    q = shlex.quote(entry["file"])
                    self.writer.run(f"rm -f {q} {shlex.quote(entry['file'][:-4] + '.qcow2')}")
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
