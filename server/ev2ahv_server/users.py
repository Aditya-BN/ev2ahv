"""UI users and roles (D13, minimal).

Local accounts live in the app database (kind "user", id = username) with a PBKDF2 hash, a role and a must_change flag.
Roles:
  admin   everything, including users and settings
  viewer  read only: every non-GET API call is refused, except logging in and out and changing their own password
The first start imports the single account from ui.json (the 0.2.0 login) as an admin, so upgrades keep their login.
LDAP is a stub: see docs/security.md, "UI users and roles".
"""
import hmac, json, os, re, secrets, time

from ev2ahv import web as oldweb

ROLES = ("admin", "viewer")
NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._@-]{0,63}$")
MIN_PASSWORD = 8
# write calls a viewer may still make: their own session and their own password
VIEWER_WRITES = {"/api/v2/token", "/api/v2/logout", "/api/v2/password"}


class UserError(ValueError):
    pass


def write_allowed(role, method, path):
    """True if a session with this role may make this API call. Reads are open to every role."""
    if method in ("GET", "HEAD", "OPTIONS") or not path.startswith("/api/"):
        return True
    return role == "admin" or path in VIEWER_WRITES


def _record(username, password, role, must_change):
    salt = secrets.token_hex(16)
    return {"id": username, "username": username, "salt": salt, "hash": oldweb._hash(password, salt), "role": role,
            "must_change": bool(must_change), "source": "local", "created_at": time.time()}


def public(u):
    return {k: u.get(k) for k in ("username", "role", "must_change", "source", "created_at")}


class Users:
    def __init__(self, store):
        self.store = store

    def all(self):
        return sorted(self.store.all("user"), key=lambda u: u["username"])

    def get(self, username):
        return self.store.get("user", username)

    def ensure(self, default_user, default_password, must_change_default, reset=False):
        """Make sure at least one admin exists. First start: import ui.json (0.2.0) or create the default account.
        reset=True (console 'reset UI password'): the default account gets the default password, admin, must_change."""
        if reset:
            self.store.put("user", _record(default_user, default_password, "admin", True))
            return
        if self.all():
            return
        if os.path.exists(oldweb.UI_FILE):
            with open(oldweb.UI_FILE) as fh:
                old = json.load(fh)
            self.store.put("user", {"id": old["username"], "username": old["username"], "salt": old["salt"],
                                    "hash": old["hash"], "role": "admin", "must_change": bool(old.get("must_change")),
                                    "source": "local", "created_at": time.time()})
        else:
            self.store.put("user", _record(default_user, default_password, "admin", must_change_default))

    def check(self, username, password):
        """The user record if the password matches, else None. Always hashes, so timing does not reveal names."""
        u = self.get(username) if NAME.match(username or "") else None
        h = oldweb._hash(password or "", u["salt"] if u else "00" * 16)
        if u and u.get("source", "local") == "local" and hmac.compare_digest(h, u["hash"]):
            return u
        return None

    def create(self, username, password, role):
        if not NAME.match(username or ""):
            raise UserError("User name: letters, digits, . _ @ - (up to 64), starting with a letter or digit")
        if role not in ROLES:
            raise UserError(f"Role must be one of {', '.join(ROLES)}")
        if self.get(username):
            raise UserError(f"User {username} already exists")
        self._password_ok(password)
        u = _record(username, password, role, True)   # the new user picks their own password at first login
        self.store.put("user", u)
        return u

    def update(self, username, role=None, password=None, actor=None):
        u = self.get(username)
        if not u:
            raise UserError(f"No user {username}")
        if role is not None:
            if role not in ROLES:
                raise UserError(f"Role must be one of {', '.join(ROLES)}")
            if u["role"] == "admin" and role != "admin" and self._admins() == [username]:
                raise UserError("Keep at least one admin")
            u["role"] = role
        if password is not None:
            self._password_ok(password)
            u.update({k: v for k, v in _record(username, password, u["role"], actor != username).items()
                      if k in ("salt", "hash", "must_change")})
        self.store.put("user", u)
        return u

    def change_own(self, username, current, new, banned=()):
        u = self.check(username, current)
        if not u:
            raise UserError("Current password is incorrect")
        self._password_ok(new)
        if new in banned or hmac.compare_digest(oldweb._hash(new, u["salt"]), u["hash"]):
            raise UserError("Choose a password different from the current one")
        return self.update(username, password=new, actor=username)

    def delete(self, username, actor):
        u = self.get(username)
        if not u:
            raise UserError(f"No user {username}")
        if username == actor:
            raise UserError("You cannot delete your own account")
        if u["role"] == "admin" and self._admins() == [username]:
            raise UserError("Keep at least one admin")
        self.store.delete("user", username)

    def _admins(self):
        return [u["username"] for u in self.all() if u["role"] == "admin"]

    @staticmethod
    def _password_ok(p):
        if not isinstance(p, str) or len(p) < MIN_PASSWORD:
            raise UserError(f"Password must be at least {MIN_PASSWORD} characters")


def ldap_login(settings, username, password):
    """Stub. LDAP is not implemented; see docs/security.md. Returns None (no LDAP user)."""
    return None
