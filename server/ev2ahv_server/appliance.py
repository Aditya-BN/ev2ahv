"""Appliance health: who am I on AHV, can I hot-add and remove my own disks, TLS certificate, SSH key."""
import datetime, hashlib, json, os, subprocess, threading, time

from fastapi import APIRouter, Depends, HTTPException, Request

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from ev2ahv import target as tgt  # noqa: E402
from ev2ahv.transport import BlockdTransport  # noqa: E402

DATA = os.path.expanduser(os.environ.get("EV2AHV_DATA", "~/.local/share/ev2ahv"))
TLS = os.path.join(DATA, "tls")
BOOT_UUID = "/run/ev2ahv-boot/vm-uuid"
SPOOL = os.path.join(DATA, "upgrade")          # D7: the app writes bundle.tar.gz + request.json; root applies
MAX_BUNDLE = 512 << 20
GiB = 1 << 30


def router(store, user, bump):
    r = APIRouter(prefix="/api/v2")
    state = {"selftest": store.get("settings", "selftest") or {"id": "selftest", "status": "never run"}}

    def vm_uuid():
        try:
            with open(BOOT_UUID) as fh:
                return fh.read().strip() or None
        except OSError:
            return None

    def target_provider():
        ps = [p for p in store.all("provider") if p["type"] == "aos"]
        return ps[0] if ps else None

    def ssh_key():
        key = os.path.join(tgt.CONFIG, "id_ed25519")
        if not os.path.exists(key):
            os.makedirs(tgt.CONFIG, mode=0o700, exist_ok=True)
            subprocess.run(["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-C", "ev2ahv-appliance", "-f", key],
                           check=True)
        with open(key + ".pub") as fh:
            return fh.read().strip()

    def cert_info():
        path = os.path.join(TLS, "server.crt")
        if not os.path.exists(path):
            return None
        from cryptography import x509
        from cryptography.hazmat.primitives import hashes
        with open(path, "rb") as fh:
            c = x509.load_pem_x509_certificate(fh.read())
        return {"subject": c.subject.rfc4514_string(), "issuer": c.issuer.rfc4514_string(),
                "not_after": c.not_valid_after_utc.isoformat(), "self_signed": c.subject == c.issuer,
                "sha256": c.fingerprint(hashes.SHA256()).hex(":").upper()}

    @r.get("/health")
    def health(who=Depends(user)):
        from .live import prism_for
        out = {"vm_uuid": vm_uuid(), "ahv_vm": None, "prism": None, "selftest": state["selftest"],
               "tls": cert_info(), "ssh_public_key": None}
        try:
            out["ssh_public_key"] = ssh_key()
        except Exception as e:
            out["ssh_public_key_error"] = str(e)
        tp = target_provider()
        if tp and out["vm_uuid"]:
            try:
                vm = prism_for(tp, store.secret).vm(out["vm_uuid"])
                out["ahv_vm"] = {"name": vm.get("name"), "power": vm.get("powerState"),
                                 "disks": len(vm.get("disks") or [])}
                out["prism"] = f"OK: this VM is {vm.get('name')} on {tp['name']}"
            except Exception as e:
                out["prism"] = f"This VM was not found through {tp['name']}: {e}"
        elif not tp:
            out["prism"] = "Add a Nutanix AOS environment first"
        return out

    @r.post("/health/selftest")
    def selftest(body: dict = None, who=Depends(user)):
        if state["selftest"].get("status") == "running":
            raise HTTPException(409, "Self-test already running")
        tp, uuid = target_provider(), vm_uuid()
        if not tp or not uuid:
            raise HTTPException(400, "Needs a Nutanix AOS environment and the appliance's VM UUID")
        container = (body or {}).get("container") or next((c["name"] for c in tp.get("containers", [])), None)
        state["selftest"] = {"id": "selftest", "status": "running", "started": time.time(), "steps": []}
        threading.Thread(target=_selftest, args=(tp, uuid, container, who), daemon=True).start()
        return state["selftest"]

    def _selftest(tp, uuid, container, who):
        from .live import prism_for
        st, steps = state["selftest"], state["selftest"]["steps"]

        def step(msg):
            steps.append({"time": time.time(), "message": msg})
            bump()
        pr, t, disk = prism_for(tp, store.secret), BlockdTransport(), None
        try:
            vm = pr.vm(uuid)
            pr.disk_only.add(vm["name"])
            step(f"Found this appliance in Prism: {vm['name']}")
            cluster = vm["cluster"]["extId"]
            ct = pr.container_id(container, cluster)
            disk, path = tgt.attach_staging(pr, t, uuid, GiB, container=ct)
            step(f"Hot-added a 1 GiB disk; it appeared as {os.path.realpath(path)}")
            size = t.size(path)
            step(f"Root helper accepted the disk and read its size ({size} bytes)")
            t.offline(path)
            step("Took the disk offline in the guest")
            pr.remove_vm_disk(uuid, disk)
            disk = None
            step("Removed the disk through Prism")
            st.update(status="PASS")
        except Exception as e:
            st.update(status="FAIL", error=str(e))
            if disk:
                try:
                    pr.remove_vm_disk(uuid, disk)
                    step("Cleaned up the test disk")
                except Exception as e2:
                    step(f"Could not remove the test disk {disk}: {e2}")
        st["ended"] = time.time()
        store.put("settings", st)
        store.event("info" if st["status"] == "PASS" else "error", f"{who}: disk hot-add self-test {st['status']}")
        bump()

    @r.put("/tls")
    def put_tls(body: dict, who=Depends(user)):
        """Replace the UI certificate. nginx reloads it on the appliance (a systemd path unit watches the file)."""
        from cryptography import x509
        from cryptography.hazmat.primitives import serialization
        try:
            cert = x509.load_pem_x509_certificate(body["cert"].encode())
            key = serialization.load_pem_private_key(body["key"].encode(), None)
        except Exception as e:
            raise HTTPException(400, f"Not a PEM certificate and unencrypted private key: {e}")
        pub = lambda k: k.public_bytes(serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo)
        if pub(cert.public_key()) != pub(key.public_key()):
            raise HTTPException(400, "The private key does not match the certificate")
        if cert.not_valid_after_utc < datetime.datetime.now(datetime.timezone.utc):
            raise HTTPException(400, "The certificate has expired")
        os.makedirs(TLS, mode=0o750, exist_ok=True)
        for name, data in (("server.key", body["key"]), ("server.crt", body["cert"])):   # key first: crt triggers reload
            fd = os.open(os.path.join(TLS, name + ".tmp"), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o640)
            with os.fdopen(fd, "w") as fh:
                fh.write(data if data.endswith("\n") else data + "\n")
            os.replace(os.path.join(TLS, name + ".tmp"), os.path.join(TLS, name))
        store.event("info", f"{who} replaced the TLS certificate ({cert.subject.rfc4514_string()})")
        return cert_info()

    return r


def redact(text, secrets_=()):
    """For support bundles: drop known secret values and anything that looks like one."""
    import re
    for sv in secrets_:
        if sv and len(sv) >= 4:
            text = text.replace(sv, "<redacted>")
    # ---- D7: upgrade from the UI ----------------------------------------------
    def upgrade_state():
        st = {}
        try:
            with open(os.path.join(SPOOL, "status.json")) as fh:
                st = json.load(fh)
        except (OSError, ValueError):
            pass
        pending = os.path.exists(os.path.join(SPOOL, "request.json"))
        return {"enabled": os.path.isdir(SPOOL), "pending": pending, "status": st}

    @r.get("/upgrade")
    def upgrade_status(who=Depends(user)):
        return upgrade_state()

    @r.put("/upgrade")
    async def upgrade_upload(request: Request, force: bool = False, who=Depends(user)):
        """Admin only (the role middleware refuses writes from viewers). Streams the bundle into the spool and asks
        ev2ahv-upgrade.path (root) to apply it. Root re-verifies everything; nothing here is trusted."""
        if not os.path.isdir(SPOOL):
            raise HTTPException(409, "This appliance cannot upgrade from the UI (no upgrade spool). Use ev2ahv-upgrade")
        cur = upgrade_state()
        if cur["pending"] or cur["status"].get("state") == "running":
            raise HTTPException(409, "An upgrade is already pending or running")
        part, final = os.path.join(SPOOL, "bundle.tar.gz.part"), os.path.join(SPOOL, "bundle.tar.gz")
        size = 0
        with open(part, "wb") as fh:
            async for chunk in request.stream():
                size += len(chunk)
                if size > MAX_BUNDLE:
                    fh.close()
                    os.remove(part)
                    raise HTTPException(413, f"Bundle larger than {MAX_BUNDLE >> 20} MiB")
                fh.write(chunk)
        if size < 1024:
            os.remove(part)
            raise HTTPException(400, "Empty or truncated upload")
        os.replace(part, final)
        with open(os.path.join(SPOOL, "request.json.tmp"), "w") as fh:
            json.dump({"by": who, "force": bool(force), "bytes": size, "time": time.time()}, fh)
        os.replace(os.path.join(SPOOL, "request.json.tmp"), os.path.join(SPOOL, "request.json"))
        store.event("warning", f"{who} uploaded an upgrade bundle ({size >> 20} MiB){' with force' if force else ''}; "
                    "the appliance applies it and restarts the app")
        return upgrade_state()

    return re.sub(r"(?i)(password|passwd|api[_-]?key|token|secret|authorization)(\W{1,3})\S+", r"\1\2<redacted>", text)


def fingerprint(path):
    with open(path, "rb") as fh:
        return hashlib.sha256(fh.read()).hexdigest()
