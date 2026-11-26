"""Changed-block tracking with QEMU dirty bitmaps (D5). Design and limits: docs/cbt.md.

Flow per disk (one QEMU block node):
  start()     before the first full pass: add bitmap <prefix>0, which records every guest write from then on
  rotate()    at each resync: in one QMP transaction add <prefix>N+1 and disable <prefix>N, so N is frozen and
              N+1 records from that instant; returns the frozen name
  dirty()     export the node over NBD with the frozen bitmap and read its dirty extents (qemu-img map; QEMU
              reports dirty extents as "data": false), as byte ranges
  drop()      remove the export and the frozen bitmap after the pass succeeded
If a bitmap is missing (guest restarted, FT failover, QEMU replaced), BitmapLost is raised and the caller runs a full
pass after start() again.

QMP transports: UnixQmp (qemu-storage-daemon or a test QEMU) and VirshQmp (virsh qemu-monitor-command over ssh;
lab-tested on everRun 8.1.1, see docs/cbt.md; it changes the running guest's QEMU state, so it stays off unless a plan asks for it).
"""
import json, shlex, socket, subprocess


class BitmapLost(RuntimeError):
    pass


class UnixQmp:
    def __init__(self, path):
        self.path = path

    def __call__(self, execute, arguments=None):
        s = socket.socket(socket.AF_UNIX)
        s.connect(self.path)
        f = s.makefile("rw")
        try:
            json.loads(f.readline())
            f.write(json.dumps({"execute": "qmp_capabilities"}) + "\n")
            f.flush()
            json.loads(f.readline())
            f.write(json.dumps({"execute": execute, **({"arguments": arguments} if arguments else {})}) + "\n")
            f.flush()
            while True:
                r = json.loads(f.readline())
                if "event" not in r:
                    break
        finally:
            f.close()
            s.close()
        if "error" in r:
            raise RuntimeError(f"QMP {execute}: {r['error'].get('desc')}")
        return r.get("return")


class VirshQmp:
    """QMP through libvirt on an everRun node (needs read-write virsh). Not used unless sync.cbt is set."""
    def __init__(self, shell, domain):
        self.shell, self.domain = shell, domain

    def __call__(self, execute, arguments=None):
        cmd = json.dumps({"execute": execute, **({"arguments": arguments} if arguments else {})})
        r = json.loads(self.shell.run(f"virsh qemu-monitor-command {shlex.quote(self.domain)} {shlex.quote(cmd)}"))
        if "error" in r:
            raise RuntimeError(f"QMP {execute}: {r['error'].get('desc')}")
        return r.get("return")


def find_node(qmp, path):
    """The format-layer block node of the guest disk backed by file `path` (everRun: libvirt-N-format)."""
    for nd in qmp("query-named-block-nodes") or []:
        if nd.get("file") == path and nd.get("drv") != "file":
            return nd["node-name"]
    raise BitmapLost(f"no block node serves {path}")


def ranges_to_chunks(ranges, chunk):
    """Byte ranges [(start, length)] to the sorted chunk indexes they touch."""
    out = set()
    for start, length in ranges:
        if length > 0:
            out.update(range(start // chunk, (start + length - 1) // chunk + 1))
    return sorted(out)


def parse_map(entries):
    """qemu-img map --output=json over an NBD export with x-dirty-bitmap: dirty extents have data == false."""
    return [(e["start"], e["length"]) for e in entries if not e.get("data")]


class QemuBitmap:
    def __init__(self, qmp, node, nbd_socket, prefix="ev2ahv-cbt-", map_cmd=None, start_server=False):
        self.start_server = start_server  # everRun: the guest QEMU has no NBD server yet; start it on a unix socket
        self.qmp, self.node, self.sock, self.prefix = qmp, node, nbd_socket, prefix
        self.map_cmd = map_cmd or (lambda argv: subprocess.run(argv, check=True, capture_output=True, text=True).stdout)

    def _ours(self):
        names = []
        for dev in self.qmp("query-block") or []:
            ins = dev.get("inserted") or {}
            if self.node in (ins.get("node-name"), dev.get("device")):
                names += [b["name"] for b in ins.get("dirty-bitmaps") or [] if b["name"].startswith(self.prefix)]
        for nd in self.qmp("query-named-block-nodes") or []:
            if nd.get("node-name") == self.node:
                names += [b["name"] for b in nd.get("dirty-bitmaps") or [] if b["name"].startswith(self.prefix)]
        return sorted(set(names), key=lambda n: int(n[len(self.prefix):]))

    def start(self):
        """Begin tracking from now. Removes any older ev2ahv bitmaps on the node first."""
        for name in self._ours():
            self.qmp("block-dirty-bitmap-remove", {"node": self.node, "name": name})
        self.qmp("block-dirty-bitmap-add", {"node": self.node, "name": self.prefix + "0"})
        return self.prefix + "0"

    def rotate(self):
        ours = self._ours()
        if not ours:
            raise BitmapLost(f"no {self.prefix}* bitmap on {self.node}: tracking was interrupted")
        cur = ours[-1]
        nxt = self.prefix + str(int(cur[len(self.prefix):]) + 1)
        self.qmp("transaction", {"actions": [
            {"type": "block-dirty-bitmap-add", "data": {"node": self.node, "name": nxt}},
            {"type": "block-dirty-bitmap-disable", "data": {"node": self.node, "name": cur}}]})
        return cur

    def dirty(self, frozen):
        export = "ev2ahv-cbt-export"
        started = False
        if self.start_server:
            self.qmp("nbd-server-start", {"addr": {"type": "unix", "data": {"path": self.sock}}})
            started = True
        self.qmp("block-export-add", {"type": "nbd", "id": export, "node-name": self.node, "name": export,
                                      "bitmaps": [frozen]})
        try:
            out = self.map_cmd(["qemu-img", "map", "--output=json", "--image-opts",
                                f"driver=nbd,server.type=unix,server.path={self.sock},export={export},"
                                f"x-dirty-bitmap=qemu:dirty-bitmap:{frozen}"])
        finally:
            self.qmp("block-export-del", {"id": export})
            if started:
                self.qmp("nbd-server-stop")
        return parse_map(json.loads(out))

    def drop(self, frozen):
        self.qmp("block-dirty-bitmap-remove", {"node": self.node, "name": frozen})
