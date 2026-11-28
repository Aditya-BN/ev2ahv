#!/usr/bin/env python3
"""ev2ahv in-guest agent (Linux). Python 3.6+ stdlib only. Installed by `ev2ahv agent-install`.

The agent lets the ev2ahv appliance read this guest's own block devices without touching the hypervisor host. It is
the read-only half of ev2ahv_helper (hash, read, size, caps; never `write`) plus three guest-side commands:

  check     JSON report: version, O_DIRECT support, allowed disks with sizes, mounts and which would be frozen
  devices   JSON list of the guest's block devices (for building a plan)
  quiesce   fsfreeze every filesystem on the allowed disks, report `frozen`, hold until told to thaw (see below)
  gate      the SSH forced command: validates SSH_ORIGINAL_COMMAND and runs this file under sudo

Security model (details in docs/agent-mode.md):
  * the appliance logs in as the unprivileged user `ev2ahv-agent` with a per-plan key whose authorized_keys line is
    `restrict,from=<appliance>,expiry-time=...,command="<this file> gate"`;
  * `gate` accepts only the sub-commands below with validated arguments; sudoers allows exactly this file;
  * every device argument must be in /etc/ev2ahv-agent/devices.allow (real path compared), a block device, opened O_RDONLY.

`quiesce` is the one command with a side effect on the guest. It runs as root, syncs, freezes the filesystems
(FIFREEZE, deepest mount first), prints `frozen N`, then waits for one of: EOF on stdin, `thaw`, SIGTERM/SIGHUP/SIGINT,
or --max-seconds. A forked watchdog (own session, sees the same file descriptors) thaws when the parent dies in any way
including SIGKILL, so a crashed appliance connection can never leave the guest frozen. `status` on stdin answers
`frozen` or `thawed`, so the appliance can prove the guest stayed frozen for the whole final sync.
Reads and `quiesce` never write to the guest's filesystems (python is run with -B so it does not write .pyc files).
"""
import errno
import fcntl
import json
import os
import re
import select
import shlex
import signal
import stat
import sys
import time

AGENT_VERSION = "1"
HERE = os.path.dirname(os.path.realpath(__file__))
ALLOW_FILE = os.environ.get("EV2AHV_AGENT_ALLOW", "/etc/ev2ahv-agent/devices.allow")
PYTHON = "/usr/bin/python3"
FIFREEZE, FITHAW = 0xC0045877, 0xC0045878
DEVICE_RE = re.compile(r"^/dev/[A-Za-z0-9][A-Za-z0-9/_.:-]*$")
INT_RE = re.compile(r"^[0-9]+$")
FLOAT_RE = re.compile(r"^[0-9]+(\.[0-9]+)?$")
ALGO_RE = re.compile(r"^(xxh64|blake2b|sha256)$")
# sub-command -> option -> kind of value ("int", "float", "algo")
OPTIONS = {
    "caps": {}, "size": {}, "check": {}, "devices": {}, "version": {},
    "hash": {"--chunk": "int", "--algo": "algo", "--start": "int", "--end": "int", "--limit": "int", "--max-mbps": "float"},
    "read": {"--chunk": "int", "--limit": "int", "--max-mbps": "float"},
    "quiesce": {"--max-seconds": "int"},
}
NEEDS_DEVICE = {"size", "hash", "read"}


class Refused(Exception):
    pass


# ------------------------------------------------------------------ allowlist and validation
def allowed_devices():
    """Real paths of the devices the plan named at install time. Empty list means: refuse everything."""
    try:
        with open(ALLOW_FILE) as fh:
            lines = [l.strip() for l in fh if l.strip() and not l.startswith("#")]
    except OSError:
        return []
    return sorted({os.path.realpath(l) for l in lines})


def check_device(path):
    if not DEVICE_RE.match(path) or ".." in path.split("/"):
        raise Refused("bad device path %r" % path)
    real = os.path.realpath(path)
    if real not in allowed_devices():
        raise Refused("%s is not in %s" % (real, ALLOW_FILE))
    if not stat.S_ISBLK(os.stat(real).st_mode):
        raise Refused("%s is not a block device" % real)
    return real


def parse(argv):
    """Validate agent arguments. Returns (sub-command, [args]) with devices replaced by their real paths."""
    if not argv or argv[0] not in OPTIONS:
        raise Refused("unknown command %r" % (argv[:1],))
    sub, rest, opts, out, devices = argv[0], argv[1:], OPTIONS[argv[0]], [], []
    i = 0
    while i < len(rest):
        a = rest[i]
        if a in opts:
            if i + 1 >= len(rest):
                raise Refused("%s needs a value" % a)
            v, kind = rest[i + 1], opts[a]
            ok = {"int": INT_RE, "float": FLOAT_RE, "algo": ALGO_RE}[kind].match(v)
            if not ok or (kind == "int" and int(v) > 2 ** 62):
                raise Refused("bad value for %s: %r" % (a, v))
            out += [a, v]
            i += 2
        elif a.startswith("-"):
            raise Refused("unknown option %r" % a)
        else:
            devices.append(a)
            i += 1
    if sub in NEEDS_DEVICE and len(devices) != 1:
        raise Refused("%s needs exactly one device" % sub)
    if sub not in NEEDS_DEVICE and sub != "quiesce" and devices:
        raise Refused("%s takes no device" % sub)
    devices = [check_device(d) for d in devices]
    if sub == "quiesce" and not devices:
        devices = allowed_devices()
    return sub, (devices + out if sub in NEEDS_DEVICE else out), devices


# ------------------------------------------------------------------ mounts, disks
def _unescape(s):
    return re.sub(r"\\([0-7]{3})", lambda m: chr(int(m.group(1), 8)), s)


def mounts():
    """[(mountpoint, fstype, source)] from /proc/self/mountinfo, real block sources only."""
    out = []
    with open("/proc/self/mountinfo") as fh:
        for line in fh:
            left, _, right = line.partition(" - ")
            f, r = left.split(), right.split()
            if len(r) < 2:
                continue
            fstype, src = r[0], r[1]
            if src.startswith("/dev/"):
                out.append((_unescape(f[4]), fstype, os.path.realpath(src)))
    return out


def base_disks(dev):
    """Whole disks under a block device: partitions and device-mapper/md stacks resolved through sysfs."""
    name = os.path.basename(os.path.realpath(dev))
    seen, todo, disks = set(), [name], set()
    while todo:
        n = todo.pop()
        if n in seen:
            continue
        seen.add(n)
        sysdir = os.path.join("/sys/class/block", n)
        if not os.path.isdir(sysdir):
            continue
        slaves = os.path.join(sysdir, "slaves")
        parents = os.listdir(slaves) if os.path.isdir(slaves) else []
        if os.path.exists(os.path.join(sysdir, "partition")):   # /sys/class/block/vda1 -> .../vda/vda1
            parents.append(os.path.basename(os.path.dirname(os.path.realpath(sysdir))))
        if parents:
            todo += parents
        else:
            disks.add("/dev/" + n)
    return disks


def freeze_targets(devices):
    """Mountpoints whose backing disks intersect `devices`, deepest path first (children before parents)."""
    want = {os.path.realpath(d) for d in devices}
    chosen, seen = [], set()
    for mp, fstype, src in mounts():
        if mp in seen or not (base_disks(src) & want):
            continue
        seen.add(mp)
        chosen.append((mp, fstype, src))
    chosen.sort(key=lambda m: (-m[0].rstrip("/").count("/"), m[0]))
    return chosen


# ------------------------------------------------------------------ quiesce (fsfreeze)
def _ioctl(fd, req):
    try:
        fcntl.ioctl(fd, req, 0)
        return True
    except OSError as e:
        if e.errno in (errno.EBUSY, errno.EINVAL):   # already frozen / not frozen
            return e.errno == errno.EBUSY and req == FIFREEZE
        raise


def thaw_all(fds):
    for fd in reversed(fds):
        try:
            fcntl.ioctl(fd, FITHAW, 0)
        except OSError:
            pass


def cmd_quiesce(devices, max_seconds=300, stdin=None, out=None, hold_extra=None):
    if os.geteuid() != 0:
        raise Refused("quiesce needs root (run it through the gate)")
    stdin = sys.stdin if stdin is None else stdin
    out = sys.stdout if out is None else out
    targets = freeze_targets(devices)
    if not targets:
        out.write("error nothing to freeze for %s\n" % ",".join(devices))
        out.flush()
        return 1
    fds, names = [], []
    for mp, _fs, _src in targets:
        fds.append(os.open(mp, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC))
        names.append(mp)
    rfd, wfd = os.pipe()
    pid = os.fork()
    if pid == 0:      # watchdog: thaws when the parent dies (pipe EOF) or the deadline passes, whatever happens to the parent
        os.setsid()
        os.close(wfd)
        for n in (0, 1, 2):
            os.dup2(os.open(os.devnull, os.O_RDWR), n)
        select.select([rfd], [], [], max_seconds + 5)
        thaw_all(fds)
        os._exit(0)
    os.close(rfd)
    state = {"frozen": False, "why": None}

    def release(why):
        if state["frozen"]:
            thaw_all(fds)
            state.update(frozen=False, why=why)

    def on_signal(signum, _frame):
        release("signal %d" % signum)
        raise SystemExit(0)
    for s in (signal.SIGTERM, signal.SIGHUP, signal.SIGINT):
        signal.signal(s, on_signal)
    try:
        os.sync()
        done = []
        for fd, name in zip(fds, names):
            fcntl.ioctl(fd, FIFREEZE, 0)
            done.append(fd)
        state["frozen"] = True
        deadline = time.monotonic() + max_seconds
        out.write("frozen %d %s\n" % (len(names), ",".join(names)))
        out.flush()
        while True:
            left = deadline - time.monotonic()
            if left <= 0:
                release("timeout")
                out.write("thawed timeout\n")
                out.flush()
                break
            r, _, _ = select.select([stdin], [], [], min(left, 1.0))
            if not r:
                continue
            line = stdin.readline()
            if not line or line.strip() == "thaw":
                release("requested" if line else "eof")
                out.write("thawed %s\n" % state["why"])
                out.flush()
                break
            if line.strip() == "status":
                out.write("%s\n" % ("frozen" if state["frozen"] else "thawed"))
                out.flush()
    except BaseException:
        release("error")
        thaw_all(done if "done" in dir() else [])
        raise
    finally:
        os.close(wfd)       # the watchdog sees EOF and makes one more (harmless) thaw attempt
        try:
            os.waitpid(pid, 0)
        except OSError:
            pass
    return 0


# ------------------------------------------------------------------ reports
def read_sys(path):
    try:
        with open(path) as fh:
            return fh.read().strip()
    except OSError:
        return ""


def cmd_devices():
    out = []
    for name in sorted(os.listdir("/sys/block")):
        if name.startswith(("loop", "ram", "sr", "fd", "zram", "dm-", "md")):
            continue
        sec = read_sys("/sys/block/%s/size" % name)
        out.append({"path": "/dev/" + name, "size_bytes": int(sec or 0) * 512, "model": read_sys("/sys/block/%s/device/model" % name),
                    "serial": read_sys("/sys/block/%s/serial" % name) or read_sys("/sys/block/%s/device/serial" % name),
                    "mounts": [m[0] for m in mounts() if "/dev/" + name in base_disks(m[2])]})
    print(json.dumps(out))


def o_direct_ok(dev):
    try:
        fd = os.open(dev, os.O_RDONLY | os.O_DIRECT)
    except OSError:
        return False
    os.close(fd)
    return True


def cmd_check():
    devs = allowed_devices()
    report = {"agent_version": AGENT_VERSION, "python": sys.version.split()[0], "euid": os.geteuid(), "kernel": os.uname()[2],
              "hostname": os.uname()[1], "allowed": devs, "disks": [], "freeze": []}
    try:
        from ev2ahv_helper import VERSION
        report["helper_version"] = VERSION
    except ImportError:
        report["helper_version"] = None
    for d in devs:
        try:
            fd = os.open(d, os.O_RDONLY)
            size = os.lseek(fd, 0, os.SEEK_END)
            os.close(fd)
            report["disks"].append({"path": d, "size_bytes": size, "o_direct": o_direct_ok(d)})
        except OSError as e:
            report["disks"].append({"path": d, "error": str(e)})
    report["freeze"] = [{"mount": m, "fstype": fs, "source": src} for m, fs, src in freeze_targets(devs)]
    print(json.dumps(report))


# ------------------------------------------------------------------ dispatch
def run(sub, args, devices):
    if sub == "version":
        print(AGENT_VERSION)
    elif sub == "check":
        cmd_check()
    elif sub == "devices":
        cmd_devices()
    elif sub == "quiesce":
        secs = 300
        if "--max-seconds" in args:
            secs = int(args[args.index("--max-seconds") + 1])
        return cmd_quiesce(devices, max_seconds=min(max(secs, 5), 3600))
    else:       # caps, size, hash, read: the read-only half of the helper
        try:
            os.nice(10)
        except OSError:
            pass
        set_io_idle()
        sys.path.insert(0, HERE)
        import ev2ahv_helper
        ev2ahv_helper.main([sub] + args)
    return 0


def set_io_idle():
    """Best effort: IOPRIO_CLASS_IDLE (3) so the guest's own I/O wins. No-op where ioprio_set is unavailable."""
    try:
        import ctypes
        libc = ctypes.CDLL(None, use_errno=True)
        sysno = {"x86_64": 251, "aarch64": 30}.get(os.uname()[4])
        if sysno:
            libc.syscall(sysno, 1, 0, (3 << 13))   # IOPRIO_WHO_PROCESS, this process
    except Exception:
        pass


def gate():
    """SSH forced command. The appliance sends `ev2ahv-agent <sub> <args>`; anything else is refused."""
    original = os.environ.get("SSH_ORIGINAL_COMMAND", "")
    try:
        words = shlex.split(original)
    except ValueError:
        raise Refused("unparsable command")
    if not words or words[0] != "ev2ahv-agent":
        raise Refused("only `ev2ahv-agent <command>` is allowed here")
    sub, args, devices = parse(words[1:])      # validated here as the unprivileged user too
    me = os.path.realpath(__file__)
    argv = ["sudo", "-n", PYTHON, "-I", "-B", me] + words[1:]
    os.execv("/usr/bin/sudo", argv)


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    try:
        if argv[:1] == ["gate"]:
            gate()
            return 0
        sub, args, devices = parse(argv)
        return run(sub, args, devices)
    except Refused as e:
        sys.stderr.write("ev2ahv-agent refused: %s\n" % e)
        return 2


if __name__ == "__main__":
    try:
        sys.exit(main())
    except BrokenPipeError:
        try:
            os.dup2(os.open(os.devnull, os.O_WRONLY), sys.stdout.fileno())
        except OSError:
            pass
        sys.exit(0)
