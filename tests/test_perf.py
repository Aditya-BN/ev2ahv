"""D31/D32: parallel disks, pipelined writes with batched fsync (crash safety), wire compression."""
import os, subprocess, sys, tempfile, threading, time, unittest
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from ev2ahv import engine, plan as planlib  # noqa: E402
from ev2ahv.helper import ev2ahv_helper as helper  # noqa: E402
from ev2ahv.readers import LocalReader  # noqa: E402
from ev2ahv.state import State  # noqa: E402
from ev2ahv.transport import BlockEndpoint, LocalTransport  # noqa: E402
from ev2ahv.workflow import Migration  # noqa: E402

C = 64 * 1024
N = 48


def make_file(path, chunks=N, seed_zero=3):
    with open(path, "wb") as f:
        for i in range(chunks):
            f.write(bytes(C) if i % seed_zero == 0 else os.urandom(C))


class SpyTarget:
    """Wraps a real BlockEndpoint and records the order of writes, fsyncs and (via the state) commits."""
    def __init__(self, inner, fail_flush_at=None):
        self.inner, self.fail_flush_at = inner, fail_flush_at
        self.written, self.flushed, self.flushes = set(), set(), 0

    def __getattr__(self, name):
        return getattr(self.inner, name)

    def write_batch(self, items, flush=True):
        items = list(items)
        self.inner.write_batch(items, flush=False)
        self.written.update(i for i, _ in items)
        if flush:
            self.flush()

    def flush(self):
        self.flushes += 1
        if self.fail_flush_at is not None and self.flushes >= self.fail_flush_at:
            raise RuntimeError("fsync failed")
        self.inner.flush()
        self.flushed |= self.written


class SpyState(State):
    """Fails the test if a digest is committed for a chunk that was written but not yet fsynced."""
    target = None
    violations = None

    def commit(self, key, updates, scan_pos):
        if self.target is not None:
            for idx, _ in updates:
                if idx in self.target.written and idx not in self.target.flushed:
                    self.violations.append(idx)
        super().commit(key, updates, scan_pos)


class Rig(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.src = os.path.join(self.tmp.name, "src")
        self.dst = os.path.join(self.tmp.name, "dst")
        make_file(self.src)
        with open(self.dst, "wb") as f:
            f.truncate(N * C)
        self.state = SpyState(os.path.join(self.tmp.name, "s.db"))
        self.state.violations = []
        self.r = LocalReader(self.src, chunk=C)
        self.w = BlockEndpoint(LocalTransport(), self.dst, chunk=C, writable=True)
        self.spy = SpyTarget(self.w)
        self.state.target = self.spy

    def tearDown(self):
        self.r.close(); self.w.close(); self.state.close(); self.tmp.cleanup()

    def same(self):
        self.w.close()
        with open(self.src, "rb") as a, open(self.dst, "rb") as b:
            return a.read() == b.read()

    def sync(self, target=None, **kw):
        kw.setdefault("batch", 4)
        return engine.sync(self.r, target or self.spy, self.state, "d", "blake2b", **kw)


class PipelineCrashSafety(Rig):
    def test_no_digest_before_fsync(self):
        for threaded in (False, True):
            self.setUp() if threaded else None
            self.sync(fsync_bytes=8 * C, threaded=threaded)
            self.assertEqual(self.state.violations, [], f"threaded={threaded}")
            self.assertTrue(self.same())

    def test_fsync_is_batched(self):
        s = self.sync(fsync_bytes=10 ** 9, threaded=False)    # only the final drain flushes
        self.assertEqual(self.spy.flushes, 1)
        self.assertEqual(s["fsyncs"], 1)
        s2 = self.sync(fsync_bytes=0, threaded=False)         # nothing changed, nothing to flush
        self.assertEqual(s2["sent"], 0)

    def test_fsync_every_batch_when_zero(self):
        s = self.sync(fsync_bytes=0, threaded=False)
        self.assertGreater(s["fsyncs"], 4)
        self.assertEqual(self.state.violations, [])

    def test_failed_fsync_commits_nothing_for_unflushed_chunks(self):
        bad = SpyTarget(self.w, fail_flush_at=1)
        self.state.target = bad
        for threaded in (False, True):
            self.state.db.execute("DELETE FROM chunks"); self.state.db.commit()
            bad.flushes = 0
            with self.assertRaises(RuntimeError):
                self.sync(bad, fsync_bytes=10 ** 9, threaded=threaded)
            self.assertEqual(self.state.digests("d"), {}, "digests must not outlive a failed fsync")
        # the next pass starts clean, resends everything and converges
        self.state.target = self.spy
        self.sync(fsync_bytes=4 * C)
        self.assertTrue(self.same())
        self.assertEqual(self.state.violations, [])

    def test_crash_between_write_and_fsync_resends_chunk(self):
        """Chunks written but never fsynced have no digest, so the next pass sends them again (after a crash the
        target may hold anything for them)."""
        bad = SpyTarget(self.w, fail_flush_at=2)
        self.state.target = bad
        with self.assertRaises(RuntimeError):
            self.sync(bad, fsync_bytes=6 * C, threaded=False)
        committed = set(self.state.digests("d"))
        self.assertTrue(committed.issubset(bad.flushed | {i for i in range(N) if i % 3 == 0}))
        self.state.target = self.spy
        s = self.sync(threaded=False)
        self.assertTrue(self.same())
        self.assertGreater(s["sent"], 0)

    def test_scan_position_advances_without_writes(self):
        self.sync(threaded=False)
        # nothing changed: the jobs are commit-only and are recorded at once, not held for an fsync that never comes
        positions = []
        orig = self.state.commit
        self.state.commit = lambda key, u, pos: (positions.append(pos), orig(key, u, pos))[1]
        self.sync(threaded=False)
        self.assertEqual(positions, sorted(positions))
        self.assertEqual(positions[-1], N)

    def test_threaded_and_inline_agree_and_resume_is_exact(self):
        self.sync(fsync_bytes=6 * C, threaded=True)
        digests = self.state.digests("d")
        for i in (5, 17, 40):
            with open(self.src, "r+b") as f:
                f.seek(i * C); f.write(os.urandom(C))
        s = self.sync(fsync_bytes=6 * C, threaded=True)
        self.assertEqual(s["sent"], 3)
        self.assertEqual(sum(1 for k, v in self.state.digests("d").items() if digests.get(k) != v), 3)
        self.assertTrue(self.same())

    def test_stop_commits_what_was_flushed_and_resumes(self):
        n = [0]
        def stop():
            n[0] += 1
            return n[0] > 20
        with self.assertRaises(InterruptedError):
            self.sync(stop=stop, fsync_bytes=10 ** 9, threaded=True)
        d = self.state.disk("d")
        self.assertEqual(self.state.violations, [])
        self.assertTrue(d["pass_active"] and 0 < d["scan_pos"] < N)
        self.sync(threaded=True)
        self.assertTrue(self.same())

    def test_idle_age_flush(self):
        """With a slow producer the worker fsyncs by itself once the data is older than max_age."""
        self.state.target = None
        out = engine.Committer(self.spy, self.state, "d", fsync_bytes=10 ** 9, max_age=0.2, threaded=True)
        self.state.ensure_disk("d", N * C, C, "blake2b")
        out.reserve()
        out.submit([(1, os.urandom(C))], [(1, "x")], 2)
        deadline = time.time() + 5
        while self.spy.flushes == 0 and time.time() < deadline:
            time.sleep(0.05)
        self.assertEqual(self.spy.flushes, 1)
        out.finish()
        self.assertEqual(self.state.digests("d"), {1: "x"})


class Compression(unittest.TestCase):
    def test_roundtrip_over_real_helper(self):
        with tempfile.TemporaryDirectory() as tmp:
            src, dst = os.path.join(tmp, "src"), os.path.join(tmp, "dst")
            with open(src, "wb") as f:
                for i in range(8):
                    f.write((b"abc" * (C // 3 + 1))[:C] if i % 2 else os.urandom(C))   # half compressible
            with open(dst, "wb") as f:
                f.truncate(8 * C)
            r = LocalReader(src, chunk=C); r.compress = True
            w = BlockEndpoint(LocalTransport(), dst, chunk=C, writable=True); w.compress = True
            st = State(os.path.join(tmp, "s.db"))
            engine.sync(r, w, st, "d", "blake2b", batch=3)
            w.close(); r.close(); st.close()
            with open(src, "rb") as a, open(dst, "rb") as b:
                self.assertEqual(a.read(), b.read())

    def test_pack_payload(self):
        zeros = bytes(C)
        n, p = helper.pack_payload(zeros, True)
        self.assertTrue(n & helper.COMPRESSED and len(p) < 1000)
        rnd = os.urandom(C)
        self.assertEqual(helper.pack_payload(rnd, True), (C, rnd))      # incompressible: plain
        self.assertEqual(helper.pack_payload(zeros, False), (C, zeros))

    def run_write(self, frames, extra=()):
        with tempfile.TemporaryDirectory() as tmp:
            dst = os.path.join(tmp, "dst")
            with open(dst, "wb") as f:
                f.truncate(4 * C)
            p = subprocess.run([sys.executable, "-I", helper.__file__, "write", dst, "--chunk", str(C), *extra],
                               input=frames, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            return p

    def test_helper_refuses_compressed_frame_without_flag(self):
        import zlib
        z = zlib.compress(bytes(C))
        frames = helper.FRAME.pack(0, len(z) | helper.COMPRESSED) + z + helper.FRAME.pack(helper.END, 0)
        self.assertNotEqual(self.run_write(frames).returncode, 0)
        self.assertEqual(self.run_write(frames, ["--compress"]).returncode, 0)

    def test_helper_refuses_inflation_past_chunk(self):
        import zlib
        z = zlib.compress(bytes(C * 8))
        frames = helper.FRAME.pack(0, len(z) | helper.COMPRESSED) + z + helper.FRAME.pack(helper.END, 0)
        p = self.run_write(frames, ["--compress"])
        self.assertNotEqual(p.returncode, 0)
        self.assertIn(b"out of bounds", p.stderr)


def fake_migration(tmp, disks, sync=None, max_mbps=0):
    m = Migration.__new__(Migration)
    m.p = {"name": "t", "writer": {"local": False}, "source": {"type": "local", "max_mbps": max_mbps},
           "disks": [{"name": f"d{i}", "path": p} for i, (p, _) in enumerate(disks)],
           "sync": dict({"batch": 4, "algo": "auto", "chunk_mib": 4, "parallel_disks": "auto"}, **(sync or {}))}
    m.dry_run, m.progress, m.chunk = False, None, C
    m.state = State(os.path.join(tmp, "s.db"))
    m._writer = LocalTransport()
    targets = {f"d{i}": d for i, (_, d) in enumerate(disks)}
    m.ensure_staging = lambda disk, size: targets[disk["name"]]
    return m


class ParallelDisks(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.disks = []
        for i in range(5):
            s, d = os.path.join(self.tmp.name, f"s{i}"), os.path.join(self.tmp.name, f"t{i}")
            make_file(s, 12 + i)
            with open(d, "wb") as f:
                f.truncate((12 + i) * C)
            self.disks.append((s, d))
        self.m = fake_migration(self.tmp.name, self.disks)

    def tearDown(self):
        self.m.state.close(); self.tmp.cleanup()

    def check_copies(self):
        for s, d in self.disks:
            with open(s, "rb") as a, open(d, "rb") as b:
                self.assertEqual(a.read(), b.read())

    def test_count(self):
        pc = self.m.parallel_count
        self.assertEqual([pc(n) for n in (1, 2, 4, 5, 9)], [1, 2, 4, 4, 4])
        self.m.p["sync"]["parallel_disks"] = 2
        self.assertEqual([pc(n) for n in (1, 2, 5)], [1, 2, 2])
        self.m.p["sync"]["parallel_disks"] = 16
        self.assertEqual(pc(5), 5)
        self.m.p["sync"]["parallel_disks"] = 1
        self.assertEqual(pc(5), 1)

    def test_plan_validation(self):
        for good in ("auto", 1, 16):
            p = {"sync": {"parallel_disks": good}}
            self.assertEqual(planlib.validate(self.valid(p))["sync"]["parallel_disks"], good)
        self.assertEqual(planlib.validate(self.valid({}))["sync"]["parallel_disks"], "auto")
        for bad in (0, 17, "x", True, 2.5):
            with self.assertRaises(planlib.PlanError):
                planlib.validate(self.valid({"sync": {"parallel_disks": bad}}))
        with self.assertRaises(planlib.PlanError):
            planlib.validate(self.valid({"sync": {"compress": "lz4"}}))
        with self.assertRaises(planlib.PlanError):
            planlib.validate(self.valid({"sync": {"fsync_mib": -1}}))

    def valid(self, extra):
        p = {"name": "x", "writer": {"local": True}, "source": {"type": "local"},
             "target": {"vm_name": "ev2ahv-x", "cluster": "c", "container": "k"}, "disks": [{"path": "/x"}]}
        p.update(extra)
        return p

    def test_sync_all_parallel_copies_everything(self):
        res = self.m._sync_all()
        self.assertEqual(list(res), [f"d{i}" for i in range(5)])
        self.check_copies()
        again = self.m._sync_all()
        self.assertTrue(all(r["sent"] == 0 for r in again.values()))

    def test_same_result_serial_and_parallel(self):
        self.m._sync_all()
        parallel = {k: v for k, v in self.m.state.digests("d3").items()}
        m2 = fake_migration(self.tmp.name + "", self.disks, {"parallel_disks": 1})
        m2.state.close()
        m2.state = State(os.path.join(self.tmp.name, "s2.db"))
        for _, d in self.disks:
            with open(d, "r+b") as f:
                f.truncate(0); f.truncate(os.path.getsize(self.disks[0][0]) if False else os.path.getsize(_))
        m2.p["disks"] = self.m.p["disks"]
        m2._sync_all()
        self.assertEqual(m2.state.digests("d3"), parallel)
        m2.state.close()

    def test_runs_at_the_same_time(self):
        live, peak, lock = [0], [0], threading.Lock()
        orig = self.m._sync_disk
        def slow(*a, **kw):
            with lock:
                live[0] += 1; peak[0] = max(peak[0], live[0])
            time.sleep(0.3)
            try:
                return orig(*a, **kw)
            finally:
                with lock:
                    live[0] -= 1
        self.m._sync_disk = slow
        self.m._sync_all()
        self.assertEqual(peak[0], 4)       # 5 disks, auto = 4
        peak[0] = 0
        self.m.p["sync"]["parallel_disks"] = 1
        self.m._sync_all()
        self.assertEqual(peak[0], 1)

    def test_max_mbps_is_split(self):
        seen = []
        orig = self.m._sync_disk
        self.m._sync_disk = lambda disk, reader, *a, **kw: (seen.append(reader.max_mbps), orig(disk, reader, *a, **kw))[1]
        self.m.p["source"]["max_mbps"] = 100
        self.m._sync_all()
        self.assertEqual(seen, [25.0] * 5)
        seen.clear()
        self.m.p["sync"]["parallel_disks"] = 1
        self.m._sync_all()
        self.assertEqual(seen, [100] * 5)

    def test_failure_stops_the_others_and_keeps_their_progress(self):
        orig = self.m._sync_disk
        def boom(disk, reader, writer, algo, kw, verify, final):
            if disk["name"] == "d1":
                raise RuntimeError("d1 broke")
            return orig(disk, reader, writer, algo, kw, verify, final)
        self.m._sync_disk = boom
        with self.assertRaisesRegex(RuntimeError, "d1 broke"):
            self.m._sync_all()
        self.m._sync_disk = orig
        self.m._sync_all()          # resumes: everything converges
        self.check_copies()

    def test_final_sync_verified_parallel(self):
        self.m.p["sync"]["final_verify"] = "full"
        self.m._sync_all()
        with open(self.disks[2][0], "r+b") as f:
            f.seek(3 * C); f.write(os.urandom(C))
        with open(self.disks[2][1], "r+b") as f:    # corrupt the target too: the verified pass must repair it
            f.seek(5 * C); f.write(os.urandom(C))
        res = self.m._sync_all(final=True)
        self.assertEqual(res["d2"]["verified_rounds"], 2)   # round 1 repairs the target, round 2 finds nothing
        self.check_copies()

    def test_progress_from_threads(self):
        calls, lock = [], threading.Lock()
        self.m.progress = lambda key, i, t, st: (lock.acquire(), calls.append(key), lock.release())
        self.m._sync_all()
        self.assertEqual({c for c in calls}, {f"d{i}" for i in range(5)})


if __name__ == "__main__":
    unittest.main()
