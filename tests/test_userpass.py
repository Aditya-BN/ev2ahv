"""D11/T10: Prism username/password (HTTP Basic) auth: header, 401 handling, no secret in errors."""
import base64, io, os, sys, unittest, urllib.error
from unittest import mock
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from ev2ahv import credentials  # noqa: E402
from ev2ahv.prism import Prism, PrismError  # noqa: E402

PW = "S3cret:pa ss!"


def creds():
    return credentials.Credentials("pc", username="svc", password=PW)


class UserPass(unittest.TestCase):
    def test_basic_header_and_method(self):
        c = creds()
        self.assertEqual(c.method, "basic")
        auth = c.headers()["Authorization"]
        self.assertEqual(base64.b64decode(auth.split()[1]).decode(), f"svc:{PW}")   # a colon in the password survives

    def test_public_has_no_secret(self):
        self.assertNotIn(PW, str(creds().public()))

    def test_load_prefers_api_key_and_falls_back_to_userpass(self):
        env = {"NTNX_PC": "pc", "NTNX_USERNAME": "svc", "NTNX_PASSWORD": PW}
        with mock.patch.dict(os.environ, env, clear=True):
            self.assertEqual(credentials.load().method, "basic")
        with mock.patch.dict(os.environ, dict(env, NTNX_API_KEY="k"), clear=True):
            self.assertEqual(credentials.load().method, "api_key")

    def test_401_is_a_clear_error_without_the_password(self):
        err = urllib.error.HTTPError("u", 401, "Unauthorized", {}, io.BytesIO(b'{"message": "invalid credentials"}'))
        with mock.patch("urllib.request.urlopen", side_effect=err):
            with self.assertRaises(PrismError) as cm:
                Prism(creds()).get("/vmm/v4.2/ahv/config/vms")
        msg = str(cm.exception)
        self.assertIn("HTTP 401", msg)
        self.assertNotIn(PW, msg)
        self.assertNotIn(base64.b64encode(f"svc:{PW}".encode()).decode(), msg)

    def test_401_mid_run_fails_only_that_call(self):
        """Basic auth has no session: every call carries the header, so a 401 mid-run (password changed) fails that call."""
        ok = mock.MagicMock()
        ok.__enter__.return_value.read.return_value = b'{"data": []}'
        ok.__enter__.return_value.headers = {}
        bad = urllib.error.HTTPError("u", 401, "Unauthorized", {}, io.BytesIO(b"{}"))
        p = Prism(creds())
        with mock.patch("urllib.request.urlopen", side_effect=[ok, bad, ok]) as m:
            self.assertEqual(p.get("/a"), {"data": []})
            with self.assertRaises(PrismError):
                p.get("/b")
            self.assertEqual(p.get("/c"), {"data": []})
        for call in m.call_args_list:
            self.assertTrue(call.args[0].get_header("Authorization").startswith("Basic "))


if __name__ == "__main__":
    unittest.main()


class PickProvider(unittest.TestCase):
    def test_selftest_provider_choice(self):
        sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "server"))
        from ev2ahv_server.appliance import pick_provider
        ps = [{"id": "1", "name": "PC-key", "type": "aos"}, {"id": "2", "name": "PC-userpass", "type": "aos"},
              {"id": "3", "name": "er", "type": "everrun"}]
        self.assertEqual(pick_provider(ps)["id"], "1")
        self.assertEqual(pick_provider(ps, "PC-userpass")["id"], "2")
        self.assertEqual(pick_provider(ps, "2")["id"], "2")
        self.assertIsNone(pick_provider(ps, "er"))
        self.assertIsNone(pick_provider(ps, "nope"))
