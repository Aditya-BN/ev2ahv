"""Snapshot-export source mode (D27, PREVIEW, feature flag, default off, not lab-tested yet).

Uses only what Stratus documents for getting data out of a running VM: `avcli vm-snapshot-create`, then
`avcli vm-snapshot-export --format vhd|vhdx --path <dir>` to an NFS/CIFS share that the node has mounted and the
appliance can read. The appliance converts the exported VHD/VHDX to raw with qemu-img and syncs it with the normal
engine. No QEMU monitor, no libvirt writes, no reads of live volume files.

Not like the direct mode: an export is a full copy at a point in time, so a resync is another full export. The mode is
therefore: one export for the seed (VM running, crash-consistent unless the guest agent quiesces), no scheduled
resyncs, and a final sync from a second export taken after the VM is shut down (that second export is the downtime).
See docs/snapshot-export.md.

The avcli flags below follow the Stratus CLI pages (see docs/research/stratus-support.md); create/delete flags are not
verified on a real system yet, so every command is logged in full and `source.export.create_args` can override them.
"""
import json, logging, os, re, shlex, time
import xml.etree.ElementTree as ET

from .readers import ExportReader
from .transport import LocalTransport, ssh_transport

log = logging.getLogger("ev2ahv.snapexport")
SAFE = re.compile(r"^[\w.-]+$")


def snapshot_name(plan, label):
    return f"ev2ahv-{plan}-{label}"[:60]


def avcli_prefix(user):
    """`avcli` against the local system; the password arrives on stdin and is read into a shell variable."""
    return f"read -r EV2AHV_PW; avcli -H localhost -u {shlex.quote(user)} -p \"$EV2AHV_PW\""


def create_cmd(user, vm, name, create_args=None):
    args = create_args if create_args is not None else ["--name", name]
    return f"{avcli_prefix(user)} vm-snapshot-create {' '.join(map(shlex.quote, args))} {shlex.quote(vm)}"


def export_cmd(user, snapshot, node_dir, fmt="vhdx"):
    if fmt not in ("vhd", "vhdx"):
        raise ValueError("format must be vhd or vhdx")
    return (f"{avcli_prefix(user)} vm-snapshot-export --wait --format {fmt} --path {shlex.quote(node_dir)} "
            f"{shlex.quote(snapshot)}")


def delete_cmd(user, snapshot):
    return f"{avcli_prefix(user)} vm-snapshot-delete {shlex.quote(snapshot)}"


def ovf_disks(text):
    """Disk file names in an OVF, in the order of its References section."""
    root = ET.fromstring(text)
    out = []
    for el in root.iter():
        if el.tag.rsplit("}", 1)[-1] == "File":
            href = next((v for k, v in el.attrib.items() if k.rsplit("}", 1)[-1] == "href"), None)
            if href and re.search(r"\.vhdx?$", href, re.I):
                out.append(href)
    return out


class SnapshotExporter:
    """snapshot -> export -> wait for the OVF -> raw files. `node` runs avcli on an everRun node; `local` runs on the appliance."""

    def __init__(self, src, plan_name, password, node=None, local=None):
        e = src["export"]
        self.vm, self.user, self.plan = src["vm"], src.get("everrun_user", "admin"), plan_name
        self.node_root, self.local_root = e["node_path"].rstrip("/"), e["appliance_path"].rstrip("/")
        self.fmt, self.create_args = e.get("format", "vhdx"), e.get("create_args")
        self.keep_snapshot, self.timeout = bool(e.get("keep_snapshot")), int(e.get("timeout", 4 * 3600))
        self.workdir = src.get("workdir", "/var/tmp/ev2ahv")
        self.password = password or ""
        self.node = node or ssh_transport(src["host"], src.get("user", "root"), src.get("key"), src.get("port", 22),
                                          False, src.get("known_hosts"))
        self.local = local or LocalTransport()

    def _avcli(self, cmd, timeout=600):
        log.info("avcli on node: %s", cmd)   # never contains the password: it goes in on stdin
        return self.node.run(cmd, input=(self.password + "\n").encode(), timeout=timeout)

    def export(self, label, expected=None):
        """Take one snapshot and export it. Returns {"dir", "snapshot", "files": [appliance paths in OVF order]}."""
        if not SAFE.match(label):
            raise ValueError("bad export label")
        name = snapshot_name(self.plan, label)
        sub = f"{self.plan}-{label}"
        local_dir, node_dir = f"{self.local_root}/{sub}", f"{self.node_root}/{sub}"
        self.local.run(f"mkdir -p {shlex.quote(local_dir)}")
        self._avcli(create_cmd(self.user, self.vm, name, self.create_args))
        try:
            self._avcli(export_cmd(self.user, name, node_dir, self.fmt), timeout=self.timeout)
            files = self.wait_files(local_dir)
        finally:
            if not self.keep_snapshot:
                try:
                    self._avcli(delete_cmd(self.user, name))
                except Exception as exc:   # an undeleted snapshot costs space on everRun: say so, do not fail the pass
                    log.warning("snapshot %s was not deleted, remove it in the everRun console: %s", name, exc)
        if expected is not None and len(files) != expected:
            raise RuntimeError(f"export has {len(files)} disk file(s), the plan has {expected}")
        return {"dir": local_dir, "snapshot": name, "files": files}

    def wait_files(self, local_dir):
        """Stratus: the export is complete when the OVF appears. Read the disk list from it."""
        deadline = time.time() + self.timeout
        while True:
            out = self.local.run(f"ls -1 {shlex.quote(local_dir)}", check=False).split()
            ovf = next((f for f in out if f.lower().endswith(".ovf")), None)
            if ovf:
                text = self.local.run(f"cat {shlex.quote(local_dir + '/' + ovf)}")
                names = ovf_disks(text) or sorted(f for f in out if re.search(r"\.vhdx?$", f, re.I))
                return [f"{local_dir}/{n}" for n in names]
            if time.time() > deadline:
                raise RuntimeError(f"export did not finish in {self.timeout}s: no OVF in {local_dir}")
            time.sleep(10)

    def virtual_size(self, path):
        return int(json.loads(self.local.run(f"qemu-img info --output=json {shlex.quote(path)}"))["virtual-size"])

    def reader(self, path, label, chunk, max_mbps=0):
        """Raw view of one exported disk (qemu-img convert into the workdir), as a normal source reader."""
        return ExportReader(path, f"{self.workdir}/{self.plan}-{label}", self.local, chunk, max_mbps)

    def remove(self, info):
        """Delete an export directory and its raw copies (called when the plan is cleaned up)."""
        d = info.get("dir", "")
        if d and d.startswith(self.local_root + "/") and ".." not in d:
            self.local.run(f"rm -rf {shlex.quote(d)}", check=False)
