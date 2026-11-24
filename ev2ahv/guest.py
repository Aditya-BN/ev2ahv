"""Guest preparation and cleanup, modelled on Nutanix Move.

Access is over the network only: WinRM for Windows, ssh for Linux. ev2ahv never uses a hypervisor
guest agent or hypervisor-side guest exec. Scripts in prep/ collect facts and make changes; the
grading into PASS/WARN/FAIL lives here so it can be tested.

Secrets come from an environment variable or a 0600 file and are never logged or stored in state.
"""
import base64, json, logging, os, re, shlex, subprocess, tempfile, time

from .target import CONFIG
from .transport import SshTransport

log = logging.getLogger("ev2ahv.guest")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPTS = {"windows": os.path.join(ROOT, "prep", "prep-windows.ps1"), "linux": os.path.join(ROOT, "prep", "prep-linux.sh")}
MARK = "EV2AHV-JSON:"
PASS, WARN, FAIL = "PASS", "WARN", "FAIL"


def parse_facts(output):
    for line in reversed(output.splitlines()):
        if line.startswith(MARK):
            return json.loads(line[len(MARK):])
    raise RuntimeError("guest script printed no facts. Output ended with: " + output.strip()[-400:])


# ------------------------------------------------------------------ grading
def _item(name, status, detail):
    return {"name": name, "status": status, "detail": detail}


def grade_windows(f, bus, firmware):
    out = []
    boot = "vioscsi" if bus == "scsi" else "viostor"
    other = "viostor" if bus == "scsi" else "vioscsi"
    d = f.get("drivers") or {}
    out.append(_item(f"Boot driver {boot} for disk_bus {bus}", PASS if (d.get(boot) or {}).get("boot_ready") else FAIL,
                     "boot-start" if (d.get(boot) or {}).get("boot_ready") else
                     "not boot-start: Windows would stop with INACCESSIBLE_BOOT_DEVICE. Run guest-prep with VirtIO drivers"))
    if not (d.get(other) or {}).get("boot_ready"):
        out.append(_item(f"Alternate boot driver {other}", WARN, "not boot-start, so the other disk bus is not an option"))
    net = d.get("netkvm") or {}
    out.append(_item("Network driver netkvm", PASS if net.get("sys") or net.get("staged") else FAIL,
                     "present" if net.get("sys") or net.get("staged") else "missing: no network on AHV"))
    san = f.get("san_policy", "unknown")
    out.append(_item("SAN policy", PASS if "Online All" in san else WARN, san))
    static = [a for a in f.get("adapters") or [] if not a.get("dhcp")]
    task = f.get("task") or {}
    missing = [a["mac"] for a in static if a["mac"] not in (task.get("saved_macs") or [])]
    if not static:
        out.append(_item("Network re-apply", PASS, "all adapters use DHCP"))
    elif task.get("registered") and task.get("script") and not missing:
        out.append(_item("Network re-apply", PASS, f"startup task saved {len(static)} static adapter(s) by MAC"))
    else:
        out.append(_item("Network re-apply", FAIL, "static IP would be lost on AHV: " +
                         ("task not registered" if not task.get("registered") else f"not saved for {missing}")))
    out.append(_item("Firmware", PASS if f.get("firmware") == firmware else FAIL,
                     f"guest {f.get('firmware')}, plan {firmware}"))
    if f.get("stratus"):
        out.append(_item("Stratus guest software", WARN, f"{f['stratus']}. Removed by guest-cleanup on AHV"))
    out.append(_item("Prepared", PASS if f.get("prepared_at") else WARN, f.get("prepared_at") or "guest-prep has not run"))
    for p in f.get("problems") or []:
        out.append(_item("Prep script", WARN, p))
    return out


def grade_linux(f, bus, firmware):
    out = []
    i = f.get("initramfs") or {}
    boot = "virtio_scsi" if bus == "scsi" else "virtio_blk"
    if not i.get("readable"):
        out.append(_item("Initramfs", WARN, "could not list the initramfs. Check the boot drivers by hand"))
    else:
        out.append(_item(f"Boot driver {boot} for disk_bus {bus}", PASS if i.get(boot) else FAIL,
                         "in the initramfs or built in" if i.get(boot) else "missing: the root disk will not be found"))
        out.append(_item("virtio_pci", PASS if i.get("virtio_pci") else FAIL, "present" if i.get("virtio_pci") else "missing"))
        if not i.get("virtio_net"):
            out.append(_item("virtio_net at boot", WARN, "loads from the root filesystem, fine unless root is on the network"))
    out.append(_item("fstab references", FAIL if f.get("fstab_bad") else PASS,
                     f"device names that change on AHV: {f['fstab_bad']}" if f.get("fstab_bad") else "UUID, LABEL or LVM"))
    if f.get("crypttab_bad"):
        out.append(_item("crypttab references", FAIL, f"device names that change on AHV: {f['crypttab_bad']}"))
    if f.get("lvm_devices_file"):
        out.append(_item("LVM devices file", WARN, "device IDs change on AHV. guest-prep moves it aside"))
    for n in f.get("nics") or []:
        safe = n.get("pinned") or n.get("nm_by_mac") or not n.get("bound_by_name")
        out.append(_item(f"NIC {n['name']} {n['mac']}", PASS if safe else FAIL,
                         "name pinned by MAC" if n.get("pinned") else "profile matches by MAC" if n.get("nm_by_mac")
                         else "not bound to a name" if not n.get("bound_by_name")
                         else "config is bound to the name, which changes on AHV. Run guest-prep"))
    out.append(_item("Firmware", PASS if f.get("firmware") == firmware else FAIL,
                     f"guest {f.get('firmware')}, plan {firmware}"))
    if f.get("stratus"):
        out.append(_item("Stratus packages", WARN, f"{f['stratus']}. Removed by guest-cleanup on AHV"))
    out.append(_item("Prepared", PASS if f.get("prepared_at") else WARN, f.get("prepared_at") or "guest-prep has not run"))
    for p in f.get("problems") or []:
        out.append(_item("Prep script", WARN, p))
    return out


def grade(os_name, facts, bus, firmware):
    return (grade_windows if os_name == "windows" else grade_linux)(facts, bus, firmware)


# ------------------------------------------------------------------ access
def secret(g):
    """Password from guest.password_env, or from guest.password_file (raw, or KEY=VALUE using password_env)."""
    var = g.get("password_env")
    if var and os.environ.get(var):
        return os.environ[var]
    path = g.get("password_file")
    if path:
        path = os.path.expanduser(path)
        if os.stat(path).st_mode & 0o077:
            raise RuntimeError(f"{path} must not be readable by group or others (chmod 600)")
        text = open(path).read()
        if var:
            for line in text.splitlines():
                if line.strip().startswith(var + "="):
                    return line.split("=", 1)[1].strip()
        lines = [l for l in text.splitlines() if l.strip()]
        if len(lines) == 1 and "=" not in lines[0]:
            return lines[0].strip()
    raise RuntimeError("no guest password: set guest.password_env or guest.password_file")


class WindowsGuest:
    def __init__(self, host, user, password):
        import winrm   # optional dependency, only needed for Windows guests
        self.host = host
        self.p = winrm.protocol.Protocol(endpoint=f"http://{host}:5985/wsman", transport="ntlm", username=user,
                                         password=password, server_cert_validation="ignore",
                                         read_timeout_sec=900, operation_timeout_sec=870)

    def _run(self, command, args=(), stdin=None):
        shell = self.p.open_shell()
        try:
            cid = self.p.run_command(shell, command, list(args))
            if stdin is not None:
                for i in range(0, len(stdin), 60000):
                    self.p.send_command_input(shell, cid, stdin[i:i + 60000], end=False)
                self.p.send_command_input(shell, cid, "", end=True)
            out, err, rc = self.p.get_command_output(shell, cid)
            self.p.cleanup_command(shell, cid)
            return rc, out.decode(errors="replace"), err.decode(errors="replace")
        finally:
            self.p.close_shell(shell)

    def ps(self, script):
        """Runs a script of any length by streaming it to powershell's stdin."""
        return self._run("powershell.exe", ["-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-Command", "-"],
                         stdin=script.replace("\r\n", "\n").replace("\n", "\r\n") + "\r\n")

    def put(self, local, remote):
        data = open(local, "rb").read()
        r = remote.replace("'", "''")
        lines = [f"New-Item -ItemType Directory -Force -Path (Split-Path '{r}') | Out-Null",
                 f"$f = [IO.File]::Open('{r}', 'Create')"]
        for i in range(0, len(data), 45000):
            lines.append(f"$b = [Convert]::FromBase64String('{base64.b64encode(data[i:i + 45000]).decode()}'); $f.Write($b, 0, $b.Length)")
        lines += ["$f.Close()", f"(Get-Item '{r}').Length"]
        rc, out, err = self.ps("\n".join(lines))
        got = out.strip().splitlines()[-1] if out.strip() else ""
        if rc or got != str(len(data)):
            raise RuntimeError(f"upload to {self.host}:{remote} failed: {err.strip()[-300:] or out.strip()[-300:]}")

    def run_script(self, remote, args):
        quoted = " ".join(a if re.match(r"^-[A-Za-z]+$", a) else "'" + a.replace("'", "''") + "'" for a in args)
        rc, out, err = self.ps(f"& powershell.exe -NoProfile -ExecutionPolicy Bypass -File '{remote}' {quoted} *>&1 | Out-String -Width 400; exit $LASTEXITCODE")
        return rc, out + err


class LinuxGuest:
    def __init__(self, host, user, key):
        self.t = SshTransport(host, user, key, sudo=(user != "root"), known_hosts=os.path.join(CONFIG, "known_hosts"))

    def put(self, local, remote):
        with open(local, "rb") as fh:
            body = fh.read()
        r = subprocess.run(self.t.argv(f"mkdir -p {shlex.quote(os.path.dirname(remote))} && cat > {shlex.quote(remote)} && chmod 700 {shlex.quote(remote)}"),
                           input=body, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=120)
        if r.returncode:
            raise RuntimeError(f"upload failed: {r.stderr.decode(errors='replace')[-300:]}")

    def run_script(self, remote, args):
        r = subprocess.run(self.t.argv(" ".join(shlex.quote(x) for x in ["bash", remote, *args])),
                           stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=3600)
        return r.returncode, r.stdout.decode(errors="replace")


# ------------------------------------------------------------------ orchestration
class GuestPrep:
    def __init__(self, plan, workdir):
        self.p, self.g, self.dir = plan, plan.get("guest") or {}, workdir

    @property
    def os(self):
        return self.g.get("os")

    @property
    def mode(self):
        return self.g.get("prep", "manual")

    def address(self, target=False):
        return (self.p.get("verify") or {}).get("ip") if target else self.g.get("address")

    def access(self, target=False):
        host = self.address(target) or self.g.get("address")
        if not host:
            raise RuntimeError("guest.address is not set")
        if self.os == "windows":
            return WindowsGuest(host, self.g.get("user", "Administrator"), secret(self.g))
        return LinuxGuest(host, self.g.get("user", "root"), os.path.expanduser(self.g.get("ssh_key") or os.path.join(CONFIG, "id_ed25519")))

    def remote_script(self):
        return r"C:\ev2ahv\prep-windows.ps1" if self.os == "windows" else "/root/ev2ahv/prep-linux.sh"

    def args(self, mode, virtio_path=None):
        if self.os == "windows":
            a = ["-Mode", {"check": "Check", "prep": "Prep", "cleanup": "Cleanup"}[mode], "-Bus", self.p["target"]["disk_bus"]]
            if virtio_path:
                a += ["-VirtioPath", virtio_path]
            if mode == "cleanup" and self.g.get("ngt"):
                a.append("-InstallNgt")
            return a
        return {"check": [], "prep": ["--apply"], "cleanup": ["--cleanup"]}[mode]

    def stage_virtio(self, access):
        """Copies only this OS's vioscsi/viostor/netkvm folders from guest.virtio_iso into the guest."""
        iso = os.path.expanduser(self.g.get("virtio_iso") or "")
        if not iso:
            return self.g.get("virtio_path")
        with tempfile.TemporaryDirectory() as tmp:
            listing = subprocess.run(["xorriso", "-indev", iso, "-find", "/", "-type", "f"], stdout=subprocess.PIPE,
                                     stderr=subprocess.DEVNULL, check=True).stdout.decode().split("\n")
            folder = self.g.get("virtio_os_folder", "2k22")
            wanted = [l.strip().strip("'") for l in listing
                      if re.search(rf"/(vioscsi|viostor|netkvm)/{folder}/amd64/", l.strip().strip("'"), re.I)]
            if not wanted:
                raise RuntimeError(f"no vioscsi/viostor/netkvm for {folder}/amd64 in {iso}")
            for path in wanted:
                dest = os.path.join(tmp, path.lstrip("/"))
                os.makedirs(os.path.dirname(dest), exist_ok=True)
                subprocess.run(["xorriso", "-osirrox", "on", "-indev", iso, "-extract", path, dest],
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)
            archive = os.path.join(tmp, "virtio.zip")
            subprocess.run(["python3", "-m", "zipfile", "-c", archive] + sorted({p.split("/")[1] for p in wanted}),
                           cwd=tmp, check=True, stdout=subprocess.DEVNULL)
            access.put(archive, r"C:\ev2ahv\virtio.zip")
        rc, out, err = access.ps(r"Remove-Item C:\ev2ahv\virtio -Recurse -Force -ErrorAction SilentlyContinue; "
                                 r"Expand-Archive C:\ev2ahv\virtio.zip C:\ev2ahv\virtio -Force; Remove-Item C:\ev2ahv\virtio.zip; "
                                 r"(Get-ChildItem C:\ev2ahv\virtio -Recurse -Filter *.inf).Count")
        if rc:
            raise RuntimeError(f"could not unpack drivers in the guest: {err.strip()[-300:]}")
        log.info("staged %d VirtIO files for %s in the guest", len(wanted), folder)
        return r"C:\ev2ahv\virtio"

    def run(self, mode, target=False):
        """Auto mode: upload the script and run it. Returns the facts."""
        access = self.access(target)
        access.put(SCRIPTS[self.os], self.remote_script())
        virtio = self.stage_virtio(access) if (mode == "prep" and self.os == "windows") else None
        rc, out = access.run_script(self.remote_script(), self.args(mode, virtio))
        for line in out.splitlines():
            if line.startswith(("[CHG", "[PROB")):
                log.info("guest: %s", line)
        facts = parse_facts(out)
        if rc == 2:
            raise RuntimeError(f"guest script refused: {facts.get('problems')}")
        return facts

    def facts_for_check(self):
        """Auto mode runs the read-only check live. Manual mode only reads the prep.json the operator's run
        wrote, from a copy on the bastion (guest.prep_json_file) or straight from the guest."""
        if self.g.get("prep_json_file"):
            with open(os.path.expanduser(self.g["prep_json_file"]), encoding="utf-8-sig") as fh:
                return json.load(fh)
        if self.mode == "auto":
            return self.run("check")
        access = self.access()
        if self.os == "windows":
            rc, out, err = access.ps(r"if (Test-Path C:\ev2ahv\prep.json) { Get-Content C:\ev2ahv\prep.json -Raw } else { 'MISSING' }")
        else:
            rc, out = 0, access.t.run("cat /root/ev2ahv/prep.json 2>/dev/null || echo MISSING")
        if out.strip() == "MISSING" or not out.strip():
            raise RuntimeError("prep.json not found in the guest. Run the manual prep bundle first")
        return json.loads(out.strip().lstrip("\ufeff"))

    def bundle(self, mode):
        """Manual mode: writes the script and step-by-step instructions. Nothing runs on the guest."""
        out = os.path.join(self.dir, f"guest-{mode}")
        os.makedirs(out, exist_ok=True)
        name = os.path.basename(SCRIPTS[self.os])
        with open(SCRIPTS[self.os], "rb") as src, open(os.path.join(out, name), "wb") as dst:
            dst.write(src.read())
        bus = self.p["target"]["disk_bus"]
        if self.os == "windows":
            where, result = r"C:\ev2ahv", r"C:\ev2ahv\prep.json" if mode == "prep" else r"C:\ev2ahv\cleanup.json"
            run = (f"powershell -ExecutionPolicy Bypass -File C:\\ev2ahv\\{name} -Mode {'Prep' if mode == 'prep' else 'Cleanup'} -Bus {bus}"
                   + (" -VirtioPath <folder or share with the VirtIO drivers, e.g. D:\\ or \\\\server\\virtio>" if mode == "prep" else "")
                   + (" -InstallNgt" if mode == "cleanup" and self.g.get("ngt") else ""))
            shell = "an elevated PowerShell"
        else:
            where, result = "/root/ev2ahv", "/root/ev2ahv/prep.json" if mode == "prep" else "/root/ev2ahv/cleanup.json"
            run = f"bash /root/ev2ahv/{name} {'--apply' if mode == 'prep' else '--cleanup'}"
            shell = "a root shell"
        on = "the source VM, while it is still on everRun" if mode == "prep" else "the migrated VM on AHV"
        steps = [f"# ev2ahv guest {mode}: manual steps for plan {self.p['name']}", "",
                 f"1. Copy `{name}` to `{where}` on {on}.", f"2. In {shell}, run:", "", f"       {run}", "",
                 f"3. Check that it ends with exit code 0. It writes `{result}`."]
        if mode == "prep":
            steps += [f"4. Then run `ev2ahv prep-check {self.p['name']}` on the bastion. It reads the guest's state over "
                      f"{'WinRM' if self.os == 'windows' else 'ssh'}, or set `guest.prep_json_file` to a copy of `{result}` "
                      "if the bastion cannot reach the guest."]
        with open(os.path.join(out, "INSTRUCTIONS.md"), "w") as fh:
            fh.write("\n".join(steps) + "\n")
        return out
