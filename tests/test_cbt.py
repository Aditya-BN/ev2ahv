"""D5: dirty-bitmap tracker logic and the changed-block pass (no QEMU needed; tools/cbt_bench.py covers QEMU)."""
import os, sys, tempfile, unittest, unittest.mock
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from ev2ahv import engine  # noqa: E402
from ev2ahv.cbt import BitmapLost, QemuBitmap, find_node, parse_map, ranges_to_chunks  # noqa: E402
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


class EverrunShapeTest(unittest.TestCase):
    def test_nbd_server_started_and_stopped_around_the_export(self):
        q = FakeQmp()
        QemuBitmap(q, "disk0", "/nbd.sock", map_cmd=lambda argv: "[]", start_server=True).dirty("ev2ahv-cbt-0")
        names = [e for e, _ in q.calls]
        self.assertEqual(names, ["nbd-server-start", "block-export-add", "block-export-del", "nbd-server-stop"])
        self.assertEqual(q.calls[0][1], {"addr": {"type": "unix", "data": {"path": "/nbd.sock"}}})

    def test_find_node_takes_the_format_node_not_the_file_node(self):
        nodes = [{"node-name": "libvirt-4-storage", "drv": "file", "file": "/d/a"},
                 {"node-name": "libvirt-4-format", "drv": "raw", "file": "/d/a"}]
        self.assertEqual(find_node(lambda *a: nodes, "/d/a"), "libvirt-4-format")
        with self.assertRaises(BitmapLost):
            find_node(lambda *a: nodes, "/d/other")


class WorkflowCbtTest(unittest.TestCase):
    """Migration._sync_disk: when the dirty pass is used and when it falls back to a full pass."""
    def setUp(self):
        from ev2ahv import workflow
        self.tmp = tempfile.TemporaryDirectory()
        self.src, self.dst = os.path.join(self.tmp.name, "s"), os.path.join(self.tmp.name, "d")
        with open(self.src, "wb") as f:
            f.write(os.urandom(32 * C))
        with open(self.dst, "wb") as f:
            f.truncate(32 * C)
        m = workflow.Migration.__new__(workflow.Migration)
        m.state, m.chunk = State(os.path.join(self.tmp.name, "st.db")), C
        m.p = {"name": "t", "sync": {"cbt": "auto"}}
        self.m, self.disk = m, {"name": "d", "path": self.src}
        self.r = LocalReader(self.src, chunk=C)
        self.w = BlockEndpoint(LocalTransport(), self.dst, chunk=C, writable=True)
        self.kw = {"batch": 8, "progress": None}
        self.bm = mock_bm = unittest.mock.MagicMock()
        mock_bm.rotate.return_value = "f"
        mock_bm.dirty.return_value = []
        m._cbt_for = lambda disk: self.bm

    def tearDown(self):
        self.r.close(); self.w.close(); self.m.state.close(); self.tmp.cleanup()

    def run_pass(self, **kw):
        return self.m._sync_disk(self.disk, self.r, self.w, "blake2b", self.kw, kw.get("verify", False), False)

    def test_first_pass_is_full_and_starts_tracking_before_it(self):
        st = self.run_pass()
        self.assertEqual(st["sent"], 32)
        self.bm.start.assert_called_once()
        self.assertEqual(self.m.state.get("cbt:d"), "tracking")

    def test_resync_uses_the_dirty_pass_then_stays_tracking(self):
        self.run_pass()
        with open(self.src, "r+b") as f:
            f.seek(4 * C); f.write(os.urandom(C))
        self.bm.dirty.return_value = [(4 * C, C)]
        st = self.run_pass()
        self.assertTrue(st["cbt"])
        self.assertEqual((st["read_chunks"], st["sent"]), (1, 1))
        self.bm.drop.assert_called_once_with("f")
        self.assertEqual(self.m.state.get("cbt:d"), "tracking")

    def test_lost_bitmap_falls_back_to_a_full_pass_and_restarts_tracking(self):
        self.run_pass()
        self.bm.rotate.side_effect = BitmapLost("gone")
        st = self.run_pass()
        self.assertNotIn("cbt", st)
        self.assertEqual(st["scanned"], 32)
        self.assertEqual(self.bm.start.call_count, 2)
        self.assertEqual(self.m.state.get("cbt:d"), "tracking")

    def test_verify_pass_is_always_full_and_drops_tracking(self):
        self.run_pass()
        st = self.run_pass(verify=True)
        self.assertEqual(st["scanned"], 32)
        self.assertEqual(self.m.state.get("cbt:d"), "off")

    def test_tracker_unavailable_means_plain_full_pass(self):
        def boom(disk):
            raise BitmapLost("no node")
        self.m._cbt_for = boom
        self.assertEqual(self.run_pass()["sent"], 32)
        self.assertIsNone(self.m.state.get("cbt:d"))


class PlanCbtTest(unittest.TestCase):
    def test_default_off_and_auto_needs_a_domain(self):
        from ev2ahv import plan
        base = plan.load(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "plans", "win22.yaml"))
        self.assertEqual(base["sync"]["cbt"], "off")
        bad = dict(base, sync=dict(base["sync"], cbt="auto"))
        with self.assertRaises(plan.PlanError):
            plan.validate(bad)
        ok = dict(bad, source=dict(base["source"], qemu_domain="ev2ahv-src-win22"))
        self.assertEqual(plan.validate(ok)["sync"]["cbt"], "auto")


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
