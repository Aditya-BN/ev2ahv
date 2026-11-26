"""D4: the cutover result is kept on the plan VM and survives the /api/v2 response model."""
import os, sys, unittest
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path[:0] = [ROOT, os.path.join(ROOT, "server")]
from ev2ahv_server import models as m  # noqa: E402
from ev2ahv_server.live import Live, cutover_summary  # noqa: E402

ENGINE_RESULT = {  # shape of Migration.cutover()'s return value
    "vm": "8e75066d-a0cb-4d79-69c2-0bf01223efa2",
    "timings": {"final_sync_seconds": 364.0, "downtime_seconds": 448.0, "last_resync_seconds": 118.0},
    "verify": {"power": "ON", "reachable": "10.12.136.162:22", "commands": {"uname -r": "5.14"}},
    "conversion": None,
}


class FakeStore:
    def get(self, kind, id_):
        return {"address": "prism.example"} if kind == "provider" else None


class CutoverResultTest(unittest.TestCase):
    def test_summary_keeps_timings_and_drops_command_output(self):
        s = cutover_summary(ENGINE_RESULT, now=1000.0)
        self.assertEqual(s["completed_at"], 1000.0)
        self.assertEqual(s["downtime_seconds"], 448.0)
        self.assertEqual(s["final_sync_seconds"], 364.0)
        self.assertEqual(s["target_vm_id"], ENGINE_RESULT["vm"])
        self.assertEqual(s["verify"], {"power": "ON", "reachable": "10.12.136.162:22"})
        self.assertNotIn("conversion_seconds", s)

    def test_cut_stores_result_and_api_model_returns_it(self):
        live = Live(FakeStore(), None, None, None)
        v = {"id": "vm1", "name": "ev2ahv-src-x", "status": "Cutover in Progress", "force": True}
        live._cut({"target": {"id": "t"}}, v, ENGINE_RESULT)
        self.assertEqual(v["status"], "Completed")
        vm = m.PlanVm(**v)
        self.assertEqual(vm.cutover_result.downtime_seconds, 448.0)
        self.assertEqual(vm.model_dump()["cutover_result"]["final_sync_seconds"], 364.0)

    def test_cut_with_no_engine_result(self):
        v = {"id": "vm1", "name": "x", "status": "Cutover in Progress"}
        Live(FakeStore(), None, None, None)._cut({}, v, None)
        self.assertIsNone(m.PlanVm(**v).cutover_result.downtime_seconds)

    def test_rollback_clears_result(self):
        v = {"id": "vm1", "name": "x", "status": "Rolling Back", "cutover_result": cutover_summary(ENGINE_RESULT)}
        Live._rolled_back({}, v, None)
        self.assertIsNone(m.PlanVm(**v).cutover_result)


if __name__ == "__main__":
    unittest.main()
