"""SQLite state: one row per transferred chunk, so any step can resume after a crash."""
import functools, json, os, sqlite3, threading, time

SCHEMA = """
CREATE TABLE IF NOT EXISTS disks(
  key TEXT PRIMARY KEY, size INTEGER NOT NULL, chunk INTEGER NOT NULL, algo TEXT NOT NULL,
  scan_pos INTEGER NOT NULL DEFAULT 0, pass_active INTEGER NOT NULL DEFAULT 0,
  passes INTEGER NOT NULL DEFAULT 0, meta TEXT NOT NULL DEFAULT '{}');
CREATE TABLE IF NOT EXISTS chunks(
  disk TEXT NOT NULL, idx INTEGER NOT NULL, digest TEXT NOT NULL, PRIMARY KEY(disk, idx)) WITHOUT ROWID;
CREATE TABLE IF NOT EXISTS kv(key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS events(
  id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL NOT NULL, kind TEXT NOT NULL, detail TEXT NOT NULL);
"""


def _locked(fn):
    @functools.wraps(fn)
    def wrapper(self, *a, **kw):
        with self._lock:
            return fn(self, *a, **kw)
    return wrapper


class State:
    """One SQLite connection shared by the threads of a step (parallel disks, the write pipeline). Every method takes
    the lock, so a transaction of one thread never interleaves with another's."""

    def __init__(self, path):
        self._lock = threading.RLock()
        if path != ":memory:":
            os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        self.db = sqlite3.connect(path, timeout=30, check_same_thread=False)
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA synchronous=FULL")
        self.db.executescript(SCHEMA)

    @_locked
    def close(self):
        self.db.close()

    @_locked
    def ensure_disk(self, key, size, chunk, algo):
        row = self.db.execute("SELECT size, chunk, algo FROM disks WHERE key=?", (key,)).fetchone()
        if row is None:
            with self.db:
                self.db.execute("INSERT INTO disks(key,size,chunk,algo) VALUES(?,?,?,?)", (key, size, chunk, algo))
        elif tuple(row) != (size, chunk, algo):
            raise RuntimeError(f"disk {key}: state has size/chunk/algo {tuple(row)}, "
                               f"now {(size, chunk, algo)}. Refusing to mix them")

    @_locked
    def disk(self, key):
        cur = self.db.execute("SELECT * FROM disks WHERE key=?", (key,))
        row = cur.fetchone()
        return dict(zip([c[0] for c in cur.description], row)) if row else None

    @_locked
    def begin_pass(self, key):
        """Returns the chunk index to start from. An interrupted pass continues where it stopped."""
        d = self.disk(key)
        if d["pass_active"]:
            return d["scan_pos"]
        with self.db:
            self.db.execute("UPDATE disks SET pass_active=1, scan_pos=0 WHERE key=?", (key,))
        return 0

    @_locked
    def commit(self, key, updates, scan_pos):
        """Record acknowledged chunks and the scan position in one transaction."""
        with self.db:
            self.db.executemany("INSERT OR REPLACE INTO chunks(disk,idx,digest) VALUES(?,?,?)",
                                [(key, i, dg) for i, dg in updates])
            self.db.execute("UPDATE disks SET scan_pos=? WHERE key=?", (scan_pos, key))

    @_locked
    def end_pass(self, key):
        with self.db:
            self.db.execute("UPDATE disks SET pass_active=0, scan_pos=0, passes=passes+1 WHERE key=?", (key,))

    @_locked
    def digests(self, key, start=0):
        return dict(self.db.execute("SELECT idx, digest FROM chunks WHERE disk=? AND idx>=?", (key, start)))

    @_locked
    def set(self, key, value):
        with self.db:
            self.db.execute("INSERT OR REPLACE INTO kv VALUES(?,?)", (key, json.dumps(value)))

    @_locked
    def get(self, key, default=None):
        row = self.db.execute("SELECT value FROM kv WHERE key=?", (key,)).fetchone()
        return json.loads(row[0]) if row else default

    @_locked
    def event(self, kind, **detail):
        with self.db:
            self.db.execute("INSERT INTO events(ts,kind,detail) VALUES(?,?,?)", (time.time(), kind, json.dumps(detail)))

    @_locked
    def events(self, limit=200):
        rows = self.db.execute("SELECT ts,kind,detail FROM events ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
        return [{"ts": t, "kind": k, "detail": json.loads(d)} for t, k, d in reversed(rows)]
