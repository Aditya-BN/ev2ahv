"""Prism Central v4 client. Every mutating call goes through an ownership guard."""
import json, logging, ssl, time, urllib.error, urllib.parse, urllib.request, uuid

log = logging.getLogger("ev2ahv.prism")
OWNED_PREFIX = "ev2ahv-"
NEVER_PREFIXES = ("everrun-",)   # production everRun VMs: never owned, not even when a plan names one
VMS = "/vmm/v4.2/ahv/config/vms"


class PrismError(RuntimeError):
    pass


class NotOwned(PrismError):
    pass


class Prism:
    def __init__(self, creds, dry_run=False, extra_owned=()):
        self.c, self.dry_run = creds, dry_run
        self.extra_owned = set(extra_owned)   # whole-VM ownership: the plan's target VM
        self.disk_only = set()                # the writer / appliance VM: disks may be added and removed, nothing else
        self.ctx = ssl.create_default_context() if creds.verify_tls else ssl._create_unverified_context()

    def _call(self, method, path, body=None, etag=None, base="/api"):
        url = f"https://{self.c.host}:{self.c.port}{base}{urllib.parse.quote(path, safe='/:?=&$,()' + chr(39) + '-_.~%')}"
        headers = dict(self.c.headers(), Accept="application/json")
        if method != "GET":
            headers["NTNX-Request-Id"] = str(uuid.uuid4())
        if etag:
            headers["If-Match"] = etag
        data = None
        if body is not None:
            data, headers["Content-Type"] = json.dumps(body).encode(), "application/json"
        req = urllib.request.Request(url, data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(req, context=self.ctx, timeout=120) as r:
                raw = r.read()
                return (json.loads(raw) if raw else {}), dict(r.headers)
        except urllib.error.HTTPError as e:
            raise PrismError(f"{method} {path}: HTTP {e.code}: {e.read()[:1500].decode(errors='replace')}") from None

    def get(self, path):
        return self._call("GET", path)[0]

    # ---- ownership ---------------------------------------------------------
    def is_owned(self, name, disk_ok=False):
        if not name or name.startswith(NEVER_PREFIXES):
            return False
        return name.startswith(OWNED_PREFIX) or name in self.extra_owned or (disk_ok and name in self.disk_only)

    def _owned_vm(self, vm_id, disk_ok=False):
        doc, hdr = self._call("GET", f"{VMS}/{vm_id}")
        vm = doc["data"]
        if not self.is_owned(vm.get("name"), disk_ok):
            raise NotOwned(f"refusing to modify VM {vm.get('name')!r} ({vm_id}): not ev2ahv-owned")
        etag = hdr.get("ETag") or hdr.get("Etag") or hdr.get("etag")
        return vm, etag

    # ---- tasks -------------------------------------------------------------
    def wait(self, doc, timeout=900):
        ext = (doc.get("data") or {}).get("extId")
        if not ext:
            raise PrismError(f"no task id in response: {json.dumps(doc)[:400]}")
        deadline = time.time() + timeout
        while time.time() < deadline:
            t = self.get(f"/prism/v4.0/config/tasks/{ext}")["data"]
            if t.get("status") == "SUCCEEDED":
                return t
            if t.get("status") in ("FAILED", "CANCELED"):
                raise PrismError(f"task {ext} {t['status']}: {self._task_error(ext, t)}")
            time.sleep(2)
        raise PrismError(f"task {ext} timed out")

    def _task_error(self, ext, task):
        details = [m.get("message") for m in task.get("errorMessages") or []]
        try:
            v3 = ext.split(":")[-1]
            doc = self._call("GET", f"/tasks/{v3}", base="/api/nutanix/v3")[0]
            for sub in doc.get("subtask_reference_list") or []:
                s = self._call("GET", f"/tasks/{sub['uuid']}", base="/api/nutanix/v3")[0]
                if s.get("error_detail"):
                    details.append(s["error_detail"])
            if doc.get("error_detail"):
                details.append(doc["error_detail"])
        except Exception:  # the v3 tree is best-effort diagnostics
            pass
        return "; ".join(str(d) for d in details if d) or "unknown error"

    @staticmethod
    def affected(task, suffix):
        for e in task.get("entitiesAffected") or []:
            if (e.get("rel") or "").endswith(suffix):
                return e.get("extId")
        return None

    # ---- reads -------------------------------------------------------------
    def list_vms(self, flt=None, select=None):
        out, page = [], 0
        while True:
            q = f"?$page={page}&$limit=100"
            if flt:
                q += "&$filter=" + urllib.parse.quote(flt, safe="(),'/:")
            if select:
                q += "&$select=" + select
            rows = self.get(VMS + q).get("data") or []
            out += rows
            if len(rows) < 100:
                return out
            page += 1

    def vm(self, vm_id):
        return self.get(f"{VMS}/{vm_id}")["data"]

    def vm_by_name(self, name):
        rows = [v for v in self.list_vms(f"name eq '{name}'") if v.get("name") == name]
        if len(rows) > 1:
            raise PrismError(f"{len(rows)} VMs are named {name!r}")
        return rows[0] if rows else None

    def _all(self, path):
        out, page = [], 0
        while True:
            rows = self.get(f"{path}?$page={page}&$limit=100").get("data") or []
            out += rows
            if len(rows) < 100:
                return out
            page += 1

    def cluster_id(self, name):
        rows = [c for c in self._all("/clustermgmt/v4.2/config/clusters") if name in (c.get("name"), c.get("extId"))]
        if len(rows) != 1:
            raise PrismError(f"cluster {name!r}: {len(rows)} matches")
        return rows[0]["extId"]

    def container_id(self, name, cluster_id):
        rows = [c for c in self._all("/clustermgmt/v4.2/config/storage-containers")
                if c.get("clusterExtId") == cluster_id and name in (c.get("name"), c.get("containerExtId"))]
        if len(rows) != 1:
            raise PrismError(f"storage container {name!r}: {len(rows)} matches")
        return rows[0]["containerExtId"]

    def subnet_id(self, name, cluster_id):
        rows = [s for s in self._all("/networking/v4.0/config/subnets")
                if name in (s.get("name"), s.get("extId"))
                and cluster_id in (s.get("clusterReference"), *(s.get("clusterReferenceList") or []))]
        if len(rows) != 1:
            raise PrismError(f"subnet {name!r}: {len(rows)} matches")
        return rows[0]["extId"]

    def mac_owners(self, mac):
        mac = mac.lower()
        return [v.get("name") for v in self.list_vms(select="name,nics")
                if any(((n.get("backingInfo") or {}).get("macAddress") or "").lower() == mac
                       for n in v.get("nics") or [])]

    def disk_ref(self, vm_id, disk_id):
        """v4 equivalent of clone_from_vmdisk: AHV needs the disk, its address and its VM."""
        disk = next((d for d in self.vm(vm_id).get("disks") or [] if d["extId"] == disk_id), None)
        if disk is None:
            raise PrismError(f"disk {disk_id} is not on VM {vm_id}")
        addr = disk["diskAddress"]
        return {"$objectType": "vmm.v4.ahv.config.VmDiskReference", "diskExtId": disk_id,
                "diskAddress": {"busType": addr["busType"], "index": addr["index"]},
                "vmReference": {"extId": vm_id}}

    # ---- writes (all guarded) ----------------------------------------------
    def create_vm(self, payload):
        if not self.is_owned(payload.get("name")):
            raise NotOwned(f"refusing to create VM {payload.get('name')!r}: not ev2ahv-owned")
        existing = self.vm_by_name(payload["name"])
        if existing:
            log.info("VM %s already exists", payload["name"])
            return existing["extId"]
        if self.dry_run:
            log.info("[dry-run] create VM %s", payload["name"])
            return None
        task = self.wait(self._call("POST", VMS, payload)[0])
        ext = self.affected(task, "vm") or self.vm_by_name(payload["name"])["extId"]
        log.info("created VM %s %s", payload["name"], ext)
        return ext

    def customize_cloud_init(self, vm_id, userdata_b64):
        _, etag = self._owned_vm(vm_id)
        body = {"config": {"$objectType": "vmm.v4.ahv.config.CloudInit",
                           "cloudInitScript": {"$objectType": "vmm.v4.ahv.config.Userdata",
                                               "value": userdata_b64}}}
        if not self.dry_run:
            self.wait(self._call("POST", f"{VMS}/{vm_id}/$actions/customize-guest", body, etag)[0])

    def power(self, vm_id, action):
        if action not in ("power-on", "power-off", "shutdown", "guest-shutdown"):
            raise ValueError(action)
        vm, etag = self._owned_vm(vm_id)
        want = "ON" if action == "power-on" else "OFF"
        if vm.get("powerState") == want:
            return
        if self.dry_run:
            log.info("[dry-run] %s %s", action, vm["name"])
            return
        self.wait(self._call("POST", f"{VMS}/{vm_id}/$actions/{action}", None, etag)[0])

    def add_disk(self, vm_id, size_bytes, container=None, bus="SCSI", index=None, clone_from=None):
        """Add an empty disk, or a clone of `clone_from` (from disk_ref). Returns its extId."""
        vm, etag = self._owned_vm(vm_id, disk_ok=True)
        before = {d["extId"] for d in vm.get("disks") or []}
        used = {d["diskAddress"]["index"] for d in vm.get("disks") or []
                if d["diskAddress"]["busType"] == bus}
        index = index if index is not None else next(i for i in range(256) if i not in used)
        backing = {"$objectType": "vmm.v4.ahv.config.VmDisk", "diskSizeBytes": int(size_bytes)}
        if clone_from:
            backing["dataSource"] = {"reference": clone_from}
        elif container:
            backing["storageContainer"] = {"extId": container}
        body = {"diskAddress": {"busType": bus, "index": index}, "backingInfo": backing}
        if self.dry_run:
            log.info("[dry-run] add %s-byte disk to %s at %s.%s", size_bytes, vm["name"], bus, index)
            return None
        self.wait(self._call("POST", f"{VMS}/{vm_id}/disks", body, etag)[0])
        after = {d["extId"]: d for d in self.vm(vm_id).get("disks") or []}
        new = [d for d in after if d not in before]
        if len(new) != 1:
            raise PrismError(f"expected one new disk on {vm['name']}, saw {new}")
        log.info("added disk %s to %s at %s.%s", new[0], vm["name"], bus, index)
        return new[0]

    def remove_vm_disk(self, vm_id, disk_id):
        """Remove (and on AHV, delete) one disk. Always the VM-scoped path, never clustermgmt."""
        vm, _ = self._owned_vm(vm_id, disk_ok=True)
        if disk_id not in {d["extId"] for d in vm.get("disks") or []}:
            log.info("disk %s is already gone from %s", disk_id, vm["name"])
            return
        if self.dry_run:
            log.info("[dry-run] remove disk %s from %s", disk_id, vm["name"])
            return
        _, hdr = self._call("GET", f"{VMS}/{vm_id}/disks/{disk_id}")
        etag = hdr.get("ETag") or hdr.get("Etag") or hdr.get("etag")
        self.wait(self._call("DELETE", f"{VMS}/{vm_id}/disks/{disk_id}", None, etag)[0])

    # ---- images (upload fallback) -------------------------------------------
    def image_by_name(self, name):
        rows = [i for i in self._all("/vmm/v4.2/content/images") if i.get("name") == name]
        if len(rows) > 1:
            raise PrismError(f"{len(rows)} images are named {name!r}")
        return rows[0] if rows else None

    # ---- projects (D8a; v4 has no project assignment, so this uses v3) -------
    def projects(self):
        """[{"id", "name"}] of every Prism project."""
        out, offset = [], 0
        while True:
            d = self._call("POST", "/projects/list", {"kind": "project", "length": 100, "offset": offset},
                           base="/api/nutanix/v3")[0]
            ents = d.get("entities") or []
            out += [{"id": e["metadata"]["uuid"], "name": (e.get("status") or e.get("spec") or {}).get("name", "")}
                    for e in ents]
            offset += len(ents)
            if not ents or offset >= int((d.get("metadata") or {}).get("total_matches") or 0):
                return sorted(out, key=lambda p: p["name"].lower())

    def project_id(self, name):
        rows = [p for p in self.projects() if p["name"] == name]
        if len(rows) != 1:
            raise PrismError(f"project {name!r}: found {len(rows)}")
        return rows[0]["id"]

    def set_project(self, vm_id, project_id):
        """Put an ev2ahv-owned VM into a project (v3 metadata.project_reference). No-op if it is already there."""
        self._owned_vm(vm_id)
        doc = self._call("GET", f"/vms/{vm_id}", base="/api/nutanix/v3")[0]
        md = doc["metadata"]
        if (md.get("project_reference") or {}).get("uuid") == project_id:
            return False
        if self.dry_run:
            log.info("[dry-run] set project of %s to %s", vm_id, project_id)
            return False
        md["project_reference"] = {"kind": "project", "uuid": project_id}
        body = {"api_version": doc.get("api_version", "3.1"), "metadata": md, "spec": doc["spec"]}
        r = self._call("PUT", f"/vms/{vm_id}", body, base="/api/nutanix/v3")[0]
        self._wait_v3(r["status"]["execution_context"]["task_uuid"])
        log.info("VM %s is now in project %s", vm_id, project_id)
        return True

    def _wait_v3(self, task_uuid, timeout=900):
        deadline = time.time() + timeout
        while time.time() < deadline:
            t = self._call("GET", f"/tasks/{task_uuid}", base="/api/nutanix/v3")[0]
            if t.get("status") == "SUCCEEDED":
                return t
            if t.get("status") in ("FAILED", "ABORTED"):
                raise PrismError(f"task {task_uuid} {t['status']}: {t.get('error_detail')}")
            time.sleep(2)
        raise PrismError(f"task {task_uuid} timed out")

    def upload_image(self, name, stream, size, cluster_id, description="ev2ahv staging image", timeout=7200):
        """v4 has no file upload, so this uses the v3 image file API. Returns the image extId."""
        if not name.startswith(OWNED_PREFIX):
            raise NotOwned(f"refusing to create image {name!r}: not ev2ahv-owned")
        if self.image_by_name(name):
            raise PrismError(f"image {name} already exists")
        if self.dry_run:
            log.info("[dry-run] upload image %s (%d bytes)", name, size)
            return None
        body = {"spec": {"name": name, "description": description, "resources": {"image_type": "DISK_IMAGE"}},
                "metadata": {"kind": "image"}}
        doc = self._call("POST", "/images", body, base="/api/nutanix/v3")[0]
        uuid_ = doc["metadata"]["uuid"]
        task = doc["status"]["execution_context"]["task_uuid"]
        self._wait_v3(task[0] if isinstance(task, list) else task)
        url = f"https://{self.c.host}:{self.c.port}/api/nutanix/v3/images/{uuid_}/file"
        headers = dict(self.c.headers(), **{"Content-Type": "application/octet-stream", "Content-Length": str(size),
                                            "X-Nutanix-Destination-Cluster": cluster_id})
        req = urllib.request.Request(url, data=stream, headers=headers, method="PUT")
        try:
            with urllib.request.urlopen(req, context=self.ctx, timeout=600) as r:
                r.read()
        except urllib.error.HTTPError as e:
            raise PrismError(f"image upload: HTTP {e.code}: {e.read()[:800].decode(errors='replace')}") from None
        deadline = time.time() + timeout
        while time.time() < deadline:
            state = (self._call("GET", f"/images/{uuid_}", base="/api/nutanix/v3")[0].get("status") or {}).get("state")
            if state == "COMPLETE":
                log.info("uploaded image %s %s", name, uuid_)
                return uuid_
            if state == "ERROR":
                raise PrismError(f"image {name} ended in ERROR")
            time.sleep(3)
        raise PrismError(f"image {name} did not complete")

    def delete_image(self, image_id):
        doc, hdr = self._call("GET", f"/vmm/v4.2/content/images/{image_id}")
        name = doc["data"].get("name") or ""
        if not name.startswith(OWNED_PREFIX):
            raise NotOwned(f"refusing to delete image {name!r}: not ev2ahv-owned")
        if self.dry_run:
            log.info("[dry-run] delete image %s", name)
            return
        etag = hdr.get("ETag") or hdr.get("Etag") or hdr.get("etag")
        self.wait(self._call("DELETE", f"/vmm/v4.2/content/images/{image_id}", None, etag)[0])
        log.info("deleted image %s", name)

    @staticmethod
    def image_ref(image_id):
        return {"$objectType": "vmm.v4.ahv.config.ImageReference", "imageExtId": image_id}

    def set_boot_disk(self, vm_id, bus, index):
        """Points LegacyBoot or UefiBoot at a disk address. PUTs the VM back with only that field changed."""
        vm, etag = self._owned_vm(vm_id)
        boot = vm.get("bootConfig") or {}
        boot["bootDevice"] = {"$objectType": "vmm.v4.ahv.config.BootDeviceDisk",
                              "diskAddress": {"busType": bus, "index": int(index)}}
        vm["bootConfig"] = boot
        if self.dry_run:
            log.info("[dry-run] boot %s from %s.%s", vm["name"], bus, index)
            return
        self.wait(self._call("PUT", f"{VMS}/{vm_id}", vm, etag)[0])
        log.info("%s now boots from %s.%s", vm["name"], bus, index)

    def wait_power(self, vm_id, state, timeout=600):
        deadline = time.time() + timeout
        while self.vm(vm_id).get("powerState") != state:
            if time.time() > deadline:
                raise PrismError(f"VM {vm_id} did not reach {state}")
            time.sleep(5)

    def delete_vm(self, vm_id):
        vm, etag = self._owned_vm(vm_id)
        if self.dry_run:
            log.info("[dry-run] delete VM %s", vm["name"])
            return
        if vm.get("powerState") == "ON":
            self.power(vm_id, "power-off")
            _, etag = self._owned_vm(vm_id)
        self.wait(self._call("DELETE", f"{VMS}/{vm_id}", None, etag)[0])
        log.info("deleted VM %s", vm["name"])
