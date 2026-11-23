"""Builds the v4 createVm payload for the migrated VM."""


def align(size, unit=1024 ** 2):
    """AHV disks are sized in whole MiB; a source is rounded up, never truncated."""
    return (int(size) + unit - 1) // unit * unit


def boot_config(firmware, bus, index=0):
    device = {"$objectType": "vmm.v4.ahv.config.BootDeviceDisk",
              "diskAddress": {"busType": bus, "index": index}}
    if firmware == "uefi":
        return {"$objectType": "vmm.v4.ahv.config.UefiBoot", "isSecureBootEnabled": False, "bootDevice": device}
    return {"$objectType": "vmm.v4.ahv.config.LegacyBoot", "bootDevice": device}


def disk_entries(plan, sources):
    """sources: list of (size, clone reference). Index is per bus, in plan order."""
    next_index, out = {}, []
    default = plan["target"]["disk_bus"]
    for disk, (size, reference) in zip(plan["disks"], sources):
        bus = (disk.get("bus") or default).upper()
        index = next_index.get(bus, 0)
        next_index[bus] = index + 1
        out.append({"diskAddress": {"busType": bus, "index": index},
                    "backingInfo": {"$objectType": "vmm.v4.ahv.config.VmDisk", "diskSizeBytes": int(size),
                                    "dataSource": {"reference": reference}}})
    return out


def nic_entries(plan, subnets):
    out = []
    for nic in plan.get("nics") or []:
        backing = {"model": nic.get("model", "VIRTIO"), "isConnected": True}
        if nic.get("keep_mac", True) and nic.get("mac"):
            backing["macAddress"] = nic["mac"]
        out.append({"backingInfo": backing,
                    "networkInfo": {"nicType": "NORMAL_NIC", "subnet": {"extId": subnets[nic["subnet"]]},
                                    "vlanMode": "ACCESS"}})
    return out


def vm_payload(plan, cluster_id, sources, subnets):
    t = plan["target"]
    disks = disk_entries(plan, sources)
    first = disks[0]["diskAddress"]
    return {"name": t["vm_name"], "description": f"Migrated by ev2ahv, plan {plan['name']}",
            "numSockets": int(t["sockets"]), "numCoresPerSocket": int(t["cores"]),
            "numThreadsPerCore": int(t["threads"]), "memorySizeBytes": int(t["memory_mib"]) * 1024 ** 2,
            "machineType": t["machine_type"], "cluster": {"extId": cluster_id},
            "bootConfig": boot_config(t["firmware"], first["busType"], first["index"]),
            "disks": disks, "nics": nic_entries(plan, subnets)}
