"""ev2ahv command line."""
import argparse, getpass, json, logging, os, sys, time

from . import __version__, credentials, everrun, plan as planlib
from .prism import Prism
from .transport import SshTransport
from .workflow import Migration, plan_dir, DATA_DIR

TEMPLATE = {
    "name": "my-vm", "writer": {"vm": "ev2ahv-writer", "host": "10.12.136.211", "user": "nutanix"},
    "source": {"type": "direct", "host": "SOURCE-NODE", "user": "root", "sudo": False, "max_mbps": 100,
               "power": {"status": "avcli vm-info my-vm | grep -i state", "stop": "avcli vm-shutdown my-vm",
                         "start": "avcli vm-boot my-vm"}},
    "disks": [{"name": "boot", "path": "/dev/CHANGE-ME"}],
    "nics": [{"subnet": "VLAN136", "mac": "50:6b:8d:00:00:00", "keep_mac": True}],
    "target": {"vm_name": "ev2ahv-my-vm", "cluster": "NTNX-DC", "container": "VM_Container", "sockets": 1,
               "cores": 2, "threads": 1, "memory_mib": 4096, "firmware": "bios", "disk_bus": "scsi"},
    "sync": {"chunk_mib": 4, "algo": "auto", "batch": 32}, "verify": {"ip": "", "port": 22}}


def setup_logging(name=None, verbose=False):
    logging.Formatter.converter = time.gmtime
    fmt = logging.Formatter("%(asctime)sZ %(levelname)s %(name)s %(message)s")
    root = logging.getLogger()
    root.setLevel(logging.DEBUG if verbose else logging.INFO)
    out = logging.StreamHandler(sys.stderr)
    out.setFormatter(fmt)
    root.addHandler(out)
    path = os.path.join(plan_dir(name) if name else DATA_DIR, "ev2ahv.log")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    fh = logging.FileHandler(path)
    fh.setFormatter(fmt)
    root.addHandler(fh)


def bar(key, done, total, stats):
    pct = 100 * done // max(total, 1)
    sys.stderr.write(f"\r{key}: {pct:3d}% scanned {done}/{total}, sent {stats['sent']} chunks   ")
    if done >= total:
        sys.stderr.write("\n")
    sys.stderr.flush()


def cmd_creds(a):
    if a.action == "show":
        print(json.dumps(credentials.load().public(), indent=1))
    elif a.action == "test":
        c = credentials.load()
        n = len(Prism(c).list_vms(select="name"))
        print(f"OK: {c.host} via {c.method}, {n} VMs visible")
    else:
        current = None
        try:
            current = credentials.load()
        except RuntimeError:
            pass
        host = a.host or (current.host if current else input("Prism Central host: "))
        if a.method == "api-key":
            key = os.environ.get("EV2AHV_API_KEY") or getpass.getpass("API key: ")
            new = credentials.Credentials(host, a.port, api_key=key, verify_tls=a.verify_tls, source="ev2ahv")
            kw = {"api_key": key}
        else:
            user = a.username or input("Username: ")
            pw = os.environ.get("EV2AHV_PASSWORD") or getpass.getpass("Password: ")
            new = credentials.Credentials(host, a.port, username=user, password=pw, verify_tls=a.verify_tls, source="ev2ahv")
            kw = {"username": user, "password": pw}
        n = len(Prism(new).list_vms(select="name"))
        credentials.save(host, a.port, verify_tls=a.verify_tls, **kw)
        print(f"Saved and verified: {host} via {new.method}, {n} VMs visible")


def cmd_inventory(a):
    t = SshTransport(a.host, a.user, a.key, a.port, a.sudo)
    for title, c in (("Hypervisor VMs (read-only)", "virsh -r list --all 2>/dev/null || echo 'virsh not available'"),
                     ("Block devices", "lsblk -o NAME,SIZE,TYPE,MOUNTPOINT")):
        print(f"--- {title}")
        print(t.run(c, check=False))


def cmd_discover(a):
    info = everrun.discover(a.vm, [n.strip() for n in a.nodes.split(",")])
    print(json.dumps(info, indent=1))
    if a.out:
        if os.path.exists(a.out):
            sys.exit(f"{a.out} already exists")
        doc = everrun.build_plan(info, a.target_name, a.subnet, a.avcli, max_mbps=a.max_mbps, disk_bus=a.disk_bus)
        planlib.dump(planlib.validate(doc), a.out)
        print(f"Wrote {a.out}", file=sys.stderr)


def cmd_plan(a):
    if a.action == "new":
        if os.path.exists(a.file):
            sys.exit(f"{a.file} already exists")
        planlib.dump(TEMPLATE, a.file)
        print(f"Wrote {a.file}. Edit it, then run: ev2ahv plan validate {a.file}")
    else:
        p = planlib.load(a.file)
        print(f"Plan {p['name']} is valid: {len(p['disks'])} disk(s), {len(p.get('nics') or [])} NIC(s)")


def run_step(a):
    p = planlib.load(a.plan)
    setup_logging(p["name"], a.verbose)
    m = Migration(p, Prism(credentials.load(), dry_run=a.dry_run), dry_run=a.dry_run, progress=bar)
    try:
        if a.cmd == "prep-check":
            checks = m.prep_check()
            for c in checks:
                print(f"[{c['status']}] {c['name']}: {c['detail']}")
            counts = {s: sum(c["status"] == s for c in checks) for s in ("PASS", "WARN", "FAIL")}
            print(f"Summary: {counts['PASS']} PASS, {counts['WARN']} WARN, {counts['FAIL']} FAIL")
            return 1 if counts["FAIL"] else 0
        if a.cmd == "cutover":
            r = m.cutover(assume_stopped=a.source_already_stopped, force=a.force)
        elif a.cmd == "cleanup":
            r = m.cleanup(delete_target=a.delete_target)
        else:
            r = getattr(m, a.cmd.replace("-", "_"))()
        print(json.dumps(r, indent=1, default=str))
        return 0
    finally:
        m.close()


def main(argv=None):
    ap = argparse.ArgumentParser(prog="ev2ahv", description="Migrate VMs from Stratus everRun to Nutanix AHV")
    ap.add_argument("--version", action="version", version=__version__)
    sub = ap.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("creds", help="show, test or update Prism credentials")
    c.add_argument("action", choices=["show", "test", "set"])
    c.add_argument("--method", choices=["api-key", "password"], default="api-key")
    c.add_argument("--host"); c.add_argument("--port", type=int, default=9440); c.add_argument("--username")
    c.add_argument("--verify-tls", action="store_true")
    c.set_defaults(fn=cmd_creds)
    i = sub.add_parser("inventory", help="list VMs and block devices on a source node")
    i.add_argument("host"); i.add_argument("--user", default="root"); i.add_argument("--key")
    i.add_argument("--port", type=int, default=22); i.add_argument("--sudo", action="store_true")
    i.set_defaults(fn=cmd_inventory)
    d = sub.add_parser("discover", help="read an everRun guest's layout and optionally write a plan")
    d.add_argument("vm"); d.add_argument("--nodes", required=True, help="comma-separated node IPs")
    d.add_argument("--out"); d.add_argument("--target-name"); d.add_argument("--subnet", default="VLAN136")
    d.add_argument("--avcli", default="~/everrun-lab/av.sh"); d.add_argument("--max-mbps", type=float, default=100)
    d.add_argument("--disk-bus", choices=["scsi", "pci"], default="scsi")
    d.set_defaults(fn=cmd_discover)
    l = sub.add_parser("plan", help="create or validate a plan")
    l.add_argument("action", choices=["new", "validate"]); l.add_argument("file")
    l.set_defaults(fn=cmd_plan)
    for name in ("prep-check", "guest-prep", "seed", "resync", "cutover", "rollback", "discard-target", "guest-cleanup", "cleanup", "status"):
        s = sub.add_parser(name)
        s.add_argument("plan"); s.add_argument("--dry-run", action="store_true"); s.add_argument("-v", "--verbose", action="store_true")
        if name == "cutover":
            s.add_argument("--source-already-stopped", action="store_true")
            s.add_argument("--force", action="store_true", help="cut over even if prep-check has FAIL items")
        if name == "cleanup":
            s.add_argument("--delete-target", action="store_true", help="also delete the migrated AHV VM")
        s.set_defaults(fn=run_step)
    u = sub.add_parser("ui", help="start the web console")
    u.add_argument("--bind", default="127.0.0.1"); u.add_argument("--port", type=int, default=8460)
    u.set_defaults(fn=lambda a: __import__("ev2ahv.web", fromlist=["serve"]).serve(a.bind, a.port))
    a = ap.parse_args(argv)
    try:
        return a.fn(a) or 0
    except (RuntimeError, ValueError, OSError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
