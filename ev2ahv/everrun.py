"""Read-only discovery of an everRun guest, and turning it into a migration plan.

Facts established on everRun 8.1.1 (see docs/discovery.md):
- each guest disk is a raw sparse file under /mnt/everrun, one copy per node;
- the last 2 KiB of the file is everRun metadata that the guest never sees;
- for FT guests the partner domain on the other node is paused, so the running one is the source.
"""
import json, os, re, shlex
import xml.etree.ElementTree as ET

from .target import CONFIG
from .transport import SshTransport, SudoExactTransport

TRAILER_BYTES = 2048
DEFAULT_STORAGE = "/mnt/everrun"


def parse_domain(xml):
    root = ET.fromstring(xml)
    loader = root.find("os/loader")
    disks, nics = [], []
    for d in root.findall("devices/disk"):
        if d.get("device") != "disk":
            continue
        src = d.find("source")
        driver = d.find("driver")
        path = src.get("file") or src.get("dev")
        disks.append({"dev": d.find("target").get("dev"), "path": path,
                      "format": driver.get("type") if driver is not None else None})
    for n in root.findall("devices/interface"):
        model = n.find("model")
        nics.append({"mac": n.find("mac").get("address").lower(),
                     "model": model.get("type") if model is not None else None})
    return {"name": root.findtext("name"), "vcpus": int(root.findtext("vcpu")),
            "memory_mib": int(root.findtext("memory")) // 1024,
            "firmware": "uefi" if loader is not None else "bios", "disks": disks, "nics": nics}


def active_node(states):
    """states: {host: libvirt state}. Exactly one node may be running the guest."""
    running = [h for h, s in states.items() if s == "running"]
    if len(running) != 1:
        raise RuntimeError(f"expected the guest to be running on exactly one node, saw {states}")
    return running[0]


def node_transport(node, ssh_user="root", key=None, storage_path=DEFAULT_STORAGE):
    """Root: the classic ssh transport. Any other user: sudo -n with exact commands only (docs/least-privilege.md)."""
    key = key or os.path.join(CONFIG, "id_ed25519")
    hosts = os.path.join(CONFIG, "known_hosts")
    if (ssh_user or "root") == "root":
        return SshTransport(node, "root", key, known_hosts=hosts)
    return SudoExactTransport(node, ssh_user, key, known_hosts=hosts, storage_path=storage_path)


def virsh_ro(t, *args):
    """A read-only virsh command line for this transport (`virsh -r ...`, or `sudo -n /usr/bin/virsh -r ...`)."""
    if isinstance(t, SudoExactTransport):
        return t.virsh(*args)
    return " ".join(shlex.quote(a) for a in ("virsh", "-r") + tuple(map(str, args)))


def file_size(t, path):
    """Size of a volume file: stat as root, the installed read-only helper for a least-privilege account."""
    if isinstance(t, SudoExactTransport):
        return t.size(path)
    return int(t.run(f"stat -c %s {shlex.quote(path)}").strip())


def list_domains(t):
    return [n for n in t.run(virsh_ro(t, "list", "--all", "--name")).split("\n") if n.strip()]


def domain_state(t, name):
    return (t.run(virsh_ro(t, "domstate", name) + " 2>/dev/null || echo absent").strip().split("\n") or [""])[0]


def discover(vm, nodes, key=None, ssh_user="root", storage_path=DEFAULT_STORAGE):
    states = {}
    for node in nodes:
        states[node] = domain_state(node_transport(node, ssh_user, key, storage_path), vm)
    host = active_node(states)
    t = node_transport(host, ssh_user, key, storage_path)
    info = parse_domain(t.run(virsh_ro(t, "dumpxml", vm)))
    for d in info["disks"]:
        if d["format"] != "raw":
            raise RuntimeError(f"{d['dev']} is {d['format']}, only raw everRun disks are supported directly")
        d["file_bytes"] = file_size(t, d["path"])
        d["guest_bytes"] = d["file_bytes"] - TRAILER_BYTES
    info.update(active_host=host, node_states=states)
    return info


def build_plan(info, target_name, subnet, avcli, writer=None, container="VM_Container", cluster="NTNX-DC",
               max_mbps=100, disk_bus="scsi", plan_name=None, ssh_user="root", storage_path=DEFAULT_STORAGE):
    vm = shlex.quote(info["name"])
    cli = shlex.quote(os.path.expanduser(avcli))
    return {
        "name": plan_name or re.sub(r"[^\w.-]", "-", target_name),
        "writer": writer or {"vm": "ev2ahv-writer", "host": "10.12.136.211", "user": "nutanix"},
        "source": {"type": "direct", "qemu_domain": info["name"], "host": info["active_host"], "user": ssh_user, "max_mbps": max_mbps,
                   "stop_timeout": 600, **({"sudo": "exact", "storage_path": storage_path} if ssh_user != "root" else {}),
                   "power": {"local": True,
                             "status": f"{cli} vm-info {vm} | awk '/-> state/{{print $NF; exit}}'",
                             "stop": f"{cli} vm-shutdown {vm}", "start": f"{cli} vm-poweron {vm}"}},
        "disks": [{"name": d["dev"], "path": d["path"], "size_bytes": d["guest_bytes"]} for d in info["disks"]],
        "nics": [{"subnet": subnet, "mac": n["mac"], "keep_mac": True} for n in info["nics"]],
        "target": {"vm_name": target_name, "cluster": cluster, "container": container, "sockets": 1,
                   "cores": info["vcpus"], "threads": 1, "memory_mib": info["memory_mib"],
                   "firmware": info["firmware"], "disk_bus": disk_bus},
        "sync": {"chunk_mib": 4, "algo": "auto", "batch": 32}, "verify": {}}
