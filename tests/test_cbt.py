"""D5: dirty-bitmap tracker logic and the changed-block pass (no QEMU needed; tools/cbt_bench.py covers QEMU)."""
import os, sys, tempfile, unittest, unittest.mock
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from ev2ahv import engine  # noqa: E402
from ev2ahv import cbt  # noqa: E402
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


class HaltAndCaptureTest(unittest.TestCase):
    """cbt.halt_and_capture: halt from inside, wait for silence, pause over QMP, then read the bitmaps."""
    def setUp(self):
        self.t, self.calls, self.status, self.writes = 0.0, [], "running", [10]

    def clock(self):
        return self.t

    def sleep(self, s):
        self.t += s

    def qmp(self, execute, arguments=None):
        self.calls.append(execute)
        if execute == "query-status":
            return {"status": self.status}
        if execute == "query-blockstats":
            return [{"stats": {"wr_operations": self.writes[0], "flush_operations": 1}}]
        if execute == "stop":
            self.status = "paused"
        return {}

    def bm(self, ranges=((C, C),), has=True, fail=None):
        b = unittest.mock.MagicMock()
        b.has_bitmap.return_value = has
        b.rotate.return_value = "f"
        b.dirty.side_effect = fail or (lambda f: list(ranges))
        return b

    def run_capture(self, trackers, halt=None, up=lambda: False, **kw):
        return cbt.halt_and_capture(self.qmp, trackers, halt or (lambda: self.calls.append("halt")), up, C,
                                    sleep=self.sleep, now=self.clock, **kw)

    def test_halt_wait_pause_then_rotate_in_that_order(self):
        out = self.run_capture({"a": self.bm(), "b": self.bm(((0, 3 * C),))})
        self.assertEqual(out, {"a": [1], "b": [0, 1, 2]})
        self.assertLess(self.calls.index("halt"), self.calls.index("stop"))
        self.assertGreaterEqual(self.t, 5.0)        # waited for the quiet period

    def test_writes_or_a_reachable_guest_restart_the_quiet_period(self):
        ups = iter([True, True, False] + [False] * 100)
        ticks = [0]

        def writes_grow(execute, arguments=None):
            r = self.qmp(execute, arguments)
            if execute == "query-blockstats":
                ticks[0] += 1
                if ticks[0] < 6:
                    self.writes[0] += 1
            return r
        out = cbt.halt_and_capture(writes_grow, {"a": self.bm()}, lambda: None, lambda: next(ups), C,
                                   sleep=self.sleep, now=self.clock)
        self.assertEqual(out, {"a": [1]})
        self.assertGreaterEqual(self.t, 8.0)        # 6 busy polls at 0.5 s plus the quiet period

    def test_nothing_is_halted_when_the_guest_is_not_running_or_a_bitmap_is_missing(self):
        halted = []
        self.status = "paused"
        with self.assertRaises(cbt.NotReady):
            self.run_capture({"a": self.bm()}, halt=lambda: halted.append(1))
        self.status = "running"
        with self.assertRaises(cbt.NotReady):
            self.run_capture({"a": self.bm(), "b": self.bm(has=False)}, halt=lambda: halted.append(1))
        self.assertEqual(halted, [])
        self.assertNotIn("stop", self.calls)

    def test_a_failed_halt_command_is_not_ready(self):
        def boom():
            raise RuntimeError("ssh down")
        with self.assertRaises(cbt.NotReady):
            self.run_capture({"a": self.bm()}, halt=boom)
        self.assertNotIn("stop", self.calls)

    def test_timeout_and_bitmap_errors_after_the_halt_are_capture_failures(self):
        with self.assertRaises(cbt.CaptureFailed):
            self.run_capture({"a": self.bm()}, up=lambda: True, timeout=30)
        self.assertNotIn("stop", self.calls)         # never pauses a guest that has not halted
        with self.assertRaises(cbt.CaptureFailed):
            self.run_capture({"a": self.bm(fail=BitmapLost("gone"))})

    def test_a_write_during_the_capture_voids_the_dirty_set(self):
        """everRun resumes a paused guest within a second, so the pause alone proves nothing."""
        def resumed_and_wrote(execute, arguments=None):
            r = self.qmp(execute, arguments)
            if execute == "stop":
                self.writes[0] += 1                 # a write lands after the pause
            return r
        with self.assertRaises(cbt.CaptureFailed) as e:
            cbt.halt_and_capture(resumed_and_wrote, {"a": self.bm()}, lambda: None, lambda: False, C,
                                 sleep=self.sleep, now=self.clock)
        self.assertIn("wrote", str(e.exception))


class FinalSyncDirtyTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.src, self.dst = os.path.join(self.tmp.name, "s"), os.path.join(self.tmp.name, "d")
        with open(self.src, "wb") as f:
            f.write(os.urandom(512 * C))
        with open(self.dst, "wb") as f:
            f.truncate(512 * C)
        self.state = State(os.path.join(self.tmp.name, "st.db"))
        self.r = LocalReader(self.src, chunk=C)
        self.w = BlockEndpoint(LocalTransport(), self.dst, chunk=C, writable=True)
        engine.sync(self.r, self.w, self.state, "d", "blake2b")

    def tearDown(self):
        self.r.close(); self.w.close(); self.state.close(); self.tmp.cleanup()

    def poke(self, idx):
        with open(self.src, "r+b") as f:
            f.seek(idx * C); f.write(os.urandom(C))

    def same(self):
        self.w.close()
        with open(self.src, "rb") as a, open(self.dst, "rb") as b:
            return a.read() == b.read()

    def test_full_verify_reads_dirty_then_hashes_both_sides(self):
        for i in (7, 300):
            self.poke(i)
        rounds, st = engine.final_sync_dirty(self.r, self.w, self.state, "d", "blake2b", [7, 300], verify="full")
        self.assertEqual((rounds, st["final_mode"], st["dirty_chunks"]), (1, "dirty+full", 2))
        self.assertTrue(self.same())

    def test_full_verify_repairs_a_chunk_the_bitmap_missed(self):
        self.poke(7); self.poke(9)                       # 9 is not in the dirty list
        rounds, st = engine.final_sync_dirty(self.r, self.w, self.state, "d", "blake2b", [7], verify="full")
        self.assertTrue(self.same())
        self.assertEqual(rounds, 2)                      # the first verified pass sent chunk 9

    def test_none_trusts_the_dirty_pass(self):
        self.poke(7); self.poke(9)
        rounds, st = engine.final_sync_dirty(self.r, self.w, self.state, "d", "blake2b", [7], verify="none")
        self.assertEqual((rounds, st["final_mode"], st["read_chunks"]), (0, "dirty+none", 1))
        self.assertFalse(self.same())                    # the missed chunk is only found by full or sample

    def test_sample_passes_when_clean_and_escalates_to_full_on_a_difference(self):
        self.poke(7)
        rounds, st = engine.final_sync_dirty(self.r, self.w, self.state, "d", "blake2b", [7], verify="sample")
        self.assertEqual((rounds, st["final_mode"]), (0, "dirty+sample"))
        self.assertTrue(self.same())
        # sample size forced to the whole disk, so a missed write is always seen and the full passes repair it
        self.poke(9)
        with unittest.mock.patch.object(engine, "SAMPLE_MIN", 512):
            rounds, st = engine.final_sync_dirty(self.r, self.w, self.state, "d", "blake2b", [7], verify="sample")
        self.assertEqual(st["final_mode"], "dirty+sample->full")
        self.assertGreaterEqual(rounds, 1)
        self.assertTrue(self.same())

    def test_bad_verify_value_is_refused(self):
        with self.assertRaises(ValueError):
            engine.final_sync_dirty(self.r, self.w, self.state, "d", "blake2b", [], verify="maybe")


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


class WorkflowFinalTest(unittest.TestCase):
    """Migration: when the final dirty pass is possible, and how _sync_disk(final=True) uses or drops its result."""
    def setUp(self):
        from ev2ahv import workflow
        self.tmp = tempfile.TemporaryDirectory()
        self.src, self.dst = os.path.join(self.tmp.name, "s"), os.path.join(self.tmp.name, "d")
        with open(self.src, "wb") as f:
            f.write(os.urandom(64 * C))
        with open(self.dst, "wb") as f:
            f.truncate(64 * C)
        m = workflow.Migration.__new__(workflow.Migration)
        m.state, m.chunk = State(os.path.join(self.tmp.name, "st.db")), C
        m.p = {"name": "t", "sync": {"cbt": "auto", "final_cbt": "auto", "final_verify": "full"},
               "source": {"type": "direct", "qemu_domain": "dom", "power": {"poweroff": "x"}},
               "guest": {"os": "linux", "address": "10.0.0.5", "prep": "auto"}, "disks": [{"name": "d", "path": self.src}]}
        self.m, self.disk = m, m.p["disks"][0]
        self.r = LocalReader(self.src, chunk=C)
        self.w = BlockEndpoint(LocalTransport(), self.dst, chunk=C, writable=True)
        engine.sync(self.r, self.w, m.state, "d", "blake2b")
        m.state.set("cbt:d", "tracking")

    def tearDown(self):
        self.r.close(); self.w.close(); self.m.state.close(); self.tmp.cleanup()

    def final(self):
        return self.m._sync_disk(self.disk, self.r, self.w, "blake2b", {"batch": 8, "progress": None}, False, True)

    def poke(self, idx):
        with open(self.src, "r+b") as f:
            f.seek(idx * C); f.write(os.urandom(C))

    def test_possible_only_when_everything_lines_up(self):
        self.assertTrue(self.m.final_dirty_possible())
        self.assertFalse(self.m.final_dirty_possible(assume_stopped=True))
        for mutate in (lambda p: p["sync"].update(cbt="off"), lambda p: p["sync"].update(final_cbt="off"),
                       lambda p: p["guest"].update(os="windows"), lambda p: p["guest"].pop("address"),
                       lambda p: p["source"]["power"].pop("poweroff"), lambda p: p["source"].update(type="local")):
            saved = __import__("copy").deepcopy(self.m.p)
            mutate(self.m.p)
            self.assertFalse(self.m.final_dirty_possible())
            self.m.p = saved
        self.m.state.set("cbt:d", "off")          # tracking had a gap
        self.assertFalse(self.m.final_dirty_possible())

    def test_dirty_list_gives_a_dirty_final_pass(self):
        self.poke(5)
        self.m.state.set("final_dirty", {"d": [5]})
        st = self.final()
        self.assertEqual((st["final_mode"], st["dirty_chunks"], st["verified_rounds"]), ("dirty+full", 1, 1))

    def test_no_list_or_a_failing_dirty_pass_means_the_full_final_pass(self):
        self.poke(5)
        st = self.final()                                   # nothing captured: current behaviour
        self.assertEqual(st["final_mode"], "full")
        self.poke(6)
        self.m.state.set("final_dirty", {"d": [6]})
        self.m.state.begin_pass("d")                        # a half-done pass: sync_dirty refuses
        st = self.final()
        self.assertEqual(st["final_mode"], "full")
        with open(self.src, "rb") as a, open(self.dst, "rb") as b:
            self.assertEqual(a.read(), b.read())

    def test_final_verify_setting_is_used(self):
        self.poke(5); self.poke(6)
        self.m.p["sync"]["final_verify"] = "none"
        self.m.state.set("final_dirty", {"d": [5]})
        st = self.final()
        self.assertEqual((st["final_mode"], st["verified_rounds"]), ("dirty+none", 0))

    def test_halt_and_capture_records_the_list_or_stays_full(self):
        m = self.m
        m.source_shell = lambda: None
        m._cbt_for = lambda d: unittest.mock.MagicMock()
        m.guest = lambda: unittest.mock.MagicMock()
        with unittest.mock.patch.object(cbt, "halt_and_capture", return_value={"d": [1, 2]}):
            self.assertTrue(m.halt_and_capture())
        self.assertEqual((m.state.get("final_mode"), m.state.get("final_dirty")), ("dirty", {"d": [1, 2]}))
        with unittest.mock.patch.object(cbt, "halt_and_capture", side_effect=cbt.NotReady("x")):
            self.assertFalse(m.halt_and_capture())      # guest untouched: normal stop
        self.assertEqual((m.state.get("final_mode"), m.state.get("final_dirty")), ("full", {}))
        with unittest.mock.patch.object(cbt, "halt_and_capture", side_effect=cbt.CaptureFailed("x")):
            self.assertTrue(m.halt_and_capture())       # guest halted: power it off, full pass
        self.assertEqual((m.state.get("final_mode"), m.state.get("final_dirty")), ("full", {}))
        m._cbt_for = lambda d: None
        self.assertFalse(m.halt_and_capture())


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
        self.assertEqual((ok["sync"]["final_verify"], ok["sync"]["final_cbt"]), ("full", "auto"))
        for key, bad_value in (("final_verify", "maybe"), ("final_cbt", "on")):
            with self.assertRaises(plan.PlanError):
                plan.validate(dict(ok, sync=dict(ok["sync"], **{key: bad_value})))


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
