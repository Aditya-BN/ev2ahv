"""D4: the cutover result is kept on the plan VM and survives the /api/v2 response model."""
import os, sys, threading, time, unittest
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

    def test_legacy_raw_engine_result_is_read(self):
        """Found on a real 0.2.0 -> 0.3.x upgrade: the stored raw result lacked completed_at and broke /plans."""
        v = m.PlanVm(id="a", name="a", cutover_result={"vm": "uuid", "timings": {"downtime_seconds": 448.0},
                                                       "verify": {"power": "ON", "out": {"x": 1}}, "conversion": None})
        self.assertEqual(v.cutover_result.target_vm_id, "uuid")
        self.assertEqual(v.cutover_result.downtime_seconds, 448.0)
        self.assertEqual(v.cutover_result.verify, {"power": "ON"})
        self.assertIsNone(v.cutover_result.completed_at)   # D17: unknown, not 1970

    def test_completed_at_zero_is_normalised_to_null(self):
        """D17: 0.3.2 stored completed_at 0 for legacy results; the API now returns null, a real time is kept."""
        v = m.PlanVm(id="a", name="a", cutover_result={"completed_at": 0, "target_vm_id": "u", "downtime_seconds": 1.0})
        self.assertIsNone(v.cutover_result.completed_at)
        self.assertIsNone(v.model_dump()["cutover_result"]["completed_at"])
        self.assertEqual(v.cutover_result.downtime_seconds, 1.0)
        v = m.PlanVm(id="a", name="a", cutover_result={"completed_at": 1700000000.5})
        self.assertEqual(v.cutover_result.completed_at, 1700000000.5)


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


class CbtSettingTest(unittest.TestCase):
    """The per-VM cbt setting reaches the engine plan only for direct everRun sources, and defaults to off."""
    INFO = {"name": "ev2ahv-src-x", "active_host": "10.0.0.1", "vcpus": 2, "memory_mib": 2048, "firmware": "bios",
            "disks": [{"dev": "vda", "path": "/mnt/x/a.img", "guest_bytes": 1 << 30}], "nics": [{"mac": "52:54:00:00:00:01"}]}

    def _plan(self, cbt, final_verify=None):
        import tempfile
        tmp = tempfile.TemporaryDirectory(); self.addCleanup(tmp.cleanup)
        old = live.DATA; live.DATA = tmp.name; self.addCleanup(setattr, live, "DATA", old)
        orig = live.everrun.discover; live.everrun.discover = lambda *_: dict(self.INFO)
        self.addCleanup(setattr, live.everrun, "discover", orig)
        vs = {} if cbt is None else {"cbt": cbt}
        if final_verify:
            vs["final_verify"] = final_verify
        plan = {"id": "p1", "name": "n", "vm_settings": {"v": vs}, "prep": {"mode": "manual"}, "network_map": []}
        src = {"type": "everrun", "node_addresses": ["10.0.0.1"], "address": "10.0.0.9", "username": "u"}
        wl = {"os_type": "linux", "nics": []}
        path = live.engine_plan(plan, {"id": "v", "name": "ev2ahv-src-x"}, src, wl, lambda k: None, {})
        return live.planlib.load(path)

    def test_default_is_off(self):
        self.assertEqual(self._plan(None)["sync"]["cbt"], "off")
        self.assertEqual(self._plan("off")["sync"]["cbt"], "off")

    def test_auto_reaches_the_engine_plan(self):
        self.assertEqual(self._plan("auto")["sync"]["cbt"], "auto")

    def test_final_verify_reaches_the_engine_plan_and_defaults_to_full(self):
        self.assertEqual(self._plan("auto")["sync"]["final_verify"], "full")
        self.assertEqual(self._plan("auto", "none")["sync"]["final_verify"], "none")
        self.assertEqual(self._plan("auto", "sample")["sync"]["final_verify"], "sample")
        self.assertEqual(self._plan(None, "none")["sync"]["final_verify"], "full")   # cbt off: not applied

    def test_engine_plan_can_power_off_the_source_hard(self):
        self.assertIn("poweroff", self._plan("auto")["source"]["power"])

    def test_model_default_and_patch_validation(self):
        self.assertEqual(m.VmSettings().cbt, "off")
        self.assertEqual(m.VmSettings().final_verify, "full")
        with self.assertRaises(Exception):
            m.VmSettingsPatch(cbt="on")
        with self.assertRaises(Exception):
            m.VmSettingsPatch(final_verify="maybe")
        self.assertIsNone(m.VmSettingsPatch(final_verify="sample").cbt)


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

    def test_resync_after_rollback_uses_the_seeded_plan(self):
        """D16: the first resync after a rollback runs while the source boots and is absent from discovery."""
        vm = dict(self.vm, rolled_back=True)
        with self.assertLogs("ev2ahv.live", "WARNING"):
            self.assertEqual(self.live.ensure_plan(self.plan, vm, "resync"), self.path)

    def test_resync_without_rollback_still_needs_discovery(self):
        with self.assertRaises(live.LiveError):
            self.live.ensure_plan(self.plan, self.vm, "resync")

    def test_seed_still_needs_discovery(self):
        with self.assertRaises(live.LiveError):
            self.live.ensure_plan(self.plan, self.vm, "seed")

    def test_no_seeded_plan_still_raises(self):
        os.remove(self.path)
        with self.assertRaises(live.LiveError):
            self.live.ensure_plan(self.plan, self.vm, "cutover")


class RetryResyncTest(unittest.TestCase):
    """D16: a resync that fails while the rolled-back source boots is retried, not marked Failed."""
    def make(self, vm):
        plan = {"id": "p1", "name": "p", "schedule": {"resync_minutes": 60}, "vms": [vm]}
        events = []

        class Store:
            def get(_, kind, id_):
                return plan

            def put(_, kind, p):
                pass

            def event(_, *a, **k):
                events.append(a)
        lv = Live(Store(), threading.Lock(), lambda p: p, lambda: None)
        return lv, plan, events

    def test_retry_is_scheduled_for_a_minute_ahead(self):
        vm = {"id": "v", "name": "v", "status": "Ready to Cutover", "rolled_back": True}
        lv, plan, events = self.make(vm)
        before = time.time()
        self.assertTrue(lv._retry_resync(plan, vm, "ssh refused"))
        self.assertEqual(vm["resync_retries"], 1)
        self.assertEqual(vm["status"], "Ready to Cutover")
        self.assertAlmostEqual(vm["last_sync_at"] + 3600 - 60, before, delta=5)
        self.assertEqual(events[0][0], "warning")

    def test_gives_up_after_the_limit(self):
        vm = {"id": "v", "name": "v", "status": "Ready to Cutover", "rolled_back": True,
              "resync_retries": live.ROLLBACK_RESYNC_RETRIES}
        lv, plan, _ = self.make(vm)
        self.assertFalse(lv._retry_resync(plan, vm, "x"))

    def test_no_retry_without_rollback_or_during_cutover(self):
        for vm in ({"id": "v", "name": "v", "status": "Ready to Cutover"},
                   {"id": "v", "name": "v", "status": "Cutover in Progress", "rolled_back": True}):
            lv, plan, _ = self.make(vm)
            self.assertFalse(lv._retry_resync(plan, vm, "x"))


if __name__ == "__main__":
    unittest.main()
