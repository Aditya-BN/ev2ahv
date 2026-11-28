"""Notifications (low priority, kept small): e-mail through SMTP and/or a generic JSON webhook.

Settings live in the settings document under "notifications"; the SMTP password and the webhook URL (it may carry a
token) are write-only and stored encrypted in the secret store. Sending never blocks the worker: emit() only puts the
message on a queue, and one daemon thread delivers it with retries and backoff.
"""
import json, logging, queue, smtplib, ssl, threading, time, urllib.request
from email.message import EmailMessage
from urllib.parse import urlparse

log = logging.getLogger("ev2ahv.notify")

EVENTS = {   # key -> (label, severity)
    "ready": ("VM Ready to Cutover", "info"),
    "failed": ("VM Failed", "error"),
    "cutover": ("Cutover completed", "info"),
    "rollback": ("Rollback completed", "warning"),
}
DEFAULTS = {"enabled": False, "events": list(EVENTS), "smtp": {"host": "", "port": 587, "tls": "starttls", "user": "",
                                                                "from": "", "to": ""}, "webhook": {}}
BACKOFF = [5, 30, 120]   # seconds before retry 1, 2, 3 (4 attempts in all)
TIMEOUT = 15
SECRET_PW, SECRET_URL = "notify:smtp_password", "notify:webhook_url"

_q = queue.Queue(maxsize=200)
_thread = None
_tlock = threading.Lock()


class NotifyError(ValueError):
    pass


def clean(cfg):
    """Validate the settings the UI sent and return the part kept in the settings document (no secrets)."""
    cfg = cfg or {}
    smtp = dict(DEFAULTS["smtp"], **(cfg.get("smtp") or {}))
    smtp.pop("password", None)
    smtp["host"] = str(smtp["host"]).strip()
    try:
        smtp["port"] = int(smtp["port"])
    except (TypeError, ValueError):
        raise NotifyError("SMTP port must be a number")
    if smtp["tls"] not in ("none", "starttls", "ssl"):
        raise NotifyError("SMTP TLS must be none, starttls or ssl")
    for k in ("user", "from", "to"):
        smtp[k] = str(smtp[k] or "").strip()
    if smtp["host"] and not (smtp["from"] and smtp["to"]):
        raise NotifyError("SMTP needs a from and a to address")
    if smtp["host"] and not 0 < smtp["port"] < 65536:
        raise NotifyError("SMTP port is out of range")
    evs = [e for e in (cfg.get("events") or list(EVENTS)) if e in EVENTS]
    return {"enabled": bool(cfg.get("enabled")), "events": evs, "smtp": smtp}


def save(store, doc_cfg, incoming):
    """Apply an incoming settings block: secrets to the store, the rest into the returned document part."""
    incoming = incoming or {}
    out = clean(incoming)
    pw = (incoming.get("smtp") or {}).get("password")
    if pw:
        store.set_secret(SECRET_PW, str(pw))
    url = (incoming.get("webhook") or {}).get("url")
    if url is not None:
        url = str(url).strip()
        if url:
            u = urlparse(url)
            if u.scheme not in ("http", "https") or not u.hostname:
                raise NotifyError("Webhook URL must start with http:// or https://")
            store.set_secret(SECRET_URL, url)
        else:
            store.db.execute("DELETE FROM secrets WHERE owner=?", (SECRET_URL,))
            store.db.commit()
    return out


def public(store, cfg):
    """What GET /settings shows: never the password or the webhook URL, only whether they are set (and the host)."""
    cfg = dict(DEFAULTS, **(cfg or {}))
    url = store.secret(SECRET_URL)
    return {**cfg, "smtp": {**DEFAULTS["smtp"], **(cfg.get("smtp") or {}), "password_set": bool(store.secret(SECRET_PW))},
            "webhook": {"set": bool(url), "host": urlparse(url).netloc.rpartition("@")[2] if url else ""}}


def _text(kind, plan, vm, detail):
    label = EVENTS[kind][0]
    subject = f"[ev2ahv] {label}: {vm} ({plan})"
    body = f"{label}\nPlan: {plan}\nVM: {vm}\n" + (f"Detail: {detail}\n" if detail else "") \
        + time.strftime("Time (UTC): %Y-%m-%d %H:%M:%S\n", time.gmtime())
    return subject, body


def _send_smtp(cfg, password, subject, body):
    s = cfg["smtp"]
    msg = EmailMessage()
    msg["Subject"], msg["From"], msg["To"] = subject, s["from"], s["to"]
    msg.set_content(body)
    ctx = ssl.create_default_context()
    if s["tls"] == "ssl":
        cli = smtplib.SMTP_SSL(s["host"], s["port"], timeout=TIMEOUT, context=ctx, local_hostname="ev2ahv")
    else:
        cli = smtplib.SMTP(s["host"], s["port"], timeout=TIMEOUT, local_hostname="ev2ahv")
    with cli:
        cli.ehlo()
        if s["tls"] == "starttls":
            cli.starttls(context=ctx)
            cli.ehlo()
        if s["user"]:
            cli.login(s["user"], password or "")
        cli.send_message(msg, to_addrs=[a.strip() for a in s["to"].replace(";", ",").split(",") if a.strip()])


def _send_webhook(url, payload):
    req = urllib.request.Request(url, json.dumps(payload).encode(), {"Content-Type": "application/json",
                                                                     "User-Agent": "ev2ahv"}, method="POST")
    with urllib.request.urlopen(req, timeout=TIMEOUT) as r:   # noqa: S310  (http/https only, checked on save)
        if r.status >= 300:
            raise RuntimeError(f"webhook answered HTTP {r.status}")


def deliver(store, kind, plan, vm, detail=""):
    """One attempt at every configured channel. Returns a list of (channel, error or None)."""
    cfg = (store.get("settings", "settings") or {}).get("notifications") or {}
    subject, body = _text(kind, plan, vm, detail)
    res = []
    if (cfg.get("smtp") or {}).get("host"):
        try:
            _send_smtp(cfg, store.secret(SECRET_PW), subject, body)
            res.append(("smtp", None))
        except Exception as e:
            res.append(("smtp", f"{type(e).__name__}: {e}"))
    url = store.secret(SECRET_URL)
    if url:
        try:
            _send_webhook(url, {"event": kind, "title": EVENTS[kind][0], "plan": plan, "vm": vm, "detail": detail,
                                "time": time.time(), "text": subject})
            res.append(("webhook", None))
        except Exception as e:
            res.append(("webhook", f"{type(e).__name__}: {e}"))
    return res


def _worker():
    while True:
        store, kind, plan, vm, detail, attempt = _q.get()
        try:
            failed = [(c, e) for c, e in deliver(store, kind, plan, vm, detail) if e]
        except Exception as e:   # settings unreadable: nothing more to do for this message
            log.warning("notification dropped: %s", e)
            continue
        if not failed:
            continue
        if attempt < len(BACKOFF):
            log.warning("notification %s failed (%s); retry %d in %ss", kind, failed[0][1], attempt + 1, BACKOFF[attempt])
            t = threading.Timer(BACKOFF[attempt], _requeue, ((store, kind, plan, vm, detail, attempt + 1),))
            t.daemon = True
            t.start()
        else:
            log.error("notification %s gave up: %s", kind, failed)
            try:
                store.event("warning", f"Notification '{EVENTS[kind][0]}' for {vm} could not be sent: {failed[0][1]}",
                            plan=plan, vm=vm)
            except Exception:
                pass


def _requeue(item):
    try:
        _q.put_nowait(item)
    except queue.Full:
        pass


def emit(store, kind, plan, vm, detail=""):
    """Queue a notification if enabled for this event. Returns at once; never raises."""
    global _thread
    try:
        cfg = (store.get("settings", "settings") or {}).get("notifications") or {}
        if not cfg.get("enabled") or kind not in cfg.get("events", list(EVENTS)):
            return False
        with _tlock:
            if _thread is None or not _thread.is_alive():
                _thread = threading.Thread(target=_worker, daemon=True, name="notify")
                _thread.start()
        _q.put_nowait((store, kind, plan, vm, detail, 0))
        return True
    except Exception as e:
        log.warning("notification not queued: %s", e)
        return False
