"""D8b: scheduled cutover. Rules (cutover_sched), the live worker's use of them across a restart, the API, and the
mock simulator. No lab, no engine: the engine step is a stub."""
import os, subprocess, sys, tempfile, textwrap, threading, time, unittest
from datetime import datetime
from zoneinfo import ZoneInfo

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path[:0] = [ROOT, os.path.join(ROOT, "server")]
from ev2ahv_server import cutover_sched as cs, models as m  # noqa: E402

JKT = ZoneInfo("Asia/Jakarta")
HOUR = 3600


def ts(y, mo, d, h, mi, tz=JKT):
    return datetime(y, mo, d, h, mi, tzinfo=tz).timestamp()


def plan(window=None, tz="Asia/Jakarta", resync=60):
    return {"id": "p1", "name": "p", "schedule": {"timezone": tz, "resync_minutes": resync, "cutover_window": window}}


def vm(at, last_sync, **kw):
    return {"id": "a", "name": "ev2ahv-a", "status": "Scheduled for Cutover", "cutover_at": at, "last_sync_at": last_sync,
            "checks": [], **kw}


class Rules(unittest.TestCase):
    def setUp(self):
        self.events = []
        self.ev = lambda sev, msg: self.events.append((sev, msg))

    def test_waits_until_the_time(self):
        now = ts(2026, 12, 5, 1, 0)
        v = vm(now + 600, now - 60)
        self.assertEqual(cs.tick_vm(plan(), v, now, self.ev), "wait")
        self.assertEqual(v["status"], "Scheduled for Cutover")

    def test_starts_at_the_time_when_the_sync_is_recent(self):
        now = ts(2026, 12, 5, 1, 0)
        v = vm(now - 5, now - 600)
        self.assertEqual(cs.tick_vm(plan(), v, now, self.ev), "start")
        self.assertEqual(v["status"], "Cutover in Progress")
        self.assertIsNone(v["cutover_at"])
        self.assertTrue(any("starting" in m_ for _, m_ in self.events))

    def test_old_sync_runs_a_resync_first_then_starts_even_when_the_resync_overruns_the_miss_limit(self):
        now = ts(2026, 12, 5, 1, 0)
        v = vm(now - 5, now - 3 * HOUR)
        self.assertEqual(cs.tick_vm(plan(), v, now, self.ev), "resync")
        self.assertTrue(v["sched_resync"])
        # resync took 45 min: later than MISSED_AFTER, but we caused the delay
        v["last_sync_at"] = now + 45 * 60
        self.assertEqual(cs.tick_vm(plan(), v, now + 46 * 60, self.ev), "start")

    def test_keeps_the_copy_fresh_while_waiting(self):
        now = ts(2026, 12, 5, 1, 0)
        v = vm(now + 5 * HOUR, now - 2 * HOUR)
        self.assertEqual(cs.tick_vm(plan(), v, now, self.ev), "resync")
        self.assertFalse(v.get("sched_resync"))   # not for the cutover yet

    def test_restart_after_the_time_is_missed_not_started_late(self):
        at = ts(2026, 12, 5, 1, 0)
        v = vm(at, at - 600, sched_from="Tested")
        self.assertEqual(cs.tick_vm(plan(), v, at + 2 * HOUR, self.ev), "missed")   # appliance was down 2 h
        self.assertEqual((v["status"], v["cutover_at"]), ("Tested", None))
        self.assertTrue(any(s == "warning" and "missed" in m_ for s, m_ in self.events))

    def test_restart_shortly_after_the_time_still_starts(self):
        at = ts(2026, 12, 5, 1, 0)
        v = vm(at, at - 600)
        self.assertEqual(cs.tick_vm(plan(), v, at + 5 * 60, self.ev), "start")

    def test_window_postpones_with_an_event(self):
        win = {"start": "22:00", "end": "04:00"}
        at = ts(2026, 12, 5, 14, 0)    # 14:00 WIB, window closed
        v = vm(at, at - 60)
        self.assertEqual(cs.tick_vm(plan(win), v, at + 1, self.ev), "postponed")
        self.assertEqual(v["cutover_at"], ts(2026, 12, 5, 22, 0))
        self.assertEqual(v["cutover_postponed_from"], at)
        self.assertEqual(v["status"], "Scheduled for Cutover")
        self.assertTrue(any("postponed" in m_ for _, m_ in self.events))
        # later, inside the window: starts
        v["last_sync_at"] = ts(2026, 12, 5, 21, 30)
        self.assertEqual(cs.tick_vm(plan(win), v, ts(2026, 12, 5, 22, 0) + 1, self.ev), "start")

    def test_window_overrun_after_a_resync_postpones_again(self):
        win = {"start": "01:00", "end": "02:00"}
        at = ts(2026, 12, 5, 1, 50)
        v = vm(at, at - 3 * HOUR)
        self.assertEqual(cs.tick_vm(plan(win), v, at + 1, self.ev), "resync")
        v["last_sync_at"] = ts(2026, 12, 5, 2, 10)
        self.assertEqual(cs.tick_vm(plan(win), v, ts(2026, 12, 5, 2, 11), self.ev), "postponed")
        self.assertEqual(v["cutover_at"], ts(2026, 12, 6, 1, 0))
        self.assertFalse(v.get("sched_resync"))

    def test_window_math_and_wrap(self):
        win = {"start": "22:00", "end": "04:00"}
        self.assertTrue(cs.window_open(ts(2026, 12, 5, 23, 0), win, "Asia/Jakarta"))
        self.assertTrue(cs.window_open(ts(2026, 12, 6, 3, 59), win, "Asia/Jakarta"))
        self.assertFalse(cs.window_open(ts(2026, 12, 6, 4, 0), win, "Asia/Jakarta"))
        self.assertTrue(cs.window_open(1.0, None, "Asia/Jakarta"))
        day = {"start": "01:00", "end": "05:00"}
        self.assertEqual(cs.next_open(ts(2026, 12, 5, 6, 0), day, "Asia/Jakarta"), ts(2026, 12, 6, 1, 0))
        self.assertEqual(cs.next_open(ts(2026, 12, 5, 0, 30), day, "Asia/Jakarta"), ts(2026, 12, 5, 1, 0))

    def test_window_uses_the_plan_timezone_not_the_server_clock(self):
        # 20:00 UTC is 03:00 WIB (inside 01-05 WIB) and 21:00 in Berlin (outside)
        now = datetime(2026, 12, 5, 20, 0, tzinfo=ZoneInfo("UTC")).timestamp()
        win = {"start": "01:00", "end": "05:00"}
        self.assertTrue(cs.window_open(now, win, "Asia/Jakarta"))
        self.assertFalse(cs.window_open(now, win, "Europe/Berlin"))

    def test_dst_zone_wall_clock(self):
        # Europe/Berlin leaves summer time on 2026-10-25: 02:00 CEST -> 02:00 CET. A 01:00 window stays 01:00 local.
        berlin = ZoneInfo("Europe/Berlin")
        win = {"start": "01:00", "end": "02:00"}
        now = ts(2026, 10, 24, 12, 0, berlin)
        nxt = cs.next_open(now, win, "Europe/Berlin")
        self.assertEqual(nxt, ts(2026, 10, 25, 1, 0, berlin))
        nxt2 = cs.next_open(ts(2026, 10, 25, 12, 0, berlin), win, "Europe/Berlin")
        self.assertEqual(nxt2, ts(2026, 10, 26, 1, 0, berlin))
        self.assertEqual((nxt2 - nxt) / HOUR, 25)   # the day had 25 hours, the window did not drift

    def test_failed_prep_check_blocks_a_scheduled_start_unless_forced(self):
        now = ts(2026, 12, 5, 1, 0)
        bad = [{"name": "Disk", "status": "FAIL"}]
        v = vm(now - 1, now - 60, checks=bad, sched_from="Ready to Cutover")
        self.assertEqual(cs.tick_vm(plan(), v, now, self.ev), "missed")
        self.assertEqual(v["status"], "Ready to Cutover")
        v = vm(now - 1, now - 60, checks=bad, sched_force=True)
        self.assertEqual(cs.tick_vm(plan(), v, now, self.ev), "start")
        self.assertTrue(v["force"])

    def test_models_validate_the_window(self):
        self.assertEqual(m.CutoverWindow(start="22:00", end="04:00").end, "04:00")
        for bad in ({"start": "25:00", "end": "04:00"}, {"start": "22:00", "end": "22:00"}, {"start": "9:00", "end": "10:00"}):
            with self.assertRaises(Exception):
                m.CutoverWindow(**bad)
        self.assertIsNone(m.Schedule().cutover_window)
        self.assertEqual(m.VmSettings().rollback_source, "start")
        self.assertFalse(m.VmSettings().post_cutover_cleanup)


class FakeStore:
    def __init__(self):
        self.docs, self.events = {}, []

    def get(self, kind, id_):
        return self.docs.get(id_)

    def put(self, kind, doc):
        self.docs[doc["id"]] = doc

    def all(self, kind):
        return list(self.docs.values())

    def event(self, sev, msg, **kw):
        self.events.append((sev, msg))


class LiveWorker(unittest.TestCase):
    """The live worker keeps the schedule in the stored plan, so a new Live (an appliance restart) picks it up."""

    def make(self, store):
        from ev2ahv_server import live
        launched = []
        w = live.Live(store, threading.RLock(), lambda p: p, lambda: None)
        w.launch = lambda plan_, vm_, steps, on_done, force=False: launched.append((vm_["id"], steps))
        return w, launched

    def stored(self, at, last_sync):
        s = FakeStore()
        s.put("plan", {"id": "p1", "name": "p", "started_at": 1.0, "prep": {}, "schedule": {"timezone": "Asia/Jakarta",
                       "resync_minutes": 60}, "vms": [vm(at, last_sync)]})
        return s

    def test_restart_keeps_the_schedule_and_resyncs_first(self):
        now = time.time()
        s = self.stored(now + 30, now - 3 * HOUR)
        w1, l1 = self.make(s)
        w1.tick()
        self.assertEqual(l1, [("a", ["resync"])])          # stale: resync even before the time
        del w1                                              # appliance restart: new worker, same database
        s.docs["p1"]["vms"][0]["cutover_at"] = now - 1      # the time has now passed
        s.docs["p1"]["vms"][0]["last_sync_at"] = now - 60   # the resync finished
        w2, l2 = self.make(s)
        w2.tick()
        self.assertEqual(s.docs["p1"]["vms"][0]["status"], "Cutover in Progress")
        w2.tick()                                           # next tick launches the cutover step
        self.assertEqual(l2[-1][1][-1], "cutover")

    def test_cancelled_schedule_is_not_started(self):
        now = time.time()
        s = self.stored(now - 1, now - 60)
        s.docs["p1"]["vms"][0].update(status="Ready to Cutover", cutover_at=None)   # user cancelled between ticks
        w, launched = self.make(s)
        w.tick()
        self.assertEqual(s.docs["p1"]["vms"][0]["status"], "Ready to Cutover")

    def test_resync_for_a_schedule_keeps_it_scheduled(self):
        from ev2ahv_server import live
        v = vm(1.0, 1.0)
        live.Live._seeded({}, v, None)
        self.assertEqual(v["status"], "Scheduled for Cutover")
        v2 = {"status": "Ready to Cutover"}
        live.Live._seeded({}, v2, None)
        self.assertEqual(v2["status"], "Ready to Cutover")

    def test_cleanup_runs_once_after_cutover_only_when_asked(self):
        from ev2ahv_server import live
        p = {"prep": {"mode": "automatic"}, "vm_settings": {"a": {"post_cutover_cleanup": True}}}
        v = {"id": "a", "cutover_result": {"completed_at": 1}}
        self.assertTrue(live.Live._wants_cleanup(p, v))
        v["cleanup_done"] = True
        self.assertFalse(live.Live._wants_cleanup(p, v))
        self.assertFalse(live.Live._wants_cleanup({**p, "vm_settings": {}}, {"id": "a", "cutover_result": {}}))
        self.assertFalse(live.Live._wants_cleanup({**p, "prep": {"mode": "manual"}}, {"id": "a", "cutover_result": {"x": 1}}))


API = textwrap.dedent("""
    import sys, time
    sys.path[:0] = [sys.argv[1], sys.argv[1] + "/server"]
    from fastapi import HTTPException
    from ev2ahv_server import api, models as m
    now = time.time()
    vm = lambda i, s, **k: {"id": i, "name": "ev2ahv-" + i, "status": s, "last_sync_at": now, "checks": [], **k}
    doc = {"id": "p1", "name": "p", "created_by": "t", "created_at": 1.0, "started_at": 2.0, "prep": {},
           "schedule": {"resync_minutes": 60, "timezone": "Asia/Jakarta"},
           "vms": [vm("a", "Ready to Cutover"), vm("b", "Seeding"), vm("c", "Tested"),
                   vm("d", "Ready to Cutover", checks=[{"name": "Disk", "status": "FAIL", "detail": ""}])]}
    api.store.put("plan", api.finish(doc))
    def st(i): return {v["id"]: v for v in api.store.get("plan", "p1")["vms"]}[i]
    def fails(fn, code):
        try:
            fn()
        except HTTPException as e:
            assert e.status_code == code, (e.status_code, e.detail)
            return e
        raise SystemExit("expected HTTP %s" % code)

    at = now + 3600
    out = api.cutover_settings("p1", m.CutoverSettingsIn(vm_ids=["a", "c"], cutover_at=at, final_verify="sample",
                               rollback_source="leave_off", post_cutover_cleanup=True,
                               edit_window=True, cutover_window=m.CutoverWindow(start="22:00", end="04:00")), who="t")
    by = {v.id: v for v in out.vms}
    assert by["a"].status == "Scheduled for Cutover" and by["a"].cutover_at == at
    assert "unschedule" in by["a"].actions and "cutover" in by["a"].actions
    assert st("c")["sched_from"] == "Tested"
    vs = api.store.get("plan", "p1")["vm_settings"]["a"]
    assert (vs["final_verify"], vs["rollback_source"], vs["post_cutover_cleanup"]) == ("sample", "leave_off", True)
    assert out.schedule.cutover_window.start == "22:00"
    assert any("scheduled the cutover" in e["message"] for e in api.store.events(10))
    assert out.counts.ready_to_cutover == 3   # a, c scheduled + d ready

    fails(lambda: api.cutover_settings("p1", m.CutoverSettingsIn(vm_ids=["b"], cutover_at=at), who="t"), 409)  # seeding
    fails(lambda: api.cutover_settings("p1", m.CutoverSettingsIn(vm_ids=["a"], cutover_at=now - 7200), who="t"), 422)
    e = fails(lambda: api.cutover_settings("p1", m.CutoverSettingsIn(vm_ids=["d"], cutover_at=at), who="t"), 409)
    assert e.detail["failing"], e.detail
    api.cutover_settings("p1", m.CutoverSettingsIn(vm_ids=["d"], cutover_at=at, force=True), who="t")
    assert st("d")["sched_force"] is True

    # save settings only, no schedule
    api.cutover_settings("p1", m.CutoverSettingsIn(vm_ids=["b"], schedule=False, final_verify="none"), who="t")
    assert api.store.get("plan", "p1")["vm_settings"]["b"]["final_verify"] == "none" and st("b")["status"] == "Seeding"

    # edit the time of a scheduled VM, then cancel the schedule
    api.cutover_settings("p1", m.CutoverSettingsIn(vm_ids=["a"], cutover_at=at + 60), who="t")
    assert st("a")["cutover_at"] == at + 60 and st("a")["sched_from"] == "Ready to Cutover"
    api.plan_action("p1", m.ActionIn(action="unschedule", vm_ids=["a", "c"]), who="t")
    assert st("a")["status"] == "Ready to Cutover" and "cutover_at" not in st("a")
    assert st("c")["status"] == "Tested", st("c")
    fails(lambda: api.plan_action("p1", m.ActionIn(action="unschedule", vm_ids=["a"]), who="t"), 409)

    # a manual cutover replaces a schedule
    api.cutover_settings("p1", m.CutoverSettingsIn(vm_ids=["a"], cutover_at=at), who="t")
    api.plan_action("p1", m.ActionIn(action="cutover", vm_ids=["a"]), who="t")
    assert st("a")["status"] == "Cutover in Progress" and "cutover_at" not in st("a")

    # remove the window
    out = api.cutover_settings("p1", m.CutoverSettingsIn(vm_ids=["b"], schedule=False, edit_window=True), who="t")
    assert out.schedule.cutover_window is None
    print("OK")
""")

MOCK = textwrap.dedent("""
    import sys, time, threading
    sys.path[:0] = [sys.argv[1], sys.argv[1] + "/server"]
    from ev2ahv_server import api, mock, models as m
    mock.TICK = 0.05
    mock.seed(api.store)
    api.store.events(1)
    threading.Thread(target=mock.simulate, args=(api.store, api.lock, api.finish, api.bump), daemon=True).start()
    def vm():
        return {v["id"]: v for v in api.store.get("plan", "pl-wave2")["vms"]}["w-erp-app01"]
    assert vm()["status"] == "Ready to Cutover"
    api.cutover_settings("pl-wave2", m.CutoverSettingsIn(vm_ids=["w-erp-app01"], cutover_at=time.time() + 1.5,
                         post_cutover_cleanup=True), who="t")
    assert vm()["status"] == "Scheduled for Cutover"
    time.sleep(0.5)
    assert vm()["status"] == "Scheduled for Cutover"
    for _ in range(100):
        if vm()["status"] == "Cutover in Progress":
            break
        time.sleep(0.1)
    assert vm()["status"] == "Cutover in Progress", vm()["status"]
    with api.lock:   # skip the 15 s mock cutover
        p = api.store.get("plan", "pl-wave2")
        for v in p["vms"]:
            v["phase_t"] = 0
        api.store.put("plan", p)
    for _ in range(100):
        if vm().get("cleanup_done"):
            break
        time.sleep(0.1)
    assert vm()["status"] == "Completed" and vm().get("cleanup_done"), vm()
    msgs = [e["message"] for e in api.store.events(50)]
    assert any("Scheduled cutover of erp-app01 starting" in x for x in msgs), msgs
    assert any("Guest cleanup ran automatically" in x for x in msgs), msgs
    print("OK")
""")


def run_script(script):
    with tempfile.TemporaryDirectory() as t:
        env = dict(os.environ, EV2AHV_SERVER_MODE="mock", EV2AHV_DATA=t, EV2AHV_SERVER_DB=os.path.join(t, "s.db"),
                   EV2AHV_CONFIG_DIR=os.path.join(t, "cfg"), EV2AHV_ETC=os.path.join(t, "etc"), HOME=t)
        return subprocess.run([sys.executable, "-B", "-c", script, ROOT], env=env, capture_output=True, text=True,
                              timeout=120)


class ApiAndMock(unittest.TestCase):
    def test_api_schedule_edit_cancel(self):
        r = run_script(API)
        self.assertEqual(r.returncode, 0, r.stderr[-2500:] + r.stdout[-500:])
        self.assertIn("OK", r.stdout)

    def test_mock_simulator_runs_the_scheduled_flow(self):
        r = run_script(MOCK)
        self.assertEqual(r.returncode, 0, r.stderr[-2500:] + r.stdout[-500:])
        self.assertIn("OK", r.stdout)


if __name__ == "__main__":
    unittest.main()
