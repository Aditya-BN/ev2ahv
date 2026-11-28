"""ev2ahv web API (Move-shaped, /api/v2). Mock mode simulates progress; live mode drives the engine (C4.2).

Run: python -m ev2ahv_server [--mock] [--port 8443]
"""
import asyncio, hmac, io, json, os, secrets, sys, threading, time, zipfile
from typing import Optional

from fastapi import Cookie, Depends, FastAPI, HTTPException, Request, Response
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)
from ev2ahv import __version__, web as oldweb  # noqa: E402  (login file + hashing shared with the CLI console)

from . import models as m  # noqa: E402
from .store import Store  # noqa: E402
from .users import UserError, Users, ldap_login, ldap_settings_ok, public as users_public, write_allowed  # noqa: E402

MODE = os.environ.get("EV2AHV_SERVER_MODE", "mock")
DIST = os.path.join(ROOT, "web", "dist")
SESSION_SECONDS = 12 * 3600
COOKIE = "ev2ahv_session"

store = Store()
sessions = {}
change = {"version": 0}
lock = threading.RLock()

app = FastAPI(title="ev2ahv API", version=__version__, docs_url="/api/v2/docs", openapi_url="/api/v2/openapi.json",
              redoc_url=None)


def bump():
    change["version"] += 1


# ---------------------------------------------------------------- auth

def user(ev2ahv_session: Optional[str] = Cookie(None)):
    s = sessions.get(ev2ahv_session or "")
    if not s or s["exp"] < time.time():
        raise HTTPException(401, "login required")
    return s["user"]


APPLIANCE = os.environ.get("EV2AHV_APPLIANCE") == "1"
DATA = os.path.expanduser(os.environ.get("EV2AHV_DATA", "~/.local/share/ev2ahv"))
DEFAULT_USER, DEFAULT_PASSWORD = "nutanix", "nutanix/4u"


users = Users(store)


def _ensure_users():
    """First start imports the 0.2.0 login (ui.json) as an admin. The console tool can request a reset (it drops a
    ui.reset file, which only root and the app can write): the default account gets the default password back."""
    reset = os.path.join(DATA, "ui.reset")
    users.ensure(DEFAULT_USER, DEFAULT_PASSWORD, must_change_default=APPLIANCE, reset=os.path.exists(reset))
    if os.path.exists(reset):
        os.remove(reset)
        for t in [t for t, s_ in sessions.items() if s_["user"] == DEFAULT_USER]:
            sessions.pop(t)
        store.event("warning", "UI password of the default account reset from the console")


def admin(ev2ahv_session: Optional[str] = Cookie(None)):
    s = sessions.get(ev2ahv_session or "")
    if not s or s["exp"] < time.time():
        raise HTTPException(401, "login required")
    if s.get("role") != "admin":
        raise HTTPException(403, "admin role required")
    return s["user"]


@app.middleware("http")
async def force_password_change(request: Request, call_next):
    s = sessions.get(request.cookies.get(COOKIE, ""))
    if s and s.get("must_change") and request.url.path.startswith("/api/") and request.url.path not in (
            "/api/v2/password", "/api/v2/logout", "/api/v2/appinfo", "/api/v2/token"):
        return JSONResponse({"message": "Change the default password first", "must_change": True}, status_code=403)
    if s and not write_allowed(s.get("role"), request.method, request.url.path):
        return JSONResponse({"message": "Your role (viewer) is read only"}, status_code=403)
    return await call_next(request)


@app.post("/api/v2/token")
def login(body: m.Login, response: Response):
    _ensure_users()
    u = users.check(body.username, body.password) or ldap_login(store.get("settings", "settings"), body.username,
                                                               body.password, secret=store.secret)
    if not u:
        store.event("warning", f"failed login for {body.username!r}")
        raise HTTPException(401, "Invalid username or password")
    tok = secrets.token_urlsafe(32)
    must = bool(u.get("must_change"))
    sessions[tok] = {"user": u["username"], "role": u["role"], "source": u.get("source", "local"), "exp": time.time() + SESSION_SECONDS,
                     "must_change": must}
    response.set_cookie(COOKIE, tok, httponly=True, secure=True, samesite="strict", max_age=SESSION_SECONDS)
    store.event("info", f"{u['username']} logged in")
    return {"user": u["username"], "role": u["role"], "must_change": must}


@app.post("/api/v2/logout")
def logout(response: Response, ev2ahv_session: Optional[str] = Cookie(None)):
    sessions.pop(ev2ahv_session or "", None)
    response.delete_cookie(COOKIE)
    return {}


@app.post("/api/v2/password")
def change_password(body: dict, who=Depends(user), ev2ahv_session: Optional[str] = Cookie(None)):
    if (sessions.get(ev2ahv_session or "") or {}).get("source") == "ldap":
        raise HTTPException(400, "This account comes from the directory (LDAP); change its password there")
    try:
        users.change_own(who, str(body.get("current", "")), str(body.get("new", "")), banned=(DEFAULT_PASSWORD,))
    except UserError as e:
        raise HTTPException(400, str(e))
    for s_ in sessions.values():
        if s_["user"] == who:
            s_["must_change"] = False
    store.event("info", f"{who} changed their password")
    return {}


# ---------------------------------------------------------------- users (admin)

def _drop_sessions(username, keep=None):
    for t in [t for t, s_ in sessions.items() if s_["user"] == username and t != keep]:
        sessions.pop(t)


@app.get("/api/v2/users")
def list_users(who=Depends(admin)):
    return {"entities": [users_public(u) for u in users.all()]}


@app.post("/api/v2/users")
def add_user(body: dict, who=Depends(admin)):
    try:
        u = users.create(str(body.get("username", "")), body.get("password"), str(body.get("role", "viewer")))
    except UserError as e:
        raise HTTPException(400, str(e))
    store.event("info", f"{who} added user {u['username']} ({u['role']})")
    return users_public(u)


@app.put("/api/v2/users/{name}")
def edit_user(name: str, body: dict, who=Depends(admin)):
    try:
        u = users.update(name, role=body.get("role"), password=body.get("password"), actor=who)
    except UserError as e:
        raise HTTPException(400, str(e))
    for s_ in sessions.values():
        if s_["user"] == name:
            s_["role"] = u["role"]
    if body.get("password") is not None and name != who:
        _drop_sessions(name)
    store.event("info", f"{who} changed user {name}" + (f" (role {u['role']})" if body.get("role") else "")
                + (" (password reset)" if body.get("password") is not None else ""))
    return users_public(u)


@app.delete("/api/v2/users/{name}")
def remove_user(name: str, who=Depends(admin)):
    try:
        users.delete(name, actor=who)
    except UserError as e:
        raise HTTPException(400, str(e))
    _drop_sessions(name)
    store.event("warning", f"{who} deleted user {name}")
    return {}


@app.get("/api/v2/appinfo", response_model=m.AppInfo)
def appinfo(ev2ahv_session: Optional[str] = Cookie(None)):
    s = sessions.get(ev2ahv_session or "")
    return m.AppInfo(version=__version__, mode=MODE, user=s["user"] if s else None, role=s.get("role") if s else None,
                     must_change=bool(s and s.get("must_change")), appliance=APPLIANCE)


# ---------------------------------------------------------------- environments (providers)

def _provider_out(p):
    return m.Provider(**{k: v for k, v in p.items() if k in m.Provider.model_fields})


@app.get("/api/v2/providers")
def providers(who=Depends(user)):
    ps = [_provider_out(p) for p in store.all("provider")]
    return {"entities": ps, "total": len(ps)}


def _probe(body: m.ProviderIn):
    if not body.address:
        raise HTTPException(400, "Address is required")
    if not (body.password or body.api_key) and body.type != "plans":
        raise HTTPException(400, "Password or API key is required")
    if body.type == "everrun" and not body.node_addresses:
        raise HTTPException(400, "everRun needs at least one node address for disk access")
    if MODE == "live":
        try:
            return dict(ok=True, **live.probe(body))
        except live.LiveError as e:
            raise HTTPException(400, str(e))
    return {"ok": True, "version": "8.1.1" if body.type == "everrun" else "pc.2025.1"}


@app.post("/api/v2/providers/validate")
def validate_provider(body: m.ProviderIn, who=Depends(user)):
    return _probe(body)


@app.post("/api/v2/providers", response_model=m.Provider)
def add_provider(body: m.ProviderIn, who=Depends(user)):
    info = _probe(body)
    if any(p["name"] == body.name for p in store.all("provider")):
        raise HTTPException(409, f"An environment named {body.name} already exists")
    pid = store.new_id()
    _save_secrets(pid, body)
    doc = _new_provider(pid, body, info["version"])
    store.put("provider", doc)
    store.event("info", f"{who} added environment {body.name}")
    bump()
    return _provider_out(doc)


def _new_provider(pid, body, version):
    role = "target" if body.type == "aos" else "source"
    doc = {"id": pid, "name": body.name, "type": body.type, "role": role, "address": body.address,
           "username": body.username, "auth": "api_key" if body.api_key else "password", "status": "Connected",
           "version": version, "node_addresses": body.node_addresses, "last_refreshed": time.time(),
           "clusters": [], "containers": [], "subnets": [], "vm_count": 0, "workloads": []}
    if MODE == "mock":
        from . import mock
        mock.fill_provider(doc)
    else:
        _live_refresh(doc)
    return doc


def _live_refresh(doc):
    try:
        live.refresh(doc, store.secret, current_settings())
    except Exception as e:
        doc["status"], doc["error"] = "Error", str(e)


def _save_secrets(pid, body):
    store.set_secret(f"provider:{pid}:password", body.password)
    store.set_secret(f"provider:{pid}:api_key", body.api_key)


def _provider(pid):
    p = store.get("provider", pid)
    if not p:
        raise HTTPException(404, "Environment not found")
    return p


@app.get("/api/v2/providers/{pid}", response_model=m.Provider)
def get_provider(pid: str, who=Depends(user)):
    return _provider_out(_provider(pid))


@app.put("/api/v2/providers/{pid}", response_model=m.Provider)
def edit_provider(pid: str, body: m.ProviderIn, who=Depends(user)):
    p = _provider(pid)
    if body.password or body.api_key:
        _probe(body)
    p.update(name=body.name, address=body.address, username=body.username, node_addresses=body.node_addresses)
    if body.api_key:
        p["auth"] = "api_key"
    elif body.password:
        p["auth"] = "password"
    _save_secrets(pid, body)
    store.put("provider", p)
    store.event("info", f"{who} edited environment {p['name']}")
    bump()
    return _provider_out(p)


@app.delete("/api/v2/providers/{pid}")
def delete_provider(pid: str, who=Depends(user)):
    p = _provider(pid)
    used = [pl["name"] for pl in store.all("plan")
            if pid in ((pl.get("source") or {}).get("id"), (pl.get("target") or {}).get("id"))]
    if used:
        raise HTTPException(409, "Environment is used by plan(s): " + ", ".join(used))
    store.delete("provider", pid)
    store.event("info", f"{who} removed environment {p['name']}")
    bump()
    return {}


@app.post("/api/v2/providers/{pid}/refresh", response_model=m.Provider)
def refresh_provider(pid: str, who=Depends(user)):
    p = _provider(pid)
    p["last_refreshed"] = time.time()
    p["status"] = "Connected"
    if MODE == "live":
        _live_refresh(p)
    store.put("provider", p)
    store.event("info", f"Refreshed inventory of {p['name']}")
    bump()
    return _provider_out(p)


@app.get("/api/v2/providers/{pid}/workloads")
def workloads(pid: str, who=Depends(user)):
    p = _provider(pid)
    if p["role"] != "source":
        raise HTTPException(400, "Workloads are listed for source environments only")
    w = [m.Workload(**{k: v for k, v in x.items() if not k.startswith("_")}) for x in p.get("workloads", [])]
    return {"entities": w, "total": len(w)}


# ---------------------------------------------------------------- plans

VM_ACTIONS = {
    "Queued": ["cancel"],
    "Preparing": ["pause", "cancel"],
    "Seeding": ["pause", "cancel"],
    "Paused": ["resume", "cancel"],
    "Ready to Cutover": ["cutover", "resync", "test", "pause", "cancel"],
    "Scheduled for Cutover": ["cutover", "cancel"],
    "Testing": [],
    "Tested": ["cutover", "resync", "test", "discard", "cancel"],
    "Cutover in Progress": [],
    "Rolling Back": [],
    "Completed": ["rollback"],
    "Failed": ["retry", "cancel"],
    "Cancelled": [],
}


# Test migration (D8d) works in live mode too: the engine clones the staging disks onto the test subnet.
LIVE_UNSUPPORTED = set()


def finish(plan):
    """Derive plan status, counts, per-VM actions and the plan-row menu from VM states."""
    vms = plan["vms"]
    c = {"total": len(vms), "in_progress": 0, "ready_to_cutover": 0, "failed": 0, "paused": 0, "completed": 0}
    for v in vms:
        s = v["status"]
        v["actions"] = [a for a in VM_ACTIONS.get(s, []) if a not in LIVE_UNSUPPORTED] if plan.get("started_at") else []
        if s in ("Preparing", "Seeding", "Cutover in Progress", "Testing", "Rolling Back") or (s == "Queued" and plan.get("started_at")):
            c["in_progress"] += 1
        elif s in ("Ready to Cutover", "Tested", "Scheduled for Cutover"):
            c["ready_to_cutover"] += 1
        elif s == "Failed":
            c["failed"] += 1
        elif s == "Paused":
            c["paused"] += 1
        elif s == "Completed":
            c["completed"] += 1
    plan["counts"] = c
    plan["data_migrated_bytes"] = sum(v.get("data_transferred_bytes", 0) for v in vms)
    states = {v["status"] for v in vms}
    if not plan.get("started_at"):
        sched = (plan.get("schedule") or {}).get("seed_at")
        plan["status"] = "Scheduled" if sched and sched > time.time() and plan.get("armed") else "Not Started"
    elif vms and states <= {"Cancelled"}:
        plan["status"] = "Cancelled"
    elif vms and states <= {"Completed", "Cancelled"}:
        plan["status"] = "Completed"
    elif "Failed" in states:
        plan["status"] = "Failed"
    elif vms and states <= {"Paused", "Completed", "Cancelled"}:
        plan["status"] = "Paused"
    else:
        plan["status"] = "In Progress"
    if plan["status"] in ("Not Started", "Scheduled"):
        plan["actions"] = ["Start", "Edit", "Delete"]
    elif plan["status"] in ("Completed", "Cancelled"):
        plan["actions"] = ["Delete"]
    else:
        plan["actions"] = ["Edit", "Delete"]
    return plan


def _plan(pid):
    p = store.get("plan", pid)
    if not p:
        raise HTTPException(404, "Plan not found")
    return p


def _plan_out(p):
    return m.Plan(**{k: v for k, v in finish(p).items() if k in m.Plan.model_fields})


@app.get("/api/v2/plans")
def plans(status: Optional[str] = None, who=Depends(user)):
    ps = [_plan_out(p) for p in store.all("plan")]
    if status and status != "All Plans":
        ps = [p for p in ps if p.status == status]
    ps.sort(key=lambda p: -p.created_at)
    return {"entities": ps, "total": len(ps)}


@app.get("/api/v2/plans/vms")
def plan_vms(status: Optional[str] = None, who=Depends(user)):
    """Dashboard drill-down (/all-plan-vms/{status}): every VM across plans, optionally by status."""
    out = []
    for p in store.all("plan"):
        p = finish(p)
        for v in p["vms"]:
            if not status or v["status"] == status or (status == "In Progress" and v["status"] in
                                                         ("Queued", "Preparing", "Seeding", "Cutover in Progress", "Testing")):
                out.append(dict(v, plan_id=p["id"], plan_name=p["name"]))
    return {"entities": out, "total": len(out)}


def _build(body: m.PlanIn, pid, who, created=None):
    src = store.get("provider", body.source_id) if body.source_id else None
    tgt = store.get("provider", body.target_id) if body.target_id else None
    wl = {w["id"]: w for w in (src or {}).get("workloads", [])}
    missing = [i for i in body.vm_ids if i not in wl]
    if missing:
        raise HTTPException(400, "Unknown VM(s): " + ", ".join(missing))
    vms = []
    for i in body.vm_ids:
        w = wl[i]
        vms.append({"id": i, "name": w["name"], "os_type": w["os_type"], "status": "Queued", "seed_percent": 0,
                    "data_transferred_bytes": 0, "total_bytes": w["total_bytes"], "checks": checks(w, body),
                    "actions": []})
    return {"id": pid, "name": body.name, "created_by": who, "created_at": created or time.time(),
            "source": {"id": src["id"], "name": src["name"]} if src else None,
            "target": {"id": tgt["id"], "name": tgt["name"]} if tgt else None,
            "cluster": body.cluster, "container": body.container, "project": body.project,
            "network_map": [n.model_dump() for n in body.network_map], "test_network": body.test_network,
            "prep": body.prep.model_dump(exclude={"windows_password", "linux_password"}),
            "vm_settings": {k: v.model_dump() for k, v in body.vm_settings.items()},
            "schedule": body.schedule.model_dump(), "status": "Not Started", "started_at": None, "vms": vms}


def checks(w, body: m.PlanIn):
    """Mock prep-check (PASS/WARN/FAIL). In live mode the engine's prep-check replaces these when the VM is
    prepared, or on POST /plans/{id}/precheck."""
    vs = body.vm_settings.get(w["id"], m.VmSettings())
    out = [m.Check(name="Source reachable", status="PASS", detail=w.get("active_node", "")).model_dump()]
    if body.prep.bypass_guest_ops:
        out.append(m.Check(name="Guest preparation", status="WARN",
                           detail="Bypassed: drivers, IP and MAC retention are your responsibility").model_dump())
    elif body.prep.mode == "manual":
        out.append(m.Check(name="Guest preparation", status="WARN",
                           detail="Manual: run the downloaded script in the guest, then refresh").model_dump())
    else:
        out.append(m.Check(name="Guest preparation", status="PASS", detail="Automatic over "
                           + ("WinRM" if w["os_type"] == "windows" else "SSH")).model_dump())
    if w["os_type"] == "windows" and vs.disk_bus == "scsi" and "virtio-blk only" in " ".join(w.get("warnings", [])):
        out.append(m.Check(name="Boot driver", status="FAIL",
                           detail="No bound vioscsi driver: use PCI with SCSI conversion").model_dump())
    else:
        out.append(m.Check(name="Boot driver", status="PASS", detail=vs.disk_bus.upper()).model_dump())
    for wn in w.get("warnings", []):
        out.append(m.Check(name="Source warning", status="WARN", detail=wn).model_dump())
    return out


@app.post("/api/v2/plans", response_model=m.Plan)
def create_plan(body: m.PlanIn, who=Depends(user)):
    if any(p["name"] == body.name for p in store.all("plan")):
        raise HTTPException(409, f"A plan named {body.name} already exists")
    pid = store.new_id()
    doc = _build(body, pid, who)
    _plan_secrets(pid, body)
    store.put("plan", doc)
    store.event("info", f"{who} created plan {body.name}", plan=body.name)
    if body.start:
        _start(doc, who)
    bump()
    return _plan_out(doc)


def _plan_secrets(pid, body):
    store.set_secret(f"plan:{pid}:windows_password", body.prep.windows_password)
    store.set_secret(f"plan:{pid}:linux_password", body.prep.linux_password)


@app.get("/api/v2/plans/{pid}", response_model=m.Plan)
def get_plan(pid: str, who=Depends(user)):
    return _plan_out(_plan(pid))


@app.put("/api/v2/plans/{pid}", response_model=m.Plan)
def edit_plan(pid: str, body: m.PlanIn, who=Depends(user)):
    old = _plan(pid)
    if old.get("started_at"):
        keep = {v["id"]: v for v in old["vms"]}
        if set(body.vm_ids) != set(keep):
            raise HTTPException(409, "VMs cannot be changed after the plan has started")
    doc = _build(body, pid, who, created=old["created_at"])
    doc["created_by"] = old["created_by"]
    if old.get("started_at"):
        doc["started_at"], doc["vms"] = old["started_at"], old["vms"]
    _plan_secrets(pid, body)
    store.put("plan", doc)
    store.event("info", f"{who} edited plan {doc['name']}", plan=doc["name"])
    if body.start and not old.get("started_at"):
        _start(doc, who)
    bump()
    return _plan_out(doc)


@app.patch("/api/v2/plans/{pid}/vms/{vid}/settings", response_model=m.Plan)
def patch_vm_settings(pid: str, vid: str, body: m.VmSettingsPatch, who=Depends(user)):
    """Change one VM setting of an existing plan without resending the whole plan (cbt, final_verify, parallel_disks)."""
    doc = _plan(pid)
    v = next((x for x in doc["vms"] if x["id"] == vid), None)
    if not v:
        raise HTTPException(404, "VM not in this plan")
    vs = doc.setdefault("vm_settings", {}).setdefault(vid, m.VmSettings().model_dump())
    changed = []
    for key, label in (("cbt", "changed-block tracking"), ("final_verify", "final verify"),
                       ("parallel_disks", "parallel disks")):
        val = getattr(body, key)
        if val is not None:
            vs[key] = val
            changed.append(f"{label} to {val}")
    if not changed:
        raise HTTPException(422, "Nothing to change: send cbt, final_verify and/or parallel_disks")
    store.put("plan", doc)
    store.event("info", f"{who} set {' and '.join(changed)} for {v['name']}", plan=doc["name"])
    bump()
    return _plan_out(doc)


# VM states that may have staging disks on the writer (seeding started)
STAGED = ("Seeding", "Ready to Cutover", "Scheduled for Cutover", "Tested", "Completed", "Failed", "Paused",
          "Rolling Back")


@app.delete("/api/v2/plans/{pid}")
def delete_plan(pid: str, cleanup: bool = False, who=Depends(user)):
    """D24: in live mode a plan whose VMs were seeded has staging disks on the writer (or appliance). Deleting it
    then needs cleanup=true, which removes them first (engine `cleanup`, target VMs are kept) and deletes the plan
    when every VM is done; without it the call is refused instead of leaving disks behind."""
    p = finish(_plan(pid))
    busy = [v["name"] for v in p["vms"] if v["status"] in ("Cutover in Progress", "Testing", "Rolling Back")
            or (MODE == "live" and worker.jobs.get((pid, v["id"])))]
    if busy:
        raise HTTPException(409, "Wait for these VMs to finish: " + ", ".join(busy))
    staged = [v for v in p["vms"] if v["status"] in STAGED and not v.get("cleaned")] if MODE == "live" else []
    if staged and not cleanup:
        raise HTTPException(409, {"message": "These VMs have staging disks on the writer: "
                                  + ", ".join(v["name"] for v in staged) + ". Delete with cleanup to remove them "
                                  "(target VMs are kept; rollback is no longer possible afterwards).",
                                  "staging": [v["name"] for v in staged]})
    if staged:
        names = {v["id"] for v in staged}
        with lock:
            doc = _plan(pid)
            doc["deleting"] = True
            for v in doc["vms"]:
                if v["id"] in names:
                    v["cleanup_pending"] = True
            store.put("plan", doc)
        store.event("warning", f"{who} is deleting plan {p['name']}: removing staging disks first", plan=p["name"])
        for v in staged:
            worker.launch(doc, v, ["cleanup-staging"], _staging_cleaned)
        bump()
        return {"cleanup_started": [v["name"] for v in staged]}
    store.delete("plan", pid)
    store.event("info", f"{who} deleted plan {p['name']} (target VMs are kept)", plan=p["name"])
    bump()
    return {}


def _staging_cleaned(p, v, r):
    """Job callback (runs inside the worker's plan update): mark the VM clean; the worker deletes the plan."""
    v.update(cleanup_pending=None, cleaned=True)


def _start(doc, who):
    sched = doc["schedule"].get("seed_at")
    if sched and sched > time.time():
        doc["armed"] = True
        store.event("info", f"{who} scheduled plan {doc['name']}", plan=doc["name"])
    else:
        doc["started_at"] = time.time()
        for v in doc["vms"]:
            v["status"] = "Preparing"
        store.event("info", f"{who} started plan {doc['name']}", plan=doc["name"])
    store.put("plan", doc)


@app.post("/api/v2/plans/{pid}/start", response_model=m.Plan)
def start_plan(pid: str, who=Depends(user)):
    doc = _plan(pid)
    if doc.get("started_at"):
        raise HTTPException(409, "Plan already started")
    doc["schedule"]["seed_at"] = None
    _start(doc, who)
    bump()
    return _plan_out(doc)


@app.post("/api/v2/plans/{pid}/action", response_model=m.Plan)
def plan_action(pid: str, body: m.ActionIn, who=Depends(user)):
    with lock:
        doc = finish(_plan(pid))
        targets = [v for v in doc["vms"] if (not body.vm_ids or v["id"] in body.vm_ids)
                   and body.action in v["actions"]]
        if not targets:
            raise HTTPException(409, f"'{body.action}' does not apply to the selected VMs")
        if body.action == "cutover" and not body.force:
            failing = [f"{v['name']}: {c['name']}" for v in targets for c in v["checks"] if c["status"] == "FAIL"]
            if failing:
                raise HTTPException(409, {"message": "Prep-check failed. Fix it, or cut over with force.",
                                          "failing": failing})
        nxt = {"pause": "Paused", "cancel": "Cancelled", "cutover": "Cutover in Progress", "test": "Testing",
               "retry": "Preparing", "rollback": "Rolling Back"}
        for v in targets:
            a = body.action
            if MODE == "live" and a in ("pause", "cancel"):
                worker.stop(doc["id"], v["id"])
            if a == "retry":
                v["status"] = v.pop("fail_from", None) or "Preparing"
                v["error"] = None
            elif a == "resume":
                v["status"] = v.pop("resume_to", "Seeding")
            elif a == "pause":
                v["resume_to"] = v["status"]
                v["status"] = "Paused"
            elif a == "resync":
                # D18: the worker resyncs a seeded VM whose last sync is older than the plan interval, so marking it
                # as never synced makes the next tick (2 s) start one; a resync already running absorbs the request
                v["last_sync_at"] = 0
            elif a == "discard":
                if MODE == "live":   # D8d: delete the test VM in the background
                    worker.launch(doc, v, ["discard-test"], lambda p_, vm_, r: None)
                v["test_vm_id"] = None
                v["status"] = "Ready to Cutover"
            else:
                v["status"] = nxt[a]
                v["error"] = None
                v["phase_t"] = time.time()
            if a == "cutover":
                v["force"] = body.force or None
            store.event("warning" if a in ("cancel", "rollback") else "info",
                        f"{who}: {a} {v['name']}" + (" (forced past prep-check FAIL)" if body.force else ""),
                        plan=doc["name"], vm=v["name"])
        store.put("plan", doc)
        bump()
        return _plan_out(doc)


@app.post("/api/v2/plans/{pid}/precheck", response_model=m.Plan)
def precheck(pid: str, who=Depends(user)):
    doc = _plan(pid)
    if MODE == "live":
        worker.precheck(doc)
    store.event("info", "Prep-check requested", plan=doc["name"])
    bump()
    return _plan_out(doc)


def _prep_file(os_type: str):
    name = "prep-windows.ps1" if os_type == "windows" else "prep-linux.sh"
    return FileResponse(os.path.join(ROOT, "prep", name), filename=name, media_type="application/octet-stream")


@app.get("/api/v2/plans/{pid}/prep-script")
def prep_script(pid: str, os_type: str = "linux", who=Depends(user)):
    _plan(pid)
    return _prep_file(os_type)


@app.get("/api/v2/prep-script")
def prep_script_any(os_type: str = "linux", who=Depends(user)):
    """Same scripts without a plan, for the wizard before the plan is saved."""
    return _prep_file(os_type)


@app.get("/api/v2/plans/{pid}/events")
def plan_events(pid: str, who=Depends(user)):
    name = _plan(pid)["name"]
    ev = [e for e in store.events(2000) if e["plan"] == name]
    return {"entities": ev, "total": len(ev)}


@app.get("/api/v2/plans/{pid}/vms/{vid}/log", response_class=PlainTextResponse)
def vm_log(pid: str, vid: str, lines: int = 400, who=Depends(user)):
    """Engine log of one VM (live mode): every step's output, UTC timestamps."""
    _plan(pid)
    import re as _re
    f = os.path.join(os.path.expanduser("~/.local/share/ev2ahv/ui"), pid, _re.sub(r"[^\w.-]", "_", vid) + ".log")
    if not os.path.exists(f):
        return "No engine log yet." if MODE == "live" else "Mock mode: no engine log."
    with open(f, errors="replace") as fh:
        return "".join(fh.readlines()[-lines:])


# ---------------------------------------------------------------- events, settings, support

@app.get("/api/v2/events")
def events(limit: int = 500, who=Depends(user)):
    ev = store.events(limit)
    return {"entities": ev, "total": len(ev)}


DEFAULT_SETTINGS = {"id": "settings", "resync_minutes": 60, "bandwidth_policies": [], "timezone": "Asia/Jakarta",
                    "source_name_prefix": "ev2ahv-",   # name guard; "" allows any VM (live.name_allowed)
                    "writer": {"local": True} if os.environ.get("EV2AHV_APPLIANCE") == "1" else
                    {"vm": "ev2ahv-writer", "host": "10.12.136.211", "user": "nutanix"}}


def current_settings():
    """Stored settings over the defaults, so a key added later (source_name_prefix) has its default on old databases."""
    return {**DEFAULT_SETTINGS, **(store.get("settings", "settings") or {})}


@app.get("/api/v2/settings")
def get_settings(who=Depends(user)):
    return current_settings()


@app.put("/api/v2/settings")
def put_settings(body: dict, who=Depends(user)):
    doc = dict(get_settings(who))
    for k in ("resync_minutes", "bandwidth_policies", "timezone", "writer"):
        if k in body:
            doc[k] = body[k]
    if "ldap" in body:   # D13; the bind password is write-only and goes to the secret store
        cfg = dict(body["ldap"] or {})
        pw = cfg.pop("bind_password", None)
        try:
            doc["ldap"] = ldap_settings_ok(cfg)
        except UserError as e:
            raise HTTPException(422, str(e))
        if pw:
            store.set_secret("ldap:bind_password", str(pw))
        store.event("warning", f"{who} changed the LDAP sign-in settings (enabled: {doc['ldap'].get('enabled')})")
    if "source_name_prefix" in body:
        new = body["source_name_prefix"]
        if not isinstance(new, str) or len(new) > 64:
            raise HTTPException(422, "source_name_prefix must be a string of at most 64 characters")
        new = new.strip()
        if new != doc.get("source_name_prefix"):
            store.event("warning", f"{who} changed the VM name guard from {doc.get('source_name_prefix')!r}* to "
                        + (f"{new!r}*" if new else "any VM"))
        doc["source_name_prefix"] = new
    store.put("settings", doc)
    store.event("info", f"{who} changed settings")
    bump()
    return doc


@app.get("/api/v2/supportbundle")
def supportbundle(who=Depends(admin)):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("events.json", json.dumps(store.events(5000), indent=1))
        z.writestr("plans.json", json.dumps([_plan_out(p).model_dump() for p in store.all("plan")], indent=1))
        z.writestr("environments.json", json.dumps([_provider_out(p).model_dump() for p in store.all("provider")],
                                                   indent=1))
        z.writestr("appinfo.json", json.dumps({"version": __version__, "mode": MODE, "time": time.time()}))
        known = [store.secret(o) for (o,) in store.db.execute("SELECT owner FROM secrets")]
        import glob as _glob
        logs = _glob.glob(os.path.join(os.path.expanduser("~/.local/share/ev2ahv/ui"), "*", "*.log"))
        logs += _glob.glob("/var/log/ev2ahv/*.log")
        for f in logs[-200:]:
            with open(f, errors="replace") as fh:
                z.writestr("logs/" + f.replace("/", "_").lstrip("_"), _appliance.redact(fh.read()[-2_000_000:], known))
    stamp = time.strftime("%Y%m%d-%H%M%S", time.gmtime())
    return Response(buf.getvalue(), media_type="application/zip",
                    headers={"Content-Disposition": f'attachment; filename="ev2ahv-support-{stamp}.zip"'})


@app.get("/api/v2/logs", response_class=PlainTextResponse)
def logs(lines: int = 300, who=Depends(user)):
    ev = reversed(store.events(lines))
    return "\n".join(f"{time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime(e['time']))} {e['severity'].upper():7} "
                     f"{e['plan'] or '-'} {e['vm'] or '-'} {e['message']}" for e in ev)


@app.get("/api/v2/stream")
async def stream(request: Request, who=Depends(user)):
    """Server-sent events: one 'change' message whenever any plan, environment or event changes."""
    async def gen():
        seen = -1
        while not await request.is_disconnected():
            if change["version"] != seen:
                seen = change["version"]
                yield f"event: change\ndata: {seen}\n\n"
            else:
                yield ": ping\n\n"
            await asyncio.sleep(1)
    return StreamingResponse(gen(), media_type="text/event-stream", headers={"Cache-Control": "no-cache"})


from . import appliance as _appliance  # noqa: E402
app.include_router(_appliance.router(store, user, bump))


# ---------------------------------------------------------------- static UI

@app.exception_handler(HTTPException)
async def http_error(request, exc):
    d = exc.detail
    return JSONResponse(d if isinstance(d, dict) else {"message": d}, status_code=exc.status_code)


if os.path.isdir(os.path.join(DIST, "assets")):
    app.mount("/assets", StaticFiles(directory=os.path.join(DIST, "assets")), name="assets")


@app.get("/{path:path}", include_in_schema=False)
def spa(path: str):
    if path.startswith("api/"):
        raise HTTPException(404, "Not found")
    f = os.path.join(DIST, path)
    if path and os.path.isfile(f) and os.path.realpath(f).startswith(os.path.realpath(DIST)):
        return FileResponse(f)
    index = os.path.join(DIST, "index.html")
    if os.path.exists(index):
        return FileResponse(index)
    return PlainTextResponse("ev2ahv API is running. The UI is not built yet (cd web && npm run build).")


worker = None
if MODE == "live":
    from . import live
    worker = live.Live(store, lock, finish, bump)


HEALTH_USER = "ev2ahv-health"


def _health_session():
    """ev2ahv-upgrade must read the stored data (plans, environments) through the real API to know that a new
    version can use it. It has no password, so at start the app creates a read-only session and writes its token to
    a file only the app user and root can read (mode 0600, in the app's runtime directory)."""
    path = os.environ.get("EV2AHV_HEALTH_COOKIE") or ("/run/ev2ahv/health.cookie" if APPLIANCE else "")
    if not path:
        return
    tok = secrets.token_urlsafe(32)
    try:
        fd = os.open(path + ".tmp", os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as fh:
            fh.write(tok)
        os.replace(path + ".tmp", path)
    except OSError as e:
        print(f"ev2ahv: health session not written: {e}", file=sys.stderr)
        return
    sessions[tok] = {"user": HEALTH_USER, "role": "viewer", "exp": time.time() + 10 * 365 * 86400,
                     "must_change": False}


@app.on_event("startup")
def _startup():
    _health_session()
    if MODE == "mock":
        from . import mock
        mock.seed(store)
        threading.Thread(target=mock.simulate, args=(store, lock, finish, bump), daemon=True).start()
    else:
        threading.Thread(target=worker.loop, daemon=True).start()
