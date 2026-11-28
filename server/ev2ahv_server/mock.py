"""Mock inventory and a progress simulator, so the UI can be built and demoed without touching a lab.

Names mirror the real lab (ev2ahv-src-rocky9, ev2ahv-src-win22) plus a few invented VMs. Mock mode never
contacts everRun or Prism.
"""
import time

GiB = 1 << 30

CLUSTERS = [{"id": "c-136", "name": "NTNX-136"}]
CONTAINERS = [{"id": "ct-default", "name": "default-container"}, {"id": "ct-ssd", "name": "ssd-container"}]
SUBNETS = [{"id": "sn-136", "name": "vlan136", "type": "VLAN"}, {"id": "sn-135", "name": "vlan135", "type": "VLAN"},
           {"id": "sn-test", "name": "ev2ahv-test-isolated", "type": "VLAN"},
           {"id": "sn-ov", "name": "app-overlay", "type": "OVERLAY", "vpc": "vpc-prd"}]


def _vm(id_, name, os_type, os_name, avail, node, cpu, mem, disks, gib, ip, mac, power="On", warnings=(),
        fw="bios", extra_nics=()):
    return {"id": id_, "name": name, "os_type": os_type, "os_name": os_name, "power_state": power,
            "availability": avail, "active_node": node, "vcpus": cpu, "memory_mib": mem, "firmware": fw,
            "disks": disks, "total_bytes": int(gib * GiB),
            "nics": [{"network": "sharednetwork0", "mac": mac, "ip": ip, "model": "virtio"}] + list(extra_nics),
            "warnings": list(warnings)}


WORKLOADS = [
    _vm("w-rocky9", "ev2ahv-src-rocky9", "linux", "Rocky Linux 9.6", "FT", "node0", 2, 4096, 4, 40,
        "10.12.136.162", "00:04:fc:40:61:02"),
    _vm("w-win22", "ev2ahv-src-win22", "windows", "Windows Server 2022", "HA", "node1", 4, 8192, 1, 70,
        "10.12.136.161", "00:04:fc:40:61:03", warnings=["virtio-blk only: boot disk must stay PCI until conversion"]),
    _vm("w-erp-db01", "erp-db01", "windows", "Windows Server 2019", "FT", "node0", 8, 32768, 3, 500,
        "10.12.136.40", "00:04:fc:40:61:10"),
    _vm("w-erp-app01", "erp-app01", "windows", "Windows Server 2019", "HA", "node1", 4, 16384, 2, 120,
        "10.12.136.41", "00:04:fc:40:61:11"),
    _vm("w-mes-web01", "mes-web01", "linux", "RHEL 8.10", "HA", "node0", 2, 4096, 1, 60, "10.12.136.50",
        "00:04:fc:40:61:12"),
    _vm("w-scada-hist", "scada-historian", "windows", "Windows Server 2016", "FT", "node1", 4, 16384, 2, 250,
        "10.12.136.60", "00:04:fc:40:61:13", warnings=["Guest has 2 NICs on the same network"]),
    _vm("w-erp-gw01", "erp-gw01", "linux", "RHEL 8.10", "HA", "node1", 2, 4096, 1, 40, "10.12.136.80",
        "00:04:fc:40:61:15", extra_nics=[{"network": "sharednetwork1", "mac": "00:04:fc:40:61:25", "ip": "192.168.50.80",
                                         "model": "virtio"}]),
    _vm("w-old-nt", "legacy-hmi", "other", "Windows XP Embedded", "HA", "node0", 1, 1024, 1, 20, "10.12.136.70",
        "00:04:fc:40:61:14", power="Off", warnings=["Unsupported guest OS: migrate as-is, no guest preparation"]),
]


PROJECTS = [{"id": "pj-default", "name": "default"}, {"id": "pj-migr", "name": "Migrations"}]


def fill_provider(doc):
    if doc["type"] == "everrun":
        doc["workloads"] = [dict(w) for w in WORKLOADS]
        doc["vm_count"] = len(WORKLOADS)
    else:
        doc.update(clusters=CLUSTERS, containers=CONTAINERS, subnets=SUBNETS, projects=PROJECTS, vm_count=42)


def seed(store):
    if store.all("provider"):
        return
    now = time.time()
    src = {"id": "p-everrun", "name": "everrun-lab", "type": "everrun", "role": "source", "address": "10.12.136.153",
           "username": "admin", "auth": "password", "status": "Connected", "version": "8.1.1",
           "node_addresses": ["10.12.136.151", "10.12.136.152"], "last_refreshed": now, "clusters": [],
           "containers": [], "subnets": []}
    tgt = {"id": "p-pc", "name": "PC-136", "type": "aos", "role": "target", "address": "10.12.135.196",
           "username": "admin", "auth": "api_key", "status": "Connected", "version": "pc.2025.1",
           "node_addresses": [], "last_refreshed": now}
    fill_provider(src)
    fill_provider(tgt)
    store.put("provider", src)
    store.put("provider", tgt)

    def plan(id_, name, vms, started, age_h, **kw):
        wl = {w["id"]: w for w in WORKLOADS}
        out = []
        for vid, status, pct in vms:
            w = wl[vid]
            out.append({"id": vid, "name": w["name"], "os_type": w["os_type"], "status": status,
                        "seed_percent": pct, "data_transferred_bytes": int(w["total_bytes"] * pct / 100),
                        "total_bytes": w["total_bytes"], "last_sync_at": now - 600 if pct >= 100 else None,
                        "checks": [{"name": "Source reachable", "status": "PASS", "detail": w["active_node"]},
                                   {"name": "Guest preparation", "status": "PASS", "detail": "Automatic"}]
                        + [{"name": "Source warning", "status": "WARN", "detail": x} for x in w["warnings"]],
                        "target_vm_id": f"tvm-{vid}" if status == "Completed" else None,
                        "target_vm_url": "https://10.12.135.196:9440/" if status == "Completed" else None,
                        "actions": []})
        d = {"id": id_, "name": name, "created_by": "nutanix", "created_at": now - age_h * 3600,
             "source": {"id": "p-everrun", "name": "everrun-lab"}, "target": {"id": "p-pc", "name": "PC-136"},
             "cluster": "NTNX-136", "container": "default-container", "project": "default",
             "network_map": [{"source_network": "sharednetwork0", "target_subnet": "vlan136",
                              "test_subnet": "ev2ahv-test-isolated"}], "test_network": True,
             "prep": {"mode": "automatic", "windows_user": "Administrator", "linux_user": "root", "retain_ip": True,
                      "retain_mac": True, "uninstall_guest_tools": True, "install_ngt": False,
                      "bypass_guest_ops": False, "virtio_source": None},
             "vm_settings": {}, "schedule": {"seed_at": None, "timezone": "Asia/Jakarta", "resync_minutes": 60},
             "status": "Not Started", "started_at": now - age_h * 3600 + 300 if started else None, "vms": out}
        d.update(kw)
        store.put("plan", d)

    plan("pl-wave1", "wave1-rocky", [("w-rocky9", "Completed", 100)], True, 30)
    plan("pl-wave2", "wave2-erp", [("w-erp-db01", "Seeding", 37), ("w-erp-app01", "Ready to Cutover", 100)], True, 5)
    plan("pl-win", "win22-pilot", [("w-win22", "Tested", 100)], True, 8)
    plan("pl-mes", "mes-web", [("w-mes-web01", "Failed", 64)], True, 3)
    plan("pl-scada", "scada-weekend", [("w-scada-hist", "Queued", 0)], False, 1)
    st = store.get("plan", "pl-mes")
    st["vms"][0]["error"] = "Source node node0 unreachable over SSH (timeout after 30 s)"
    store.put("plan", st)
    store.event("info", "Mock data loaded (no lab systems are contacted in mock mode)")


RATE = 0.6   # mock seed speed, percent of the VM per tick
TICK = 2


def simulate(store, lock, finish, bump):
    while True:
        time.sleep(TICK)
        with lock:
            changed = False
            for p in store.all("plan"):
                if not p.get("started_at"):
                    s = (p.get("schedule") or {}).get("seed_at")
                    if p.get("armed") and s and s <= time.time():
                        p["started_at"] = time.time()
                        for v in p["vms"]:
                            v["status"] = "Preparing"
                        store.event("info", f"Scheduled plan {p['name']} started", plan=p["name"])
                        store.put("plan", p)
                        changed = True
                    continue
                dirty = False
                for v in p["vms"]:
                    s, age = v["status"], time.time() - v.get("phase_t", 0)
                    if s == "Queued":
                        v["status"], v["phase_t"], dirty = "Preparing", time.time(), True
                    elif s == "Preparing" and age > 6:
                        v["status"], v["phase_t"], dirty = "Seeding", time.time(), True
                        store.event("info", "Guest preparation done, seeding", plan=p["name"], vm=v["name"])
                    elif s == "Seeding":
                        v["seed_percent"] = min(100.0, round(v["seed_percent"] + RATE * (60 / max(1, v["total_bytes"] / (1 << 30)) ** 0.5), 1))
                        v["data_transferred_bytes"] = int(v["total_bytes"] * v["seed_percent"] / 100)
                        left = 100 - v["seed_percent"]
                        v["eta_seconds"] = int(left / RATE * TICK) if left else None
                        if v["seed_percent"] >= 100:
                            v["status"], v["last_sync_at"] = "Ready to Cutover", time.time()
                            store.event("info", "Seed complete, ready to cut over", plan=p["name"], vm=v["name"])
                        dirty = True
                    elif s == "Cutover in Progress" and age > 15:
                        v["status"], v["target_vm_id"] = "Completed", f"tvm-{v['id']}"
                        v["target_vm_url"] = "https://10.12.135.196:9440/"
                        v["cutover_result"] = {"completed_at": time.time(), "target_vm_id": v["target_vm_id"],
                                               "downtime_seconds": 451.0, "final_sync_seconds": 364.0,
                                               "verify": {"power": "ON"}}
                        store.event("info", "Cutover complete, downtime 7m 31s (mock)", plan=p["name"],
                                    vm=v["name"])
                        dirty = True
                    elif s == "Rolling Back" and age > 10:
                        v["status"], v["rolled_back"], v["cutover_result"] = "Ready to Cutover", True, None
                        store.event("warning", "Rolled back: AHV VM off, source VM on (mock)", plan=p["name"],
                                    vm=v["name"])
                        dirty = True
                    elif s == "Testing" and age > 10:
                        v["status"], v["test_vm_id"] = "Tested", f"test-{v['id']}"
                        store.event("info", "Test VM is up on the isolated subnet", plan=p["name"], vm=v["name"])
                        dirty = True
                    elif s == "Ready to Cutover" and (v.get("rolled_back") is None or v.get("last_sync_at") == 0) and \
                            time.time() - (v.get("last_sync_at") or 0) > 60 * p["schedule"].get("resync_minutes", 60):
                        v["last_sync_at"] = time.time()
                        store.event("info", "Automatic resync done", plan=p["name"], vm=v["name"])
                        dirty = True
                if dirty:
                    store.put("plan", finish(p))
                    changed = True
            if changed:
                bump()
