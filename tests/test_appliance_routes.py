"""D7: the upgrade routes are registered on the appliance router, and redact() is intact (a misplaced block once
landed inside redact(), which left the routes unregistered and broke the support bundle)."""
import os, sys, unittest
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path[:0] = [ROOT, os.path.join(ROOT, "server")]
from ev2ahv_server import appliance  # noqa: E402


class FakeStore:
    def get(self, kind, id_):
        return None

    def all(self, kind):
        return []


class RoutesTest(unittest.TestCase):
    def test_upgrade_routes_exist(self):
        r = appliance.router(FakeStore(), lambda: "t", lambda: None)
        routes = {(m, rt.path) for rt in r.routes for m in rt.methods}
        self.assertIn(("GET", "/api/v2/upgrade"), routes)
        self.assertIn(("PUT", "/api/v2/upgrade"), routes)
        self.assertIn(("GET", "/api/v2/health"), routes)

    def test_redact_still_works(self):
        self.assertEqual(appliance.redact("password=hunter2 ok"), "password=<redacted> ok")


if __name__ == "__main__":
    unittest.main()
