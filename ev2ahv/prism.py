"""Prism Central v4 client. Every mutating call goes through an ownership guard."""
import json, logging, ssl, time, urllib.error, urllib.parse, urllib.request, uuid

log = logging.getLogger("ev2ahv.prism")
OWNED_PREFIX = "ev2ahv-"
VMS = "/vmm/v4.2/ahv/config/vms"


class PrismError(RuntimeError):
    pass


class NotOwned(PrismError):
    pass


class Prism:
    def __init__(self, creds, dry_run=False, extra_owned=()):
        self.c, self.dry_run = creds, dry_run
        self.extra_owned = set(extra_owned)
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
    def is_owned(self, name):
        return bool(name) and (name.startswith(OWNED_PREFIX) or name in self.extra_owned)

    def _owned_vm(self, vm_id):
        doc, hdr = self._call("GET", f"{VMS}/{vm_id}")
        vm = doc["data"]
        if not self.is_owned(vm.get("name")):
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
        vm, etag = self._owned_vm(vm_id)
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
        vm, _ = self._owned_vm(vm_id)
        if disk_id not in {d["extId"] for d in vm.get("disks") or []}:
            log.info("disk %s is already gone from %s", disk_id, vm["name"])
            return
        if self.dry_run:
            log.info("[dry-run] remove disk %s from %s", disk_id, vm["name"])
            return
        _, hdr = self._call("GET", f"{VMS}/{vm_id}/disks/{disk_id}")
        etag = hdr.get("ETag") or hdr.get("Etag") or hdr.get("etag")
        self.wait(self._call("DELETE", f"{VMS}/{vm_id}/disks/{disk_id}", None, etag)[0])

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
