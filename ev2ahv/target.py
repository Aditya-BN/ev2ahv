"""AHV staging disks on the writer VM. A device is only ever chosen by proof, never by name:
it must be new since a baseline taken before the hot-add, exactly the right size, and unused."""
import json, logging, os, time

from .transport import SshTransport

log = logging.getLogger("ev2ahv.target")
CONFIG = os.path.expanduser(os.environ.get("EV2AHV_CONFIG_DIR", "~/.config/ev2ahv"))


def writer_transport(host, user="nutanix"):
    return SshTransport(host, user=user, key=os.path.join(CONFIG, "id_ed25519"), sudo=True,
                        known_hosts=os.path.join(CONFIG, "known_hosts"))


def block_devices(t):
    t.run("udevadm settle --timeout=20", check=False)
    doc = json.loads(t.run("lsblk -J -b -d -o NAME,SIZE,TYPE,SERIAL,MOUNTPOINT"))
    return {d["name"]: d for d in doc["blockdevices"] if d["type"] == "disk"}


def stable_path(t, name):
    out = t.run(f"find /dev/disk/by-id -lname '*/{name}' -name 'scsi-SNUTANIX_VDISK_*'", check=False).split()
    return out[0] if out else f"/dev/{name}"


def pick_new_device(before, after, size):
    """Pure decision logic, unit tested: exactly one new, exact-size, unmounted disk."""
    new = [d for n, d in after.items() if n not in before]
    matches = [d for d in new if int(d["size"]) == int(size) and not d.get("mountpoint")]
    if len(new) != 1 or len(matches) != 1:
        raise RuntimeError(f"cannot identify staging device safely: new={[d['name'] for d in new]}, "
                           f"exact-size matches={[d['name'] for d in matches]}")
    return matches[0]


def attach_staging(prism, t, writer_vm, size, container=None, clone_from=None, timeout=90):
    """Hot-add a disk to the writer. Returns (disk extId, stable device path)."""
    before = block_devices(t)
    disk = prism.add_disk(writer_vm, size, container=container, clone_from=clone_from)
    deadline = time.time() + timeout
    while True:
        after = block_devices(t)
        if len(after) > len(before):
            break
        if time.time() > deadline:
            raise RuntimeError(f"disk {disk} did not appear on the writer")
        t.rescan()
        time.sleep(2)
    dev = pick_new_device(before, after, size)
    path = stable_path(t, dev["name"])
    if "scsi-SNUTANIX_VDISK_" in path:
        t.claim(path.rsplit("scsi-SNUTANIX_VDISK_", 1)[1])
    log.info("disk %s is %s (%s)", disk, path, dev.get("serial"))
    return disk, path


def quiesce_device(t, path):
    """Flush, then drop the device from the guest so the clone is taken from settled data."""
    t.offline(path)
