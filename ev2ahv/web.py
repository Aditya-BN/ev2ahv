"""ev2ahv web console. Stdlib only. Binds to localhost by default; steps run as CLI subprocesses."""
import hashlib, hmac, http.cookies, http.server, json, os, re, secrets, socketserver, sqlite3, subprocess, sys, threading, time
from urllib.parse import urlparse

import yaml

from . import __version__, credentials, plan as planlib
from .prism import Prism
from .workflow import plan_dir

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PLANS = os.path.expanduser(os.environ.get("EV2AHV_PLANS", os.path.join(ROOT, "plans")))
ASSET = os.path.join(os.path.dirname(os.path.abspath(__file__)), "web_assets", "index.html")
UI_FILE = os.path.join(credentials.CONFIG_DIR, "ui.json")
ACTIONS = {"prep-check", "seed", "resync", "cutover", "rollback", "discard-target", "cleanup"}
CONFIRM = {"cutover", "rollback", "discard-target", "cleanup"}
NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,62}$")
SESSION_SECONDS = 12 * 3600
sessions, jobs, lock = {}, {}, threading.Lock()


def _hash(password, salt):
    return hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt), 200000).hex()


def ui_user():
    if os.path.exists(UI_FILE):
        with open(UI_FILE) as fh:
            return json.load(fh)
    return set_ui_user("nutanix", "nutanix/4u")


def set_ui_user(username, password):
    salt = secrets.token_hex(16)
    doc = {"username": username, "salt": salt, "hash": _hash(password, salt)}
    os.makedirs(credentials.CONFIG_DIR, mode=0o700, exist_ok=True)
    fd = os.open(UI_FILE + ".tmp", os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as fh:
        json.dump(doc, fh)
    os.replace(UI_FILE + ".tmp", UI_FILE)
    return doc


def plan_path(name):
    if not NAME.match(name or ""):
        raise ValueError("invalid plan name")
    return os.path.join(PLANS, name + ".yaml")


def plan_summary(file_name):
    name = file_name[:-5]
    out = {"file": name}
    try:
        p = planlib.load(os.path.join(PLANS, file_name))
    except Exception as e:
        return dict(out, error=str(e)[:200], phase="invalid")
    t = p["target"]
    out.update(name=p["name"], source=p["source"]["type"], target_vm=t["vm_name"], cluster=t["cluster"],
               disks=len(p["disks"]), nics=len(p.get("nics") or []), firmware=t["firmware"], bus=t["disk_bus"],
               cpu=int(t["sockets"]) * int(t["cores"]) * int(t["threads"]), memory_mib=t["memory_mib"], phase="new",
               progress=[], timings={}, step=None)
    db = os.path.join(plan_dir(p["name"]), "state.db")
    if os.path.exists(db):
        con = sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=3)
        try:
            kv = {k: json.loads(v) for k, v in con.execute("SELECT key, value FROM kv")}
            out.update(phase=kv.get("phase") or "new", step=kv.get("cutover_step"), timings=kv.get("timings") or {},
                       target_id=kv.get("target_vm"), verify=kv.get("verify"))
            for key, size, chunk, pos, active, passes in con.execute(
                    "SELECT key,size,chunk,scan_pos,pass_active,passes FROM disks"):
                total = (size + chunk - 1) // chunk
                out["progress"].append({"disk": key, "size": size, "pct": round(100 * pos / total, 1) if active else (100 if passes else 0),
                                        "active": bool(active), "passes": passes})
            out["events"] = [{"ts": t, "kind": k, "detail": json.loads(d)} for t, k, d in
                             reversed(con.execute("SELECT ts,kind,detail FROM events ORDER BY id DESC LIMIT 12").fetchall())]
        except sqlite3.Error:
            pass
        finally:
            con.close()
    job = jobs.get(out.get("name"))
    if job:
        out["job"] = {k: job[k] for k in ("action", "status", "started", "ended")}
    return out


def run_job(name, file_path, action, flags):
    job = {"action": action, "status": "running", "started": time.time(), "ended": None, "log": ""}
    with lock:
        current = jobs.get(name)
        if current and current["status"] == "running":
            raise RuntimeError(f"{current['action']} is already running on this plan")
        jobs[name] = job

    def work():
        cmd = [sys.executable, "-m", "ev2ahv", action, file_path] + flags
        try:
            proc = subprocess.Popen(cmd, cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, bufsize=0)
            buf = b""
            while True:
                part = proc.stdout.read(4096)
                if not part:
                    break
                buf = (buf + part)[-200000:]
                job["log"] = buf.decode(errors="replace").replace("\r", "\n")
            job["status"] = "succeeded" if proc.wait() == 0 else "failed"
        except Exception as e:
            job["log"] += f"\n{e}"
            job["status"] = "failed"
        job["ended"] = time.time()
    threading.Thread(target=work, daemon=True).start()
    return job


class Handler(http.server.BaseHTTPRequestHandler):
    server_version = "ev2ahv"
    sys_version = ""

    def log_message(self, *_):
        pass

    def send_json(self, code, body, cookie=None):
        data = json.dumps(body, default=str).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        if cookie:
            self.send_header("Set-Cookie", cookie)
        self.end_headers()
        self.wfile.write(data)

    def session(self):
        raw = self.headers.get("Cookie")
        if not raw:
            return None
        c = http.cookies.SimpleCookie()
        try:
            c.load(raw)
        except http.cookies.CookieError:
            return None
        token = c.get("ev2ahv")
        s = sessions.get(token.value) if token else None
        if s and s["expires"] > time.time():
            return token.value
        return None

    def body(self):
        n = int(self.headers.get("Content-Length") or 0)
        if n > 1_000_000:
            raise ValueError("request too large")
        return json.loads(self.rfile.read(n) or b"{}")

    def do_GET(self):
        path = urlparse(self.path).path
        if path in ("/", "/index.html"):
            with open(ASSET, "rb") as fh:
                data = fh.read()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Content-Security-Policy", "default-src 'self'; style-src 'unsafe-inline'; script-src 'unsafe-inline'; frame-ancestors 'none'")
            self.send_header("X-Frame-Options", "DENY")
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(data)
            return
        if path == "/api/session":
            return self.send_json(200, {"authenticated": bool(self.session()), "version": __version__})
        if not self.session():
            return self.send_json(401, {"error": "sign in required"})
        try:
            if path == "/api/plans":
                os.makedirs(PLANS, exist_ok=True)
                return self.send_json(200, [plan_summary(f) for f in sorted(os.listdir(PLANS)) if f.endswith(".yaml")])
            m = re.match(r"^/api/plans/([^/]+)(/yaml|/log)?$", path)
            if m:
                fp = plan_path(m[1])
                if not os.path.exists(fp):
                    return self.send_json(404, {"error": "no such plan"})
                if m[2] == "/yaml":
                    with open(fp) as fh:
                        return self.send_json(200, {"yaml": fh.read()})
                summary = plan_summary(m[1] + ".yaml")
                if m[2] == "/log":
                    job = jobs.get(summary.get("name"))
                    return self.send_json(200, {"log": job["log"][-30000:] if job else ""})
                return self.send_json(200, summary)
            if path == "/api/creds":
                try:
                    return self.send_json(200, credentials.load().public())
                except RuntimeError as e:
                    return self.send_json(200, {"error": str(e)})
            return self.send_json(404, {"error": "not found"})
        except ValueError as e:
            return self.send_json(400, {"error": str(e)})

    def do_POST(self):
        path = urlparse(self.path).path
        if self.headers.get("X-Requested-With") != "ev2ahv":
            return self.send_json(403, {"error": "bad request origin"})
        try:
            b = self.body()
            if path == "/api/login":
                u = ui_user()
                good = hmac.compare_digest(_hash(str(b.get("password", "")), u["salt"]), u["hash"])
                if not (good and hmac.compare_digest(str(b.get("username", "")), u["username"])):
                    time.sleep(1)
                    return self.send_json(401, {"error": "wrong username or password"})
                token = secrets.token_urlsafe(32)
                sessions[token] = {"expires": time.time() + SESSION_SECONDS}
                return self.send_json(200, {"ok": True}, f"ev2ahv={token}; HttpOnly; SameSite=Strict; Path=/; Max-Age={SESSION_SECONDS}")
            token = self.session()
            if not token:
                return self.send_json(401, {"error": "sign in required"})
            if path == "/api/logout":
                sessions.pop(token, None)
                return self.send_json(200, {"ok": True}, "ev2ahv=; Path=/; Max-Age=0")
            if path == "/api/creds/test":
                c = credentials.load()
                return self.send_json(200, {"ok": True, "vms": len(Prism(c).list_vms(select="name")), **c.public()})
            if path == "/api/creds":
                return self.save_creds(b)
            if path == "/api/ui-password":
                u = ui_user()
                if not hmac.compare_digest(_hash(str(b.get("current", "")), u["salt"]), u["hash"]):
                    return self.send_json(403, {"error": "current password is wrong"})
                if len(str(b.get("new", ""))) < 8:
                    return self.send_json(400, {"error": "use at least 8 characters"})
                set_ui_user(u["username"], b["new"])
                return self.send_json(200, {"ok": True})
            if path == "/api/plans":
                return self.save_plan(b)
            m = re.match(r"^/api/plans/([^/]+)/action/([a-z-]+)$", path)
            if m:
                return self.action(m[1], m[2], b)
            return self.send_json(404, {"error": "not found"})
        except (ValueError, RuntimeError, OSError, yaml.YAMLError) as e:
            return self.send_json(400, {"error": str(e)[:600]})

    def save_creds(self, b):
        host, port = str(b.get("host", "")).strip(), int(b.get("port") or 9440)
        if not re.match(r"^[A-Za-z0-9.-]{1,253}$", host):
            raise ValueError("invalid Prism host")
        verify = bool(b.get("verify_tls"))
        if b.get("method") == "api_key":
            kw = {"api_key": str(b.get("api_key", "")).strip()}
            if not kw["api_key"]:
                raise ValueError("API key is required")
        else:
            kw = {"username": str(b.get("username", "")).strip(), "password": str(b.get("password", ""))}
            if not (kw["username"] and kw["password"]):
                raise ValueError("username and password are required")
        test = credentials.Credentials(host, port, verify_tls=verify, source="ev2ahv", **kw)
        n = len(Prism(test).list_vms(select="name"))    # only saved if Prism accepts them
        credentials.save(host, port, verify_tls=verify, **kw)
        return self.send_json(200, {"ok": True, "vms": n, **test.public()})

    def save_plan(self, b):
        doc = yaml.safe_load(b.get("yaml") or "")
        if not isinstance(doc, dict):
            raise ValueError("plan must be a YAML mapping")
        doc = planlib.validate(doc)
        fp = plan_path(doc["name"])
        if os.path.exists(fp) and not b.get("overwrite"):
            raise ValueError(f"plan {doc['name']} already exists")
        os.makedirs(PLANS, exist_ok=True)
        planlib.dump(doc, fp)
        return self.send_json(200, {"ok": True, "name": doc["name"]})

    def action(self, name, action, b):
        if action not in ACTIONS:
            raise ValueError("unknown action")
        fp = plan_path(name)
        p = planlib.load(fp)
        if action in CONFIRM and b.get("confirm") != p["name"]:
            raise ValueError(f"type the plan name to confirm {action}")
        flags = []
        if b.get("dry_run"):
            flags.append("--dry-run")
        if action == "cutover" and b.get("source_already_stopped"):
            flags.append("--source-already-stopped")
        if action == "cleanup" and b.get("delete_target"):
            flags.append("--delete-target")
        run_job(p["name"], fp, action, flags)
        return self.send_json(202, {"ok": True})


class Server(socketserver.ThreadingMixIn, http.server.HTTPServer):
    daemon_threads = True
    allow_reuse_address = True


def serve(bind="127.0.0.1", port=8460):
    ui_user()
    print(f"ev2ahv console on http://{bind}:{port}", flush=True)
    Server((bind, port), Handler).serve_forever()
