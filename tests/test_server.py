"""D4: the cutover result is kept on the plan VM and survives the /api/v2 response model."""
import os, sys, unittest
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path[:0] = [ROOT, os.path.join(ROOT, "server")]
from ev2ahv_server import models as m  # noqa: E402
from ev2ahv_server import live  # noqa: E402
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


class NameGuardTest(unittest.TestCase):
    """D3: the source name guard is a setting (default ev2ahv-), not only an env var."""
    def setUp(self):
        self.allow_any = live.ALLOW_ANY
        live.ALLOW_ANY = False

    def tearDown(self):
        live.ALLOW_ANY = self.allow_any

    def test_default_is_the_lab_prefix(self):
        self.assertTrue(live.name_allowed("ev2ahv-src-rocky9", {}))
        self.assertFalse(live.name_allowed("everrun-db01", {}))
        self.assertFalse(live.name_allowed("everrun-db01", None))

    def test_setting_changes_the_prefix(self):
        self.assertTrue(live.name_allowed("prod-db01", {"source_name_prefix": "prod-"}))
        self.assertFalse(live.name_allowed("ev2ahv-x", {"source_name_prefix": "prod-"}))

    def test_empty_prefix_allows_any(self):
        self.assertTrue(live.name_allowed("anything", {"source_name_prefix": ""}))

    def test_bad_value_falls_back_to_default(self):
        self.assertFalse(live.name_allowed("everrun-db01", {"source_name_prefix": None}))

    def test_env_override_still_works(self):
        live.ALLOW_ANY = True
        self.assertTrue(live.name_allowed("everrun-db01", {"source_name_prefix": "ev2ahv-"}))

    def test_engine_plan_refuses_a_guarded_name_before_touching_the_source(self):
        plan = {"vm_settings": {}, "prep": {}, "network_map": []}
        with self.assertRaises(live.LiveError):
            live.engine_plan(plan, {"id": "x", "name": "everrun-db01"}, {"type": "everrun", "node_addresses": []},
                             {}, None, {})


class StoppedSourceTest(unittest.TestCase):
    """T2 finding: everRun undefines a stopped guest in libvirt, so steps after the stop reuse the seeded plan."""
    def setUp(self):
        import tempfile
        self.tmp = tempfile.TemporaryDirectory()
        self.data, live.DATA = live.DATA, self.tmp.name
        self.plan, self.vm = {"id": "p1", "source": {"id": "s1"}}, {"id": "ev2ahv-src-x", "name": "ev2ahv-src-x"}
        path = live.plan_path(self.plan, self.vm)
        os.makedirs(os.path.dirname(path))
        open(path, "w").write("seeded")
        self.path = path

        class Store:
            def get(_, kind, id_):
                return {"name": "everrun-lab", "workloads": []} if kind == "provider" else {}
        self.live = Live(Store(), None, None, None)

    def tearDown(self):
        live.DATA = self.data
        self.tmp.cleanup()

    def test_rollback_uses_the_seeded_plan(self):
        self.assertEqual(self.live.ensure_plan(self.plan, self.vm, "rollback"), self.path)

    def test_cutover_falls_back_when_the_source_is_gone(self):
        with self.assertLogs("ev2ahv.live", "WARNING"):
            self.assertEqual(self.live.ensure_plan(self.plan, self.vm, "cutover"), self.path)

    def test_seed_still_needs_discovery(self):
        with self.assertRaises(live.LiveError):
            self.live.ensure_plan(self.plan, self.vm, "seed")

    def test_no_seeded_plan_still_raises(self):
        os.remove(self.path)
        with self.assertRaises(live.LiveError):
            self.live.ensure_plan(self.plan, self.vm, "cutover")


if __name__ == "__main__":
    unittest.main()
