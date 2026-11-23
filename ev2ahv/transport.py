"""Runs the block helper locally or over ssh, and speaks its framed protocol."""
import json, os, shlex, struct, subprocess, uuid

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
        self._reader = self._writer = None
        self.t.setup()

    def caps(self):
        return self.t.caps()

    def size(self):
        return self.t.size(self.device)

    def hashes(self, algo, start=0):
        args = ["hash", self.device, "--chunk", self.chunk, "--algo", algo, "--start", start]
        if self.max_mbps:
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

    def read(self, indices):
        if self._reader is None:
            args = ["read", self.device, "--chunk", self.chunk]
            if self.max_mbps:
                args += ["--max-mbps", self.max_mbps]
            self._reader = self.t.popen(*args, stdin=subprocess.PIPE)
        p = self._reader
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
            setattr(self, attr, None)
