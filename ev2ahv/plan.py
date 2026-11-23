"""Migration plan: YAML, validated before anything runs."""
import re
import yaml

MAC = re.compile(r"^[0-9a-f]{2}(:[0-9a-f]{2}){5}$")
DEFAULTS = {"sync": {"chunk_mib": 4, "algo": "auto", "batch": 32}, "verify": {}}


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
    for key in ("vm", "host"):
        check(lambda k=key: _need(p["writer"], k, "writer"))
    t, s = p["target"], p["source"]
    for key in ("vm_name", "cluster", "container"):
        check(lambda k=key: _need(t, k, "target"))
    t.setdefault("firmware", "bios"); t.setdefault("disk_bus", "scsi"); t.setdefault("machine_type", "PC")
    t.setdefault("transfer", "hotadd"); t.setdefault("staging_dir", "/var/tmp/ev2ahv-staging")
    if t["transfer"] not in ("hotadd", "image"):
        errors.append("target.transfer must be hotadd or image")
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
    for section, values in DEFAULTS.items():
        p.setdefault(section, {})
        for k, v in values.items():
            p[section].setdefault(k, v)
    if p["sync"]["algo"] not in ("auto", "xxh64", "blake2b", "sha256"):
        errors.append("sync.algo must be auto, xxh64, blake2b or sha256")
    if errors:
        raise PlanError("; ".join(errors))
    return p


def load(path):
    with open(path) as fh:
        return validate(yaml.safe_load(fh) or {})


def dump(plan, path):
    with open(path, "w") as fh:
        yaml.safe_dump(plan, fh, sort_keys=False)
