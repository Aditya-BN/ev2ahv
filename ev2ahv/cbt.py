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
import json, logging, shlex, socket, subprocess, time

log = logging.getLogger("ev2ahv.cbt")


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

    def has_bitmap(self):
        return bool(self._ours())

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


class NotReady(RuntimeError):
    """The final capture could not begin; the guest was not touched, so the normal stop and a full pass apply."""


class CaptureFailed(RuntimeError):
    """The guest was told to halt and then something went wrong: halted is True, the caller must power it off and
    run a full final pass."""


def blockstats_total(qmp):
    """Write plus flush operations the guest has issued on every disk (it only grows while the guest writes)."""
    return sum(d.get("stats", {}).get("wr_operations", 0) + d.get("stats", {}).get("flush_operations", 0)
               for d in qmp("query-blockstats") or [])


def halt_and_capture(qmp, trackers, halt, guest_up, chunk, timeout=600, quiet=5.0, poll=0.5, sleep=time.sleep,
                     now=time.monotonic):
    """Final dirty set with the guest stopped (docs/cbt.md, "Final sync").
    everRun's QEMU exits the moment the guest powers off, taking the bitmaps with it, and a graceful stop leaves no
    window to read them. So: halt the guest from inside (the OS unmounts and stops with the CPU still powered, so QEMU
    stays alive), wait until it answers nothing and writes nothing for `quiet` seconds, pause the vCPUs over QMP (from
    then on no write is possible and the bitmap holds every write up to that point), rotate and read each disk's
    bitmap. Returns {disk: sorted chunk indexes}. NotReady before the halt was sent; CaptureFailed after it."""
    try:
        st = qmp("query-status")
        if not st or st.get("status") != "running":
            raise NotReady(f"guest QEMU is {st and st.get('status')}, not running on this node")
        for name, bm in trackers.items():
            if not bm.has_bitmap():
                raise NotReady(f"{name}: no tracking bitmap, so it was interrupted")
    except NotReady:
        raise
    except Exception as e:
        raise NotReady(str(e))
    try:
        halt()
    except Exception as e:
        raise NotReady(f"cannot send the halt to the guest: {e}")
    try:
        t_halt = now()
        deadline, last, last_up, still_since = t_halt + timeout, None, None, None
        while True:
            writes = blockstats_total(qmp)
            up = guest_up()
            if writes != last or up != last_up:
                log.info("halt wait: %.1f s, guest %s, disk operations %d", now() - t_halt, "up" if up else "silent", writes)
            last_up = up
            if up or writes != last:
                still_since = None
            elif still_since is None:
                still_since = now()
            last = writes
            if still_since is not None and now() - still_since >= quiet:
                break
            if now() > deadline:
                raise CaptureFailed("the guest did not halt in time")
            sleep(poll)
        before = blockstats_total(qmp)
        qmp("stop")
        st = qmp("query-status")
        if not st or st.get("status") != "paused":
            raise CaptureFailed(f"the guest did not pause (status {st and st.get('status')})")
        out = {}
        for name, bm in trackers.items():
            frozen = bm.rotate()
            out[name] = ranges_to_chunks(bm.dirty(frozen), chunk)
        # everRun resumes a paused guest within a second (seen on the FT guest), so the pause is not the guarantee: the
        # guest wrote nothing at all between the quiet check and the last extent read, or the dirty set is not trusted
        if blockstats_total(qmp) != before:
            raise CaptureFailed("the guest wrote to its disks during the capture")
        return out
    except CaptureFailed:
        raise
    except Exception as e:
        raise CaptureFailed(str(e))
