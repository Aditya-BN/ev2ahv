"""D28: bandwidth policies are applied to seeds and resyncs; D30: name guard default and test-network refusal."""
import datetime, os, subprocess, sys, tempfile, textwrap, unittest
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path[:0] = [ROOT, os.path.join(ROOT, "server")]
from ev2ahv_server import bandwidth as bw, live, models as m  # noqa: E402
from ev2ahv import transport  # noqa: E402

JKT = datetime.timezone(datetime.timedelta(hours=7))


def at(y, mo, d, h, mi=0, tz=JKT):
    """Epoch seconds of a wall-clock time. 2026-11-23 is a Monday."""
    return datetime.datetime(y, mo, d, h, mi, tzinfo=tz).timestamp()


def pol(name, freq, limit, source=""):
    return {"id": name, "name": name, "source_id": source, "source_name": "", "frequency": freq, "limit_mbps": limit}


class ScheduleTest(unittest.TestCase):
    def active(self, freq, ts, tz="Asia/Jakarta"):
        return bw.is_active(freq, bw.local_now(tz, ts))

    def test_weekday_window_boundaries(self):
        f = "Weekdays 08:00-18:00"
        self.assertFalse(self.active(f, at(2026, 11, 23, 7, 59)))
        self.assertTrue(self.active(f, at(2026, 11, 23, 8, 0)))       # start is inclusive
        self.assertTrue(self.active(f, at(2026, 11, 23, 17, 59)))
        self.assertFalse(self.active(f, at(2026, 11, 23, 18, 0)))     # end is exclusive
        self.assertTrue(self.active(f, at(2026, 11, 27, 12)))         # Friday
        self.assertFalse(self.active(f, at(2026, 11, 28, 12)))        # Saturday

    def test_night_window_crosses_midnight_every_day(self):
        f = "Nights 18:00-08:00"
        self.assertFalse(self.active(f, at(2026, 11, 23, 17, 59)))
        self.assertTrue(self.active(f, at(2026, 11, 23, 18, 0)))
        self.assertTrue(self.active(f, at(2026, 11, 24, 0, 0)))
        self.assertTrue(self.active(f, at(2026, 11, 24, 7, 59)))
        self.assertFalse(self.active(f, at(2026, 11, 24, 8, 0)))
        self.assertTrue(self.active(f, at(2026, 11, 28, 3)))          # Saturday night

    def test_weekend_starts_at_midnight_saturday_and_ends_at_midnight_monday(self):
        f = "Weekends"
        self.assertFalse(self.active(f, at(2026, 11, 27, 23, 59)))
        self.assertTrue(self.active(f, at(2026, 11, 28, 0, 0)))
        self.assertTrue(self.active(f, at(2026, 11, 29, 23, 59)))
        self.assertFalse(self.active(f, at(2026, 11, 30, 0, 0)))

    def test_always_and_unknown(self):
        self.assertTrue(self.active("Always", at(2026, 11, 23, 3)))
        self.assertFalse(self.active("Fortnightly", at(2026, 11, 23, 3)))

    def test_schedule_is_read_in_the_appliance_timezone(self):
        # 2026-11-23 07:30 UTC is 14:30 Jakarta but 02:30 New York: inside a weekday window only in Jakarta
        ts = at(2026, 11, 23, 7, 30, datetime.timezone.utc)
        self.assertTrue(self.active("Weekdays 08:00-18:00", ts, "Asia/Jakarta"))
        self.assertFalse(self.active("Weekdays 08:00-18:00", ts, "America/New_York"))
        # the day can differ too: Friday 20:00 UTC is already Saturday in Jakarta
        ts = at(2026, 11, 27, 20, 0, datetime.timezone.utc)
        self.assertTrue(self.active("Weekends", ts, "Asia/Jakarta"))
        self.assertFalse(self.active("Weekends", ts, "UTC"))

    def test_bad_timezone_falls_back_to_jakarta(self):
        self.assertEqual(bw.local_now("Mars/Olympus", at(2026, 11, 23, 12)).utcoffset(), datetime.timedelta(hours=7))


class EffectiveLimitTest(unittest.TestCase):
    NOON = at(2026, 11, 23, 12)    # Monday noon Jakarta
    NIGHT = at(2026, 11, 23, 22)

    def test_no_policy_gives_100_mib_per_second(self):
        r = bw.effective_limit({}, "s1", {}, self.NOON)
        self.assertEqual((r["limit_mbps"], r["origin"], r["unlimited"]), (100, "default", False))

    def test_policy_applies_only_inside_its_window(self):
        s = {"bandwidth_policies": [pol("day", "Weekdays 08:00-18:00", 20)]}
        self.assertEqual(bw.effective_limit(s, "s1", {}, self.NOON)["limit_mbps"], 20)
        self.assertEqual(bw.effective_limit(s, "s1", {}, self.NIGHT)["origin"], "default")

    def test_day_and_night_policies_switch_at_the_boundary(self):
        s = {"bandwidth_policies": [pol("day", "Weekdays 08:00-18:00", 20), pol("night", "Nights 18:00-08:00", 0)]}
        self.assertEqual(bw.effective_limit(s, None, {}, at(2026, 11, 23, 17, 59))["limit_mbps"], 20)
        r = bw.effective_limit(s, None, {}, at(2026, 11, 23, 18, 0))
        self.assertTrue(r["unlimited"])
        self.assertEqual((r["limit_mbps"], r["policy"]), (0, "night"))

    def test_environment_policy_beats_global(self):
        s = {"bandwidth_policies": [pol("all", "Always", 10), pol("mine", "Always", 50, "s1")]}
        self.assertEqual(bw.effective_limit(s, "s1", {}, self.NOON)["policy"], "mine")
        self.assertEqual(bw.effective_limit(s, "s2", {}, self.NOON)["policy"], "all")   # other environment: global only

    def test_policy_for_another_environment_does_not_apply(self):
        s = {"bandwidth_policies": [pol("other", "Always", 5, "s9")]}
        self.assertEqual(bw.effective_limit(s, "s1", {}, self.NOON)["origin"], "default")
        self.assertEqual(bw.effective_limit(s, None, {}, self.NOON)["origin"], "default")

    def test_overlap_takes_the_lowest_limit_and_unlimited_loses(self):
        s = {"bandwidth_policies": [pol("a", "Always", 0), pol("b", "Always", 30), pol("c", "Always", 60)]}
        self.assertEqual(bw.effective_limit(s, "s1", {}, self.NOON)["policy"], "b")

    def test_plan_override_wins_including_unlimited(self):
        s = {"bandwidth_policies": [pol("day", "Always", 20)]}
        self.assertEqual(bw.effective_limit(s, "s1", {"schedule": {"max_mbps": 7}}, self.NOON)["limit_mbps"], 7)
        r = bw.effective_limit(s, "s1", {"schedule": {"max_mbps": 0}}, self.NOON)
        self.assertEqual((r["unlimited"], r["origin"]), (True, "plan"))
        self.assertEqual(bw.effective_limit(s, "s1", {"schedule": {"max_mbps": None}}, self.NOON)["origin"], "policy")

    def test_settings_timezone_moves_the_window(self):
        s = {"timezone": "UTC", "bandwidth_policies": [pol("day", "Weekdays 08:00-18:00", 20)]}
        ts = at(2026, 11, 23, 7, 30, datetime.timezone.utc)   # 07:30 UTC: outside; 14:30 in Jakarta: inside
        self.assertEqual(bw.effective_limit(s, None, {}, ts)["origin"], "default")
        self.assertEqual(bw.effective_limit(dict(s, timezone="Asia/Jakarta"), None, {}, ts)["origin"], "policy")


class ValidateTest(unittest.TestCase):
    def test_accepts_zero_as_unlimited_and_global(self):
        out = bw.validate_policies([{"id": "a", "name": " x ", "frequency": "Always", "limit_mbps": 0}])
        self.assertEqual((out[0]["name"], out[0]["limit_mbps"], out[0]["source_id"]), ("x", 0, ""))

    def test_rejects_bad_input(self):
        for bad in ([{"name": "x", "frequency": "Sometimes", "limit_mbps": 1}],
                    [{"name": "x", "frequency": "Always", "limit_mbps": -1}],
                    [{"name": "x", "frequency": "Always", "limit_mbps": "fast"}],
                    [{"name": "", "frequency": "Always", "limit_mbps": 1}], "nope"):
            with self.assertRaises(ValueError):
                bw.validate_policies(bad)


class RateFileTest(unittest.TestCase):
    def test_write_is_idempotent(self):
        with tempfile.TemporaryDirectory() as t:
            p = os.path.join(t, "sub", "v.rate")
            self.assertTrue(bw.write_rate_file(p, 100))
            self.assertFalse(bw.write_rate_file(p, 100))
            self.assertTrue(bw.write_rate_file(p, 0))
            self.assertEqual(open(p).read(), "0\n")


class EnginePlanTest(unittest.TestCase):
    """The limit reaches the engine plan at the start of every pass, and the rate file is written."""
    def build(self, settings, plan_extra=None):
        tmp = tempfile.TemporaryDirectory(); self.addCleanup(tmp.cleanup)
        old = live.DATA; live.DATA = tmp.name; self.addCleanup(setattr, live, "DATA", old)
        orig = live.everrun.discover
        live.everrun.discover = lambda *_: {"name": "ev2ahv-src-x", "active_host": "10.0.0.1", "vcpus": 2, "memory_mib": 2048,
            "firmware": "bios", "disks": [{"dev": "vda", "path": "/mnt/x/a.img", "guest_bytes": 1 << 30}], "nics": []}
        self.addCleanup(setattr, live.everrun, "discover", orig)
        plan = {"id": "p1", "name": "n", "vm_settings": {}, "prep": {"mode": "manual"}, "network_map": [], **(plan_extra or {})}
        src = {"id": "s1", "type": "everrun", "node_addresses": ["10.0.0.1"], "address": "10.0.0.9", "username": "u"}
        path = live.engine_plan(plan, {"id": "v", "name": "ev2ahv-src-x"}, src, {"os_type": "linux", "nics": []},
                                lambda k: None, settings)
        return live.planlib.load(path)["source"], live.rate_path(plan, {"id": "v"})

    def test_default_without_policy(self):
        src, rate = self.build({})
        self.assertEqual(src["max_mbps"], 100)
        self.assertEqual(open(rate).read().strip(), "100")
        self.assertEqual(src["rate_file"], rate)

    def test_policy_and_plan_override(self):
        s = {"bandwidth_policies": [pol("always", "Always", 25, "s1")]}
        self.assertEqual(self.build(s)[0]["max_mbps"], 25)
        src, rate = self.build(s, {"schedule": {"max_mbps": 0}})
        self.assertEqual(src["max_mbps"], 0)
        self.assertEqual(open(rate).read().strip(), "0")


class FakeT:
    def setup(self): pass


class Pipe:
    def __init__(self): self.sent = b""
    def write(self, b): self.sent += b
    def flush(self): pass


class MidPassTest(unittest.TestCase):
    """The engine picks up a new limit between batches and tells the helper."""
    def endpoint(self, path, chunk=4 * 1024 ** 2):
        ep = transport.BlockEndpoint(FakeT(), "/dev/x", chunk=chunk, max_mbps=100)
        ep.rate_file = path
        return ep

    def test_current_rate_follows_the_file_and_survives_garbage(self):
        with tempfile.TemporaryDirectory() as t:
            p = os.path.join(t, "r")
            bw.write_rate_file(p, 100)
            ep = self.endpoint(p)
            self.assertEqual(ep.current_rate(now=1000, every=2), 100)
            bw.write_rate_file(p, 5)
            self.assertEqual(ep.current_rate(now=1001, every=2), 100)   # inside the 2 s window: not re-read yet
            self.assertEqual(ep.current_rate(now=1003, every=2), 5)
            open(p, "w").write("junk")
            self.assertEqual(ep.current_rate(now=1010, every=2), 5)     # unreadable: keep the last limit
            bw.write_rate_file(p, 0)
            self.assertEqual(ep.current_rate(now=1020, every=2), 0)

    def test_read_sends_rate_line_on_change_and_splits_slow_batches(self):
        import struct
        with tempfile.TemporaryDirectory() as t:
            p = os.path.join(t, "r")
            bw.write_rate_file(p, 100)
            ep = self.endpoint(p)
            pipe = Pipe()

            class Proc:
                stdin = pipe
                class stdout:
                    pass
            frames = []

            def fake_read_exact(stream, n):
                return frames.pop(0)
            real = transport.read_exact
            transport.read_exact = fake_read_exact
            self.addCleanup(setattr, transport, "read_exact", real)
            ep._reader, ep._rate_seen = Proc, 100

            def feed(idx):
                for i in idx:
                    frames.extend([transport.FRAME.pack(i, 1), b"x"])
            feed([0, 1]); list(ep.read([0, 1]))
            self.assertEqual(pipe.sent, b"0\n1\n")                      # no change: no rate line
            bw.write_rate_file(p, 8)                                      # 8 MiB/s over 4 MiB chunks: 10 chunks per 5 s piece
            pipe.sent = b""; ep._rate_checked = 0
            feed(range(25)); list(ep.read(list(range(25))))
            lines = pipe.sent.decode().split("\n")
            self.assertEqual(lines[0], "rate 8.0")
            self.assertEqual(lines.count("rate 8.0"), 1)
            self.assertEqual([x for x in lines if x and not x.startswith("rate")], [str(i) for i in range(25)])

    def test_helper_throttle_changes_rate(self):
        from ev2ahv.helper import ev2ahv_helper as h
        t = h.Throttle(100)
        t.set_rate(0)
        self.assertEqual(t.rate, 0)
        t.set_rate(2)
        self.assertEqual(t.rate, 2 * 1024 * 1024)


class NameGuardDefaultTest(unittest.TestCase):
    def setUp(self):
        self.allow_any = live.ALLOW_ANY
        live.ALLOW_ANY = False
        self.addCleanup(setattr, live, "ALLOW_ANY", self.allow_any)

    def test_fresh_default_is_no_guard(self):
        old = live.GUARD_PREFIX
        live.GUARD_PREFIX = ""
        self.addCleanup(setattr, live, "GUARD_PREFIX", old)
        self.assertTrue(live.name_allowed("customer-db01", {}))
        self.assertTrue(live.name_allowed("customer-db01", None))

    def test_a_stored_prefix_always_wins(self):
        self.assertFalse(live.name_allowed("customer-db01", {"source_name_prefix": "ev2ahv-"}))
        self.assertTrue(live.name_allowed("ev2ahv-x", {"source_name_prefix": "ev2ahv-"}))


API_SCRIPT = textwrap.dedent("""
    import sys
    sys.path[:0] = [sys.argv[1], sys.argv[1] + "/server"]
    from fastapi import HTTPException
    from ev2ahv_server import api, models as m
    s = api.store
    # fresh appliance: nothing stored, nothing configured -> no guard, and the migration stores nothing
    assert api.current_settings()["source_name_prefix"] == "", api.current_settings()
    assert api.migrate_name_guard(s) is False and s.get("settings", "settings") is None
    # upgrade: a settings doc saved by an old version has no prefix key -> keeps ev2ahv-
    s.put("settings", {"id": "settings", "resync_minutes": 30, "bandwidth_policies": [], "timezone": "Asia/Jakarta"})
    assert api.migrate_name_guard(s) is True
    assert s.get("settings", "settings")["source_name_prefix"] == "ev2ahv-"
    assert s.get("settings", "settings")["resync_minutes"] == 30
    assert api.current_settings()["source_name_prefix"] == "ev2ahv-"
    assert api.migrate_name_guard(s) is False
    # a stored value, even empty, is never touched
    s.put("settings", {"id": "settings", "source_name_prefix": ""})
    assert api.migrate_name_guard(s) is False and s.get("settings", "settings")["source_name_prefix"] == ""
    s.put("settings", {"id": "settings", "source_name_prefix": "prod-"})
    assert api.migrate_name_guard(s) is False and api.current_settings()["source_name_prefix"] == "prod-"
    # no settings doc but an environment exists: in use, keeps the old guard
    s.delete("settings", "settings")
    s.put("provider", {"id": "p", "name": "x", "role": "source"})
    assert api.migrate_name_guard(s) is True and api.current_settings()["source_name_prefix"] == "ev2ahv-"
    # bandwidth policies are validated on save
    api.put_settings({"bandwidth_policies": [{"id": "a", "name": "n", "frequency": "Always", "limit_mbps": 0}]}, who="t")
    assert api.current_settings()["bandwidth_policies"][0]["limit_mbps"] == 0
    try:
        api.put_settings({"bandwidth_policies": [{"name": "n", "frequency": "Sometimes", "limit_mbps": 1}]}, who="t")
        raise SystemExit("bad policy accepted")
    except HTTPException as e:
        assert e.status_code == 422
    # the plan output carries the effective limit
    vm = {"id": "a", "name": "ev2ahv-a", "status": "Ready to Cutover", "checks": []}
    nm = lambda t: [{"source_network": "n", "target_subnet": "prod", "test_subnet": t}]
    doc = {"id": "p1", "name": "p", "created_by": "t", "created_at": 1.0, "started_at": 2.0, "prep": {},
           "schedule": {"resync_minutes": 60}, "source": {"id": "x", "name": "x"}, "vms": [vm], "network_map": nm(None)}
    s.put("plan", api.finish(doc))
    out = api.plans(who="t")["entities"][0] if isinstance(api.plans(who="t"), dict) else api.plans(who="t")[0]
    assert out.bandwidth.limit_mbps == 0 and out.bandwidth.origin == "policy", out.bandwidth
    # D30b: Test without a test network is refused; with one it is accepted
    try:
        api.plan_action("p1", m.ActionIn(action="test", vm_ids=["a"]), who="t")
        raise SystemExit("test without a test network was accepted")
    except HTTPException as e:
        assert e.status_code == 409 and "no test network" in e.detail, e.detail
    assert s.get("plan", "p1")["vms"][0]["status"] == "Ready to Cutover"
    doc["network_map"] = nm("isolated")
    s.put("plan", api.finish(doc))
    api.plan_action("p1", m.ActionIn(action="test", vm_ids=["a"]), who="t")
    assert s.get("plan", "p1")["vms"][0]["status"] == "Testing"
    print("OK")
""")


class ApiTest(unittest.TestCase):
    def test_settings_migration_policy_output_and_test_refusal(self):
        with tempfile.TemporaryDirectory() as t:
            env = dict(os.environ, EV2AHV_SERVER_MODE="mock", EV2AHV_DATA=t, EV2AHV_SERVER_DB=os.path.join(t, "s.db"),
                       EV2AHV_CONFIG_DIR=os.path.join(t, "cfg"), EV2AHV_ETC=os.path.join(t, "etc"), HOME=t)
            env.pop("EV2AHV_DEFAULT_NAME_PREFIX", None)
            r = subprocess.run([sys.executable, "-c", API_SCRIPT, ROOT], env=env, capture_output=True, text=True)
            self.assertEqual(r.stdout.strip(), "OK", r.stdout + r.stderr)


if __name__ == "__main__":
    unittest.main()
