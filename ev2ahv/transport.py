"""Runs the block helper locally or over ssh, and speaks its framed protocol."""
import io, json, os, shlex, socket, struct, subprocess, time, uuid

from .helper import ev2ahv_helper as helper

HELPER_PATH = os.path.abspath(helper.__file__)
FRAME = helper.FRAME
SSH_OPTS = ["-o", "BatchMode=yes", "-o", "ConnectTimeout=10", "-o", "ServerAliveInterval=15",
            "-o", "ServerAliveCountMax=4", "-o", "Compression=no"]


class Transport:
    """Base: `argv()` builds the command, `helper_cmd()` the helper invocation."""
    sudo = False

    def argv(self, command):
        raise NotImplementedError

    def helper_cmd(self, *args):
        raise NotImplementedError

    def setup(self):
        pass

    def close(self):
        pass

    def run(self, command, check=True, input=None, timeout=600):
        r = subprocess.run(self.argv(command), input=input, stdout=subprocess.PIPE,
                           stderr=subprocess.PIPE, timeout=timeout)
        if check and r.returncode:
            raise RuntimeError(f"command failed ({r.returncode}): {command}: {r.stderr.decode(errors='replace')[:800]}")
        return r.stdout.decode(errors="replace")

    def popen(self, *args, stdin=subprocess.DEVNULL):
        return subprocess.Popen(self.argv(self.helper_cmd(*args)), stdin=stdin, stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE, bufsize=0)

    def caps(self):
        return json.loads(self.run(self.helper_cmd("caps")))

    # privileged steps on the writer; BlockdTransport routes them through the root helper
    def rescan(self):
        self.run("for h in /sys/class/scsi_host/host*/scan; do echo '- - -' > $h; done", check=False)

    def offline(self, device):
        real = self.run(f"readlink -f {shlex.quote(device)}").strip()
        self.run(f"sync; blockdev --flushbufs {real}")
        self.run(f"echo 1 > /sys/block/{os.path.basename(real)}/device/delete")
        self.run("udevadm settle --timeout=20", check=False)

    def claim(self, serial):
        """Record a disk this tool attached (only the appliance's root helper uses the list)."""
        return None

    def size(self, device):
        return int(self.run(self.helper_cmd("size", device)).strip())


class LocalTransport(Transport):
    def __init__(self, sudo=False):
        self.sudo = sudo

    def argv(self, command):
        return ["sudo", "-n", "sh", "-c", command] if self.sudo else ["sh", "-c", command]

    def helper_cmd(self, *args):
        return " ".join(shlex.quote(a) for a in ("python3", "-I", HELPER_PATH) + tuple(map(str, args)))


class SshTransport(Transport):
    """Copies the helper to /tmp on the remote host and removes it on close."""

    def __init__(self, host, user="root", key=None, port=22, sudo=False, known_hosts=None):
        self.host, self.user, self.key, self.port, self.sudo = host, user, key, port, sudo
        self.known_hosts = known_hosts
        self.remote = f"/tmp/ev2ahv_helper_{uuid.uuid4().hex[:12]}.py"
        self.installed = False

    def _base(self):
        cmd = ["ssh"] + SSH_OPTS + ["-p", str(self.port)]
        if self.key:
            cmd += ["-i", self.key]
        if self.known_hosts:
            cmd += ["-o", f"UserKnownHostsFile={self.known_hosts}", "-o", "StrictHostKeyChecking=accept-new"]
        return cmd + [f"{self.user}@{self.host}"]

    def argv(self, command):
        return self._base() + [f"sudo -n sh -c {shlex.quote(command)}" if self.sudo else command]

    def helper_cmd(self, *args):
        return " ".join(shlex.quote(a) for a in ("python3", "-I", self.remote) + tuple(map(str, args)))

    def setup(self):
        if self.installed:
            return
        with open(HELPER_PATH, "rb") as fh:
            body = fh.read()
        r = subprocess.run(self._base() + [f"umask 077; cat > {self.remote}"], input=body,
                           stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=60)
        if r.returncode:
            raise RuntimeError(f"could not install helper on {self.host}: {r.stderr.decode(errors='replace')[:400]}")
        self.installed = True

    def close(self):
        if self.installed:
            subprocess.run(self._base() + [f"rm -f {self.remote}"], stdout=subprocess.DEVNULL,
                           stderr=subprocess.DEVNULL, timeout=30)
            self.installed = False


def read_exact(stream, n):
    buf = bytearray()
    while len(buf) < n:
        part = stream.read(n - len(buf))
        if not part:
            raise EOFError("helper stream closed")
        buf += part
    return bytes(buf)


class BlockEndpoint:
    """One block device reached through a transport: hash, read and (if allowed) write."""

    def __init__(self, transport, device, chunk=4 * 1024 ** 2, max_mbps=0, writable=False):
        self.t, self.device, self.chunk, self.max_mbps = transport, device, chunk, max_mbps
        self.writable = writable
        self.limit = None    # expose only the first N bytes (everRun files carry a metadata trailer)
        self.rate_file = None   # D28: a file holding the current limit in MiB/s; read again while data flows
        self._rate_seen, self._rate_checked = max_mbps, 0.0
        self._reader = self._writer = None
        self.t.setup()

    def caps(self):
        return self.t.caps()

    def size(self):
        size = self.t.size(self.device)
        return size if self.limit is None else min(size, self.limit)

    def hashes(self, algo, start=0, limit=None):
        args = ["hash", self.device, "--chunk", self.chunk, "--algo", algo, "--start", start]
        limit = self.limit if limit is None else limit
        if limit is not None:
            args += ["--limit", limit]
        if self.current_rate(every=0):
            args += ["--max-mbps", self.max_mbps]
        p = self.t.popen(*args)
        done = False
        try:
            for raw in iter(p.stdout.readline, b""):
                a, b = raw.decode().split()
                if a == "done":
                    done = True
                    break
                yield int(a), b
        finally:
            if not done:
                p.kill()
            p.stdout.close()
            err = p.stderr.read().decode(errors="replace")
            p.stderr.close()
            rc = p.wait()
        if not done or rc:
            raise RuntimeError(f"hash failed on {self.device}: {err[:400]}")

    def current_rate(self, now=None, every=2.0):
        """The limit now in MiB/s (0 = unlimited): the rate file when there is one, else the plan's value. The file is
        looked at every `every` seconds at most, so a policy change applies within a few seconds of the server writing it."""
        if not self.rate_file:
            return self.max_mbps
        now = time.monotonic() if now is None else now
        if now - self._rate_checked >= every:
            self._rate_checked = now
            try:
                with open(self.rate_file) as fh:
                    self.max_mbps = max(0.0, float(fh.read().strip()))
            except (OSError, ValueError):
                pass   # missing or half-written: keep the last limit
        return self.max_mbps

    def read(self, indices):
        indices = list(indices)
        rate = self.current_rate(every=1.0)
        if rate and self.rate_file:
            # a slow limit makes a big batch take minutes; hand the helper pieces of about 5 s so a change lands soon
            step = max(1, int(rate * 5 / max(self.chunk / 1024 ** 2, 1)))
            if step < len(indices):
                for i in range(0, len(indices), step):
                    yield from self.read(indices[i:i + step])
                return
        if self._reader is None:
            args = ["read", self.device, "--chunk", self.chunk]
            if self.limit is not None:
                args += ["--limit", self.limit]
            if rate:
                args += ["--max-mbps", rate]
            self._reader = self.t.popen(*args, stdin=subprocess.PIPE)
            self._rate_seen = rate
        p = self._reader
        if self.rate_file and rate != self._rate_seen:
            p.stdin.write(f"rate {rate}\n".encode())
            self._rate_seen = rate
        p.stdin.write("".join(f"{i}\n" for i in indices).encode())
        p.stdin.flush()
        for want in indices:
            index, length = FRAME.unpack(read_exact(p.stdout, FRAME.size))
            if index != want:
                raise RuntimeError(f"helper returned chunk {index}, expected {want}")
            yield index, read_exact(p.stdout, length)

    def write_batch(self, items):
        """Write chunks and return only after the remote side has fsynced them."""
        if not self.writable:
            raise PermissionError(f"{self.device} is not a write target")
        if self._writer is None:
            self._writer = self.t.popen("write", self.device, "--chunk", self.chunk, stdin=subprocess.PIPE)
        p = self._writer
        for index, data in items:
            p.stdin.write(FRAME.pack(index, len(data)))
            p.stdin.write(data)
        p.stdin.write(FRAME.pack(helper.FLUSH, 0))
        p.stdin.flush()
        line = p.stdout.readline().decode().strip()
        if not line.startswith("flushed"):
            raise RuntimeError(f"write failed on {self.device}: {line or p.stderr.read().decode(errors='replace')[:400]}")

    def close(self):
        for attr in ("_reader", "_writer"):
            p = getattr(self, attr)
            if p is None:
                continue
            try:
                if attr == "_writer":
                    p.stdin.write(FRAME.pack(helper.END, 0))
                p.stdin.close()
                p.wait(timeout=60)
            except (OSError, subprocess.TimeoutExpired):
                p.kill()
                p.wait()
            p.stdout.close()
            p.stderr.close()
            setattr(self, attr, None)


BLOCKD_SOCKET = os.environ.get("EV2AHV_BLOCKD_SOCKET", "/run/ev2ahv-blockd.sock")
CLAIMS = os.environ.get("EV2AHV_CLAIMS", "/var/lib/ev2ahv/claims")


class _Refusal(io.RawIOBase):
    """Wraps the helper's stdout and turns a blockd refusal line into an exception."""

    def __init__(self, raw):
        self.raw, self.first = raw, True

    def readable(self):
        return True

    def readinto(self, b):
        if self.first:
            self.first = False
            head = self.raw.peek(15)[:15] if hasattr(self.raw, "peek") else b""
            if head.startswith(b"EV2AHV-REFUSED"):
                raise PermissionError("blockd refused: " + self.raw.readline().decode(errors="replace")[15:].strip())
        data = self.raw.read(len(b))
        n = len(data)
        b[:n] = data
        return n

    def readline(self, *a):
        line = self.raw.readline(*a)
        if line.startswith(b"EV2AHV-REFUSED"):
            raise PermissionError("blockd refused: " + line.decode(errors="replace")[15:].strip())
        return line


class _SockProc:
    """Popen-like view of one blockd connection (stdin, stdout, stderr, wait, kill)."""

    def __init__(self, op, args):
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.sock.connect(BLOCKD_SOCKET)
        self.sock.sendall((json.dumps({"op": op, "args": [str(a) for a in args]}) + "\n").encode())
        self.stdin = self.sock.makefile("wb", buffering=0)
        self.stdout = _Refusal(self.sock.makefile("rb"))
        self.stderr = io.BytesIO(b"(see: journalctl -u 'ev2ahv-blockd@*')")
        self.returncode = None

    def wait(self, timeout=None):
        self.sock.close()
        self.returncode = 0
        return 0

    def poll(self):
        return self.returncode

    def kill(self):
        try:
            self.sock.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        self.sock.close()
        self.returncode = -9

    terminate = kill


class BlockdTransport(LocalTransport):
    """The appliance is its own writer: plain commands run unprivileged, raw disks go through blockd."""

    def __init__(self):
        super().__init__(sudo=False)

    def _call(self, op, *args):
        p = _SockProc(op, args)
        try:
            out = b"".join(iter(lambda: p.stdout.raw.read(65536), b""))
        finally:
            p.wait()
        if out.startswith(b"EV2AHV-REFUSED"):
            raise PermissionError("blockd refused: " + out.decode(errors="replace")[15:].strip())
        return out.decode(errors="replace")

    def popen(self, *args, stdin=subprocess.DEVNULL):
        return _SockProc(args[0], args[1:])

    def caps(self):
        return json.loads(self._call("caps"))

    def size(self, device):
        return int(self._call("size", device).strip())

    def rescan(self):
        self._call("rescan")

    def offline(self, device):
        self._call("offline", device)

    def uuid(self):
        return self._call("uuid").strip()

    def claim(self, serial):
        os.makedirs(os.path.dirname(CLAIMS), exist_ok=True)
        with open(CLAIMS, "a") as fh:
            fh.write(serial + "\n")
