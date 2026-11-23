import os, sys, tempfile, unittest
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from ev2ahv import engine, plan, builder, target
from ev2ahv.prism import Prism, NotOwned
from ev2ahv.credentials import Credentials
from ev2ahv.readers import LocalReader
from ev2ahv.state import State
from ev2ahv.transport import BlockEndpoint, LocalTransport

C = 64 * 1024  # small chunks keep the tests fast


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.src = os.path.join(self.tmp.name, "src"); self.dst = os.path.join(self.tmp.name, "dst")
        with open(self.src, "wb") as f:
            for i in range(40):
                f.write(bytes(C) if i % 3 == 0 else os.urandom(C))
        with open(self.dst, "wb") as f:
            f.truncate(40 * C)
        self.state = State(os.path.join(self.tmp.name, "s.db"))
        self.r = LocalReader(self.src, chunk=C)
        self.w = BlockEndpoint(LocalTransport(), self.dst, chunk=C, writable=True)

    def tearDown(self):
        self.r.close(); self.w.close(); self.state.close(); self.tmp.cleanup()

    def same(self):
        self.w.close()
        with open(self.src, "rb") as a, open(self.dst, "rb") as b:
            return a.read() == b.read()

    def poke(self, path, index, data=None):
        with open(path, "r+b") as f:
            f.seek(index * C); f.write(data if data is not None else os.urandom(C))

    def run_sync(self, **kw):
        return engine.sync(self.r, self.w, self.state, "d", "blake2b", **kw)


class Sync(Base):
    def test_seed_copies_and_skips_zero(self):
        s = self.run_sync()
        self.assertTrue(self.same())
        self.assertEqual(s["sent"], 26)      # 14 of 40 chunks are zero
        self.assertEqual(s["skipped"], 14)

    def test_diff_sends_only_changed(self):
        self.run_sync(); self.poke(self.src, 5); self.poke(self.src, 17)
        s = self.run_sync()
        self.assertEqual(s["sent"], 2)
        self.assertTrue(self.same())

    def test_second_pass_sends_nothing(self):
        self.run_sync()
        self.assertEqual(self.run_sync()["sent"], 0)

    def test_source_zeroed_chunk_is_zeroed_on_target(self):
        self.run_sync(); self.poke(self.src, 1, bytes(C))
        s = self.run_sync()
        self.assertEqual((s["sent"], s["zero_written"]), (1, 1))
        self.assertTrue(self.same())

    def test_resume_after_interruption(self):
        calls = {"n": 0}
        def stop():
            calls["n"] += 1
            return calls["n"] > 20
        with self.assertRaises(InterruptedError):
            self.run_sync(stop=stop, batch=4)
        pos = self.state.disk("d")["scan_pos"]
        self.assertGreater(pos, 0)
        s = self.run_sync()
        self.assertEqual(s["scanned"], 40 - pos)    # continued, did not restart
        self.assertTrue(self.same())

    def test_corrupted_target_chunk_is_repaired(self):
        self.run_sync(); self.w.close(); self.poke(self.dst, 7)
        self.assertEqual(self.run_sync()["sent"], 0)    # recorded digests cannot see target damage
        s = self.run_sync(verify_target=True)
        self.assertEqual((s["sent"], s["repaired"]), (1, 1))
        self.assertTrue(self.same())

    def test_final_sync_converges(self):
        self.run_sync(); self.w.close(); self.poke(self.dst, 2); self.poke(self.src, 9)
        rounds, s = engine.final_sync(self.r, self.w, self.state, "d", "blake2b")
        self.assertEqual((rounds, s["sent"]), (2, 0))
        self.assertTrue(self.same())

    def test_unaligned_source_on_larger_target_converges(self):
        with open(self.src, "r+b") as f:
            f.truncate(39 * C + 512)
        self.r.close(); self.r = LocalReader(self.src, chunk=C)
        self.run_sync()
        rounds, s = engine.final_sync(self.r, self.w, self.state, "d", "blake2b")
        self.assertEqual((rounds, s["sent"]), (1, 0))

    def test_verified_pass_records_real_target_digest(self):
        self.run_sync(); self.w.close(); self.poke(self.dst, 7)
        self.run_sync(verify_target=True)
        self.assertEqual(self.run_sync()["sent"], 0)
        self.assertEqual(self.run_sync(verify_target=True)["repaired"], 0)

    def test_refuses_smaller_target(self):
        with open(self.dst, "r+b") as f:
            f.truncate(10 * C)
        with self.assertRaises(RuntimeError):
            self.run_sync()

    def test_refuses_mixed_algorithm(self):
        self.run_sync()
        with self.assertRaises(RuntimeError):
            engine.sync(self.r, self.w, self.state, "d", "sha256")


class Safety(unittest.TestCase):
    def test_device_pick_requires_one_new_exact_disk(self):
        base = {"sda": {"name": "sda", "size": 100}}
        ok = dict(base, sdb={"name": "sdb", "size": 50})
        self.assertEqual(target.pick_new_device(base, ok, 50)["name"], "sdb")
        for after in (base, dict(base, sdb={"name": "sdb", "size": 51}),
                      dict(ok, sdc={"name": "sdc", "size": 50}),
                      dict(base, sdb={"name": "sdb", "size": 50, "mountpoint": "/mnt"})):
            with self.assertRaises(RuntimeError):
                target.pick_new_device(base, after, 50)

    def test_guard_blocks_foreign_vm(self):
        p = Prism(Credentials("h", api_key="k"))
        p._call = lambda *a, **k: ({"data": {"name": "everrun-n1"}}, {"ETag": "e"})
        for fn in (lambda: p.delete_vm("x"), lambda: p.power("x", "power-off"), lambda: p.remove_vm_disk("x", "d"),
                   lambda: p.add_disk("x", 1)):
            with self.assertRaises(NotOwned):
                fn()
        with self.assertRaises(NotOwned):
            p.create_vm({"name": "prod-db"})

    def test_plan_validation(self):
        with self.assertRaises(plan.PlanError):
            plan.validate({"name": "x"})
        p = plan.validate({"name": "x", "writer": {"vm": "w", "host": "h"}, "source": {"type": "local"},
                           "target": {"vm_name": "ev2ahv-x", "cluster": "c", "container": "k"},
                           "disks": [{"path": "/tmp/a"}], "nics": [{"subnet": "s", "mac": "50:6B:8D:00:00:01"}]})
        self.assertEqual(p["nics"][0]["mac"], "50:6b:8d:00:00:01")

    def test_alignment_rounds_up(self):
        self.assertEqual(builder.align(1), 1024 ** 2)
        self.assertEqual(builder.align(1024 ** 2), 1024 ** 2)


if __name__ == "__main__":
    unittest.main()
