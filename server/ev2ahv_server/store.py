"""Persistent state for the server: JSON documents in SQLite, so jobs and plans survive restarts."""
import json, os, sqlite3, threading, time, uuid

DB = os.path.expanduser(os.environ.get("EV2AHV_SERVER_DB", "~/.local/share/ev2ahv/server.db"))
_lock = threading.RLock()


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
        os.chmod(path, 0o600)

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
            self.db.execute("INSERT OR REPLACE INTO secrets VALUES(?,?)", (owner, value))

    def secret(self, owner):
        row = self.db.execute("SELECT body FROM secrets WHERE owner=?", (owner,)).fetchone()
        return row[0] if row else None

    def event(self, severity, message, plan=None, vm=None):
        with _lock, self.db:
            self.db.execute("INSERT INTO events(time,severity,plan,vm,message) VALUES(?,?,?,?,?)",
                            (time.time(), severity, plan, vm, message))

    def events(self, limit=500):
        rows = self.db.execute("SELECT time,severity,plan,vm,message FROM events ORDER BY id DESC LIMIT ?", (limit,))
        return [dict(zip(("time", "severity", "plan", "vm", "message"), r)) for r in rows]
