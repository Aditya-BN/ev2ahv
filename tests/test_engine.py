import os, sys, tempfile, unittest
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from ev2ahv import engine, everrun, plan, builder, target
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

    def test_limit_excludes_everrun_trailer(self):
        with open(self.src, "r+b") as f:
            f.seek(40 * C - 2048); f.write(b"T" * 2048)
        self.r.limit = 40 * C - 2048
        self.run_sync()
        self.w.close()
        with open(self.dst, "rb") as f:
            f.seek(40 * C - 2048)
            self.assertEqual(f.read(), bytes(2048))
        rounds, s = engine.final_sync(self.r, self.w, self.state, "d", "blake2b")
        self.assertEqual((rounds, s["sent"]), (1, 0))

    def test_sparse_holes_hash_as_zero(self):
        sparse = os.path.join(self.tmp.name, "sparse")
        with open(sparse, "wb") as f:
            f.truncate(20 * C); f.seek(9 * C); f.write(b"x" * C)
        r = LocalReader(sparse, chunk=C)
        try:
            got = dict(r.hashes("blake2b"))
        finally:
            r.close()
        self.assertEqual(sum(v != "z" for v in got.values()), 1)
        self.assertNotEqual(got[9], "z")

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

    def test_image_guard_blocks_foreign_image(self):
        p = Prism(Credentials("h", api_key="k"))
        p._call = lambda *a, **k: ({"data": {"name": "Rocky-Disk-9.8"}}, {"ETag": "e"})
        with self.assertRaises(NotOwned):
            p.delete_image("x")
        with self.assertRaises(NotOwned):
            p.upload_image("Rocky-Disk-9.8", None, 1, "c")

    def test_transfer_mode_is_validated(self):
        base = {"name": "x", "writer": {"vm": "w", "host": "h"}, "source": {"type": "local"},
                "target": {"vm_name": "ev2ahv-x", "cluster": "c", "container": "k", "transfer": "ftp"},
                "disks": [{"path": "/tmp/a"}]}
        with self.assertRaises(plan.PlanError):
            plan.validate(base)

    def test_everrun_domain_parsing(self):
        xml = """<domain><name>g</name><memory>4184064</memory><vcpu>2</vcpu><os><type>hvm</type></os><devices>
          <disk device='disk'><driver type='raw'/><source file='/mnt/everrun/a/b'/><target dev='vda'/></disk>
          <disk device='cdrom'><target dev='hdb'/></disk>
          <interface type='bridge'><mac address='00:E0:09:A4:D7:40'/><model type='virtio'/></interface>
        </devices></domain>"""
        d = everrun.parse_domain(xml)
        self.assertEqual((d["firmware"], d["vcpus"], d["memory_mib"]), ("bios", 2, 4086))
        self.assertEqual([x["dev"] for x in d["disks"]], ["vda"])
        self.assertEqual(d["nics"][0]["mac"], "00:e0:09:a4:d7:40")

    def test_everrun_active_node_must_be_unique(self):
        self.assertEqual(everrun.active_node({"a": "running", "b": "paused"}), "a")
        for bad in ({"a": "paused", "b": "paused"}, {"a": "running", "b": "running"}):
            with self.assertRaises(RuntimeError):
                everrun.active_node(bad)

    def test_unreadable_power_state_is_an_error(self):
        from ev2ahv.workflow import Migration
        m = Migration.__new__(Migration)
        m._source_power = lambda action: ":"
        with self.assertRaises(RuntimeError):
            m.source_stopped()
        m._source_power = lambda action: "stopped"
        self.assertTrue(m.source_stopped())
        m._source_power = lambda action: "running"
        self.assertFalse(m.source_stopped())

    def test_alignment_rounds_up(self):
        self.assertEqual(builder.align(1), 1024 ** 2)
        self.assertEqual(builder.align(1024 ** 2), 1024 ** 2)


if __name__ == "__main__":
    unittest.main()
