#!/usr/bin/python3 -I
"""Root block helper for the appliance. The web app runs unprivileged; raw disk access goes through here.

systemd runs `blockd.py serve` once per connection on /run/ev2ahv-blockd.sock (Accept=yes, socket group ev2ahv)
with the connection as stdin/stdout. The client sends one JSON line {"op": ..., "args": [...]}; after that the
connection speaks the normal ev2ahv_helper protocol for that op.

A device is accepted only if ALL hold:
  - it resolves to a whole SCSI disk /dev/sdX that AHV presents as a vdisk (by-id scsi-1NUTANIX_* link);
  - it is not a system disk (any disk in use at boot, recorded by `baseline`, is refused forever), and a disk that
    was already attached at boot is accepted only if ev2ahv claimed it (staging disks survive appliance reboots);
  - it is not mounted, has no mounted partitions and no holders (LVM, dm, md);
  - its serial is in the app's claim list (/var/lib/ev2ahv/claims: disks the app hot-added itself, after it
    verified the hot-add through the Prism API). The claim list alone grants nothing: the checks above still apply.
Ops: caps, size, hash, read, write (device), rescan (SCSI host scan), offline (flush + delete a device),
uuid (this VM's SMBIOS UUID, which equals its AHV VM extId).
`baseline` (root, at boot): records boot disks and the VM UUID in /run/ev2ahv-boot/.
Stdlib only (plus xxhash if present). Every refusal is logged to the journal via stderr.
"""
import glob, json, os, re, subprocess, sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "helper"))
import ev2ahv_helper as helper  # noqa: E402

BOOT = "/run/ev2ahv-boot"
CLAIMS = os.environ.get("EV2AHV_CLAIMS", "/var/lib/ev2ahv/claims")
DEVICE_OPS = {"size", "hash", "read", "write", "offline"}


def disks():
    return sorted(os.path.basename(p) for p in glob.glob("/sys/block/sd*"))


def serial_of(name):
    """The vdisk serial AHV presents (by-id scsi-1NUTANIX_<serial>; older stacks used scsi-SNUTANIX_VDISK_<id>)."""
    for link in glob.glob("/dev/disk/by-id/scsi-*NUTANIX_*"):
        if "-part" not in link and os.path.basename(os.path.realpath(link)) == name:
            return re.split(r"NUTANIX_(?:VDISK_)?", os.path.basename(link), 1)[1]
    return None


def baseline():
    os.makedirs(BOOT, exist_ok=True)
    os.chmod(BOOT, 0o755)   # the unprivileged app reads vm-uuid; the unit's umask would otherwise hide it
    with open(os.path.join(BOOT, "disks"), "w") as fh:
        fh.write("\n".join(disks()) + "\n")
    system = [serial_of(d) or d for d in disks() if helper.in_use("/dev/" + d)]
    with open(os.path.join(BOOT, "system-disks"), "w") as fh:   # disks in use at boot: never allowed
        fh.write("\n".join(system) + "\n")
    try:
        with open("/sys/class/dmi/id/product_uuid") as fh:
            uuid = fh.read().strip().lower()
    except OSError:
        uuid = ""
    with open(os.path.join(BOOT, "vm-uuid"), "w") as fh:
        fh.write(uuid + "\n")
    for f in ("disks", "system-disks", "vm-uuid"):
        os.chmod(os.path.join(BOOT, f), 0o644)


def check_device(path):
    """Return the canonical /dev/sdX for an allowed device, or raise PermissionError."""
    real = os.path.realpath(path)
    m = re.fullmatch(r"/dev/(sd[a-z]+)", real)
    if not m:
        raise PermissionError(f"{path}: not a whole SCSI disk")
    name = m.group(1)
    try:
        with open(os.path.join(BOOT, "disks")) as fh:
            boot = set(fh.read().split())
        with open(os.path.join(BOOT, "system-disks")) as fh:
            system = set(fh.read().split())
    except OSError:
        raise PermissionError("no boot baseline: ev2ahv-boot.service has not run")
    serial = serial_of(name)
    if not serial:
        raise PermissionError(f"{real} is not an AHV vdisk")
    if serial in system or name in system:
        raise PermissionError(f"{real} is an appliance system disk")
    try:
        with open(CLAIMS) as fh:
            claimed = set(fh.read().split())
    except OSError:
        claimed = set()
    if serial not in claimed:
        if name in boot:
            raise PermissionError(f"{real} was present at boot (an appliance disk)")
        raise PermissionError(f"{real} (serial {serial}) was not attached by ev2ahv")
    busy = helper.in_use(real)
    if busy:
        raise PermissionError(f"{real} is in use: {busy}")
    return real


def serve():
    inp, out = sys.stdin.buffer, sys.stdout.buffer
    try:
        req = json.loads(inp.readline().decode() or "{}")
        op, args = req.get("op"), [str(a) for a in req.get("args") or []]
        if op in DEVICE_OPS:
            if not args:
                raise PermissionError("device argument missing")
            args[0] = check_device(args[0])
        if op == "rescan":
            for h in glob.glob("/sys/class/scsi_host/host*/scan"):
                with open(h, "w") as fh:
                    fh.write("- - -")
            subprocess.run(["udevadm", "settle", "--timeout=20"], check=False)
            out.write(b"ok\n")
        elif op == "offline":
            dev = args[0]
            os.sync()
            subprocess.run(["blockdev", "--flushbufs", dev], check=True)
            with open(f"/sys/block/{os.path.basename(dev)}/device/delete", "w") as fh:
                fh.write("1")
            subprocess.run(["udevadm", "settle", "--timeout=20"], check=False)
            out.write(b"ok\n")
        elif op == "uuid":
            with open(os.path.join(BOOT, "vm-uuid")) as fh:
                out.write(fh.read().encode())
        elif op in ("caps", "size", "hash", "read", "write"):
            sys.stderr.write(f"ev2ahv-blockd: {op} {' '.join(args)}\n")
            out.flush()
            return helper.main([op] + args)
        else:
            raise PermissionError(f"unknown op {op!r}")
        out.flush()
    except PermissionError as e:
        sys.stderr.write(f"ev2ahv-blockd: refused: {e}\n")
        out.write(f"EV2AHV-REFUSED {e}\n".encode())
        out.flush()
        return 3


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else ""
    if cmd == "baseline":
        baseline()
    elif cmd == "serve":
        sys.exit(serve() or 0)
    else:
        sys.exit("usage: blockd.py baseline|serve")
