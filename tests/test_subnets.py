"""D8c: subnets carry their type and VPC name; overlay NICs use the same payload as VLAN NICs."""
import os, sys, unittest
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path[:0] = [ROOT, os.path.join(ROOT, "server")]
from ev2ahv import builder  # noqa: E402
from ev2ahv_server import models as m  # noqa: E402
from ev2ahv_server.live import subnet_list  # noqa: E402


class FakePr:
    def __init__(self, vpcs_ok=True):
        self.vpcs_ok = vpcs_ok

    def _all(self, path):
        if path.endswith("/vpcs"):
            if not self.vpcs_ok:
                raise RuntimeError("no rights")
            return [{"extId": "v1", "name": "vpc-prd"}]
        return [{"extId": "o1", "name": "app", "subnetType": "OVERLAY", "vpcReference": "v1"},
                {"extId": "s1", "name": "VLAN136", "subnetType": "VLAN"}, {"extId": "x", "name": "odd"}]


class SubnetTest(unittest.TestCase):
    def test_types_vpc_names_and_order(self):
        out = subnet_list(FakePr())
        self.assertEqual([(s["name"], s["type"], s["vpc"]) for s in out],
                         [("VLAN136", "VLAN", None), ("app", "OVERLAY", "vpc-prd"), ("odd", "", None)])
        for s in out:
            m.Subnet(**s)

    def test_vpc_names_are_best_effort(self):
        self.assertEqual(subnet_list(FakePr(vpcs_ok=False))[1]["vpc"], "v1")

    def test_overlay_nic_payload_matches_vlan(self):
        plan = {"nics": [{"subnet": "app", "mac": "50:6b:8d:00:00:01", "keep_mac": True}]}
        nic = builder.nic_entries(plan, {"app": "o1"})[0]
        self.assertEqual(nic["networkInfo"]["subnet"]["extId"], "o1")
        self.assertEqual(nic["backingInfo"]["macAddress"], "50:6b:8d:00:00:01")


if __name__ == "__main__":
    unittest.main()
