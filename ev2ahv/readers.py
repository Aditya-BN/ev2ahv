"""Pluggable source readers. All expose the BlockEndpoint interface: caps, size, hashes, read, close."""
import os, shlex

from .transport import BlockEndpoint, LocalTransport, SshTransport


class SourceReader(BlockEndpoint):
    kind = "base"

    def __init__(self, transport, device, chunk, max_mbps=0):
        super().__init__(transport, device, chunk=chunk, max_mbps=max_mbps, writable=False)

    def close(self):
        super().close()
        self.t.close()


class LocalReader(SourceReader):
    """A file or loop device on this machine."""
    kind = "local"

    def __init__(self, path, chunk=4 * 1024 ** 2, max_mbps=0, sudo=False):
        super().__init__(LocalTransport(sudo=sudo), path, chunk, max_mbps)


class DirectBlockReader(SourceReader):
    """A block device on a remote host, over ssh, through the temporary read-only helper."""
    kind = "direct"

    def __init__(self, host, device, user="root", key=None, port=22, sudo=False, known_hosts=None,
                 chunk=4 * 1024 ** 2, max_mbps=0):
        super().__init__(SshTransport(host, user, key, port, sudo, known_hosts), device, chunk, max_mbps)


class ExportReader(SourceReader):
    """An OVF/VHD/qcow2/raw export, converted once to raw with qemu-img on the given transport."""
    kind = "export"

    def __init__(self, path, workdir, transport=None, chunk=4 * 1024 ** 2, max_mbps=0):
        transport = transport or LocalTransport()
        transport.setup()
        raw = os.path.join(workdir, os.path.basename(path) + ".ev2ahv.raw")
        q_in, q_raw = shlex.quote(path), shlex.quote(raw)
        transport.run(f"mkdir -p {shlex.quote(workdir)}; [ -s {q_raw} ] && [ {q_raw} -nt {q_in} ] || "
                      f"qemu-img convert -p -O raw {q_in} {q_raw}.tmp && mv -f {q_raw}.tmp {q_raw} || [ -s {q_raw} ]",
                      timeout=7200)
        self.raw = raw
        super().__init__(transport, raw, chunk, max_mbps)


def make_reader(src, disk, chunk, default_key=None, known_hosts=None):
    kind, mbps = src["type"], src.get("max_mbps", 0)
    if kind == "local":
        return LocalReader(disk["path"], chunk, mbps, sudo=src.get("sudo", False))
    if kind == "direct":
        return DirectBlockReader(src["host"], disk["path"], src.get("user", "root"), src.get("key") or default_key,
                                 src.get("port", 22), src.get("sudo", False), known_hosts, chunk, mbps)
    if kind == "export":
        t = None
        if src.get("host"):
            t = SshTransport(src["host"], src.get("user", "root"), src.get("key") or default_key,
                             src.get("port", 22), src.get("sudo", False), known_hosts)
        return ExportReader(disk["path"], src.get("workdir", "/var/tmp/ev2ahv"), t, chunk, mbps)
    raise ValueError(f"unknown source type {kind}")
