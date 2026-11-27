"""D20: cutover stages and a recent-rate ETA."""
import os, sys, unittest
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path[:0] = [ROOT, os.path.join(ROOT, "server")]
from ev2ahv_server import runner  # noqa: E402
from ev2ahv_server.live import eta_seconds  # noqa: E402


class EtaTest(unittest.TestCase):
    def test_follows_the_recent_rate_after_a_fast_sparse_start(self):
        h = []
        # holes: 50 % in the first 20 s; then allocated data at 1 % per 10 s
        self.assertIsNone(eta_seconds(h, 0, 0.0))
        eta_seconds(h, 20, 0.50)
        t, f = 20, 0.50
        for _ in range(30):                      # 300 s of slow progress
            t, f = t + 10, f + 0.01
            eta = eta_seconds(h, t, f)
        self.assertAlmostEqual(eta, (1 - f) * 1000, delta=30)   # about 200 s left at 1 % per 10 s
        self.assertLessEqual(h[-1][0] - h[0][0], 130)          # only the recent window is kept

    def test_no_eta_without_progress(self):
        h = []
        eta_seconds(h, 0, 0.1)
        self.assertIsNone(eta_seconds(h, 30, 0.1))


class StageTest(unittest.TestCase):
    def test_next_stage(self):
        self.assertEqual(runner.next_stage("stopped"), "final sync")
        self.assertEqual(runner.next_stage("synced", image_mode=True), "image upload")
        self.assertEqual(runner.next_stage("synced"), "preparing disks")
        self.assertEqual(runner.next_stage("verified", converts=True), "converting to SCSI")
        self.assertEqual(runner.next_stage("verified"), "finishing")

    def test_watch_stages_emits_after_each_mark(self):
        out = []

        class M:
            image_mode = True
            def mark(self, step): out.append(("mark", step))
            def wants_conversion(self): return False
        old, runner.emit = runner.emit, lambda **kw: out.append(("emit", kw["stage"]))
        try:
            m = M()
            runner.watch_stages(m)
            m.mark("stopped")
            m.mark("synced")
        finally:
            runner.emit = old
        self.assertEqual(out, [("emit", "stopping source"), ("mark", "stopped"), ("emit", "final sync"),
                               ("mark", "synced"), ("emit", "image upload")])


if __name__ == "__main__":
    unittest.main()
