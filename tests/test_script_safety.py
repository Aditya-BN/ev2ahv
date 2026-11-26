"""Script and code-path safety: hostile inputs must never destroy anything outside the sandbox.

Shell scripts run in temporary directories (or a throw-away docker container when they need root); every test plants
a canary file outside the directory a script may touch and asserts that it survives. Where a hostile input would be
catastrophic if a guard were ever broken (a work dir of "/"), `rm` is replaced by a recording shim so the test can
only fail, never delete. No test touches the lab: no Prism, no everRun, no /var/tmp/ev2ahv-*, no ~/.config/ev2ahv.
Tests that need root or docker skip with a reason when it is missing.
"""
import copy, importlib.machinery, importlib.util, io, json, os, shutil, subprocess, sys, tarfile, tempfile, unittest
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path[:0] = [ROOT, os.path.join(ROOT, "server"), os.path.join(ROOT, "tools")]

from ev2ahv import blockd, plan, workflow  # noqa: E402
from ev2ahv.credentials import Credentials  # noqa: E402
from ev2ahv.helper import ev2ahv_helper  # noqa: E402
from ev2ahv.prism import NotOwned, Prism  # noqa: E402
from ev2ahv.state import State  # noqa: E402
import make_bundle  # noqa: E402

SBIN = os.path.join(ROOT, "appliance", "rootfs", "usr", "local", "sbin")


def load_tool(name):
    loader = importlib.machinery.SourceFileLoader(name.replace("-", "_"), os.path.join(SBIN, name))
    mod = importlib.util.module_from_spec(importlib.util.spec_from_loader(loader.name, loader))
    loader.exec_module(mod)
    return mod


up = load_tool("ev2ahv-upgrade")


def docker_ok():
    if not shutil.which("docker"):
        return False
    return subprocess.run(["docker", "info"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode == 0


def docker_image(name):
    return subprocess.run(["docker", "image", "inspect", name], stdout=subprocess.DEVNULL,
                          stderr=subprocess.DEVNULL).returncode == 0


NEED_DOCKER = unittest.skipUnless(docker_ok(), "docker is not available: root-level script tests run in a container")
DOCKER_PY = "python:3.12-slim"


class Sandbox(unittest.TestCase):
    """A temp dir with a `canary` file outside the directory a script is allowed to touch."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.t = os.path.realpath(self.tmp.name)
        self.canary_dir = os.path.join(self.t, "canary")
        os.makedirs(self.canary_dir)
        self.canary = os.path.join(self.canary_dir, "precious.txt")
        with open(self.canary, "w") as fh:
            fh.write("do not delete")
        self.procs = []

    def tearDown(self):
        for p in self.procs:
            p.kill()
            p.wait()
        self.tmp.cleanup()

    def assertCanary(self):
        self.assertTrue(os.path.exists(self.canary), "the canary file was deleted")
        with open(self.canary) as fh:
            self.assertEqual(fh.read(), "do not delete")

    def sh(self, argv, cwd=None, env=None):
        e = dict(os.environ, **(env or {}))
        return subprocess.run(argv, cwd=cwd or self.t, env=e, capture_output=True, text=True, timeout=60)

    def sleeper(self):
        p = subprocess.Popen(["sleep", "300"])
        self.procs.append(p)
        return p

    def write(self, path, text, mode=None):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        # write beside the target and rename over it: opening a script for writing while an earlier run of it is
        # still executing (run-dev.sh leaves its fake python running) fails with ETXTBSY, which made this suite flaky
        tmp = path + ".tmp"
        with open(tmp, "w") as fh:
            fh.write(text)
        if mode:
            os.chmod(tmp, mode)
        os.replace(tmp, path)
        return path

    def rm_shim(self):
        """A PATH entry whose `rm` only records its arguments. Returns (PATH value, log file)."""
        log = os.path.join(self.t, "rm.log")
        b = os.path.join(self.t, "rmshim")
        self.write(os.path.join(b, "rm"), f'#!/bin/sh\necho "$@" >> "{log}"\n', 0o755)
        return b + ":" + os.environ["PATH"], log

    def assertNoRm(self, log):
        self.assertFalse(os.path.exists(log), "rm was called: " + (open(log).read() if os.path.exists(log) else ""))


# ------------------------------------------------------------------------------------------------ shell scripts
class RunDev(Sandbox):
    script = os.path.join(ROOT, "server", "run-dev.sh")

    def env(self):
        fake = self.write(os.path.join(self.t, "py"), "#!/bin/sh\nexit 0\n", 0o755)
        return {"EV2AHV_PY": fake, "EV2AHV_RUN_DIR": self.t}

    def test_unknown_mode_and_no_args_are_refused(self):
        for args in ([], ["prod"], ["mock", "../../x"]):
            r = self.sh(["bash", self.script, *args], env=self.env())
            self.assertEqual(r.returncode, 2, r.stderr)

    def test_stale_pidfile_never_kills_an_unrelated_process(self):
        victim = self.sleeper()
        for content in (str(victim.pid), "", "abc", "1", f"{victim.pid}; kill -9 {victim.pid}"):
            self.write(os.path.join(self.t, "ev2ahv-mock.pid"), content)
            r = self.sh(["bash", self.script, "mock"], env=self.env())
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertIsNone(victim.poll(), f"pidfile {content!r} killed an unrelated process")


class PowerSh(Sandbox):
    script = os.path.join(ROOT, "tests", "e2e", "ui-fake", "power.sh")

    def run_power(self, d, *args, env=None):
        return self.sh(["bash", self.script, *args], env=dict({"EV2AHV_UIFAKE_DIR": d}, **(env or {})))

    def test_hostile_work_dirs_are_refused_and_rm_is_never_reached(self):
        path, log = self.rm_shim()   # an empty EV2AHV_UIFAKE_DIR means "use the default", so it is deliberately not tried
        for d in ("/", "relative/ev2ahv-uifake", "/tmp", "/var/tmp", self.canary_dir, "/var/tmp/other",
                  os.path.join(self.t, "x", "..")):
            r = self.run_power(d, "teardown", env={"PATH": path})
            self.assertEqual(r.returncode, 2, f"{d!r}: {r.stderr}")
        self.assertNoRm(log)
        self.assertCanary()

    def test_teardown_removes_only_its_own_dir(self):
        d = os.path.join(self.t, "ev2ahv-uifake-t")
        self.write(os.path.join(d, "disk.raw"), "x")
        r = self.run_power(d, "teardown")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertFalse(os.path.exists(d))
        self.assertCanary()

    def test_teardown_of_a_symlink_does_not_follow_it(self):
        d = os.path.join(self.t, "ev2ahv-uifake-link")
        os.symlink(self.canary_dir, d)
        self.run_power(d, "teardown")
        self.assertCanary()

    def test_stale_pid_is_not_signalled(self):
        victim = self.sleeper()
        d = os.path.join(self.t, "ev2ahv-uifake-p")
        self.write(os.path.join(d, "load.pid"), str(victim.pid))
        self.run_power(d, "stop")
        self.assertIsNone(victim.poll())

    def test_dry_run_changes_nothing(self):
        d = os.path.join(self.t, "ev2ahv-uifake-d")
        self.write(os.path.join(d, "disk.raw"), "x")
        r = self.run_power(d, "dry-run")
        self.assertEqual(r.returncode, 0)
        self.assertIn("would", r.stdout)
        self.assertTrue(os.path.exists(os.path.join(d, "disk.raw")))


class FakeSource(Sandbox):
    script = os.path.join(ROOT, "tests", "e2e", "fake-source.sh")

    def env(self, **kw):
        w = os.path.join(self.t, "ev2ahv-a4")
        os.makedirs(w, exist_ok=True)
        e = {"EV2AHV_A4_DIR": w, "EV2AHV_A4_MNT": os.path.join(self.t, "mnt", "ev2ahv-src"),
             "EV2AHV_A4_RUN": os.path.join(self.t, "running"), "EV2AHV_A4_PIDF": os.path.join(self.t, "load.pid")}
        e.update(kw)
        return e

    def test_mount_point_must_be_ours(self):
        for m in ("/", "/mnt", "/etc", self.canary_dir):
            r = self.sh(["bash", self.script, "status"], env=self.env(EV2AHV_A4_MNT=m))
            self.assertEqual(r.returncode, 2, f"{m!r}: {r.stderr}")

    def test_start_refuses_a_loop_file_that_is_not_a_loop_device(self):
        e = self.env()
        for content in ("", "/etc/passwd", "/dev/sda", "../loop0", "/dev/loop0\n/dev/sda"):
            self.write(os.path.join(e["EV2AHV_A4_DIR"], "loop"), content)
            r = self.sh(["bash", self.script, "start"], env=e)
            self.assertEqual(r.returncode, 2, f"{content!r}: {r.stderr}")
            self.assertFalse(os.path.exists(e["EV2AHV_A4_RUN"]))
        os.remove(os.path.join(e["EV2AHV_A4_DIR"], "loop"))
        self.assertEqual(self.sh(["bash", self.script, "start"], env=e).returncode, 2)   # missing file

    def test_stop_with_a_failed_cd_deletes_nothing_in_the_current_dir(self):
        e = self.env()
        open(e["EV2AHV_A4_RUN"], "w").close()
        decoy = os.path.join(self.canary_dir, "MANIFEST.sha256")   # `rm -f MANIFEST.sha256` in the wrong cwd
        self.write(decoy, "keep")
        r = self.sh(["bash", self.script, "stop"], cwd=self.canary_dir, env=e)   # data dir does not exist: cd fails
        self.assertTrue(os.path.exists(decoy), r.stderr)
        self.assertCanary()

    def test_stale_pid_is_not_signalled(self):
        victim = self.sleeper()
        e = self.env()
        open(e["EV2AHV_A4_RUN"], "w").close()
        self.write(e["EV2AHV_A4_PIDF"], str(victim.pid))
        self.sh(["bash", self.script, "stop"], env=e)
        self.assertIsNone(victim.poll())


class GuestLoad(Sandbox):
    script = os.path.join(ROOT, "tests", "e2e", "guest-load.sh")

    def test_root_and_relative_dirs_are_refused(self):
        path, log = self.rm_shim()
        for d in ("/", "relative/dir", "/data/x:/", "/ok:relative"):
            r = self.sh(["bash", self.script, "stop"], env={"EV2AHV_LOAD_DIRS": d, "EV2AHV_LOAD_PIDF": self.t + "/p",
                                                           "PATH": path})
            self.assertEqual(r.returncode, 2, f"{d!r}: {r.stderr}")
        self.assertNoRm(log)

    def test_stop_in_a_missing_dir_does_not_touch_the_cwd(self):
        decoy = os.path.join(self.canary_dir, "MANIFEST.sha256")
        self.write(decoy, "keep")
        r = self.sh(["bash", self.script, "stop"], cwd=self.canary_dir,
                    env={"EV2AHV_LOAD_DIRS": os.path.join(self.t, "does", "not", "exist"),
                         "EV2AHV_LOAD_PIDF": os.path.join(self.t, "p")})
        self.assertTrue(os.path.exists(decoy), r.stderr)

    def test_paths_with_spaces_work_and_stale_pid_is_ignored(self):
        victim = self.sleeper()
        parent = os.path.join(self.t, "a dir")
        os.makedirs(parent)
        d = os.path.join(parent, "data")
        os.makedirs(d)
        for i in range(3):
            self.write(os.path.join(d, f"f-{i}"), "data")
        pidf = os.path.join(self.t, "p")
        self.write(pidf, str(victim.pid))
        r = self.sh(["bash", self.script, "check"], env={"EV2AHV_LOAD_DIRS": os.path.join(self.t, "nope"),
                                                          "EV2AHV_LOAD_PIDF": pidf})
        self.assertIn("FAILED", r.stdout)
        self.assertIsNone(victim.poll())
        r = self.sh(["bash", self.script, "stop"], env={"EV2AHV_LOAD_DIRS": d, "EV2AHV_LOAD_PIDF": pidf})
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIsNone(victim.poll())
        self.assertTrue(os.path.exists(os.path.join(d, "MANIFEST.sha256")))
        self.assertTrue(all(os.path.exists(os.path.join(d, f"f-{i}")) for i in range(3)))


class BuildGuards(Sandbox):
    def test_mkimage_refuses_outside_the_builder_container(self):
        r = self.sh(["bash", os.path.join(ROOT, "appliance", "scripts", "mkimage.sh")])
        self.assertEqual(r.returncode, 2, r.stderr)
        self.assertIn("refusing", r.stderr)

    def test_build_refuses_a_hostile_version_before_running_docker(self):
        tree = os.path.join(self.t, "repo")
        os.makedirs(os.path.join(tree, "appliance"))
        shutil.copy(os.path.join(ROOT, "appliance", "build.sh"), os.path.join(tree, "appliance", "build.sh"))
        shim = os.path.join(self.t, "bin")
        self.write(os.path.join(shim, "docker"), f"#!/bin/sh\ntouch '{self.t}/docker-was-called'\n", 0o755)
        for ver in ("../../etc", "1.0; touch pwned", "$(id)"):
            self.write(os.path.join(tree, "VERSION"), ver + "\n")
            r = self.sh(["bash", os.path.join(tree, "appliance", "build.sh")], env={"PATH": shim + ":" + os.environ["PATH"]})
            self.assertEqual(r.returncode, 2, f"{ver!r}: {r.stderr}")
        self.assertFalse(os.path.exists(os.path.join(self.t, "docker-was-called")))
        self.assertFalse(os.path.exists(os.path.join(tree, "pwned")))


class GenCert(Sandbox):
    script = os.path.join(SBIN, "ev2ahv-gencert")

    def shims(self, openssl_ok):
        b = os.path.join(self.t, "shim")
        self.write(os.path.join(b, "install"), '#!/bin/sh\nfor a; do d=$a; done\nmkdir -p "$d"\n', 0o755)
        for n in ("ip", "restorecon", "chown"):
            self.write(os.path.join(b, n), "#!/bin/sh\nexit 0\n", 0o755)
        self.write(os.path.join(b, "openssl"), "#!/bin/sh\n" + (
            'while [ $# -gt 0 ]; do case "$1" in -keyout|-out) echo x > "$2";; esac; shift; done\n' if openssl_ok
            else "echo boom >&2\nexit 1\n"), 0o755)
        return b + ":" + os.environ["PATH"]

    def test_failed_openssl_cleans_only_its_own_scratch_dir(self):
        tls = os.path.join(self.t, "tls")
        os.makedirs(tls)
        self.write(os.path.join(tls, "server.crt"), "existing cert")
        r = self.sh(["bash", self.script], env={"EV2AHV_TLS_DIR": tls, "PATH": self.shims(False)})
        self.assertNotEqual(r.returncode, 0)
        self.assertEqual(os.listdir(tls), ["server.crt"], "scratch dir left behind or a foreign file removed")
        self.assertCanary()

    def test_success_leaves_cert_and_no_scratch_dir(self):
        tls = os.path.join(self.t, "tls")
        r = self.sh(["bash", self.script], env={"EV2AHV_TLS_DIR": tls, "PATH": self.shims(True)})
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(sorted(os.listdir(tls)), [".selfsigned", "server.crt", "server.key"])

    def test_root_dir_is_refused(self):
        r = self.sh(["bash", self.script], env={"EV2AHV_TLS_DIR": "/", "PATH": self.shims(True)})
        self.assertEqual(r.returncode, 2)


class PrepLinux(Sandbox):
    script = os.path.join(ROOT, "prep", "prep-linux.sh")

    def test_usage_errors(self):
        for args in (["--bogus"], ["--dry-run"], ["--apply", "extra"]):
            r = self.sh(["bash", self.script, *args])
            self.assertEqual(r.returncode, 2, f"{args}: {r.stdout}{r.stderr}")

    @NEED_DOCKER
    def test_root_behaviour_in_a_container(self):
        """As root, inside a throw-away container (never on this host): --apply --dry-run changes nothing, a work dir
        of / is refused, --cleanup is refused off AHV, and --apply moves (does not delete) the LVM devices file."""
        P = "bash /repo/prep/prep-linux.sh"
        script = (
            "set -u; mkdir -p /etc/lvm/devices /shim; echo dev > /etc/lvm/devices/system.devices\n"
            "echo QEMU > /vendor-src; echo Nutanix > /vendor-ahv\n"
            f"export EV2AHV_DMI_VENDOR=/vendor-src\n{P} --apply --dry-run >/tmp/o1 2>&1; echo dry=$?\n"
            "[ -e /root/ev2ahv ] && echo DRYRUN-CREATED-WORKDIR\n[ -f /etc/lvm/devices/system.devices ] && echo lvm-kept\n"
            f"EV2AHV_PREP_DIR=/ {P} >/dev/null 2>&1; echo rootdir=$?\n"
            f"{P} --cleanup --dry-run >/dev/null 2>&1; echo cleanup-on-source=$?\n"
            f"{P} --apply >/dev/null 2>&1; echo apply=$?\n"
            "[ -f /etc/lvm/devices/system.devices ] || echo lvm-moved\n"
            "[ -f /root/ev2ahv/backup/etc/lvm/devices/system.devices ] && echo lvm-backed-up\n"
            # on a (fake) AHV VM: the Stratus match must not take system packages with it, and --dry-run removes nothing
            "export EV2AHV_DMI_VENDOR=/vendor-ahv\n"
            "cat > /shim/rpm <<'X'\n#!/bin/sh\nprintf '%s\\n' everrun-agent kernel-everrun stratus-tools\nX\n"
            "cat > /shim/dnf <<'X'\n#!/bin/sh\necho \"$@\" >> /shim/removed\nX\nchmod +x /shim/rpm /shim/dnf\n"
            f"PATH=/shim:$PATH {P} --cleanup --dry-run > /tmp/o2 2>&1; echo cleanup-dry=$?; cat /tmp/o2\n"
            "[ -e /shim/removed ] && echo DRYRUN-REMOVED\n"
            f"PATH=/shim:$PATH {P} --cleanup >/dev/null 2>&1; echo cleanup=$?; cat /shim/removed\n")
        r = subprocess.run(["docker", "run", "--rm", "-v", f"{ROOT}:/repo:ro", DOCKER_PY, "bash", "-c", script],
                           capture_output=True, text=True, timeout=300)
        out = r.stdout
        self.assertIn("dry=0", out, out + r.stderr)
        self.assertNotIn("DRYRUN-CREATED-WORKDIR", out)
        self.assertIn("lvm-kept", out)
        self.assertIn("rootdir=2", out)
        self.assertIn("cleanup-on-source=2", out)
        self.assertIn("lvm-moved", out)
        self.assertIn("lvm-backed-up", out)
        self.assertIn("would remove: everrun-agent stratus-tools", out)
        self.assertNotIn("DRYRUN-REMOVED", out)
        self.assertIn("-y remove everrun-agent stratus-tools", out)   # the real run removes only the safe two
        self.assertNotIn("remove everrun-agent kernel-everrun", out)


class FirstBoot(Sandbox):
    @NEED_DOCKER
    def test_initialised_appliance_keeps_its_ssh_host_keys(self):
        script = ("mkdir -p /etc/ssh /etc/ev2ahv; echo key > /etc/ssh/ssh_host_canary; touch /etc/ev2ahv/.initialized\n"
                  "bash /repo/appliance/rootfs/usr/local/sbin/ev2ahv-firstboot; echo rc=$?\n"
                  "[ -f /etc/ssh/ssh_host_canary ] && echo kept")
        r = subprocess.run(["docker", "run", "--rm", "-v", f"{ROOT}:/repo:ro", DOCKER_PY, "bash", "-c", script],
                           capture_output=True, text=True, timeout=120)
        self.assertIn("rc=0", r.stdout, r.stderr)
        self.assertIn("kept", r.stdout)


class PowerShellGuard(Sandbox):
    """Invoke-OwnedTreeDelete (prep-windows.ps1) with hostile paths, run by pwsh (native, or in a container)."""

    def runner(self):
        if shutil.which("pwsh"):
            return lambda script: subprocess.run(["pwsh", "-NoProfile", "-Command", script.replace("/repo", ROOT)],
                                                 capture_output=True, text=True, timeout=120)
        if docker_ok() and docker_image("mcr.microsoft.com/powershell:latest"):
            def run(script):
                return subprocess.run(["docker", "run", "--rm", "-v", f"{ROOT}:/repo:ro", "-v", f"{self.t}:{self.t}",
                                       "mcr.microsoft.com/powershell:latest", "pwsh", "-NoProfile", "-Command", script],
                                      capture_output=True, text=True, timeout=300)
            return run
        self.skipTest("neither pwsh nor the mcr.microsoft.com/powershell image is available")

    def test_hostile_paths(self):
        run = self.runner()
        victim = os.path.join(self.t, "ev2ahv")
        os.makedirs(victim)
        link = os.path.join(self.t, "link", "ev2ahv")
        os.makedirs(os.path.dirname(link))
        os.symlink(self.canary_dir, link)
        plain = os.path.join(self.t, "plain", "ev2ahv")
        os.makedirs(os.path.join(plain, "sub"))
        script = (
            "$ast=[System.Management.Automation.Language.Parser]::ParseFile('/repo/prep/prep-windows.ps1',[ref]$null,[ref]$null);"
            "$fn=$ast.Find({param($n) $n -is [System.Management.Automation.Language.FunctionDefinitionAst] -and $n.Name -eq 'Invoke-OwnedTreeDelete'},$true);"
            "Invoke-Expression $fn.Extent.Text;"
            f"foreach($p in @('', ' ', '{self.t}', '{self.canary_dir}', '{link}')) {{ try {{ Invoke-OwnedTreeDelete $p 'ev2ahv'; 'NOT-REFUSED:'+$p }} catch {{ 'refused' }} }};"
            f"try {{ Invoke-OwnedTreeDelete '{victim}' '' ; 'NOT-REFUSED-EMPTY-LEAF' }} catch {{ 'refused' }};"
            f"Invoke-OwnedTreeDelete '{plain}' 'ev2ahv'; 'done'")
        r = run(script)
        self.assertNotIn("NOT-REFUSED", r.stdout, r.stdout + r.stderr)
        self.assertEqual(r.stdout.count("refused"), 6, r.stdout + r.stderr)
        self.assertIn("done", r.stdout)
        self.assertFalse(os.path.exists(plain))
        self.assertTrue(os.path.exists(victim))
        self.assertCanary()


# ------------------------------------------------------------------------------------------------ python guards
class BlockdAllowlist(Sandbox):
    """check_device / serve against a fake boot baseline, claim list and device names."""

    def setUp(self):
        super().setUp()
        self.boot = os.path.join(self.t, "boot")
        os.makedirs(self.boot)
        self.claims = os.path.join(self.t, "claims")
        self.serials = {"sda": "SYS", "sdb": "CLAIMED", "sdc": "STRANGER", "sdd": "ATBOOT"}
        self.busy = {}
        self.add("disks", "sda\nsdd\n")
        self.add("system-disks", "SYS\n")
        self.write(self.claims, "CLAIMED\nATBOOT\n")
        orig = os.path.realpath
        patches = [mock.patch.object(blockd, "BOOT", self.boot), mock.patch.object(blockd, "CLAIMS", self.claims),
                   mock.patch.object(blockd, "serial_of", lambda n: self.serials.get(n)),
                   mock.patch.object(blockd.helper, "in_use", lambda d: self.busy.get(d)),
                   mock.patch("os.path.realpath", lambda p, *a, **k: p if str(p).startswith("/dev/") else orig(p, *a, **k))]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)

    def add(self, name, text):
        self.write(os.path.join(self.boot, name), text)

    def refused(self, path):
        with self.assertRaises(PermissionError) as c:
            blockd.check_device(path)
        return str(c.exception)

    def test_claimed_new_disk_is_accepted(self):
        self.assertEqual(blockd.check_device("/dev/sdb"), "/dev/sdb")

    def test_not_a_whole_scsi_disk_is_refused(self):
        for p in ("/dev/sdb1", "/dev/nvme0n1", "/dev/dm-0", "/dev/mapper/root", "/dev/null", "/etc/passwd", "/dev/sd",
                  "/dev/sdB", "sdb", ""):
            self.assertIn("not a whole SCSI disk", self.refused(p), p)

    def test_system_disk_is_refused_even_if_claimed(self):
        self.write(self.claims, "SYS\nCLAIMED\n")
        self.assertIn("system disk", self.refused("/dev/sda"))

    def test_unclaimed_disk_is_refused(self):
        self.assertIn("not attached by ev2ahv", self.refused("/dev/sdc"))

    def test_boot_disk_that_is_not_claimed_is_refused(self):
        self.write(self.claims, "CLAIMED\n")
        self.assertIn("present at boot", self.refused("/dev/sdd"))

    def test_boot_disk_that_ev2ahv_claimed_is_accepted(self):
        self.assertEqual(blockd.check_device("/dev/sdd"), "/dev/sdd")   # staging disks survive appliance reboots

    def test_non_vdisk_is_refused(self):
        self.serials["sdb"] = None
        self.assertIn("not an AHV vdisk", self.refused("/dev/sdb"))

    def test_mounted_or_held_disk_is_refused(self):
        self.busy["/dev/sdb"] = "mounted: /dev/sdb1"
        self.assertIn("in use", self.refused("/dev/sdb"))

    def test_missing_baseline_refuses_everything(self):
        os.remove(os.path.join(self.boot, "disks"))
        self.assertIn("no boot baseline", self.refused("/dev/sdb"))

    def test_missing_claim_file_means_nothing_is_allowed(self):
        os.remove(self.claims)
        self.assertIn("not attached by ev2ahv", self.refused("/dev/sdb"))

    def serve(self, request):
        out = io.BytesIO()
        stdin = mock.Mock(buffer=io.BytesIO((json.dumps(request) + "\n").encode()))
        with mock.patch.object(sys, "stdin", stdin), mock.patch.object(sys, "stdout", mock.Mock(buffer=out)), \
                mock.patch.object(sys, "stderr", io.StringIO()), mock.patch.object(blockd.helper, "main") as hm, \
                mock.patch.object(blockd.subprocess, "run") as sp, mock.patch("builtins.open", wraps=open) as op:
            rc = blockd.serve()
            writes = [c for c in op.call_args_list if len(c.args) > 1 and "w" in str(c.args[1])]
        return rc, out.getvalue().decode(), hm, sp, writes

    def test_serve_refuses_a_write_to_an_unclaimed_disk_without_reaching_the_helper(self):
        for op in ("write", "read", "hash", "size", "offline"):
            rc, out, helper_main, subproc, writes = self.serve({"op": op, "args": ["/dev/sdc"]})
            self.assertEqual(rc, 3)
            self.assertTrue(out.startswith("EV2AHV-REFUSED"), out)
            helper_main.assert_not_called()
            subproc.assert_not_called()
            self.assertEqual(writes, [])

    def test_serve_rejects_unknown_ops_and_missing_device(self):
        for req in ({"op": "format", "args": ["/dev/sdb"]}, {"op": "write", "args": []}, {"op": None}, {}):
            rc, out, helper_main, _, _ = self.serve(req)
            self.assertEqual(rc, 3, req)
            helper_main.assert_not_called()

    def test_serve_passes_the_canonical_device_to_the_helper(self):
        rc, _, helper_main, _, _ = self.serve({"op": "write", "args": ["/dev/sdb", "--chunk", "4096"]})
        helper_main.assert_called_once_with(["write", "/dev/sdb", "--chunk", "4096"])


class HelperWrite(Sandbox):
    def test_write_refuses_non_block_non_regular_targets(self):
        for target in ("/dev/null", "/dev/zero", self.t):
            with self.subTest(target=target):
                with self.assertRaises((SystemExit, OSError)) as c:   # a directory cannot even be opened for writing
                    ev2ahv_helper.main(["write", target])
                if isinstance(c.exception, SystemExit):
                    self.assertIn("refusing", str(c.exception.code))

    def test_write_refuses_a_target_that_is_mounted(self):
        with mock.patch.object(ev2ahv_helper, "in_use", return_value="mounted: /dev/sdb1"):
            with self.assertRaises(SystemExit) as c:
                ev2ahv_helper.main(["write", "/dev/sdb"])
        self.assertIn("refusing", str(c.exception.code))


class PrismOverHttp(unittest.TestCase):
    """The Prism client with urlopen replaced: records every HTTP request that would have gone to Prism Central."""

    def setUp(self):
        self.requests = []
        self.vms = {}      # id -> name
        self.images = {}   # id -> name

    def urlopen(self, req, context=None, timeout=None):
        method, url = req.get_method(), req.full_url
        self.requests.append((method, url))
        body = {"data": {}}
        if method == "GET" and "/ahv/config/vms/" in url and "/disks/" not in url:
            body = {"data": {"name": self.vms[url.rsplit("/", 1)[1]], "powerState": "OFF",
                             "disks": [{"extId": "d1", "diskAddress": {"busType": "SCSI", "index": 0}}]}}
        elif method == "GET" and "/content/images/" in url:
            body = {"data": {"name": self.images[url.rsplit("/", 1)[1]]}}
        elif method == "DELETE":
            body = {"data": {"extId": "task1"}}

        class R(io.BytesIO):
            headers = {"ETag": "e1"}

            def __enter__(s):
                return s

            def __exit__(s, *a):
                s.close()
        return R(json.dumps(body).encode())

    def prism(self, **kw):
        p = Prism(Credentials("pc.invalid", api_key="k"), **kw)
        p.wait = lambda *a, **k: None
        patcher = mock.patch("urllib.request.urlopen", self.urlopen)
        patcher.start()
        self.addCleanup(patcher.stop)
        return p

    def mutations(self):
        return [r for r in self.requests if r[0] != "GET"]

    def test_foreign_objects_are_never_deleted_or_changed(self):
        self.vms = {"v1": "everrun-n1", "v2": "prod-db", "v3": "EV2AHV-upper", "v4": "ev2ahv", "v5": "", "v6": "x-ev2ahv-y"}
        self.images = {"i1": "Rocky-9", "i2": "ev2ahv", "i3": "my-ev2ahv-img"}
        p = self.prism()
        for vid in self.vms:
            for fn in (lambda: p.delete_vm(vid), lambda: p.power(vid, "power-off"), lambda: p.remove_vm_disk(vid, "d1"),
                       lambda: p.add_disk(vid, 1 << 30), lambda: p.set_boot_disk(vid, "SCSI", 0)):
                with self.assertRaises(NotOwned):
                    fn()
        for iid in self.images:
            with self.assertRaises(NotOwned):
                p.delete_image(iid)
        self.assertEqual(self.mutations(), [], "a non-owned object was modified over HTTP")

    def test_owned_objects_are_deleted(self):
        self.vms = {"v1": "ev2ahv-copy"}
        self.images = {"i1": "ev2ahv-img-x-vda"}
        p = self.prism()
        p.delete_vm("v1")
        p.delete_image("i1")
        self.assertEqual([m for m, _ in self.mutations()], ["DELETE", "DELETE"])

    def test_dry_run_sends_no_mutation_even_for_owned_objects(self):
        self.vms = {"v1": "ev2ahv-copy"}
        self.images = {"i1": "ev2ahv-img-x-vda"}
        p = self.prism(dry_run=True)
        p.delete_vm("v1")
        p.delete_image("i1")
        p.remove_vm_disk("v1", "d1")
        self.assertEqual(self.mutations(), [])

    def test_everrun_vm_is_never_owned_even_if_a_plan_names_it(self):
        self.vms = {"v1": "everrun-n1"}
        p = self.prism(extra_owned=["everrun-n1"])
        with self.assertRaises(NotOwned):
            p.delete_vm("v1")
        self.assertEqual(self.mutations(), [])

    def test_plan_target_is_owned_by_exact_name_only(self):
        self.vms = {"v1": "migrated-app", "v2": "migrated-app-2", "v3": "Migrated-App"}
        p = self.prism(extra_owned=["migrated-app"])
        p.delete_vm("v1")
        for vid in ("v2", "v3"):
            with self.assertRaises(NotOwned):
                p.delete_vm(vid)
        self.assertEqual(len(self.mutations()), 1)

    def test_writer_vm_may_gain_and_lose_disks_but_never_be_deleted_or_powered(self):
        self.vms = {"w": "bastion-writer"}
        p = self.prism()
        p.disk_only.add("bastion-writer")
        for fn in (lambda: p.delete_vm("w"), lambda: p.power("w", "power-off"), lambda: p.set_boot_disk("w", "SCSI", 0)):
            with self.assertRaises(NotOwned):
                fn()
        p.remove_vm_disk("w", "d1")
        self.assertTrue(any("/disks/d1" in u for _, u in self.mutations()))
        self.assertFalse(any(u.endswith("/vms/w") for _, u in self.mutations()))

    def test_create_vm_refuses_foreign_names(self):
        p = self.prism()
        for name in ("prod-db", "everrun-x", "", None):
            with self.assertRaises(NotOwned):
                p.create_vm({"name": name})
        self.assertEqual(self.requests, [])


class WorkflowCleanup(Sandbox):
    def test_safe_staging_file(self):
        ok = "/var/tmp/ev2ahv-staging/plan-vda.raw"
        self.assertEqual(workflow.safe_staging_file(ok, "/var/tmp/ev2ahv-staging"), ok)
        for bad in ("", "/", "/.raw", "/etc/passwd", "/var/tmp/ev2ahv-staging/../../etc/x.raw", "relative.raw",
                    "/var/tmp/ev2ahv-staging/sub/x.raw", "/var/tmp/other/x.raw", "/x.raw", None):
            with self.assertRaises(RuntimeError, msg=repr(bad)):
                workflow.safe_staging_file(bad, "/var/tmp/ev2ahv-staging")

    def migration(self, staging):
        class Writer:
            def __init__(self):
                self.cmds = []

            def run(self, cmd, **kw):
                self.cmds.append(cmd)
                return ""

        class P:
            extra_owned = set()

            def image_by_name(self, n):
                return None

        m = workflow.Migration.__new__(workflow.Migration)
        m.p = {"name": "t", "target": {"transfer": "image", "staging_dir": "/var/tmp/ev2ahv-staging"},
               "disks": [{"name": "vda"}]}
        m.prism, m.dry_run, m._writer = P(), False, Writer()
        m.state = State(os.path.join(self.t, "s.db"))
        m.dir, m._lock = self.t, None
        m.state.set("staging", staging)
        m.writer_vm = lambda: "vm"
        self.addCleanup(m.state.close)
        self.addCleanup(lambda: m._lock and m._lock.close())
        return m

    def test_cleanup_refuses_a_poisoned_state_record_and_runs_no_command(self):
        for bad in ("/etc/passwd", "/", "/var/tmp/ev2ahv-staging/../../etc/shadow.raw", "/home/x/.raw"):
            m = self.migration({"vda": {"file": bad, "size": 1}})
            with self.assertRaises(RuntimeError, msg=bad):
                m.cleanup()
            self.assertEqual(m._writer.cmds, [], bad)

    def test_cleanup_removes_a_legitimate_staging_file(self):
        m = self.migration({"vda": {"file": "/var/tmp/ev2ahv-staging/t-vda.raw", "size": 1}})
        m.cleanup()
        self.assertEqual(m._writer.cmds, ["rm -f /var/tmp/ev2ahv-staging/t-vda.raw /var/tmp/ev2ahv-staging/t-vda.qcow2"])

    def test_cleanup_dry_run_removes_nothing(self):
        m = self.migration({"vda": {"file": "/var/tmp/ev2ahv-staging/t-vda.raw", "size": 1}})
        m.dry_run = True
        m.cleanup()
        self.assertEqual(m._writer.cmds, [])

    def test_plan_rejects_path_tricks_and_everrun_targets(self):
        base = {"name": "ok", "writer": {"vm": "w", "host": "h"}, "source": {"type": "local"},
                "target": {"vm_name": "ev2ahv-x", "cluster": "c", "container": "k"}, "disks": [{"path": "/tmp/a"}]}

        def check(**over):
            p = copy.deepcopy(base)
            for k, v in over.items():
                if k == "tname":
                    p["target"]["vm_name"] = v
                elif k == "sdir":
                    p["target"]["staging_dir"] = v
                elif k == "dname":
                    p["disks"][0]["name"] = v
                else:
                    p[k] = v
            return plan.validate(p)
        check()
        for kw in ({"name": "../../etc"}, {"name": "a b"}, {"name": "x;rm"}, {"name": "-rf"}, {"name": "a/b"},
                   {"dname": "../x"}, {"dname": "a b"}, {"sdir": "/"}, {"sdir": "relative"}, {"sdir": "/var/tmp/../x"},
                   {"sdir": "/tmp"}, {"tname": "everrun-n1"}):
            with self.assertRaises(plan.PlanError, msg=str(kw)):
                check(**kw)


class UpgradeGuards(Sandbox):
    def setUp(self):
        super().setUp()
        a = os.path.join(self.t, "appl")
        self.saved = dict(up.P)
        up.P.update(app=f"{a}/opt/app", venv=f"{a}/opt/venv", data=f"{a}/var", state=f"{a}/var/state",
                    env=f"{a}/etc/ev2ahv.env", sock=f"{a}/run/app.sock", self=f"{a}/sbin/ev2ahv-upgrade")
        self.a = a
        self.write(f"{a}/opt/app/VERSION", "0.2.0\n")
        self.write(f"{a}/opt/venv/bin/python", "venv")
        self.write(f"{a}/var/state/db", "state")
        self.ops = {"systemctl": lambda x: None, "health": lambda v: True, "busy": lambda: False}

    def tearDown(self):
        up.P.clear()
        up.P.update(self.saved)
        super().tearDown()

    def test_rm_tree_refuses_anything_it_does_not_manage(self):
        link = os.path.join(self.a, "var", "link")
        os.symlink(self.canary_dir, link)
        fake_home = os.path.join(self.t, "home", "user")
        os.makedirs(fake_home)
        with mock.patch.object(up.shutil, "rmtree") as rmtree:   # even a broken guard cannot delete anything here
            for bad in ("", "/", "/tmp", fake_home, self.canary_dir, self.a, f"{self.a}/var", link,
                        f"{self.a}/var/../opt", "relative/path"):
                with self.assertRaises(up.UpgradeError, msg=repr(bad)):
                    up.rm_tree(bad)
            rmtree.assert_not_called()

    def test_rm_tree_removes_managed_trees(self):
        up.rm_tree(up.P["venv"])
        self.assertFalse(os.path.exists(up.P["venv"]))
        self.write(f"{self.a}/var/backups/x/f", "1")
        up.rm_tree(f"{self.a}/var/backups/x")
        self.assertFalse(os.path.exists(f"{self.a}/var/backups/x"))
        self.assertCanary()

    def test_restore_from_an_empty_or_bogus_backup_never_deletes_the_app(self):
        os.makedirs(f"{self.a}/var/backups/empty")
        with self.assertRaises(up.UpgradeError):
            up.rollback(self.ops)
        self.assertTrue(os.path.exists(f"{self.a}/opt/app/VERSION"))
        with mock.patch.object(up.shutil, "rmtree") as rmtree:
            for outside in (self.canary_dir, "/", ""):
                with self.assertRaises(up.UpgradeError):
                    up._restore(outside, self.ops)     # not inside <data>/backups
            rmtree.assert_not_called()
        self.assertTrue(os.path.exists(f"{self.a}/opt/app/VERSION"))
        self.assertCanary()

    def test_restore_with_a_backup_that_only_has_an_app_leaves_state_and_venv_alone(self):
        self.write(f"{self.a}/var/backups/b1/app/VERSION", "0.1.0\n")
        up.rollback(self.ops)
        with open(f"{self.a}/opt/app/VERSION") as fh:
            self.assertEqual(fh.read(), "0.1.0\n")
        self.assertTrue(os.path.exists(f"{self.a}/var/state/db"))
        self.assertTrue(os.path.exists(f"{self.a}/opt/venv/bin/python"))

    def test_symlink_app_dir_is_not_followed_on_restore(self):
        shutil.rmtree(f"{self.a}/opt/app")
        os.symlink(self.canary_dir, f"{self.a}/opt/app")
        self.write(f"{self.a}/var/backups/b1/app/VERSION", "0.1.0\n")
        with self.assertRaises(up.UpgradeError):
            up.rollback(self.ops)
        self.assertCanary()

    def bundle(self, members):
        path = os.path.join(self.t, "evil.tar.gz")
        with tarfile.open(path, "w:gz") as tf:
            for ti, data in members:
                tf.addfile(ti, io.BytesIO(data) if data is not None else None)
        return path

    def test_unpack_rejects_hostile_archives(self):
        def reg(name, data=b"x"):
            ti = tarfile.TarInfo(name)
            ti.size = len(data)
            return ti, data
        sym = tarfile.TarInfo("app/link")
        sym.type, sym.linkname = tarfile.SYMTYPE, self.canary_dir
        hard = tarfile.TarInfo("app/hard")
        hard.type, hard.linkname = tarfile.LNKTYPE, "/etc/passwd"
        for members in ([reg("../escape")], [reg("/abs/path")], [reg("a/../../b")], [(sym, None)], [(hard, None)]):
            work = os.path.join(self.a, "var", "work")
            os.makedirs(work, exist_ok=True)
            with self.assertRaises(up.UpgradeError):
                up.unpack(self.bundle(members), work)
        self.assertFalse(os.path.exists(os.path.join(self.t, "escape")))
        self.assertCanary()

    def test_unpack_rejects_manifest_paths_outside_the_bundle(self):
        man = json.dumps({"version": "9.9.9", "files": {"../../../etc/hostname": "0" * 64}}).encode()
        ti = tarfile.TarInfo("manifest.json")
        ti.size = len(man)
        work = os.path.join(self.a, "var", "work")
        os.makedirs(work)
        with self.assertRaises(up.UpgradeError):
            up.unpack(self.bundle([(ti, man)]), work)

    def test_apply_cleans_only_its_scratch_dir_on_failure(self):
        junk = self.write(os.path.join(self.t, "notabundle"), "garbage")
        before = set(os.listdir(f"{self.a}/var"))
        with self.assertRaises(Exception):
            up.apply(junk, ops=self.ops)
        self.assertEqual(set(os.listdir(f"{self.a}/var")), before)
        self.assertTrue(os.path.exists(f"{self.a}/opt/app/VERSION"))


class MakeBundle(Sandbox):
    def tree(self):
        src = os.path.join(self.t, "src")
        for f, txt in (("ev2ahv/__init__.py", "x"), ("server/ev2ahv_server/x.py", "x"), ("prep/p.sh", "x"),
                       ("web/dist/index.html", "x"), ("VERSION", "0.3.0\n"), ("appliance/requirements.txt", "a==1\n"),
                       ("build/wheels/a-1.whl", "w"), ("appliance/rootfs/usr/local/sbin/ev2ahv-upgrade", "#!/x\n")):
            self.write(os.path.join(src, f), txt)
        return src

    def test_a_symlink_to_a_file_outside_the_tree_is_refused(self):
        src = self.tree()
        os.symlink(self.canary, os.path.join(src, "ev2ahv", "leak.py"))
        with self.assertRaises(SystemExit):
            make_bundle.build(os.path.join(self.t, "dist"), root=src)
        self.assertFalse(os.path.exists(os.path.join(self.t, "dist", "ev2ahv-upgrade-0.3.0.tar.gz")))

    def test_bundle_is_written_only_under_out_and_contains_only_the_tree(self):
        src = self.tree()
        self.write(os.path.join(src, "tests", "secret.py"), "no")
        self.write(os.path.join(src, "ev2ahv", "__pycache__", "x.pyc"), "no")
        out = os.path.join(self.t, "dist")
        bundle, _ = make_bundle.build(out, root=src)
        self.assertEqual(sorted(os.listdir(out)), ["ev2ahv-upgrade-0.3.0.tar.gz", "ev2ahv-upgrade-0.3.0.tar.gz.sha256"])
        with tarfile.open(bundle) as tf:
            names = tf.getnames()
        self.assertTrue(all(not n.startswith("/") and ".." not in n.split("/") for n in names))
        self.assertFalse([n for n in names if "secret" in n or "pyc" in n])
        self.assertCanary()


if __name__ == "__main__":
    unittest.main()
