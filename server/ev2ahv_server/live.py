"""Live mode: real environments and the real engine behind the same API the mock serves.

Each VM in a plan becomes one engine plan (YAML under ~/.local/share/ev2ahv/ui/<plan id>/). Engine steps run
through runner.py in a child process. A worker loop moves every VM through its pipeline:

  Preparing: guest-prep (automatic mode) -> prep-check     Seeding: seed
  Ready to Cutover: resync every schedule.resync_minutes   Cutover in Progress: [discard-target, resync] cutover
  Rolling Back: rollback

Job state is not kept across restarts: a VM whose job vanished is simply resumed, since every engine step can be
rerun (sync continues from its last committed chunk, cutover skips finished steps).
"""
import glob, json, logging, os, re, shlex, subprocess, sys, threading, time

import yaml

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from ev2ahv import credentials, everrun, plan as planlib  # noqa: E402
from ev2ahv.prism import Prism  # noqa: E402

DATA = os.path.expanduser("~/.local/share/ev2ahv/ui")
SECRETS = os.path.expanduser("~/.config/ev2ahv/ui-secrets")
# Name guard: only VMs whose name starts with settings["source_name_prefix"] can be migrated (default "ev2ahv-",
# because the lab's other VMs belong to someone else). An empty prefix allows any VM; Appliance Settings changes it.
# EV2AHV_ALLOW_ANY_SOURCE=1 is the old switch and still overrides the setting (deprecated).
GUARD_PREFIX = "ev2ahv-"
ALLOW_ANY = os.environ.get("EV2AHV_ALLOW_ANY_SOURCE") == "1"


def guard_prefix(settings=None):
    if ALLOW_ANY:
        return ""
    p = (settings or {}).get("source_name_prefix", GUARD_PREFIX)
    return p if isinstance(p, str) else GUARD_PREFIX


def name_allowed(name, settings=None):
    return name.startswith(guard_prefix(settings))
# On the appliance the appliance itself is the writer; elsewhere the lab's helper VM.
DEFAULT_WRITER = ({"local": True} if os.environ.get("EV2AHV_APPLIANCE") == "1"
                  else {"vm": "ev2ahv-writer", "host": "10.12.136.211", "user": "nutanix"})
log = logging.getLogger("ev2ahv.live")


class LiveError(Exception):
    pass


def _ssh(node, cmd):
    t = everrun.SshTransport(node, "root", os.path.join(everrun.CONFIG, "id_ed25519"),
                             known_hosts=os.path.join(everrun.CONFIG, "known_hosts"))
    try:
        return t.run(cmd)
    finally:
        getattr(t, "close", lambda: None)()


def prism_for(provider, secret):
    key, pw = secret(f"provider:{provider['id']}:api_key"), secret(f"provider:{provider['id']}:password")
    c = credentials.Credentials(provider["address"], api_key=key, username=None if key else provider["username"],
                                password=None if key else pw, source="ui")
    return Prism(c)


def prism_env(provider, secret):
    key, pw = secret(f"provider:{provider['id']}:api_key"), secret(f"provider:{provider['id']}:password")
    env = {"NTNX_PC": provider["address"]}
    if key:
        env["NTNX_API_KEY"] = key
    else:
        env.update(NTNX_USERNAME=provider["username"], NTNX_PASSWORD=pw or "")
    return env


# ------------------------------------------------------------------ environments

def probe(body):
    """Validate an environment before saving it. Returns {'version': ...}."""
    if body.type == "aos":
        c = credentials.Credentials(body.address, api_key=body.api_key, username=None if body.api_key else body.username,
                                    password=None if body.api_key else body.password, source="ui")
        try:
            n = len(Prism(c).list_vms(select="name"))
        except Exception as e:
            raise LiveError(f"Cannot reach Prism Central {body.address}: {e}")
        return {"version": f"Prism Central ({n} VMs visible)"}
    if body.type == "plans":
        d = os.path.expanduser(body.address)
        if not os.path.isdir(d):
            raise LiveError(f"{d} is not a directory on the ev2ahv server")
        return {"version": "plan files"}
    errs = []
    for n in body.node_addresses:
        try:
            _ssh(n, "virsh -r list --all --name >/dev/null")
        except Exception as e:
            errs.append(f"{n}: {e}")
    if errs:
        raise LiveError("everRun node(s) not reachable over SSH with the ev2ahv key: " + "; ".join(errs))
    return {"version": "everRun"}


def refresh(doc, secret, settings=None):
    t = doc["type"]
    if t == "aos":
        pr = prism_for(doc, secret)
        doc["clusters"] = [{"id": c["extId"], "name": c["name"]} for c in pr._all("/clustermgmt/v4.2/config/clusters")
                           if "PRISM_CENTRAL" not in (c.get("config", {}).get("clusterFunction") or [])]
        doc["containers"] = [{"id": c["containerExtId"], "name": c["name"], "cluster": c.get("clusterExtId")}
                             for c in pr._all("/clustermgmt/v4.2/config/storage-containers")
                             if not c["name"].startswith(("NutanixManagementShare", "SelfServiceContainer"))]
        doc["subnets"] = [{"id": s["extId"], "name": s["name"]} for s in pr._all("/networking/v4.0/config/subnets")]
        doc["vm_count"] = len(pr.list_vms(select="name"))
    elif t == "plans":
        doc["workloads"] = _plan_file_workloads(os.path.expanduser(doc["address"]), settings)
    else:
        doc["workloads"] = _everrun_workloads(doc["node_addresses"], settings)
    doc["vm_count"] = len(doc.get("workloads", [])) if t != "aos" else doc["vm_count"]
    doc["status"], doc["error"], doc["last_refreshed"] = "Connected", None, time.time()
    return doc


def _everrun_workloads(nodes, settings=None):
    states = {}
    for n in nodes:
        out = _ssh(n, "for d in $(virsh -r list --all --name); do echo \"$d $(virsh -r domstate $d | head -1)\"; done")
        for line in out.splitlines():
            if line.strip():
                name, state = line.split(" ", 1)
                states.setdefault(name, {})[n] = state.strip()
    out = []
    for name, st in sorted(states.items()):
        running = [n for n, s in st.items() if s == "running"]
        host = running[0] if running else next(iter(st))
        xml = _ssh(host, f"virsh -r dumpxml {shlex.quote(name)}")
        info = everrun.parse_domain(xml)
        windows = "<hyperv" in xml
        warn = []
        if not name_allowed(name, settings):
            warn.append(f"Name guard: only {guard_prefix(settings)}* VMs can be migrated (Appliance Settings)")
        if windows and all(d["dev"].startswith("vd") for d in info["disks"]):
            warn.append("virtio-blk only: boot disk must stay PCI until conversion")
        if len(running) != 1:
            warn.append(f"Not running on exactly one node ({st})")
        size = 0
        for d in info["disks"]:
            try:
                size += int(_ssh(host, f"stat -c %s {shlex.quote(d['path'])}").strip()) - everrun.TRAILER_BYTES
            except Exception:
                pass
        out.append({"id": name, "name": name, "os_type": "windows" if windows else "linux",
                    "os_name": "Windows" if windows else "Linux", "power_state": "On" if running else "Off",
                    "availability": "",   # virsh shows a paused partner for both FT and HA guests
                    "active_node": host, "vcpus": info["vcpus"], "memory_mib": info["memory_mib"],
                    "firmware": info["firmware"], "disks": len(info["disks"]), "total_bytes": size,
                    "nics": [{"network": "sharednetwork", "mac": n["mac"], "ip": None} for n in info["nics"]],
                    "warnings": warn})
    return out


def _plan_file_workloads(d, settings=None):
    out = []
    for f in sorted(glob.glob(os.path.join(d, "*.yaml"))):
        try:
            p = planlib.load(f)
        except Exception as e:
            out.append({"id": os.path.basename(f), "name": os.path.basename(f), "os_type": "other",
                        "power_state": "Off", "warnings": [f"Invalid plan: {e}"]})
            continue
        g = p.get("guest") or {}
        t = p["target"]
        warn = ([] if name_allowed(t["vm_name"], settings)
                else [f"Name guard: target must be {guard_prefix(settings)}* (Appliance Settings)"])
        out.append({"id": os.path.basename(f), "name": p["name"], "os_type": g.get("os", "other"),
                    "os_name": f"{g.get('os', 'unknown')} ({os.path.basename(f)})", "power_state": "On",
                    "availability": "", "active_node": p["source"].get("host", ""),
                    "vcpus": int(t.get("cores", 1)) * int(t.get("sockets", 1)), "memory_mib": int(t["memory_mib"]),
                    "firmware": t.get("firmware", "bios"), "disks": len(p["disks"]),
                    "total_bytes": sum(int(x.get("size_bytes") or 0) for x in p["disks"]),
                    "nics": [{"network": n["subnet"], "mac": n.get("mac", ""), "ip": g.get("address")}
                             for n in p.get("nics") or []], "warnings": warn,
                    "_file": f, "_target": t["vm_name"]})
    return out


# ------------------------------------------------------------------ engine plans

def _secret_file(plan_id, values):
    os.makedirs(SECRETS, mode=0o700, exist_ok=True)
    path = os.path.join(SECRETS, f"{plan_id}.env")
    fd = os.open(path + ".tmp", os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as fh:
        for k, v in values.items():
            if v:
                fh.write(f"{k}={v}\n")
    os.replace(path + ".tmp", path)
    return path


def engine_plan(plan, vm, src, wl, secret, settings):
    """Build (or rebuild) the engine plan for one VM and return its path."""
    vs = (plan.get("vm_settings") or {}).get(vm["id"]) or {}
    prep = plan["prep"]
    nm = plan.get("network_map") or []
    subnet = nm[0]["target_subnet"] if nm else None
    if src["type"] == "plans":
        p = planlib.load(wl["_file"])
        if (settings.get("writer") or DEFAULT_WRITER).get("local"):
            p["writer"] = {"local": True}
    else:
        if not name_allowed(vm["name"], settings):
            raise LiveError(f"Name guard: only {guard_prefix(settings)}* source VMs can be migrated. "
                            "Change it in Appliance Settings")
        info = everrun.discover(vm["name"], src["node_addresses"])
        p = everrun.build_plan(info, vs.get("target_name") or vm["name"], subnet or "VLAN136", "avcli",
                               writer=settings.get("writer") or DEFAULT_WRITER, disk_bus=vs.get("disk_bus") or "scsi",
                               plan_name=f"ui-{plan['name']}-{vm['name']}")
        # everRun web API (no Java); the password reaches the command only through EV2AHV_EVERRUN_PASS
        api = (f"{shlex.quote(sys.executable)} -I {shlex.quote(os.path.join(ROOT, 'ev2ahv', 'everrun_api.py'))} "
               f"--host {shlex.quote(src['address'])} --user {shlex.quote(src['username'])}")
        q = shlex.quote(vm["name"])
        p["source"]["power"].update(status=f"{api} status {q}", stop=f"{api} stop {q}", start=f"{api} start {q}")
    t = p["target"]
    if vs.get("target_name"):
        t["vm_name"] = vs["target_name"]
    for k_ui, k_eng in (("vcpus", "cores"), ("memory_mib", "memory_mib"), ("firmware", "firmware"),
                        ("disk_bus", "disk_bus"), ("convert_to_scsi", "convert_to_scsi")):
        if vs.get(k_ui) is not None:
            t[k_eng] = vs[k_ui]
    if vs.get("vcpus"):
        t["sockets"] = 1
    if plan.get("cluster"):
        t["cluster"] = plan["cluster"]
    if plan.get("container"):
        t["container"] = plan["container"]
    for n in p.get("nics") or []:
        if subnet:
            n["subnet"] = subnet
        n["keep_mac"] = bool(prep.get("retain_mac", True))
    if src["type"] == "plans" and not p.get("guest"):
        p.pop("guest", None)   # plan file without guest handling (e.g. the fake source): keep it that way
        return _write(plan, vm, p)
    g = p.get("guest") or {}
    if src["type"] != "plans":
        g = {"os": wl["os_type"] if wl["os_type"] in ("windows", "linux") else "linux"}
        addr = vs.get("guest_address") or next((n.get("ip") for n in wl.get("nics", []) if n.get("ip")), None)
        if addr:
            g["address"] = addr
            p["verify"] = {"ip": addr, "port": 5985 if g["os"] == "windows" else 22, "timeout": 900,
                           **({"ssh_user": "root", "commands": ["hostname"]} if g["os"] == "linux" else {})}
    g["prep"] = "none" if prep.get("bypass_guest_ops") else ("auto" if prep.get("mode") == "automatic" else "manual")
    if g["prep"] == "auto" and not g.get("address"):
        g["prep"] = "manual"   # no address to reach the guest: the UI says so in the checks
    g["ngt"] = bool(prep.get("install_ngt"))
    if prep.get("virtio_source"):
        g["virtio_iso"] = prep["virtio_source"]
    winpw, linpw = secret(f"plan:{plan['id']}:windows_password"), secret(f"plan:{plan['id']}:linux_password")
    if g.get("os") == "windows":
        g["user"] = prep.get("windows_user") or "Administrator"
        if winpw:
            g["password_env"], g["password_file"] = "EV2AHV_WIN_PASS", _secret_file(plan["id"], {"EV2AHV_WIN_PASS": winpw})
    elif g.get("os") == "linux":
        g["user"] = prep.get("linux_user") or "root"
    p["guest"] = g
    return _write(plan, vm, p)


def _write(plan, vm, p):
    planlib.validate(p)
    d = os.path.join(DATA, plan["id"])
    os.makedirs(d, exist_ok=True)
    path = os.path.join(d, re.sub(r"[^\w.-]", "_", vm["id"]) + ".yaml")
    planlib.dump(p, path)
    return path


TIMINGS = ("downtime_seconds", "final_sync_seconds", "image_upload_seconds", "conversion_seconds", "last_resync_seconds")


def cutover_summary(r, now=None):
    """Reduce the engine's cutover result to what the plan keeps (models.CutoverResult).

    The engine returns {"vm", "timings", "verify", "conversion"}; verify may hold command output, so only its
    short scalar fields (power, reachable) are kept."""
    t = r.get("timings") or {}
    verify = {k: val for k, val in (r.get("verify") or {}).items() if isinstance(val, (str, int, float, bool))}
    out = {"completed_at": now or time.time(), "target_vm_id": r.get("vm"), "verify": verify}
    out.update({k: t[k] for k in TIMINGS if t.get(k) is not None})
    return out


# ------------------------------------------------------------------ jobs and the worker loop

class Live:
    def __init__(self, store, lock, finish, bump):
        self.store, self.lock, self.finish, self.bump = store, lock, finish, bump
        self.jobs = {}   # (plan id, vm id) -> {"proc", "action", "started"}

    def settings(self):
        return self.store.get("settings", "settings") or {}

    # -- helpers to read-modify-write one VM under the lock
    def _update(self, pid, vid, fn):
        with self.lock:
            p = self.store.get("plan", pid)
            if not p:
                return
            for v in p["vms"]:
                if v["id"] == vid:
                    fn(p, v)
            self.store.put("plan", self.finish(p))
        self.bump()

    def _env(self, plan):
        env = dict(os.environ)
        tgt = self.store.get("provider", (plan.get("target") or {}).get("id"))
        src = self.store.get("provider", (plan.get("source") or {}).get("id"))
        if tgt:
            env.update(prism_env(tgt, self.store.secret))
        if src and src["type"] == "everrun":
            env["EV2AHV_EVERRUN_PASS"] = self.store.secret(f"provider:{src['id']}:password") or ""
        env["PYTHONPATH"] = os.path.join(ROOT, "server") + os.pathsep + ROOT
        return env

    def ensure_plan(self, plan, vm):
        src = self.store.get("provider", plan["source"]["id"])
        wl = next((w for w in src.get("workloads", []) if w["id"] == vm["id"]), None)
        if not wl:
            raise LiveError(f"{vm['name']} is no longer in {src['name']}. Refresh the environment")
        return engine_plan(plan, vm, src, wl, self.store.secret, self.settings())

    def launch(self, plan, vm, steps, on_done, force=False):
        """Run engine steps for one VM in sequence in a background thread."""
        key = (plan["id"], vm["id"])
        if key in self.jobs:
            return
        self.jobs[key] = {"action": steps[0], "started": time.time(), "proc": None, "cancelled": False}
        env = self._env(plan)

        def run():
            job = self.jobs[key]
            logf = os.path.join(DATA, plan["id"], re.sub(r"[^\w.-]", "_", vm["id"]) + ".log")
            result, error = None, None
            try:
                path = self.ensure_plan(plan, vm)
                guest = planlib.load(path).get("guest") or {}
                ran = []
                for step in steps:
                    if step == "guest-prep" and guest.get("prep") != "auto":
                        continue   # manual, bypassed, or no guest handling in this plan

                    job["action"] = step
                    ran.append(step)
                    self._update(plan["id"], vm["id"], lambda p, v: v.update(phase=step))
                    argv = [sys.executable, "-m", "ev2ahv_server.runner", path, step]
                    if force and step == "cutover":
                        argv.append("--force")
                    self.store.event("info", f"{step} started", plan=plan["name"], vm=vm["name"])
                    with open(logf, "a") as lf:
                        lf.write(f"\n=== {time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())} {step}\n")
                        lf.flush()
                        proc = subprocess.Popen(argv, cwd=ROOT, env=env, stdout=subprocess.PIPE, stderr=lf, text=True)
                        job["proc"] = proc
                        result, error = None, None
                        for line in proc.stdout:
                            if not line.startswith("EV2AHV "):
                                continue
                            msg = json.loads(line[7:])
                            if "progress" in msg:
                                self._progress(plan["id"], vm["id"], step, msg["progress"], job)
                            result, error = msg.get("result", result), msg.get("error", error)
                        rc = proc.wait()
                    if job["cancelled"]:
                        return
                    if rc in (-15, -1):
                        # stopped by a service stop or reboot, not by the user: leave the status as it is so the
                        # worker resumes the step after restart (every step can be rerun safely)
                        self.store.event("warning", f"{step} interrupted by a service stop; it resumes on restart",
                                         plan=plan["name"], vm=vm["name"])
                        job["cancelled"] = True
                        return
                    if rc != 0:
                        error = error or f"{step} exited with {rc}. See the job log"
                        break
                    if step == "prep-check":
                        self._update(plan["id"], vm["id"], lambda p, v: v.update(checks=result or []))
            except Exception as e:
                error = str(e)
            finally:
                self.jobs.pop(key, None)
            if job["cancelled"]:
                return
            if error:
                self.store.event("error", f"{job['action']} failed: {error}", plan=plan["name"], vm=vm["name"])
                self._update(plan["id"], vm["id"],
                             lambda p, v: v.update(fail_from=v["status"], status="Failed", error=f"{job['action']}: {error}",
                                                  phase=None))
            else:
                self.store.event("info", f"{' + '.join(ran)} finished", plan=plan["name"], vm=vm["name"])
                self._update(plan["id"], vm["id"], lambda p, v: (on_done(p, v, result), v.update(phase=None)))

        threading.Thread(target=run, daemon=True, name=f"job-{vm['name']}").start()

    def _progress(self, pid, vid, step, pr, job):
        frac = pr["done"] / max(pr["total"], 1)
        el = time.time() - job["started"]
        eta = int(el / frac * (1 - frac)) if frac > 0.02 else None

        def fn(p, v):
            if step == "seed":
                v["seed_percent"] = round(100 * frac, 1)
                v["data_transferred_bytes"] = int(frac * (v.get("total_bytes") or 0))
            v["eta_seconds"] = eta
            v["sync_progress"] = round(100 * frac, 1)
        self._update(pid, vid, fn)

    def stop(self, pid, vid):
        job = self.jobs.get((pid, vid))
        if job:
            job["cancelled"] = True
            if job["proc"] and job["proc"].poll() is None:
                job["proc"].terminate()
            return job["action"]

    def precheck(self, plan):
        for v in plan["vms"]:
            if (plan["id"], v["id"]) not in self.jobs:
                self.launch(plan, v, ["prep-check"], lambda p, vm, r: None)

    # -- the worker loop
    def loop(self):
        while True:
            time.sleep(2)
            try:
                self.tick()
            except Exception:
                log.exception("worker tick failed")

    def tick(self):
        now = time.time()
        for p in self.store.all("plan"):
            if not p.get("started_at"):
                s = (p.get("schedule") or {}).get("seed_at")
                if p.get("armed") and s and s <= now:
                    with self.lock:
                        p["started_at"] = now
                        for v in p["vms"]:
                            v["status"] = "Preparing"
                        self.store.put("plan", self.finish(p))
                    self.store.event("info", f"Scheduled plan {p['name']} started", plan=p["name"])
                    self.bump()
                continue
            resync = 60 * int((p.get("schedule") or {}).get("resync_minutes") or 60)
            for v in p["vms"]:
                if (p["id"], v["id"]) in self.jobs:
                    continue
                s = v["status"]
                if s in ("Queued", "Preparing"):
                    steps = (["guest-prep"] if p["prep"].get("mode") == "automatic"
                             and not p["prep"].get("bypass_guest_ops") else []) + ["prep-check"]
                    self.launch(p, v, steps, self._prepared)
                elif s == "Seeding":
                    self.launch(p, v, ["seed"], self._seeded)
                elif s == "Ready to Cutover" and now - (v.get("last_sync_at") or 0) > resync:
                    self.launch(p, v, ["resync"], self._seeded)
                elif s == "Cutover in Progress":
                    steps = (["discard-target", "resync"] if v.get("rolled_back") else []) + ["cutover"]
                    self.launch(p, v, steps, self._cut, force=bool(v.get("force")))
                elif s == "Rolling Back":
                    self.launch(p, v, ["rollback"], self._rolled_back)

    @staticmethod
    def _prepared(p, v, checks):
        v["status"] = "Seeding"

    @staticmethod
    def _seeded(p, v, r):
        v.update(status="Ready to Cutover", seed_percent=100.0, last_sync_at=time.time(), eta_seconds=None,
                 data_transferred_bytes=v.get("total_bytes") or v.get("data_transferred_bytes", 0))

    def _cut(self, p, v, r):
        r = r or {}
        v.update(status="Completed", rolled_back=None, force=None, cutover_result=cutover_summary(r))
        tgt = self.store.get("provider", (p.get("target") or {}).get("id")) or {}
        if r.get("vm"):
            v["target_vm_id"] = r["vm"]
            v["target_vm_url"] = f"https://{tgt.get('address')}:9440/console/#page/explore/s/vms"

    @staticmethod
    def _rolled_back(p, v, r):
        v.update(status="Ready to Cutover", rolled_back=True, last_sync_at=0, cutover_result=None)
