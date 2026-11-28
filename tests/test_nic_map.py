"""D29: every source NIC lands on its own mapped target subnet (fakes only, no Prism, no lab)."""
import os, sys, tempfile, unittest
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path[:0] = [ROOT, os.path.join(ROOT, "server")]
from ev2ahv import builder, everrun  # noqa: E402
from ev2ahv_server import live, models as m  # noqa: E402
from ev2ahv_server.nicmap import NicMapError, resolve_nic_subnets  # noqa: E402

XML = """<domain><name>ev2ahv-src-two</name><vcpu>2</vcpu><memory unit='KiB'>2097152</memory>
<devices><disk type='file' device='disk'><driver type='raw'/><source file='/x/a.img'/><target dev='vda'/></disk>
<interface type='bridge'><mac address='52:54:00:AA:00:01'/><source bridge='sharednetwork0'/><model type='virtio'/></interface>
<interface type='bridge'><mac address='52:54:00:AA:00:02'/><source bridge='sharednetwork1'/><model type='e1000'/></interface>
</devices></domain>"""

TWO = [{"network": "sharednetwork0", "mac": "52:54:00:aa:00:01"}, {"network": "sharednetwork1", "mac": "52:54:00:aa:00:02"}]
NM = [{"source_network": "sharednetwork0", "target_subnet": "VLAN136", "test_subnet": "TEST0"},
      {"source_network": "sharednetwork1", "target_subnet": "VLAN135"}]


class Resolve(unittest.TestCase):
    def test_each_nic_on_its_own_subnet(self):
        picks, warns = resolve_nic_subnets(TWO, NM)
        self.assertEqual(picks, [("VLAN136", "TEST0"), ("VLAN135", None)])
        self.assertEqual(warns, [])

    def test_override_wins_for_one_nic(self):
        picks, _ = resolve_nic_subnets(TWO, NM, {"1": "DMZ"})
        self.assertEqual([p[0] for p in picks], ["VLAN136", "DMZ"])

    def test_unmapped_nic_refuses_and_names_it(self):
        with self.assertRaises(NicMapError) as c:
            resolve_nic_subnets(TWO, NM[:1] + [{"source_network": "other", "target_subnet": "X"}])
        self.assertIn("NIC 1 (sharednetwork1, 52:54:00:aa:00:02)", str(c.exception))
        with self.assertRaises(NicMapError):
            resolve_nic_subnets(TWO, [])

    def test_old_single_mapping_applies_to_all_nics_with_a_warning(self):
        picks, warns = resolve_nic_subnets(TWO, [{"source_network": "sharednetwork", "target_subnet": "OLD"}])
        self.assertEqual([p[0] for p in picks], ["OLD", "OLD"])
        self.assertEqual(len(warns), 2)
        self.assertIn("first mapped subnet", warns[0])

    def test_single_nic_on_old_mapping_is_quiet(self):
        picks, warns = resolve_nic_subnets(TWO[:1], [{"source_network": "sharednetwork", "target_subnet": "OLD"}])
        self.assertEqual((picks[0][0], warns), ("OLD", []))


class Discovery(unittest.TestCase):
    def test_parse_domain_records_network_mac_model(self):
        nics = everrun.parse_domain(XML)["nics"]
        self.assertEqual([(n["mac"], n["network"], n["model"]) for n in nics],
                         [("52:54:00:aa:00:01", "sharednetwork0", "virtio"), ("52:54:00:aa:00:02", "sharednetwork1", "e1000")])


class EnginePlan(unittest.TestCase):
    def _plan(self, nm, vs=None):
        tmp = tempfile.TemporaryDirectory(); self.addCleanup(tmp.cleanup)
        old = live.DATA; live.DATA = tmp.name; self.addCleanup(setattr, live, "DATA", old)
        info = everrun.parse_domain(XML)
        info.update(active_host="10.0.0.1", disks=[{"dev": "vda", "path": "/x/a.img", "guest_bytes": 1 << 30}])
        orig = live.everrun.discover; live.everrun.discover = lambda *_: dict(info)
        self.addCleanup(setattr, live.everrun, "discover", orig)
        plan = {"id": "p1", "name": "n", "vm_settings": {"v": vs or {}}, "prep": {"mode": "manual"}, "network_map": nm}
        src = {"type": "everrun", "node_addresses": ["10.0.0.1"], "address": "10.0.0.9", "username": "u"}
        path = live.engine_plan(plan, {"id": "v", "name": "ev2ahv-src-two"}, src, {"os_type": "linux", "nics": []},
                                lambda k: None, {})
        return live.planlib.load(path)

    def test_two_networks_become_two_subnets_mac_kept(self):
        nics = self._plan(NM)["nics"]
        self.assertEqual([(n["subnet"], n["mac"], n["keep_mac"], n["model"]) for n in nics],
                         [("VLAN136", "52:54:00:aa:00:01", True, "VIRTIO"), ("VLAN135", "52:54:00:aa:00:02", True, "E1000")])
        self.assertEqual(nics[0]["test_subnet"], "TEST0")

    def test_override_and_refusal(self):
        self.assertEqual([n["subnet"] for n in self._plan(NM, {"nic_subnets": {"0": "DMZ"}})["nics"]], ["DMZ", "VLAN135"])
        with self.assertRaises(live.LiveError):
            self._plan([])


class Builder(unittest.TestCase):
    def test_payload_has_one_nic_per_subnet_in_order(self):
        plan = {"nics": [{"subnet": "VLAN136", "mac": "52:54:00:aa:00:01", "keep_mac": True},
                         {"subnet": "VLAN135", "mac": "52:54:00:aa:00:02", "keep_mac": True, "model": "E1000"}]}
        out = builder.nic_entries(plan, {"VLAN136": "id136", "VLAN135": "id135"})
        self.assertEqual([n["networkInfo"]["subnet"]["extId"] for n in out], ["id136", "id135"])
        self.assertEqual([n["backingInfo"]["macAddress"] for n in out], ["52:54:00:aa:00:01", "52:54:00:aa:00:02"])
        self.assertEqual([n["backingInfo"]["model"] for n in out], ["VIRTIO", "E1000"])


class ApiCheck(unittest.TestCase):
    def test_model_accepts_nic_subnets(self):
        self.assertEqual(m.VmSettings(nic_subnets={"1": "DMZ"}).nic_subnets, {"1": "DMZ"})
        self.assertEqual(m.VmSettings().nic_subnets, {})


if __name__ == "__main__":
    unittest.main()
