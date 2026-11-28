"""Bandwidth policies (D28): which read limit applies to a seed or resync right now.

Limits are MiB/s of source reads (the unit the engine and helper use); 0 means unlimited. The choice, in order:
  1. the plan's own override (plan["schedule"]["max_mbps"]) when it is set;
  2. the policies that are active now (their schedule, read in the appliance timezone) and apply to the plan's
     source environment or to every environment. A policy for the plan's own environment beats a global one;
     among equals the lowest limit wins (unlimited counts as the highest);
  3. DEFAULT_MBPS (100 MiB/s, the recommendation in docs/runbook.md section 7).
"""
import datetime, os

try:
    from zoneinfo import ZoneInfo
except ImportError:   # pragma: no cover
    ZoneInfo = None

DEFAULT_MBPS = 100
DEFAULT_TZ = "Asia/Jakarta"
FREQUENCIES = ("Always", "Weekdays 08:00-18:00", "Nights 18:00-08:00", "Weekends")


def local_now(tz_name, now=None):
    """`now` (epoch seconds, default the clock) as a datetime in the appliance timezone."""
    ts = datetime.datetime.now(datetime.timezone.utc) if now is None else \
        datetime.datetime.fromtimestamp(now, datetime.timezone.utc)
    for name in (tz_name, DEFAULT_TZ):
        try:
            return ts.astimezone(ZoneInfo(name))
        except Exception:
            continue
    return ts.astimezone(datetime.timezone(datetime.timedelta(hours=7)))   # WIB when no tz database is installed


def is_active(frequency, when):
    """Is the schedule `frequency` in force at the local datetime `when`? Windows start inclusive, end exclusive."""
    weekday, hour = when.weekday(), when.hour   # Monday is 0
    if frequency == "Always":
        return True
    if frequency == "Weekdays 08:00-18:00":
        return weekday < 5 and 8 <= hour < 18
    if frequency == "Nights 18:00-08:00":   # every night: from 18:00 to the next 08:00
        return hour >= 18 or hour < 8
    if frequency == "Weekends":
        return weekday >= 5
    return False


def _rank(limit):
    return float("inf") if not limit else limit


def effective_limit(settings, source_id=None, plan=None, now=None):
    """Return {"limit_mbps": float, "unlimited": bool, "origin": "plan"|"policy"|"default", "policy": name|None}."""
    settings = settings or {}
    override = ((plan or {}).get("schedule") or {}).get("max_mbps")
    if override is not None:
        return _out(override, "plan", "plan override")
    when = local_now(settings.get("timezone") or DEFAULT_TZ, now)
    hits = [p for p in settings.get("bandwidth_policies") or []
            if p.get("source_id") in (None, "", source_id) and is_active(p.get("frequency"), when)]
    if hits:
        own = [p for p in hits if p.get("source_id") and p.get("source_id") == source_id]
        pick = min(own or hits, key=lambda p: _rank(p.get("limit_mbps")))
        return _out(pick.get("limit_mbps") or 0, "policy", pick.get("name"))
    return _out(DEFAULT_MBPS, "default", None)


def _out(limit, origin, policy):
    limit = max(0.0, float(limit or 0))
    return {"limit_mbps": limit, "unlimited": limit == 0, "origin": origin, "policy": policy}


def write_rate_file(path, limit_mbps):
    """Atomically store the limit for a running engine step; returns True when the file changed."""
    text = f"{limit_mbps:g}\n"
    try:
        with open(path) as fh:
            if fh.read() == text:
                return False
    except OSError:
        pass
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path + ".tmp", "w") as fh:
        fh.write(text)
    os.replace(path + ".tmp", path)
    return True


def validate_policies(policies, source_ids=None):
    """Return a clean list or raise ValueError with a message for the user."""
    if not isinstance(policies, list):
        raise ValueError("bandwidth_policies must be a list")
    out = []
    for p in policies:
        if not isinstance(p, dict) or not str(p.get("name") or "").strip():
            raise ValueError("every bandwidth policy needs a name")
        if p.get("frequency") not in FREQUENCIES:
            raise ValueError(f"policy {p.get('name')!r}: frequency must be one of {', '.join(FREQUENCIES)}")
        try:
            limit = float(p.get("limit_mbps") or 0)
        except (TypeError, ValueError):
            raise ValueError(f"policy {p.get('name')!r}: the limit must be a number (0 = unlimited)")
        if limit < 0 or limit > 100000:
            raise ValueError(f"policy {p.get('name')!r}: the limit must be between 0 (unlimited) and 100000 MiB/s")
        out.append({**p, "name": str(p["name"]).strip(), "source_id": p.get("source_id") or "",
                    "source_name": p.get("source_name") or "", "limit_mbps": limit if limit != int(limit) else int(limit)})
    return out
