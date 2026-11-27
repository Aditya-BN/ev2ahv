"""D18: "Resync now" on a seeded VM, and the 5-minute minimum interval.

api.py reads its paths when imported, so the API part runs in a subprocess with a throw-away database.
"""
import os, subprocess, sys, tempfile, textwrap, unittest
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path[:0] = [ROOT, os.path.join(ROOT, "server")]
from ev2ahv_server import models as m  # noqa: E402

SCRIPT = textwrap.dedent("""
    import sys, time
    sys.path[:0] = [sys.argv[1], sys.argv[1] + "/server"]
    from fastapi import HTTPException
    from ev2ahv_server import api, models as m
    vm = lambda i, s: {"id": i, "name": "ev2ahv-" + i, "status": s, "last_sync_at": 123.0, "checks": []}
    doc = {"id": "p1", "name": "p", "created_by": "t", "created_at": 1.0, "started_at": 2.0, "prep": {},
           "schedule": {"resync_minutes": 60}, "vms": [vm("a", "Ready to Cutover"), vm("b", "Seeding"),
                                                       vm("c", "Tested")]}
    api.store.put("plan", api.finish(doc))
    out = api.plan_action("p1", m.ActionIn(action="resync", vm_ids=["a", "c"]), who="t")
    acts = {v.id: v.actions for v in out.vms}
    assert "resync" in acts["a"] and "resync" in acts["c"] and "resync" not in acts["b"], acts
    stored = {v["id"]: v for v in api.store.get("plan", "p1")["vms"]}
    assert stored["a"]["last_sync_at"] == 0 and stored["c"]["last_sync_at"] == 0, stored
    assert stored["b"]["last_sync_at"] == 123.0 and stored["a"]["status"] == "Ready to Cutover"
    try:
        api.plan_action("p1", m.ActionIn(action="resync", vm_ids=["b"]), who="t")
        raise SystemExit("resync on a seeding VM was accepted")
    except HTTPException as e:
        assert e.status_code == 409
    assert any("resync" in e["message"] for e in api.store.events(10))
    print("OK")
""")


class ResyncNowTest(unittest.TestCase):
    def test_action_marks_the_vm_for_the_next_tick(self):
        with tempfile.TemporaryDirectory() as t:
            env = dict(os.environ, EV2AHV_SERVER_MODE="mock", EV2AHV_DATA=t, EV2AHV_SERVER_DB=os.path.join(t, "s.db"),
                       EV2AHV_CONFIG_DIR=os.path.join(t, "cfg"), EV2AHV_ETC=os.path.join(t, "etc"), HOME=t)
            r = subprocess.run([sys.executable, "-B", "-c", SCRIPT, ROOT], env=env, capture_output=True, text=True,
                               timeout=120)
            self.assertEqual(r.returncode, 0, r.stderr[-2000:])
            self.assertIn("OK", r.stdout)

    def test_interval_minimum_is_five_minutes(self):
        self.assertEqual(m.Schedule(resync_minutes=5).resync_minutes, 5)
        with self.assertRaises(Exception):
            m.Schedule(resync_minutes=1)


if __name__ == "__main__":
    unittest.main()
