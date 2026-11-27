"""D8d: test migration clones the staging disks into <target>-test on the test subnet (fakes, no Prism)."""
import os, sys, tempfile, unittest
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from ev2ahv.state import State  # noqa: E402
from ev2ahv.workflow import Migration  # noqa: E402

G = 1 << 30


class FakeWriter:
    def __init__(self):
        self.offlined = []

    def offline(self, path):
        self.offlined.append(path)


class FakePrism:
    def __init__(self, existing=()):
        self.extra_owned, self.created, self.powered, self.deleted = set(), [], [], []
        self.vms = {n: {"extId": "x-" + n, "name": n} for n in existing}

    def cluster_id(self, name):
        return "c1"

    def subnet_id(self, name, cluster):
        return "sn-" + name

    def disk_ref(self, vm, disk):
        return {"vm": vm, "disk": disk}

    def vm_by_name(self, name):
        return self.vms.get(name)

    def create_vm(self, payload):
        self.created.append(payload)
        return "test-vm-1"

    def power(self, vm, action):
        self.powered.append((vm, action))

    def delete_vm(self, vm):
        self.deleted.append(vm)


def migration(prism, state, transfer="hotadd", test_subnet="ISOLATED"):
    m = Migration.__new__(Migration)
    m.p = {"name": "p", "disks": [{"name": "vda"}, {"name": "vdb"}],
           "nics": [{"subnet": "VLAN136", "mac": "50:6b:8d:00:00:01", "keep_mac": True}],
           "target": {"vm_name": "ev2ahv-x", "cluster": "C", "transfer": transfer, "test_subnet": test_subnet,
                      "sockets": 1, "cores": 2, "threads": 1, "memory_mib": 2048, "machine_type": "PC",
                      "firmware": "bios", "disk_bus": "scsi"}}
    m.prism, m.state, m.dry_run = prism, state, False
    m._writer = FakeWriter()
    m.lock = lambda: None
    m.writer_vm = lambda: "writer-1"
    m.device_for = lambda entry: entry["path"]
    state.set("staging", {d: {"disk_id": "d-" + d, "path": "/dev/" + d, "size": G} for d in ("vda", "vdb")})
    return m


class TestMigrationTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.state = State(os.path.join(self.tmp.name, "s.db"))

    def tearDown(self):
        self.state.close()
        self.tmp.cleanup()

    def test_clone_on_test_subnet_with_new_macs(self):
        p = FakePrism()
        m = migration(p, self.state)
        r = m.test()
        self.assertEqual(r, {"test_vm": "test-vm-1", "name": "ev2ahv-x-test", "subnet": "ISOLATED"})
        pay = p.created[0]
        self.assertEqual(pay["name"], "ev2ahv-x-test")
        self.assertEqual([n["networkInfo"]["subnet"]["extId"] for n in pay["nics"]], ["sn-ISOLATED"])
        self.assertNotIn("macAddress", pay["nics"][0]["backingInfo"])            # never the source's MAC
        refs = [d["backingInfo"]["dataSource"]["reference"] for d in pay["disks"]]
        self.assertEqual([(x["vm"], x["disk"]) for x in refs], [("writer-1", "d-vda"), ("writer-1", "d-vdb")])
        self.assertEqual(m._writer.offlined, ["/dev/vda", "/dev/vdb"])            # flushed before the clone
        self.assertEqual(p.powered, [("test-vm-1", "power-on")])
        self.assertIn("ev2ahv-x-test", p.extra_owned)
        self.assertEqual(self.state.get("test_vm"), "test-vm-1")
        self.assertEqual(m.p["target"]["vm_name"], "ev2ahv-x")                   # the real plan is untouched

    def test_falls_back_to_the_plan_subnet(self):
        p = FakePrism()
        migration(p, self.state, test_subnet=None).test()
        self.assertEqual(p.created[0]["nics"][0]["networkInfo"]["subnet"]["extId"], "sn-VLAN136")

    def test_refusals(self):
        with self.assertRaises(RuntimeError):
            migration(FakePrism(), self.state, transfer="image").test()
        with self.assertRaises(RuntimeError):
            migration(FakePrism(existing=["ev2ahv-x-test"]), self.state).test()
        m = migration(FakePrism(), self.state)
        self.state.set("staging", {})
        with self.assertRaises(RuntimeError):
            m.test()

    def test_discard_deletes_only_the_test_vm(self):
        p = FakePrism(existing=["ev2ahv-x-test", "ev2ahv-x"])
        m = migration(p, self.state)
        self.assertEqual(m.discard_test()["discarded"], "x-ev2ahv-x-test")
        self.assertEqual(p.deleted, ["x-ev2ahv-x-test"])
        self.assertEqual(migration(FakePrism(), self.state).discard_test()["discarded"], None)


if __name__ == "__main__":
    unittest.main()
