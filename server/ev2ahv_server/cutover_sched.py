"""Scheduled cutover (D8b): when a VM in "Scheduled for Cutover" may start, and what to do when it may not yet.

Pure functions, no store and no engine, so the rules are unit-tested without a lab. The live worker (live.py) and the
mock simulator (mock.py) both call tick_vm() for every scheduled VM on every tick.

State lives on the plan VM in the database (cutover_at, sched_resync), so it survives an appliance restart:
the worker simply reads it again on the next tick.

Rules, in order, for a VM whose cutover_at is set:
  1. resync is older than the plan's interval -> "resync" (keeps the copy fresh while waiting, and before the cutover);
  2. cutover_at is still in the future -> "wait";
  3. a cutover window is set and it is closed now -> postpone: cutover_at moves to the next opening, with an event;
  4. cutover_at is more than MISSED_AFTER seconds in the past (appliance was down, or the VM was not ready) -> "missed":
     the schedule is dropped, the VM goes back to Ready to Cutover, with a warning event. A late cutover nobody expects
     is worse than none;
  5. a prep-check FAIL appeared and the schedule was not made with force -> dropped like "missed";
  6. otherwise -> "start".
"""
import time
from datetime import datetime, timedelta
from typing import Callable, Dict, Optional
from zoneinfo import ZoneInfo

MISSED_AFTER = 30 * 60          # seconds past cutover_at after which a schedule is dropped instead of started
SCHEDULABLE = ("Ready to Cutover", "Tested", "Scheduled for Cutover")


def _zone(tz: Optional[str]) -> ZoneInfo:
    try:
        return ZoneInfo(tz or "Asia/Jakarta")
    except Exception:
        return ZoneInfo("UTC")


def parse_hhmm(s: str) -> int:
    """'HH:MM' to minutes after midnight. Raises ValueError on anything else."""
    try:
        h, mi = s.split(":")
        h, mi = int(h), int(mi)
    except Exception:
        raise ValueError(f"time must be HH:MM, got {s!r}")
    if not (0 <= h <= 23 and 0 <= mi <= 59):
        raise ValueError(f"time must be HH:MM, got {s!r}")
    return h * 60 + mi


def _minutes(local: datetime) -> int:
    return local.hour * 60 + local.minute


def window_open(now: float, window: Optional[Dict], tz: Optional[str]) -> bool:
    """True when no window is set, or `now` is inside it. start <= time < end; end < start wraps past midnight."""
    if not window:
        return True
    s, e = parse_hhmm(window["start"]), parse_hhmm(window["end"])
    cur = _minutes(datetime.fromtimestamp(now, _zone(tz)))
    return s <= cur < e if s < e else (cur >= s or cur < e)


def next_open(now: float, window: Optional[Dict], tz: Optional[str]) -> float:
    """First instant at or after `now` when the window is open. Wall-clock arithmetic in the zone, so a DST change
    moves the opening with the clock, not by 24 hours."""
    if window_open(now, window, tz):
        return now
    z = _zone(tz)
    s = parse_hhmm(window["start"])
    day = datetime.fromtimestamp(now, z).date()
    for add in (0, 1, 2):
        d = day + timedelta(days=add)
        cand = datetime(d.year, d.month, d.day, s // 60, s % 60, tzinfo=z)
        ts = cand.timestamp()
        if ts > now:
            return ts
    return now + 86400   # unreachable


def fmt_local(t: float, tz: Optional[str]) -> str:
    return datetime.fromtimestamp(t, _zone(tz)).strftime("%Y-%m-%d %H:%M %Z")


def validate_window(window: Optional[Dict]) -> None:
    if window:
        s, e = parse_hhmm(window["start"]), parse_hhmm(window["end"])
        if s == e:
            raise ValueError("cutover window start and end must differ")


def tick_vm(plan: Dict, v: Dict, now: Optional[float] = None, event: Callable = lambda *a, **k: None) -> str:
    """Decide what a Scheduled for Cutover VM does now. Mutates the VM dict for postpone / missed / start and returns
    one of "wait", "resync", "postponed", "missed", "start". The caller launches the resync and the cutover."""
    now = time.time() if now is None else now
    sched = plan.get("schedule") or {}
    tz, window = sched.get("timezone"), sched.get("cutover_window")
    at = v.get("cutover_at")
    name = v.get("name")
    if not at:   # status says scheduled but nothing to wait for: a stale record, fall back to Ready
        v.update(status="Ready to Cutover", sched_resync=None)
        event("warning", f"Scheduled cutover of {name} had no time set; back to Ready to Cutover")
        return "missed"
    interval = 60 * int(sched.get("resync_minutes") or 60)
    stale = now - (v.get("last_sync_at") or 0) > interval
    if now < at:
        return "resync" if stale else "wait"
    if window and not window_open(now, window, tz):
        nxt = next_open(now, window, tz)
        v.update(cutover_postponed_from=v.get("cutover_postponed_from") or at, cutover_at=nxt, sched_resync=None)
        event("warning", f"Scheduled cutover of {name} is outside the cutover window "
                         f"({window['start']}-{window['end']} {tz}); postponed to {fmt_local(nxt, tz)}")
        return "postponed"
    if now > at + MISSED_AFTER and not v.get("sched_resync"):
        v.update(status=v.get("sched_from") or "Ready to Cutover", cutover_at=None, sched_resync=None,
                 cutover_postponed_from=None)
        event("warning", f"Scheduled cutover of {name} for {fmt_local(at, tz)} was missed "
                         f"(the appliance was down or the VM was not ready); not started. Schedule it again")
        return "missed"
    if stale:
        v["sched_resync"] = True   # we are the ones delaying the start: do not call that "missed" afterwards
        return "resync"
    fails = [c["name"] for c in v.get("checks") or [] if c.get("status") == "FAIL"]
    if fails and not v.get("sched_force"):
        v.update(status=v.get("sched_from") or "Ready to Cutover", cutover_at=None, sched_resync=None,
                 cutover_postponed_from=None)
        event("error", f"Scheduled cutover of {name} not started: prep-check FAIL ({', '.join(fails)}). "
                       "Fix it and schedule again, or cut over with force")
        return "missed"
    v.update(status="Cutover in Progress", phase_t=now, error=None, cutover_at=None, sched_resync=None,
             cutover_postponed_from=None, force=v.get("sched_force") or None, sched_force=None)
    event("info", f"Scheduled cutover of {name} starting (set for {fmt_local(at, tz)})")
    return "start"
