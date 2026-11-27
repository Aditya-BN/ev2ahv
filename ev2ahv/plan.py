"""Migration plan: YAML, validated before anything runs."""
import os
import re
import yaml

SAFE_NAME = re.compile(r"^\w[\w.-]*$")   # plan and disk names end up in file names on the writer
MAC = re.compile(r"^[0-9a-f]{2}(:[0-9a-f]{2}){5}$")
DEFAULTS = {"sync": {"chunk_mib": 4, "algo": "auto", "batch": 32, "cbt": "off", "final_verify": "full"}, "verify": {}}


class PlanError(ValueError):
    pass


def _need(d, key, where):
    if d.get(key) in (None, ""):
        raise PlanError(f"{where}.{key} is required")
    return d[key]


def validate(p):
    errors = []
    def check(fn):
        try:
            fn()
        except PlanError as e:
            errors.append(str(e))
    for section in ("writer", "target", "source"):
        if not isinstance(p.get(section), dict):
            errors.append(f"{section} section is required")
    if errors:
        raise PlanError("; ".join(errors))
    check(lambda: _need(p, "name", "plan"))
    if p.get("name") and not SAFE_NAME.match(str(p["name"])):
        errors.append("plan.name may only contain letters, digits, '.', '_' and '-'")
    if not p["writer"].get("local"):   # local: the appliance itself is the writer
        for key in ("vm", "host"):
            check(lambda k=key: _need(p["writer"], k, "writer"))
    t, s = p["target"], p["source"]
    for key in ("vm_name", "cluster", "container"):
        check(lambda k=key: _need(t, k, "target"))
    if str(t.get("vm_name") or "").startswith("everrun-"):
        errors.append("target.vm_name must not start with everrun-: those are production VMs")
    t.setdefault("firmware", "bios"); t.setdefault("disk_bus", "scsi"); t.setdefault("machine_type", "PC")
    t.setdefault("convert_to_scsi", True)    # only acts when disk_bus is pci
    t.setdefault("transfer", "hotadd"); t.setdefault("staging_dir", "/var/tmp/ev2ahv-staging")
    sd = str(t["staging_dir"])
    if not sd.startswith("/") or sd != os.path.normpath(sd) or sd.count("/") < 2:
        errors.append("target.staging_dir must be a normalised absolute path at least two levels deep")
    if t["transfer"] not in ("hotadd", "image"):
        errors.append("target.transfer must be hotadd or image")
    t.setdefault("image_format", "qcow2")   # image transfer only: qcow2 (convert first) or raw (upload as is, D14)
    if t["image_format"] not in ("qcow2", "raw"):
        errors.append("target.image_format must be qcow2 or raw")
    t.setdefault("sockets", 1); t.setdefault("cores", 2); t.setdefault("threads", 1); t.setdefault("memory_mib", 2048)
    if t["firmware"] not in ("bios", "uefi"):
        errors.append("target.firmware must be bios or uefi")
    if t["disk_bus"] not in ("scsi", "pci"):
        errors.append("target.disk_bus must be scsi or pci")
    if s.get("type") not in ("local", "direct", "export"):
        errors.append("source.type must be local, direct or export")
    if s.get("type") == "direct" and not s.get("host"):
        errors.append("source.host is required for direct")
    if not p.get("disks"):
        errors.append("at least one disk is required")
    for i, d in enumerate(p.get("disks") or []):
        if not d.get("path"):
            errors.append(f"disks[{i}].path is required")
        d.setdefault("name", f"disk{i}")
        if not SAFE_NAME.match(str(d["name"])):
            errors.append(f"disks[{i}].name may only contain letters, digits, '.', '_' and '-'")
        if d.get("size_bytes") is not None and (not isinstance(d["size_bytes"], int) or d["size_bytes"] <= 0):
            errors.append(f"disks[{i}].size_bytes must be a positive integer")
        if d.get("bus") not in (None, "scsi", "pci"):
            errors.append(f"disks[{i}].bus must be scsi or pci")
    names = [d.get("name") for d in p.get("disks") or []]
    if len(set(names)) != len(names):
        errors.append("disk names must be unique")
    for i, n in enumerate(p.get("nics") or []):
        if not n.get("subnet"):
            errors.append(f"nics[{i}].subnet is required")
        n.setdefault("keep_mac", True)
        if n.get("mac"):
            n["mac"] = n["mac"].lower()
            if not MAC.match(n["mac"]):
                errors.append(f"nics[{i}].mac is invalid")
        elif n["keep_mac"]:
            errors.append(f"nics[{i}].mac is required when keep_mac is true")
    g = p.get("guest")
    if g is not None:
        if g.get("os") not in ("windows", "linux"):
            errors.append("guest.os must be windows or linux")
        g.setdefault("prep", "manual")
        if g["prep"] not in ("auto", "manual", "none"):
            errors.append("guest.prep must be auto, manual or none")
        if g["prep"] == "auto" and not g.get("address"):
            errors.append("guest.address is required for guest.prep auto")
        if g.get("os") == "windows" and g["prep"] == "auto" and not (g.get("password_env") or g.get("password_file")):
            errors.append("guest.password_env or guest.password_file is required for Windows auto prep")
        if g.get("password"):
            errors.append("guest.password is not allowed in a plan. Use password_env or password_file")
    for section, values in DEFAULTS.items():
        p.setdefault(section, {})
        for k, v in values.items():
            p[section].setdefault(k, v)
    if p["sync"]["algo"] not in ("auto", "xxh64", "blake2b", "sha256"):
        errors.append("sync.algo must be auto, xxh64, blake2b or sha256")
    if p["sync"]["cbt"] not in ("auto", "off"):
        errors.append("sync.cbt must be auto or off")
    if p["sync"]["final_verify"] not in ("full", "sample", "none"):
        errors.append("sync.final_verify must be full, sample or none")
    if p["sync"]["cbt"] == "auto" and not (p["source"].get("qemu_domain") and p["source"].get("type") == "direct"):
        errors.append("sync.cbt: auto needs source.type direct and source.qemu_domain (the libvirt domain name)")
    if errors:
        raise PlanError("; ".join(errors))
    return p


def load(path):
    with open(path) as fh:
        return validate(yaml.safe_load(fh) or {})


def dump(plan, path):
    with open(path, "w") as fh:
        yaml.safe_dump(plan, fh, sort_keys=False)
