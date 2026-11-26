"""D13: local users with admin and viewer roles."""
import json, os, sys, tempfile, unittest
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path[:0] = [ROOT, os.path.join(ROOT, "server")]
from ev2ahv import web as oldweb  # noqa: E402
from ev2ahv_server.store import Store  # noqa: E402
from ev2ahv_server.users import UserError, Users, public, write_allowed  # noqa: E402


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.ui_file, oldweb.UI_FILE = oldweb.UI_FILE, os.path.join(self.tmp.name, "ui.json")
        self.store = Store(os.path.join(self.tmp.name, "t.db"))
        self.u = Users(self.store)

    def tearDown(self):
        oldweb.UI_FILE = self.ui_file
        self.tmp.cleanup()


class RolesTest(unittest.TestCase):
    def test_viewer_reads_and_own_session_only(self):
        self.assertTrue(write_allowed("viewer", "GET", "/api/v2/plans"))
        self.assertTrue(write_allowed("viewer", "POST", "/api/v2/password"))
        self.assertTrue(write_allowed("viewer", "POST", "/api/v2/logout"))
        for m, p in (("POST", "/api/v2/plans"), ("PUT", "/api/v2/settings"), ("DELETE", "/api/v2/plans/x"),
                     ("POST", "/api/v2/plans/x/actions"), ("POST", "/api/v2/users"), ("PUT", "/api/v2/appliance/tls")):
            self.assertFalse(write_allowed("viewer", m, p), (m, p))
            self.assertTrue(write_allowed("admin", m, p), (m, p))

    def test_unknown_role_is_read_only(self):
        self.assertFalse(write_allowed(None, "POST", "/api/v2/plans"))


class EnsureTest(Base):
    def test_first_start_creates_default_admin(self):
        self.u.ensure("nutanix", "nutanix/4u", must_change_default=True)
        u = self.u.get("nutanix")
        self.assertEqual((u["role"], u["must_change"]), ("admin", True))
        self.assertTrue(self.u.check("nutanix", "nutanix/4u"))

    def test_upgrade_imports_ui_json_with_its_password(self):
        oldweb.set_ui_user("ops", "S3cret-pass")
        self.u.ensure("nutanix", "nutanix/4u", must_change_default=True)
        self.assertEqual([x["username"] for x in self.u.all()], ["ops"])
        self.assertEqual(self.u.check("ops", "S3cret-pass")["role"], "admin")

    def test_console_reset_restores_default_password(self):
        self.u.ensure("nutanix", "nutanix/4u", must_change_default=False)
        self.u.update("nutanix", password="changed-it", actor="nutanix")
        self.u.ensure("nutanix", "nutanix/4u", must_change_default=False, reset=True)
        self.assertTrue(self.u.check("nutanix", "nutanix/4u")["must_change"])

    def test_second_ensure_changes_nothing(self):
        self.u.ensure("nutanix", "nutanix/4u", must_change_default=False)
        self.u.create("view1", "viewer-pass", "viewer")
        self.u.ensure("nutanix", "nutanix/4u", must_change_default=False)
        self.assertEqual(len(self.u.all()), 2)


class ManageTest(Base):
    def setUp(self):
        super().setUp()
        self.u.ensure("nutanix", "nutanix/4u", must_change_default=False)

    def test_create_viewer_must_change_and_no_secrets_in_public(self):
        u = self.u.create("view1", "viewer-pass", "viewer")
        self.assertTrue(u["must_change"])
        self.assertEqual(set(public(u)), {"username", "role", "must_change", "source", "created_at"})
        self.assertNotIn("hash", json.dumps(public(u)))

    def test_bad_input(self):
        for args in (("bad name", "longenough", "viewer"), ("ok", "short", "viewer"), ("ok", "longenough", "root"),
                     ("nutanix", "longenough", "viewer")):
            with self.assertRaises(UserError, msg=args):
                self.u.create(*args)

    def test_wrong_password_and_unknown_user(self):
        self.assertIsNone(self.u.check("nutanix", "wrong"))
        self.assertIsNone(self.u.check("nobody", "nutanix/4u"))
        self.assertIsNone(self.u.check("../etc", "x"))

    def test_last_admin_is_protected(self):
        with self.assertRaises(UserError):
            self.u.update("nutanix", role="viewer")
        self.u.create("view1", "viewer-pass", "viewer")
        with self.assertRaises(UserError):
            self.u.delete("view1", actor="view1")      # not yourself
        with self.assertRaises(UserError):
            self.u.delete("nutanix", actor="view1")    # not the last admin
        self.u.create("adm2", "admin-pass2", "admin")
        self.u.update("nutanix", role="viewer")
        self.u.delete("nutanix", actor="adm2")
        self.assertEqual([x["username"] for x in self.u.all()], ["adm2", "view1"])

    def test_admin_reset_forces_change_own_change_does_not(self):
        self.u.create("view1", "viewer-pass", "viewer")
        self.u.update("view1", password="reset-by-admin", actor="nutanix")
        self.assertTrue(self.u.get("view1")["must_change"])
        self.u.change_own("view1", "reset-by-admin", "my-own-pass")
        self.assertFalse(self.u.get("view1")["must_change"])
        with self.assertRaises(UserError):
            self.u.change_own("view1", "wrong", "another-pass")
        with self.assertRaises(UserError):
            self.u.change_own("view1", "my-own-pass", "nutanix/4u", banned=("nutanix/4u",))


if __name__ == "__main__":
    unittest.main()
