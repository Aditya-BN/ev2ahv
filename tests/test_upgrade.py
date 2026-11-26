"""D7: upgrade bundle and ev2ahv-upgrade apply/rollback on a fake appliance tree."""
import importlib.machinery, importlib.util, json, os, sqlite3, sys, tarfile, tempfile, unittest
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path[:0] = [ROOT, os.path.join(ROOT, "server"), os.path.join(ROOT, "tools")]
import make_bundle  # noqa: E402
from ev2ahv_server.store import MIGRATIONS, migrate  # noqa: E402

TOOL = os.path.join(ROOT, "appliance", "rootfs", "usr", "local", "sbin", "ev2ahv-upgrade")
loader = importlib.machinery.SourceFileLoader("ev2ahv_upgrade", TOOL)
up = importlib.util.module_from_spec(importlib.util.spec_from_loader("ev2ahv_upgrade", loader))
loader.exec_module(up)


def write(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as fh:
        fh.write(text)


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        t = self.tmp.name
        # a fake source tree for the bundle
        self.src = os.path.join(t, "src")
        for f, txt in (("ev2ahv/__init__.py", "v = 'new'\n"), ("server/ev2ahv_server/x.py", "x = 1\n"),
                       ("prep/p.sh", "echo\n"), ("web/dist/index.html", "<html>new</html>"), ("VERSION", "0.3.0\n"),
                       ("appliance/requirements.txt", "fastapi==1\n"), ("build/wheels/fastapi-1.whl", "wheel")):
            write(os.path.join(self.src, f), txt)
        with open(TOOL) as fh:
            write(os.path.join(self.src, "appliance/rootfs/usr/local/sbin/ev2ahv-upgrade"), fh.read())
        self.bundle, self.man = make_bundle.build(os.path.join(t, "dist"), "0.2.0", root=self.src)
        # a fake appliance
        a = os.path.join(t, "appl")
        up.P.update(app=f"{a}/opt/app", venv=f"{a}/opt/venv", data=f"{a}/var", state=f"{a}/var/state",
                    env=f"{a}/etc/ev2ahv.env", sock=f"{a}/run/app.sock", self=f"{a}/sbin/ev2ahv-upgrade",
                    link=f"{a}/usrsbin/ev2ahv-upgrade", cookie=f"{a}/run/health.cookie")
        os.makedirs(f"{a}/usrsbin", exist_ok=True)
        write(f"{a}/opt/app/VERSION", "0.2.0\n")
        write(f"{a}/opt/app/ev2ahv/__init__.py", "v = 'old'\n")
        write(f"{a}/opt/venv/bin/python", "old venv")
        write(f"{a}/etc/ev2ahv.env", "EV2AHV_APPLIANCE=1\n")
        os.makedirs(f"{a}/var/state", exist_ok=True)
        db = sqlite3.connect(f"{a}/var/state/server.db")
        db.execute("CREATE TABLE docs(id TEXT)")
        db.execute("INSERT INTO docs VALUES('old-plan')")
        db.commit()
        db.close()
        os.makedirs(f"{a}/sbin", exist_ok=True)
        self.a = a
        self.calls = []

    def tearDown(self):
        self.tmp.cleanup()

    def ops(self, healthy=True, busy=False):
        def health(v):
            self.calls.append(("health", v))
            if not healthy:
                raise up.UpgradeError("no answer")
        return {"systemctl": lambda a: self.calls.append(("systemctl", a)), "health": health,
                "busy": lambda: busy, "install_venv": lambda w, r: self.calls.append(("venv", os.listdir(w)))}

    def app_text(self):
        return open(f"{self.a}/opt/app/ev2ahv/__init__.py").read()

    def plans(self):
        db = sqlite3.connect(f"{self.a}/var/state/server.db")
        try:
            return [r[0] for r in db.execute("SELECT id FROM docs")]
        finally:
            db.close()


class HealthTest(Base):
    """D7 notes: the health check reads the stored data, so a broken data model restores the old version."""
    def setUp(self):
        super().setUp()
        write(os.path.join(self.src, "VERSION"), "0.3.4\n")
        self.bundle, self.man = make_bundle.build(os.path.join(self.tmp.name, "dist2"), "0.2.0", root=self.src)
        write(up.P["cookie"], "tok\n")
        self.fetched = []
        self.status = {}
        self.orig = up.fetch

        def fetch(path, cookie=None):
            self.fetched.append((path, cookie))
            if path == "/api/v2/appinfo":
                return 200, json.dumps({"version": "0.3.4"})
            return self.status.get(path, 200), "{}"
        up.fetch = fetch

    def tearDown(self):
        up.fetch = self.orig
        super().tearDown()

    def real_health_ops(self):
        ops = self.ops()
        del ops["health"]
        ops["health"] = up.health
        return ops

    def test_health_reads_plans_and_the_other_lists(self):
        up.apply(self.bundle, ops=self.real_health_ops())
        paths = [p for p, _ in self.fetched]
        for want in ("/api/v2/plans", "/api/v2/plans/vms", "/api/v2/providers", "/api/v2/events", "/api/v2/settings"):
            self.assertIn(want, paths)
        self.assertEqual({c for p, c in self.fetched if p == "/api/v2/plans"}, {"tok"})
        self.assertEqual(up.current_version(), "0.3.4")

    def test_plans_500_after_upgrade_restores_the_old_version(self):
        self.status["/api/v2/plans"] = 500
        with self.assertRaises(up.UpgradeError) as cm:
            up.apply(self.bundle, ops=self.real_health_ops())
        self.assertIn("/api/v2/plans", str(cm.exception))
        self.assertIn("restored", str(cm.exception))
        self.assertEqual(self.app_text(), "v = 'old'\n")
        self.assertEqual(up.current_version(), "0.2.0")
        self.assertEqual(self.plans(), ["old-plan"])
        self.assertEqual(self.calls[-1], ("systemctl", "start"))

    def test_missing_health_session_restores(self):
        os.remove(up.P["cookie"])
        with self.assertRaises(up.UpgradeError):
            up.apply(self.bundle, ops=self.real_health_ops())
        self.assertEqual(up.current_version(), "0.2.0")

    def test_older_bundle_only_checks_appinfo(self):
        up.data_check("0.3.2")
        self.assertEqual(self.fetched, [])

    def test_sudo_link_is_created_and_never_replaces_a_file(self):
        up.apply(self.bundle, ops=self.real_health_ops())
        self.assertEqual(os.readlink(up.P["link"]), up.P["self"])
        os.remove(up.P["link"])
        write(up.P["link"], "mine")
        up.link_self()
        self.assertEqual(open(up.P["link"]).read(), "mine")


class TarfileWarningTest(Base):
    def test_unpack_emits_no_warning(self):
        import warnings
        with warnings.catch_warnings():
            warnings.simplefilter("error")
            up.unpack(self.bundle, os.path.join(self.tmp.name, "x"))

    def test_unsafe_members_are_still_refused(self):
        evil = os.path.join(self.tmp.name, "evil.tar.gz")
        with tarfile.open(evil, "w:gz") as tf:
            ti = tarfile.TarInfo("../escape")
            tf.addfile(ti, __import__("io").BytesIO(b""))
        with self.assertRaises(up.UpgradeError):
            up.unpack(evil, os.path.join(self.tmp.name, "y"))
        self.assertFalse(os.path.exists(os.path.join(self.tmp.name, "escape")))


class BundleTest(Base):
    def test_manifest_covers_every_file(self):
        with tarfile.open(self.bundle) as tf:
            names = {m.name for m in tf.getmembers()} - {"manifest.json"}
        self.assertEqual(names, set(self.man["files"]))
        self.assertIn("app/web/dist/index.html", names)
        self.assertIn("wheels/fastapi-1.whl", names)
        self.assertEqual(self.man["version"], "0.3.0")
        self.assertTrue(os.path.exists(self.bundle + ".sha256"))


class ApplyTest(Base):
    def test_upgrade_installs_backs_up_and_checks_health(self):
        bdir = up.apply(self.bundle, ops=self.ops())
        self.assertEqual(self.app_text(), "v = 'new'\n")
        self.assertEqual(up.current_version(), "0.3.0")
        self.assertEqual([c for c in self.calls if c[0] != "venv"],
                         [("systemctl", "stop"), ("systemctl", "start"), ("health", "0.3.0")])
        self.assertIn(("venv", ["fastapi-1.whl"]), self.calls)     # 0.2.0 had no requirements.txt in the app
        self.assertTrue(os.path.exists(os.path.join(bdir, "state", "server.db")))
        self.assertEqual(json.load(open(os.path.join(bdir, "backup.json")))["from"], "0.2.0")
        self.assertTrue(os.path.exists(up.P["self"]))

    def test_failed_health_check_restores_everything(self):
        db = sqlite3.connect(f"{self.a}/var/state/server.db")

        def bad_start(action):
            self.calls.append(("systemctl", action))
            if action == "start" and up.current_version() == "0.3.0":   # the new version breaks the database
                db.execute("DELETE FROM docs")
                db.commit()
        ops = self.ops(healthy=False)
        ops["systemctl"] = bad_start
        with self.assertRaises(up.UpgradeError):
            up.apply(self.bundle, ops=ops)
        db.close()
        self.assertEqual(self.app_text(), "v = 'old'\n")
        self.assertEqual(up.current_version(), "0.2.0")
        self.assertEqual(self.plans(), ["old-plan"])
        self.assertEqual(open(f"{self.a}/opt/venv/bin/python").read(), "old venv")
        self.assertEqual(self.calls[-1], ("systemctl", "start"))

    def test_tampered_bundle_is_refused_before_anything_stops(self):
        t = os.path.join(self.tmp.name, "bad.tar.gz")
        with tarfile.open(self.bundle) as src, tarfile.open(t, "w:gz") as dst:
            for m in src.getmembers():
                data = src.extractfile(m).read()
                if m.name == "app/ev2ahv/__init__.py":
                    data = b"evil = 1\n"
                    m.size = len(data)
                import io
                dst.addfile(m, io.BytesIO(data))
        with self.assertRaises(up.UpgradeError):
            up.apply(t, ops=self.ops())
        self.assertEqual(self.calls, [])
        self.assertEqual(self.app_text(), "v = 'old'\n")

    def test_downgrade_and_busy_are_refused(self):
        write(f"{self.a}/opt/app/VERSION", "0.4.0\n")
        with self.assertRaises(up.UpgradeError):
            up.apply(self.bundle, ops=self.ops())
        write(f"{self.a}/opt/app/VERSION", "0.2.0\n")
        with self.assertRaises(up.UpgradeError):
            up.apply(self.bundle, ops=self.ops(busy=True))
        self.assertEqual(self.calls, [])

    def test_manual_rollback_returns_to_the_previous_version(self):
        up.apply(self.bundle, ops=self.ops())
        up.rollback(ops={"systemctl": lambda a: self.calls.append(("systemctl", a))})
        self.assertEqual(up.current_version(), "0.2.0")
        self.assertEqual(self.app_text(), "v = 'old'\n")


class MigrateTest(unittest.TestCase):
    def test_versions_and_newer_database_refused(self):
        db = sqlite3.connect(":memory:")
        self.assertEqual(migrate(db), len(MIGRATIONS))
        self.assertEqual(db.execute("PRAGMA user_version").fetchone()[0], len(MIGRATIONS))
        db.execute(f"PRAGMA user_version={len(MIGRATIONS) + 1}")
        with self.assertRaises(RuntimeError):
            migrate(db)


if __name__ == "__main__":
    unittest.main()
