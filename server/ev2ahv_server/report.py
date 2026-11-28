"""Migration report per plan (or one VM): a customer-grade summary as printable HTML, or CSV (one row per VM).

PDF: the appliance has no PDF renderer and none is added (no heavy dependencies). format=pdf serves the same
print-ready HTML with a "Print / Save as PDF" button; the browser makes the PDF.
Everything text goes through the support-bundle redaction (appliance.redact) before it is rendered.
"""
import csv, html, io, re, time
from datetime import datetime, timezone

try:
    from zoneinfo import ZoneInfo
except ImportError:   # pragma: no cover
    ZoneInfo = None

from . import appliance

STEP_LABEL = {"seed": "Seed", "resync": "Resync", "cutover": "Cutover", "rollback": "Rollback", "test": "Test migration",
              "guest-prep": "Guest preparation", "prep-check": "Prep-check", "discard-target": "Discard target VM",
              "discard-test": "Discard test VM"}
STARTED = re.compile(r"^([\w-]+) started$")
FINISHED = re.compile(r"^([\w+ -]+) finished$")
FAILED = re.compile(r"^([\w-]+) failed: (.*)$")
SKIP = re.compile(r"^(Mock data loaded|Prep-check requested)")
BRAND = "#6750a4"


def fmt_bytes(n):
    n = float(n or 0)
    for u in ("B", "KiB", "MiB", "GiB", "TiB"):
        if n < 1024 or u == "TiB":
            return f"{int(n)} B" if u == "B" else f"{n:.1f} {u}"
        n /= 1024


def fmt_dur(sec):
    if sec is None:
        return "-"
    sec = int(round(sec))
    h, r = divmod(sec, 3600)
    m_, s = divmod(r, 60)
    return (f"{h}h " if h else "") + (f"{m_}m " if h or m_ else "") + f"{s}s"


def _tz(name):
    try:
        return ZoneInfo(name) if ZoneInfo else timezone.utc
    except Exception:
        return timezone.utc


def fmt_time(ts, tz):
    if not ts:
        return "-"
    return datetime.fromtimestamp(ts, tz).strftime("%Y-%m-%d %H:%M:%S")


def timeline(events, vm_name):
    """Per-VM state timeline derived from the audit events (engine steps, user actions, failures)."""
    out = []
    for e in sorted((e for e in events if e.get("vm") == vm_name), key=lambda e: e["time"]):
        msg = e["message"]
        if SKIP.match(msg):
            continue
        label, detail = msg, ""
        if (mt := STARTED.match(msg)) and mt.group(1) in STEP_LABEL:
            label = STEP_LABEL[mt.group(1)] + " started"
        elif (mt := FINISHED.match(msg)):
            label = " + ".join(STEP_LABEL.get(x.strip(), x.strip()) for x in mt.group(1).split(" + ")) + " finished"
        elif (mt := FAILED.match(msg)):
            label, detail = STEP_LABEL.get(mt.group(1), mt.group(1)) + " failed", mt.group(2)
        elif (mt := re.match(r"^(\S+): (cutover|rollback|cancel|pause|resume|retry|test|discard|resync|complete) ", msg)):
            label, detail = {"cutover": "Cutover requested", "rollback": "Rollback requested"}.get(
                mt.group(2), mt.group(2).capitalize() + " requested"), "by " + mt.group(1)
        out.append({"time": e["time"], "severity": e["severity"], "label": label, "detail": detail, "raw": msg})
    return out


def _verify_text(cr):
    mode = cr.get("final_verify")
    if not mode:
        return "-", "-"
    rounds = cr.get("final_verified_rounds")
    result = {"full": f"Target hashed and matched the source ({rounds} verified pass{'es' if rounds != 1 else ''})"
              if rounds else "Target hashed in full",
              "sample": "Random sample of the target matched the source",
              "sample->full": f"Sample differed, full verification ran and matched ({rounds} pass(es))",
              "none": "Not verified against the target (recorded digests trusted)"}.get(mode, mode)
    return mode, result


def build(plan, events, providers, settings, version, vm_id=None, now=None):
    """Plain dict with everything the renderers need. plan is Plan.model_dump() (+ started_by)."""
    tzname = (settings or {}).get("timezone") or plan.get("schedule", {}).get("timezone") or "UTC"
    tz = _tz(tzname)
    pev = [e for e in events if e.get("plan") == plan["name"]]
    started_by = plan.get("started_by")
    if not started_by:
        for e in sorted(pev, key=lambda e: e["time"]):
            if (mt := re.match(r"^(\S+) (started|scheduled) plan ", e["message"])):
                started_by = mt.group(1)
                break
    byid = {p["id"]: p for p in providers}
    src, tgt = byid.get((plan.get("source") or {}).get("id"), {}), byid.get((plan.get("target") or {}).get("id"), {})
    vms = [v for v in plan["vms"] if not vm_id or v["id"] == vm_id]
    rows = []
    for v in vms:
        cr = v.get("cutover_result") or {}
        st = plan["vm_settings"].get(v["id"]) or {}
        tl = timeline(pev, v["name"])
        mode, result = _verify_text(cr)
        rows.append({
            "id": v["id"], "name": v["name"], "os": v.get("os_type", ""), "status": v["status"],
            "target_name": st.get("target_name") or v["name"], "target_vm_id": v.get("target_vm_id") or cr.get("target_vm_id") or "",
            "total_bytes": v.get("total_bytes", 0), "copied_bytes": v.get("data_transferred_bytes", 0),
            "final_sync_sent_bytes": cr.get("final_sync_sent_bytes"),
            "seeds": sum(1 for t in tl if t["label"] == "Seed started"),
            "resyncs": sum(1 for t in tl if t["label"] == "Resync finished"),
            "last_sync_at": v.get("last_sync_at"), "completed_at": cr.get("completed_at"),
            "downtime_seconds": cr.get("downtime_seconds"), "final_sync_seconds": cr.get("final_sync_seconds"),
            "final_verify": mode, "final_verify_result": result,
            "verify": cr.get("verify") or {}, "checks": v.get("checks") or [], "error": v.get("error") or "",
            "rolled_back": any("Rollback" in t["label"] for t in tl), "timeline": tl,
        })
    vm_names = {r["name"] for r in rows}
    audit = [e for e in sorted(pev, key=lambda e: e["time"]) if not vm_id or e.get("vm") in vm_names or not e.get("vm")]
    rep = {
        "title": f"Migration report: {plan['name']}" + (f" ({rows[0]['name']})" if vm_id and rows else ""),
        "generated_at": now or time.time(), "tz": tzname, "version": version,
        "plan": {"name": plan["name"], "status": plan["status"], "created_by": plan.get("created_by"),
                 "created_at": plan.get("created_at"), "started_by": started_by or "-", "started_at": plan.get("started_at"),
                 "seed_at": (plan.get("schedule") or {}).get("seed_at"),
                 "resync_minutes": (plan.get("schedule") or {}).get("resync_minutes"),
                 "source": {"name": (plan.get("source") or {}).get("name", "-"), "type": src.get("type", ""),
                            "address": src.get("address", ""), "version": src.get("version") or ""},
                 "target": {"name": (plan.get("target") or {}).get("name", "-"), "type": tgt.get("type", ""),
                            "address": tgt.get("address", ""), "version": tgt.get("version") or "",
                            "cluster": plan.get("cluster") or "-", "container": plan.get("container") or "-",
                            "project": plan.get("project") or "-"},
                 "network_map": plan.get("network_map") or [], "test_network": plan.get("test_network"),
                 "prep": {k: v for k, v in (plan.get("prep") or {}).items() if "password" not in k},
                 "data_migrated_bytes": plan.get("data_migrated_bytes", 0)},
        "vms": rows, "events": audit,
    }
    return rep


def _redact(obj, known):
    if isinstance(obj, str):
        return appliance.redact(obj, known)
    if isinstance(obj, list):
        return [_redact(x, known) for x in obj]
    if isinstance(obj, dict):
        return {k: _redact(v, known) for k, v in obj.items()}
    return obj


def redacted(rep, known=()):
    return _redact(rep, [k for k in known if k])


# ---------------------------------------------------------------- HTML

CSS = f"""
:root{{--p:{BRAND};--ink:#1d1b20;--mut:#625b71;--line:#cac4d0;--bg:#fef7ff;--sc:#f3edf7}}
*{{box-sizing:border-box}}body{{margin:0;background:var(--bg);color:var(--ink);font:14px/1.5 Roboto,"Segoe UI",Arial,sans-serif}}
.page{{max-width:980px;margin:0 auto;padding:28px 32px 40px}}
header.top{{display:flex;align-items:center;gap:16px;border-bottom:3px solid var(--p);padding-bottom:14px;margin-bottom:18px}}
.logo{{width:44px;height:44px;border-radius:14px;background:var(--p);color:#fff;font-weight:700;font-size:20px;display:flex;
align-items:center;justify-content:center}}
h1{{font-size:24px;margin:0;font-weight:500}}h2{{font-size:17px;margin:26px 0 8px;color:var(--p);font-weight:500}}
h3{{font-size:15px;margin:18px 0 6px;font-weight:600}}.sub{{color:var(--mut);font-size:12.5px}}
.grow{{flex:1}}button.print{{background:var(--p);color:#fff;border:0;border-radius:20px;padding:9px 20px;font:inherit;cursor:pointer}}
.note{{background:var(--sc);border-radius:12px;padding:10px 14px;margin:0 0 14px;color:var(--mut)}}
dl{{display:grid;grid-template-columns:200px 1fr;gap:4px 14px;margin:0}}dt{{color:var(--mut)}}dd{{margin:0}}
table{{border-collapse:collapse;width:100%;margin:6px 0}}th,td{{text-align:left;padding:6px 8px;border-bottom:1px solid var(--line);
vertical-align:top;font-size:13px}}th{{background:var(--sc);font-weight:600}}
.vm{{border:1px solid var(--line);border-radius:16px;padding:6px 18px 14px;margin:16px 0;background:#fff;break-inside:avoid-page}}
.pill{{display:inline-block;border-radius:10px;padding:1px 10px;font-size:12px;background:var(--sc)}}
.PASS,.Completed,.info{{color:#1b6e3a}}.WARN,.warning{{color:#8a5a00}}.FAIL,.Failed,.error{{color:#ba1a1a}}
footer{{margin-top:30px;border-top:1px solid var(--line);padding-top:10px;color:var(--mut);font-size:12px}}
@media print{{body{{background:#fff}}.noprint{{display:none}}.page{{padding:0}}.vm{{box-shadow:none}}}}
"""


def _e(x):
    return html.escape(str(x if x is not None else "-"))


def render_html(rep, pdf_note=False):
    tz = _tz(rep["tz"])
    t = lambda ts: _e(fmt_time(ts, tz))  # noqa: E731
    p = rep["plan"]
    s, g = p["source"], p["target"]
    o = [f"<!doctype html><html lang=en><head><meta charset=utf-8><title>{_e(rep['title'])}</title>"
         f"<style>{CSS}</style></head><body><div class=page>",
         f"<header class=top><div class=logo>ev</div><div><h1>{_e(rep['title'])}</h1>"
         f"<div class=sub>Generated {t(rep['generated_at'])} ({_e(rep['tz'])})</div></div><div class=grow></div>"
         "<button class='print noprint' onclick='window.print()'>Print / Save as PDF</button></header>"]
    if pdf_note:
        o.append("<p class='note noprint'>This appliance does not render PDF files itself. Use <b>Print / Save as PDF</b> "
                 "in your browser and choose &quot;Save as PDF&quot;.</p>")
    o.append("<h2>Plan summary</h2><dl>"
             f"<dt>Plan</dt><dd>{_e(p['name'])} <span class=pill>{_e(p['status'])}</span></dd>"
             f"<dt>Source environment</dt><dd>{_e(s['name'])} ({_e(s['type'] or 'everRun')}, {_e(s['address'])}"
             f"{', v' + _e(s['version']) if s['version'] else ''})</dd>"
             f"<dt>Target environment</dt><dd>{_e(g['name'])} ({_e(g['address'])}{', ' + _e(g['version']) if g['version'] else ''})</dd>"
             f"<dt>Target cluster / container</dt><dd>{_e(g['cluster'])} / {_e(g['container'])}</dd>"
             f"<dt>Project</dt><dd>{_e(g['project'])}</dd>"
             f"<dt>Created</dt><dd>{t(p['created_at'])} by {_e(p['created_by'])}</dd>"
             f"<dt>Started</dt><dd>{t(p['started_at'])} by {_e(p['started_by'])}</dd>"
             f"<dt>Scheduled seed</dt><dd>{t(p['seed_at']) if p['seed_at'] else 'On start'}</dd>"
             f"<dt>Resync interval</dt><dd>{_e(p['resync_minutes'])} minutes</dd>"
             f"<dt>Data copied</dt><dd>{fmt_bytes(p['data_migrated_bytes'])}</dd></dl>")
    pr = p["prep"]
    o.append("<h3>Settings</h3><dl>"
             f"<dt>Guest preparation</dt><dd>{_e(pr.get('mode', 'automatic'))}"
             f"{', guest operations bypassed' if pr.get('bypass_guest_ops') else ''}</dd>"
             f"<dt>Keep IP / MAC</dt><dd>{'IP' if pr.get('retain_ip') else 'no IP'}, {'MAC' if pr.get('retain_mac') else 'no MAC'}</dd>"
             f"<dt>Remove Stratus guest tools</dt><dd>{'yes' if pr.get('uninstall_guest_tools') else 'no'}</dd>"
             f"<dt>Install Nutanix Guest Tools</dt><dd>{'yes' if pr.get('install_ngt') else 'no'}</dd>"
             f"<dt>Test network</dt><dd>{'yes' if p['test_network'] else 'no'}</dd></dl>")
    if p["network_map"]:
        o.append("<table><tr><th>Source network</th><th>Target network</th><th>Test network</th></tr>" + "".join(
            f"<tr><td>{_e(n['source_network'])}</td><td>{_e(n['target_subnet'])}</td><td>{_e(n.get('test_subnet') or '-')}</td></tr>"
            for n in p["network_map"]) + "</table>")
    o.append("<h2>Overview</h2><table><tr><th>VM</th><th>Target VM</th><th>Status</th><th>Size</th><th>Copied</th>"
             "<th>Downtime</th><th>Final sync check</th></tr>" + "".join(
        f"<tr><td>{_e(v['name'])}</td><td>{_e(v['target_name'])}</td><td class='{_e(v['status'])}'>{_e(v['status'])}</td>"
        f"<td>{fmt_bytes(v['total_bytes'])}</td><td>{fmt_bytes(v['copied_bytes'])}</td>"
        f"<td>{fmt_dur(v['downtime_seconds'])}</td><td>{_e(v['final_verify'])}</td></tr>" for v in rep["vms"]) + "</table>")
    for v in rep["vms"]:
        vf = v["verify"]
        o.append(f"<section class=vm><h2>{_e(v['name'])} <span class=pill>{_e(v['status'])}</span></h2><dl>"
                 f"<dt>Source OS</dt><dd>{_e(v['os'])}</dd>"
                 f"<dt>Target VM</dt><dd>{_e(v['target_name'])} (id {_e(v['target_vm_id'] or 'not created')})</dd>"
                 f"<dt>Data size / copied</dt><dd>{fmt_bytes(v['total_bytes'])} / {fmt_bytes(v['copied_bytes'])}</dd>"
                 f"<dt>Sent in the final sync</dt><dd>{fmt_bytes(v['final_sync_sent_bytes']) if v['final_sync_sent_bytes'] is not None else '-'}</dd>"
                 f"<dt>Resyncs completed</dt><dd>{v['resyncs']}</dd>"
                 f"<dt>Downtime</dt><dd>{fmt_dur(v['downtime_seconds'])}</dd>"
                 f"<dt>Final sync</dt><dd>{fmt_dur(v['final_sync_seconds'])}</dd>"
                 f"<dt>Final verification</dt><dd>{_e(v['final_verify'])}: {_e(v['final_verify_result'])}</dd>"
                 f"<dt>Cutover completed</dt><dd>{t(v['completed_at'])}</dd>"
                 f"<dt>Post-cutover check</dt><dd>power {_e(vf.get('power', '-'))}, "
                 f"IP reachable: {_e(vf.get('reachable', '-'))}</dd>"
                 f"<dt>Rolled back</dt><dd>{'yes (at least once)' if v['rolled_back'] else 'no'}</dd>"
                 + (f"<dt>Error</dt><dd class=error>{_e(v['error'])}</dd>" if v["error"] else "") + "</dl>")
        o.append("<h3>Prep-check</h3>")
        o.append("<table><tr><th>Check</th><th>Result</th><th>Detail</th></tr>" + "".join(
            f"<tr><td>{_e(c['name'])}</td><td class={_e(c['status'])}>{_e(c['status'])}</td><td>{_e(c.get('detail'))}</td></tr>"
            for c in v["checks"]) + "</table>" if v["checks"] else "<p class=sub>No prep-check results recorded.</p>")
        o.append("<h3>Timeline</h3>")
        o.append("<table><tr><th style='width:170px'>Time</th><th>Event</th><th>Detail</th></tr>" + "".join(
            f"<tr><td>{t(x['time'])}</td><td class={_e(x['severity'])}>{_e(x['label'])}</td><td>{_e(x['detail'])}</td></tr>"
            for x in v["timeline"]) + "</table>" if v["timeline"] else "<p class=sub>No events recorded for this VM.</p>")
        o.append("</section>")
    o.append("<h2>Audit trail</h2>" + ("<table><tr><th style='width:170px'>Time</th><th>Severity</th><th>VM</th><th>Message</th></tr>" + "".join(
        f"<tr><td>{t(e['time'])}</td><td class={_e(e['severity'])}>{_e(e['severity'])}</td><td>{_e(e.get('vm') or '-')}</td>"
        f"<td>{_e(e['message'])}</td></tr>" for e in rep["events"]) + "</table>" if rep["events"]
                                else "<p class=sub>No events recorded.</p>"))
    o.append(f"<footer>ev2ahv appliance version {_e(rep['version'])}. Generated {t(rep['generated_at'])} ({_e(rep['tz'])}). "
             "Credentials and tokens are removed from this report.</footer></div></body></html>")
    return "".join(o)


# ---------------------------------------------------------------- CSV

CSV_COLUMNS = ["plan", "plan_status", "vm", "status", "os", "target_vm_name", "target_vm_id", "size_bytes", "copied_bytes",
               "final_sync_sent_bytes", "resyncs", "downtime_seconds", "final_sync_seconds", "final_verify",
               "final_verify_result", "cutover_completed_utc", "power_check", "ip_reachable", "rolled_back",
               "prep_checks_pass", "prep_checks_warn", "prep_checks_fail", "error"]


def _cell(x):
    """Spreadsheet formula injection guard: a text cell starting with = + - @ gets a leading quote."""
    x = "" if x is None else x
    return "'" + x if isinstance(x, str) and x[:1] in ("=", "+", "-", "@", "\t", "\r") else x


def render_csv(rep):
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\r\n")
    w.writerow(CSV_COLUMNS)
    for v in rep["vms"]:
        c = {k: sum(1 for x in v["checks"] if x["status"] == k) for k in ("PASS", "WARN", "FAIL")}
        done = datetime.fromtimestamp(v["completed_at"], timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ") if v["completed_at"] else ""
        w.writerow([_cell(x) for x in [
            rep["plan"]["name"], rep["plan"]["status"], v["name"], v["status"], v["os"], v["target_name"], v["target_vm_id"],
            v["total_bytes"], v["copied_bytes"], v["final_sync_sent_bytes"], v["resyncs"], v["downtime_seconds"],
            v["final_sync_seconds"], v["final_verify"] if v["final_verify"] != "-" else "", v["final_verify_result"] if v["final_verify"] != "-" else "",
            done, v["verify"].get("power", ""), v["verify"].get("reachable", ""), "yes" if v["rolled_back"] else "no",
            c["PASS"], c["WARN"], c["FAIL"], v["error"]]])
    return buf.getvalue()
