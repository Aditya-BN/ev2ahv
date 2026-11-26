"""D5: dirty-bitmap tracker logic and the changed-block pass (no QEMU needed; tools/cbt_bench.py covers QEMU)."""
import os, sys, tempfile, unittest
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from ev2ahv import engine  # noqa: E402
from ev2ahv.cbt import BitmapLost, QemuBitmap, parse_map, ranges_to_chunks  # noqa: E402
from ev2ahv.readers import LocalReader  # noqa: E402
from ev2ahv.state import State  # noqa: E402
from ev2ahv.transport import BlockEndpoint, LocalTransport  # noqa: E402

C = 64 * 1024


class FakeQmp:
    def __init__(self):
        self.bitmaps, self.calls = [], []

    def __call__(self, execute, arguments=None):
        self.calls.append((execute, arguments))
        if execute == "query-block":
            return []
        if execute == "query-named-block-nodes":
            return [{"node-name": "disk0", "dirty-bitmaps": [{"name": n} for n in self.bitmaps]}]
        if execute == "block-dirty-bitmap-add":
            self.bitmaps.append(arguments["name"])
        if execute == "block-dirty-bitmap-remove":
            self.bitmaps.remove(arguments["name"])
        if execute == "transaction":
            for a in arguments["actions"]:
                if a["type"] == "block-dirty-bitmap-add":
                    self.bitmaps.append(a["data"]["name"])
        return {}


class TrackerTest(unittest.TestCase):
    def test_ranges_to_chunks(self):
        self.assertEqual(ranges_to_chunks([(0, 1), (C - 1, 2), (5 * C, C), (9 * C + 10, 0)], C), [0, 1, 5])

    def test_parse_map_takes_data_false_as_dirty(self):
        m = [{"start": 0, "length": 100, "data": True}, {"start": 100, "length": 50, "data": False}]
        self.assertEqual(parse_map(m), [(100, 50)])

    def test_rotate_names_and_lost_bitmap(self):
        q = FakeQmp()
        b = QemuBitmap(q, "disk0", "/nbd.sock")
        q.bitmaps.append("ev2ahv-cbt-7")        # stale one from an earlier run
        self.assertEqual(b.start(), "ev2ahv-cbt-0")
        self.assertEqual(q.bitmaps, ["ev2ahv-cbt-0"])
        self.assertEqual(b.rotate(), "ev2ahv-cbt-0")
        self.assertEqual(b.rotate(), "ev2ahv-cbt-1")
        tx = [a for e, a in q.calls if e == "transaction"][-1]["actions"]
        self.assertEqual([a["type"] for a in tx], ["block-dirty-bitmap-add", "block-dirty-bitmap-disable"])
        q.bitmaps.clear()                        # guest restarted: bitmaps are gone
        with self.assertRaises(BitmapLost):
            b.rotate()

    def test_dirty_removes_its_export_even_on_failure(self):
        q = FakeQmp()

        def boom(argv):
            raise RuntimeError("qemu-img failed")
        with self.assertRaises(RuntimeError):
            QemuBitmap(q, "disk0", "/nbd.sock", map_cmd=boom).dirty("ev2ahv-cbt-0")
        self.assertEqual(q.calls[-1][0], "block-export-del")


class SyncDirtyTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.src, self.dst = os.path.join(self.tmp.name, "s"), os.path.join(self.tmp.name, "d")
        with open(self.src, "wb") as f:
            f.write(os.urandom(32 * C))
        with open(self.dst, "wb") as f:
            f.truncate(32 * C)
        self.state = State(os.path.join(self.tmp.name, "st.db"))
        self.r = LocalReader(self.src, chunk=C)
        self.w = BlockEndpoint(LocalTransport(), self.dst, chunk=C, writable=True)

    def tearDown(self):
        self.r.close(); self.w.close(); self.state.close(); self.tmp.cleanup()

    def poke(self, idx):
        with open(self.src, "r+b") as f:
            f.seek(idx * C)
            f.write(os.urandom(C))

    def same(self):
        self.w.close()
        with open(self.src, "rb") as a, open(self.dst, "rb") as b:
            return a.read() == b.read()

    def test_needs_a_full_pass_first(self):
        with self.assertRaises(RuntimeError):
            engine.sync_dirty(self.r, self.w, self.state, "d", "blake2b", [1])

    def test_reads_only_dirty_chunks_and_converges(self):
        engine.sync(self.r, self.w, self.state, "d", "blake2b")
        for i in (3, 17, 30):
            self.poke(i)
        st = engine.sync_dirty(self.r, self.w, self.state, "d", "blake2b", [3, 17, 30, 99, -1])
        self.assertEqual((st["read_chunks"], st["sent"]), (3, 3))
        self.assertTrue(self.same())
        full = engine.sync(BlockEndpoint(LocalTransport(), self.src, chunk=C), BlockEndpoint(
            LocalTransport(), self.dst, chunk=C, writable=True), self.state, "d", "blake2b")
        self.assertEqual(full["sent"], 0)        # the records the dirty pass wrote are right

    def test_dirty_but_unchanged_chunk_is_not_sent(self):
        engine.sync(self.r, self.w, self.state, "d", "blake2b")
        st = engine.sync_dirty(self.r, self.w, self.state, "d", "blake2b", [5])
        self.assertEqual((st["read_chunks"], st["sent"], st["skipped"]), (1, 0, 1))


if __name__ == "__main__":
    unittest.main()
