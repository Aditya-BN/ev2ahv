"""everRun power control over its web API (DOH, the XML API the portal and avcli use). Stdlib only, no Java.

    EV2AHV_EVERRUN_PASS=... python3 everrun_api.py --host 10.12.136.153 --user admin status|stop|start|poweroff VM

status prints running / stopped / <other everRun state>. Verified on everRun 8.1.1: login, watch, vm stop, vm start.
everRun stores "/" in passwords as "\\/", so the password is escaped the same way before login.
"""
import argparse, http.cookiejar, os, ssl, sys, urllib.request
import xml.etree.ElementTree as ET
from xml.sax.saxutils import escape


def availability_level(text):
    """everRun availability text to "FT", "HA" or "" (unknown). Accepts FT/HA in any case and the long forms."""
    t = (text or "").strip().lower().replace("-", " ").replace("_", " ")
    if t in ("ft", "fault tolerant", "fault tolerance") or t.startswith("ft"):
        return "FT"
    if t in ("ha", "high availability") or t.startswith("ha"):
        return "HA"
    return ""


class EverRun:
    def __init__(self, host, user, password, timeout=60):
        self.url, self.timeout = f"https://{host}/doh/", timeout
        ctx = ssl.create_default_context()
        ctx.check_hostname, ctx.verify_mode = False, ssl.CERT_NONE   # everRun ships a self-signed certificate
        self.http = urllib.request.build_opener(urllib.request.HTTPSHandler(context=ctx),
                                                urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
        r = self.call("session", f"<login><username>{escape(user)}</username>"
                                 f"<password>{escape(password.replace('/', chr(92) + '/'))}</password></login>")
        if r.find(".//login").get("status") != "ok":
            raise RuntimeError("everRun login failed: check the user and password")

    def call(self, target, body):
        data = f"<requests output='XML'><request id='1' target='{escape(target)}'>{body}</request></requests>"
        req = urllib.request.Request(self.url, data.encode(), {"Content-Type": "text/xml"})
        with self.http.open(req, timeout=self.timeout) as resp:
            root = ET.fromstring(resp.read())
        r = root.find("response")
        if r is None or r.get("status") != "ok":
            raise RuntimeError(f"everRun {target} request failed: {ET.tostring(root, 'unicode')[:300]}")
        return r

    def vms(self):
        return {v.findtext("name"): v for v in self.call("supernova", "<watch/>").iter("vm") if v.get("id")}

    def vm(self, name):
        v = self.vms().get(name)
        if v is None:
            raise RuntimeError(f"no everRun VM named {name}")
        return v

    def availability(self):
        """{vm name: "FT" | "HA" | ""} for every VM, from the same watch call the portal uses."""
        return {name: availability_level(v.findtext("availability")) for name, v in self.vms().items()}

    def inventory(self):
        """{vm name: {"state": everRun state, "availability": "FT" | "HA" | ""}} for every VM, stopped ones included."""
        return {name: {"state": (v.findtext("state") or "").strip(),
                       "availability": availability_level(v.findtext("availability"))}
                for name, v in self.vms().items()}

    def state(self, name):
        return self.vm(name).findtext("state")

    def action(self, name, method):
        self.call(self.vm(name).get("id"), f"<{method}/>")


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--host", required=True)
    ap.add_argument("--user", default="admin")
    ap.add_argument("action", choices=("status", "stop", "start", "poweroff", "list"))
    ap.add_argument("vm", nargs="?")
    a = ap.parse_args(argv)
    pw = os.environ.get("EV2AHV_EVERRUN_PASS")
    if not pw:
        sys.exit("set EV2AHV_EVERRUN_PASS")
    e = EverRun(a.host, a.user, pw)
    if a.action == "list":
        for name, v in sorted(e.vms().items()):
            print(name, v.findtext("state"), v.findtext("availability"))
    elif a.action == "status":
        print(e.state(a.vm))
    else:
        e.action(a.vm, a.action)
        print(f"{a.action} sent")


if __name__ == "__main__":
    main()
