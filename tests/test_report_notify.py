"""Migration report (HTML / CSV, redaction, timeline) and notifications (fake SMTP and webhook servers)."""
import base64, csv, io, json, os, socketserver, subprocess, sys, tempfile, textwrap, threading, time, unittest
from http.server import BaseHTTPRequestHandler, HTTPServer
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path[:0] = [ROOT, os.path.join(ROOT, "server")]
from ev2ahv_server import notify, report, store as store_mod  # noqa: E402

T0 = 1_700_000_000.0
PLAN = {
    "id": "p1", "name": "wave1", "created_by": "alice", "created_at": T0 - 100, "status": "Completed", "started_at": T0,
    "source": {"id": "s", "name": "everrun-lab"}, "target": {"id": "t", "name": "PC-136"}, "cluster": "C1",
    "container": "default", "project": "default", "network_map": [{"source_network": "n0", "target_subnet": "vlan136"}],
    "test_network": False, "prep": {"mode": "automatic", "windows_password": "hunter2hunter2", "retain_ip": True},
    "vm_settings": {"v1": {"target_name": "web01-ahv"}}, "schedule": {"seed_at": None, "resync_minutes": 60},
    "data_migrated_bytes": 5 << 30,
    "vms": [{"id": "v1", "name": "web01", "os_type": "linux", "status": "Completed", "total_bytes": 5 << 30,
             "data_transferred_bytes": 5 << 30, "target_vm_id": "tvm-1", "error": None,
             "checks": [{"name": "Source reachable", "status": "PASS", "detail": "node0"},
                        {"name": "Guest", "status": "WARN", "detail": "login password=s3cretvalue ok"}],
             "cutover_result": {"completed_at": T0 + 900, "downtime_seconds": 120.0, "final_sync_seconds": 60.0,
                                "final_verify": "full", "final_verified_rounds": 1, "final_sync_sent_bytes": 1 << 20,
                                "verify": {"power": "ON", "reachable": "10.0.0.5:22"}}},
            {"id": "v2", "name": "=cmd|calc", "os_type": "windows", "status": "Failed", "total_bytes": 1, "checks": [],
             "data_transferred_bytes": 0, "error": "boom"}],
}
EVENTS = [{"time": T0 + t, "severity": s, "plan": "wave1", "vm": vm, "message": msg} for t, s, vm, msg in [
    (0, "info", None, "alice started plan wave1"), (5, "info", "web01", "seed started"), (300, "info", "web01", "seed finished"),
    (400, "info", "web01", "resync started"), (460, "info", "web01", "resync finished"),
    (800, "info", "web01", "alice: cutover web01"), (810, "info", "web01", "cutover started"),
    (900, "warning", "web01", "rollback started"), (990, "info", "web01", "cutover finished"),
    (10, "error", "=cmd|calc", "seed failed: token abcdef123 expired")]]
PROVIDERS = [{"id": "s", "type": "everrun", "address": "10.0.0.1", "version": "8.1"},
             {"id": "t", "type": "aos", "address": "10.0.0.2", "version": "pc.2025"}]


def build(**kw):
    return report.redacted(report.build(PLAN, EVENTS, PROVIDERS, {"timezone": "Asia/Jakarta"}, "9.9.9", now=T0 + 2000, **kw),
                           ["hunter2hunter2"])


class ReportTest(unittest.TestCase):
    def test_timeline_and_numbers(self):
        r = build()
        v = r["vms"][0]
        self.assertEqual([t["label"] for t in v["timeline"]], [
            "Seed started", "Seed finished", "Resync started", "Resync finished", "Cutover requested", "Cutover started",
            "Rollback started", "Cutover finished"])
        self.assertEqual((v["resyncs"], v["rolled_back"], v["downtime_seconds"]), (1, True, 120.0))
        self.assertEqual(r["plan"]["started_by"], "alice")
        self.assertIn("matched the source", v["final_verify_result"])
        self.assertEqual(v["target_name"], "web01-ahv")

    def test_html_has_sections_footer_and_no_secrets(self):
        h = report.render_html(build())
        for s in ("Plan summary", "Prep-check", "Timeline", "Audit trail", "ev2ahv appliance version 9.9.9", "tvm-1",
                  "Print / Save as PDF", "2023-11-15 05:13"):   # T0 shown in WIB
            self.assertIn(s, h)
        self.assertNotIn("hunter2", h)
        self.assertNotIn("s3cretvalue", h)
        self.assertNotIn("abcdef123", h)
        self.assertNotIn("http://", h.replace("http://www.w3.org", ""))   # inline only, no CDN
        self.assertNotIn("<script src", h)
        self.assertNotIn("&lt;b&gt;", report.render_html(build(), pdf_note=True))
        self.assertIn("does not render PDF", report.render_html(build(), pdf_note=True))

    def test_single_vm(self):
        r = build(vm_id="v1")
        self.assertEqual([v["name"] for v in r["vms"]], ["web01"])
        self.assertFalse(any(e["vm"] == "=cmd|calc" for e in r["events"]))

    def test_csv_one_row_per_vm_and_formula_guard(self):
        rows = list(csv.reader(io.StringIO(report.render_csv(build()))))
        self.assertEqual(len(rows), 3)
        self.assertEqual(rows[0], report.CSV_COLUMNS)
        d = dict(zip(rows[0], rows[1]))
        self.assertEqual((d["vm"], d["target_vm_id"], d["downtime_seconds"], d["power_check"]), ("web01", "tvm-1", "120.0", "ON"))
        self.assertEqual(d["cutover_completed_utc"], "2023-11-14T22:28:20Z")
        self.assertEqual(rows[2][2], "'=cmd|calc")
        self.assertNotIn("hunter2", report.render_csv(build()))


class FakeSmtp(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True

    def __init__(self):
        self.mail, self.auth = [], []
        super().__init__(("127.0.0.1", 0), FakeSmtpHandler)


class FakeSmtpHandler(socketserver.StreamRequestHandler):
    def send(self, s):
        self.wfile.write(s.encode() + b"\r\n")

    def handle(self):
        self.send("220 fake ESMTP")
        data, msg = False, []
        for raw in self.rfile:
            line = raw.decode().rstrip("\r\n")
            if data:
                if line == ".":
                    self.server.mail.append("\n".join(msg))
                    data = False
                    self.send("250 queued")
                else:
                    msg.append(line)
                continue
            cmd = line.split(" ")[0].upper()
            if cmd == "EHLO":
                self.send("250-fake"), self.send("250 AUTH PLAIN")
            elif cmd == "AUTH":
                self.server.auth.append(base64.b64decode(line.split(" ")[2]).split(b"\0")[1:])
                self.send("235 ok")
            elif cmd == "DATA":
                data = True
                self.send("354 go")
            elif cmd == "QUIT":
                self.send("221 bye")
                return
            else:
                self.send("250 ok")


class Hook(BaseHTTPRequestHandler):
    def do_POST(self):
        body = self.rfile.read(int(self.headers["Content-Length"]))
        if self.server.fails > 0:
            self.server.fails -= 1
            self.send_response(500)
        else:
            self.server.got.append(json.loads(body))
            self.send_response(200)
        self.end_headers()

    def log_message(self, *a):
        pass


class NotifyTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        store_mod.ETC = self.tmp.name
        self.store = store_mod.Store(os.path.join(self.tmp.name, "s.db"))
        self.smtp = FakeSmtp()
        threading.Thread(target=self.smtp.serve_forever, daemon=True).start()
        self.hook = HTTPServer(("127.0.0.1", 0), Hook)
        self.hook.got, self.hook.fails = [], 0
        threading.Thread(target=self.hook.serve_forever, daemon=True).start()
        self.old = notify.BACKOFF
        notify.BACKOFF = [0.05, 0.05, 0.05]

    def tearDown(self):
        notify.BACKOFF = self.old
        self.smtp.shutdown(), self.hook.shutdown(), self.smtp.server_close(), self.hook.server_close()
        self.tmp.cleanup()

    def configure(self, **kw):
        cfg = {"enabled": True, "events": list(notify.EVENTS),
               "smtp": {"host": "127.0.0.1", "port": self.smtp.server_address[1], "tls": "none", "user": "u",
                        "password": "smtp-pass-1", "from": "ev2ahv@example.com", "to": "ops@example.com"},
               "webhook": {"url": f"http://127.0.0.1:{self.hook.server_address[1]}/hook"}}
        cfg.update(kw)
        doc = {"id": "settings", "notifications": notify.save(self.store, None, cfg)}
        self.store.put("settings", doc)
        return doc

    def wait(self, fn, secs=5):
        end = time.time() + secs
        while time.time() < end and not fn():
            time.sleep(0.02)
        return fn()

    def test_secrets_are_encrypted_and_never_public(self):
        doc = self.configure()
        self.assertNotIn("password", json.dumps(doc))
        self.assertNotIn("hook", json.dumps(doc))
        pub = json.dumps(notify.public(self.store, doc["notifications"]))
        self.assertNotIn("smtp-pass-1", pub)
        self.assertNotIn("/hook", pub)
        self.assertIn('"password_set": true', pub)
        raw = self.store.db.execute("SELECT body FROM secrets WHERE owner=?", (notify.SECRET_PW,)).fetchone()[0]
        self.assertNotIn("smtp-pass-1", raw)
        self.assertEqual(self.store.secret(notify.SECRET_PW), "smtp-pass-1")

    def test_emit_sends_mail_and_webhook(self):
        self.configure()
        self.assertTrue(notify.emit(self.store, "cutover", "wave1", "web01", "ok"))
        self.assertTrue(self.wait(lambda: self.smtp.mail and self.hook.got))
        self.assertIn("Subject: [ev2ahv] Cutover completed: web01 (wave1)", self.smtp.mail[0])
        self.assertEqual(self.smtp.auth[0], [b"u", b"smtp-pass-1"])
        self.assertEqual((self.hook.got[0]["event"], self.hook.got[0]["vm"]), ("cutover", "web01"))

    def test_disabled_or_unselected_events_send_nothing(self):
        self.configure(events=["failed"])
        self.assertFalse(notify.emit(self.store, "ready", "p", "v"))
        self.configure(enabled=False)
        self.assertFalse(notify.emit(self.store, "failed", "p", "v"))

    def test_retries_with_backoff_and_does_not_block(self):
        self.configure(smtp={"host": ""})
        self.hook.fails = 2
        t = time.time()
        self.assertTrue(notify.emit(self.store, "failed", "p", "v", "x"))
        self.assertLess(time.time() - t, 0.5)
        self.assertTrue(self.wait(lambda: self.hook.got))
        self.assertEqual(self.hook.fails, 0)

    def test_gives_up_and_records_an_event(self):
        self.configure(smtp={"host": ""})
        self.hook.fails = 99
        notify.emit(self.store, "rollback", "p", "v")
        self.assertTrue(self.wait(lambda: any("could not be sent" in e["message"] for e in self.store.events(20))))
        self.assertEqual(self.hook.got, [])

    def test_deliver_reports_errors(self):
        self.configure(smtp={"host": "127.0.0.1", "port": 1, "tls": "none", "from": "a@b", "to": "c@d"})
        res = dict(notify.deliver(self.store, "ready", "p", "v"))
        self.assertIn("Error", res["smtp"])
        self.assertIsNone(res["webhook"])

    def test_validation(self):
        for bad in ({"smtp": {"host": "h", "port": "x"}}, {"smtp": {"host": "h", "from": "a@b"}},
                    {"smtp": {"host": "h", "from": "a@b", "to": "c@d", "tls": "weird"}},
                    {"webhook": {"url": "ftp://x"}}):
            with self.assertRaises(notify.NotifyError):
                notify.save(self.store, None, bad)


SCRIPT = textwrap.dedent("""
    import sys
    sys.path[:0] = [sys.argv[1], sys.argv[1] + "/server"]
    from ev2ahv_server import api
    from fastapi import HTTPException
    api.mock.seed(api.store) if hasattr(api, "mock") else __import__("ev2ahv_server.mock", fromlist=["x"]).seed(api.store)
    r = api.plan_report("pl-wave1", "html", None, "t")
    t = r.body.decode()
    assert "Migration report: wave1-rocky" in t and "tvm-w-rocky9" in t and "Cutover finished" in t, t[:500]
    assert api.plan_report("pl-wave1", "csv", None, "t").body.decode().count("\\r\\n") == 2
    assert "does not render PDF" in api.plan_report("pl-wave1", "pdf", None, "t").body.decode()
    try:
        api.plan_report("pl-wave1", "docx", None, "t"); raise SystemExit("bad format accepted")
    except HTTPException as e:
        assert e.status_code == 422
    api.put_settings({"notifications": {"enabled": True, "smtp": {"host": "mail.x", "from": "a@b", "to": "c@d",
                      "password": "topsecretpw"}, "webhook": {"url": "https://h.example/tok123"}}}, "t")
    s = api.get_settings("t")["notifications"]
    assert s["smtp"]["password_set"] and s["webhook"] == {"set": True, "host": "h.example"}, s
    assert "topsecretpw" not in str(api.store.get("settings", "settings")) and "tok123" not in str(s)
    print("OK")
""")


class ApiTest(unittest.TestCase):
    def test_report_route_and_settings(self):
        with tempfile.TemporaryDirectory() as t:
            env = dict(os.environ, EV2AHV_SERVER_MODE="mock", EV2AHV_DATA=t, EV2AHV_SERVER_DB=os.path.join(t, "s.db"),
                       EV2AHV_CONFIG_DIR=os.path.join(t, "cfg"), EV2AHV_ETC=os.path.join(t, "etc"), HOME=t)
            r = subprocess.run([sys.executable, "-B", "-c", SCRIPT, ROOT], env=env, capture_output=True, text=True, timeout=120)
            self.assertEqual(r.returncode, 0, r.stderr[-2000:] + r.stdout[-500:])
            self.assertIn("OK", r.stdout)


if __name__ == "__main__":
    unittest.main()
