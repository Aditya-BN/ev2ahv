"""Persistent state for the server: JSON documents in SQLite, so jobs and plans survive restarts."""
import base64, hashlib, json, os, sqlite3, threading, time, uuid

_default = "server-mock.db" if os.environ.get("EV2AHV_SERVER_MODE", "mock") == "mock" else "server.db"
DB = os.path.expanduser(os.environ.get("EV2AHV_SERVER_DB", "~/.local/share/ev2ahv/" + _default))
_lock = threading.RLock()
ETC = os.path.expanduser(os.environ.get("EV2AHV_ETC", "~/.config/ev2ahv"))


def _fernet():
    """Secrets are encrypted at rest with a key derived from the per-instance key (generated at first boot on the
    appliance; created here on first use elsewhere)."""
    from cryptography.fernet import Fernet
    path = os.path.join(ETC, "instance.key")
    if not os.path.exists(path):
        os.makedirs(ETC, mode=0o700, exist_ok=True)
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w") as fh:
            fh.write(base64.b64encode(os.urandom(32)).decode())
    with open(path) as fh:
        raw = fh.read().strip().encode()
    return Fernet(base64.urlsafe_b64encode(hashlib.sha256(b"ev2ahv-secrets\0" + raw).digest()))


def app_secret():
    with open(os.path.join(ETC, "instance.key")) as fh:
        return hashlib.sha256(b"ev2ahv-app\0" + fh.read().strip().encode()).hexdigest()


# Schema migrations (D7). The database records its schema version in PRAGMA user_version. A new release that changes
# the schema appends a function here; it runs once, in order, at startup. ev2ahv-upgrade backs up the state directory
# before the new version starts, so a failed migration is rolled back with the rest of the upgrade.
#   1: 0.3.0 baseline (docs, secrets, events, jobs). UI users became docs of kind "user"; no table change.
MIGRATIONS = [lambda db: None]


def migrate(db):
    have = db.execute("PRAGMA user_version").fetchone()[0]
    if have > len(MIGRATIONS):
        raise RuntimeError(f"database schema {have} is newer than this version knows ({len(MIGRATIONS)}). "
                           "Roll back with ev2ahv-upgrade rollback, or upgrade the app")
    for n in range(have, len(MIGRATIONS)):
        with db:
            MIGRATIONS[n](db)
            db.execute(f"PRAGMA user_version={n + 1}")
    return len(MIGRATIONS)


class Store:
    def __init__(self, path=DB):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        self.db = sqlite3.connect(path, check_same_thread=False, timeout=30)
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.executescript("""
          CREATE TABLE IF NOT EXISTS docs(kind TEXT, id TEXT, body TEXT, updated REAL, PRIMARY KEY(kind, id));
          CREATE TABLE IF NOT EXISTS secrets(owner TEXT PRIMARY KEY, body TEXT);
          CREATE TABLE IF NOT EXISTS events(id INTEGER PRIMARY KEY AUTOINCREMENT, time REAL, severity TEXT,
                                            plan TEXT, vm TEXT, message TEXT);
          CREATE TABLE IF NOT EXISTS jobs(id TEXT PRIMARY KEY, plan TEXT, vm TEXT, action TEXT, state TEXT,
                                          started REAL, ended REAL, detail TEXT);""")
        migrate(self.db)
        os.chmod(path, 0o600)
        self.f = _fernet()

    @staticmethod
    def new_id():
        return str(uuid.uuid4())

    def put(self, kind, doc):
        with _lock, self.db:
            self.db.execute("INSERT OR REPLACE INTO docs VALUES(?,?,?,?)", (kind, doc["id"], json.dumps(doc), time.time()))
        return doc

    def get(self, kind, id_):
        row = self.db.execute("SELECT body FROM docs WHERE kind=? AND id=?", (kind, id_)).fetchone()
        return json.loads(row[0]) if row else None

    def all(self, kind):
        return [json.loads(r[0]) for r in self.db.execute("SELECT body FROM docs WHERE kind=? ORDER BY updated", (kind,))]

    def delete(self, kind, id_):
        with _lock, self.db:
            self.db.execute("DELETE FROM docs WHERE kind=? AND id=?", (kind, id_))
            self.db.execute("DELETE FROM secrets WHERE owner LIKE ?", (f"{kind}:{id_}%",))

    def set_secret(self, owner, value):
        """Secrets live in their own table and are never part of an API response."""
        with _lock, self.db:
            if value is None:
                return
            self.db.execute("INSERT OR REPLACE INTO secrets VALUES(?,?)", (owner, "v1:" + self.f.encrypt(value.encode()).decode()))

    def secret(self, owner):
        row = self.db.execute("SELECT body FROM secrets WHERE owner=?", (owner,)).fetchone()
        if not row:
            return None
        if row[0].startswith("v1:"):
            return self.f.decrypt(row[0][3:].encode()).decode()
        self.set_secret(owner, row[0])   # written before encryption existed: re-store encrypted
        return row[0]

    def event(self, severity, message, plan=None, vm=None):
        with _lock, self.db:
            self.db.execute("INSERT INTO events(time,severity,plan,vm,message) VALUES(?,?,?,?,?)",
                            (time.time(), severity, plan, vm, message))

    def events(self, limit=500):
        rows = self.db.execute("SELECT time,severity,plan,vm,message FROM events ORDER BY id DESC LIMIT ?", (limit,))
        return [dict(zip(("time", "severity", "plan", "vm", "message"), r)) for r in rows]
