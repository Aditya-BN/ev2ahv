"""D23 (no MAC to keep on plan-file sources) and D24 (deleting a seeded plan removes its staging disks first).

api.py reads its paths and mode at import, so the API part runs in a subprocess in live mode with a throw-away
database; the worker thread is not started (that happens at app startup) and its launch() is replaced by a recorder.
"""
import os, subprocess, sys, tempfile, textwrap, unittest
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

SCRIPT = textwrap.dedent("""
    import sys
    sys.path[:0] = [sys.argv[1], sys.argv[1] + "/server"]
    from fastapi import HTTPException
    from ev2ahv_server import api
    launched = []
    api.worker.launch = lambda plan, vm, steps, on_done, force=False: launched.append((vm["id"], steps, on_done))
    vm = lambda i, s: {"id": i, "name": "ev2ahv-" + i, "status": s, "checks": []}
    doc = {"id": "p1", "name": "p", "created_by": "t", "created_at": 1.0, "started_at": 2.0, "prep": {},
           "schedule": {}, "vms": [vm("a", "Ready to Cutover"), vm("b", "Completed"), vm("c", "Queued")]}
    api.store.put("plan", api.finish(doc))

    # 1. refused without cleanup, nothing launched, plan kept
    try:
        api.delete_plan("p1", cleanup=False, who="t"); raise SystemExit("deleted without cleanup")
    except HTTPException as e:
        assert e.status_code == 409 and e.detail["staging"] == ["ev2ahv-a", "ev2ahv-b"], e.detail
    assert api.store.get("plan", "p1") and not launched

    # 2. with cleanup: staging-only step per seeded VM, plan marked deleting
    r = api.delete_plan("p1", cleanup=True, who="t")
    assert r == {"cleanup_started": ["ev2ahv-a", "ev2ahv-b"]}, r
    assert [(i, s) for i, s, _ in launched] == [("a", ["cleanup-staging"]), ("b", ["cleanup-staging"])], launched
    d = api.store.get("plan", "p1")
    assert d["deleting"] and [v.get("cleanup_pending") for v in d["vms"]] == [True, True, None]

    # 3. the worker keeps the plan while cleanup is pending ...
    api.worker.tick()
    assert api.store.get("plan", "p1")
    # ... and deletes it once every VM reported clean (the job callback runs inside the worker's plan update)
    for vid, _, cb in launched:
        api.worker._update("p1", vid, lambda p, v, cb=cb: cb(p, v, {}))
    api.worker.tick()
    assert api.store.get("plan", "p1") is None
    assert any("deleted after its staging disks were removed" in e["message"] for e in api.store.events(20))

    # 4. a plan that never seeded is deleted at once
    api.store.put("plan", api.finish(dict(doc, id="p2", name="q", vms=[vm("x", "Queued")])))
    assert api.delete_plan("p2", cleanup=False, who="t") == {} and api.store.get("plan", "p2") is None
    print("OK")
""")


class PlanDeleteTest(unittest.TestCase):
    def test_seeded_plan_cleans_staging_before_delete(self):
        with tempfile.TemporaryDirectory() as t:
            env = dict(os.environ, EV2AHV_SERVER_MODE="live", EV2AHV_DATA=t, EV2AHV_SERVER_DB=os.path.join(t, "s.db"),
                       EV2AHV_CONFIG_DIR=os.path.join(t, "cfg"), EV2AHV_ETC=os.path.join(t, "etc"), HOME=t)
            r = subprocess.run([sys.executable, "-B", "-c", SCRIPT, ROOT], env=env, capture_output=True, text=True,
                               timeout=120)
            self.assertEqual(r.returncode, 0, r.stderr[-3000:])
            self.assertIn("OK", r.stdout)


class KeepMacTest(unittest.TestCase):
    """D23: a NIC without a MAC (plan-file sources) never gets keep_mac, whatever the wizard's retain_mac says."""
    def test_no_mac_no_keep(self):
        sys.path[:0] = [ROOT, os.path.join(ROOT, "server")]
        from ev2ahv_server import live
        with tempfile.TemporaryDirectory() as t:
            f = os.path.join(t, "src.yaml")
            with open(f, "w") as fh:
                fh.write("name: ev2ahv-x\nwriter: {local: true}\nsource: {type: direct, host: h, user: u}\n"
                         "disks: [{name: d, path: /x}]\nnics: [{subnet: S, keep_mac: false}]\n"
                         "target: {vm_name: ev2ahv-x, cluster: C, container: K}\n")
            old, live.DATA = live.DATA, t
            try:
                plan = {"id": "p", "name": "p", "prep": {"retain_mac": True}, "network_map": []}
                path = live.engine_plan(plan, {"id": "src.yaml", "name": "ev2ahv-x"}, {"type": "plans"},
                                        {"_file": f}, None, {})
            finally:
                live.DATA = old
            from ev2ahv import plan as planlib
            self.assertFalse(planlib.load(path)["nics"][0]["keep_mac"])


if __name__ == "__main__":
    unittest.main()
