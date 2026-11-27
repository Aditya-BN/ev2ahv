"""D13: LDAP sign-in with ldap3's offline mock directory (no network). Skipped when ldap3 is not installed."""
import os, sys, unittest
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path[:0] = [ROOT, os.path.join(ROOT, "server")]
from ev2ahv_server.users import UserError, ldap_login, ldap_settings_ok  # noqa: E402

try:
    import ldap3
except ImportError:
    ldap3 = None

CFG = {"enabled": True, "url": "ldaps://dc.example.com", "bind_dn": "cn=svc,dc=ex,dc=com", "user_base": "ou=people,dc=ex,dc=com",
       "user_filter": "(uid={username})", "admin_group": "cn=ev2ahv-admins,ou=groups,dc=ex,dc=com",
       "viewer_group": "cn=ev2ahv-viewers,ou=groups,dc=ex,dc=com"}


@unittest.skipIf(ldap3 is None, "ldap3 not installed")
class LdapLoginTest(unittest.TestCase):
    def setUp(self):
        self.server = ldap3.Server("mock")
        c = ldap3.Connection(self.server, client_strategy=ldap3.MOCK_SYNC)
        add = c.strategy.add_entry
        add("cn=svc,dc=ex,dc=com", {"userPassword": "svc-pw", "objectClass": "person"})
        for uid in ("alice", "bob", "carol"):
            add(f"uid={uid},ou=people,dc=ex,dc=com", {"uid": uid, "userPassword": f"{uid}-pw", "objectClass": "person"})
        add("cn=ev2ahv-admins,ou=groups,dc=ex,dc=com", {"objectClass": "groupOfNames",
                                                      "member": ["uid=alice,ou=people,dc=ex,dc=com"]})
        add("cn=ev2ahv-viewers,ou=groups,dc=ex,dc=com", {"objectClass": "groupOfNames",
                                                       "member": ["uid=bob,ou=people,dc=ex,dc=com"]})

    def login(self, user, pw, cfg=CFG, secret="svc-pw"):
        return ldap_login({"ldap": cfg}, user, pw, secret=lambda k: secret, server=self.server,
                          strategy=ldap3.MOCK_SYNC)

    def test_roles_from_groups(self):
        self.assertEqual(self.login("alice", "alice-pw"),
                         {"username": "alice", "role": "admin", "must_change": False, "source": "ldap"})
        self.assertEqual(self.login("bob", "bob-pw")["role"], "viewer")

    def test_wrong_password_unknown_user_and_no_group(self):
        self.assertIsNone(self.login("alice", "nope"))
        self.assertIsNone(self.login("mallory", "x"))
        self.assertIsNone(self.login("carol", "carol-pw"))     # valid user, in no ev2ahv group

    def test_service_bind_failure_and_disabled(self):
        self.assertIsNone(self.login("alice", "alice-pw", secret="wrong"))
        self.assertIsNone(self.login("alice", "alice-pw", cfg=dict(CFG, enabled=False)))

    def test_filter_injection_is_escaped(self):
        self.assertIsNone(self.login("*", "alice-pw"))
        self.assertIsNone(self.login("alice)(uid=*", "alice-pw"))

    def test_empty_password_never_binds(self):
        self.assertIsNone(self.login("alice", ""))


class LdapSettingsTest(unittest.TestCase):
    def test_valid_and_defaults(self):
        out = ldap_settings_ok(dict(CFG, user_filter=None, bind_password="x"))
        self.assertEqual(out["user_filter"], "(sAMAccountName={username})")
        self.assertNotIn("bind_password", out)

    def test_refusals(self):
        for bad in (dict(CFG, url="http://x"), dict(CFG, url="ldap://dc"),            # clear text without StartTLS
                    dict(CFG, bind_dn=""), dict(CFG, admin_group=None, viewer_group=None),
                    dict(CFG, user_filter="(uid=alice)")):
            with self.assertRaises(UserError, msg=bad):
                ldap_settings_ok(bad)
        self.assertTrue(ldap_settings_ok(dict(CFG, url="ldap://dc", start_tls=True)))
        self.assertTrue(ldap_settings_ok(dict(CFG, url="ldap://dc", allow_plain=True)))
        self.assertEqual(ldap_settings_ok({"enabled": False}), {"enabled": False})


if __name__ == "__main__":
    unittest.main()
