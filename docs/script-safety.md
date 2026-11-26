# Script and code-path safety

Goal: no script or code path here can delete, overwrite or reconfigure more than it created or was pointed at,
even with empty variables, odd paths, symlinks, a failed `cd` or a stale pidfile. This page lists every such path,
what guards it, and how that is tested (`tests/test_script_safety.py`). Audit date 2026-11-27 (branch `adit/script-safety`).

State of the evidence: the guards and tests below ran on the bastion (Python 3.9) and in `python:3.12-slim`; nothing was
run against the lab. `appliance/build.sh`, `mkimage.sh`, `smoke.sh` and `scan.sh` were linted and their refusal paths
tested, but the full image build was **not** re-run after the edits (unconfirmed). The Windows scripts were parsed and
linted with PSScriptAnalyzer, and the `Invoke-OwnedTreeDelete` guard was tested in a PowerShell container; the Windows
prep itself was not run on a Windows guest.

## Inventory

Risk is what could go wrong if the guard were missing or wrong, after the fixes in this branch.

| Script / code | What it can destroy | Guards now | Risk |
|---|---|---|---|
| `appliance/scripts/mkimage.sh` (runs in the privileged builder container) | `sfdisk`/`mkfs` on a loop device, `rm -rf` inside the image chroot, `rm -rf /out/tree`, `umount -l` in the trap | refuses outside a container or without `/src` and `/out`; `IMG_SIZE` pattern; loop device must be `/dev/loopN` **and** backed by `/out/ev2ahv.raw` before `mkfs`; all vars quoted; trap only unmounts its own `$ROOT` mounts and detaches its own loop; the cleanup `rm -rf` in the chroot refuses unless `$ROOT` is a mountpoint holding bash and the fstab it just wrote | med (privileged by design) |
| `appliance/build.sh` | `rm -f build/wheels/*`, `docker run --privileged` | must run in the repo root; `VERSION` must match `[0-9A-Za-z._-]+`; `rm` uses `${REPO:?}` and only the wheel cache; vars quoted | low |
| `appliance/scripts/smoke.sh` (+ injected `inject.sh`) | `truncate`, loop mount of the copied image, `mknod`, `chcon` | inject step refuses outside the smoke container or without `/s/test.raw`; loop device must be `/dev/loopN`; trap unmounts `/mnt` and detaches only its loop; works on a copy, never the shipped image | low |
| `appliance/scripts/scan.sh`, `smoke-inner.sh` | `scan.sh`: `mount -t proc` and `umount` inside a scanner container only. `smoke-inner.sh`: read-only checks | vars quoted, `cd ... \|\| exit`; mounts are inside `docker run` containers | low |
| `appliance/rootfs/usr/local/sbin/ev2ahv-firstboot` | `rm -f /etc/ssh/ssh_host_*` (SSH host keys) | exits 0 when `/etc/ev2ahv/.initialized` exists (the systemd condition was the only guard before); `set -euo pipefail` | med |
| `.../ev2ahv-gencert` | overwrites the TLS key and cert, scratch dir cleanup | TLS dir `/` refused; scratch dir via `mktemp`, `chmod 0700`; trap removes only the `.gen.*` dir it created (a failed openssl no longer leaves a private key behind); files replaced by `mv` | low |
| `.../ev2ahv-config` (console menu, root) | `nmcli connection delete` for every other ethernet profile, `chrony`/`sshd` config rewrite, `hostnamectl` | interactive and confirmed; deletes by connection **type** (not by name pattern) and keeps the managed profile; validated IPv4 input; `sshd -t` before reload. Not changed in this branch | med |
| `.../ev2ahv-upgrade` (root, Python) | `rmtree` of the app, venv, state and scratch dirs; overwrites the app, DB and env from a backup | new `rm_tree` refuses anything that is not one of its own trees or strictly inside the data dir (no `""`, `/`, `$HOME`, symlink, `..`, other path); restore checks that the backup is inside `<data>/backups` and has an `app/` **before** stopping or deleting; restore builds the copy beside the target and swaps it in, so a missing or broken backup never deletes the live app; tar members must be regular files or dirs (no links, devices, `..`, absolute), `filter="data"` on 3.12+; manifest paths cannot escape the bundle; the scratch dir is removed with the same guard | med |
| `ev2ahv/blockd.py` (root daemon) | raw write to a block device, `offline` (SCSI delete), SCSI host rescan | allowlist, unchanged and now tested: whole `/dev/sdX` only; AHV vdisk serial present; never a disk in use at boot; a boot disk only if ev2ahv claimed it; serial must be in the claim list; not mounted, no partitions mounted, no holders; no baseline means refuse everything; the check runs per connection right before the op and the helper receives the canonical path | high by nature, guarded |
| `ev2ahv/helper/ev2ahv_helper.py` | `write` opens a device `O_WRONLY` | refuses mounted or held devices (existing); now also refuses anything that is not a block device or a regular file (char devices, fifos, directories) and a non-positive chunk; frames are bounds-checked against the device size; never `O_CREAT`/`O_TRUNC` | med |
| `ev2ahv/target.py` | picks the staging device on the writer | device chosen by proof only: new since the baseline, exact size, unmounted, exactly one candidate (existing, tested) | low |
| `ev2ahv/workflow.py` `ensure_staging`, `upload_images`, `cleanup`, `discard_target`, `forget_target_host_key` | `rm -f` / `truncate` of staging files on the writer (as root via sudo), Prism deletes, `os.remove(known_hosts)` | every staging path goes through `safe_staging_file` (absolute, normalised, `*.raw`, directly inside the plan's `staging_dir`), so a poisoned state record or plan name cannot point `rm` elsewhere; `rm -f` only of the exact file and its `.qcow2` twin, never a glob; Prism deletes by id after the owned-name check; `cleanup` and `discard` honour `--dry-run`; the source is never deleted | low |
| `ev2ahv/plan.py` | plan/disk names and `staging_dir` end up in file names | names must match `\w[\w.-]*`; `staging_dir` must be absolute, normalised, two levels deep; `target.vm_name` may not start with `everrun-` | low |
| `ev2ahv/prism.py` `delete_vm`, `delete_image`, `remove_vm_disk`, `power`, `create_vm`, `add_disk`, `set_boot_disk` | VMs, images, disks in Prism Central | every mutation re-reads the object and checks its **name** (not an id from the caller): prefix `ev2ahv-` or the plan's exact target name; `everrun-*` is never owned, even if a plan names it; the writer/appliance VM is now `disk_only` (disks can be added and removed, but it can no longer be deleted, powered or re-booted: the old comment promised this, the code did not enforce it); no delete by pattern anywhere (`image_by_name` is an exact match and errors on duplicates); `dry_run` sends no mutation | high by nature, guarded |
| `tools/make_bundle.py` | writes only `dist/` | now refuses a file that resolves outside the repo (a symlink could have bundled a key or `/etc/shadow`); skips tests, `node_modules`, caches | low |
| `server/run-dev.sh` | `kill` of the PID in `/tmp/ev2ahv-<mode>.pid` (world-writable dir) | PID is signalled only when `/proc/<pid>/cmdline` contains `ev2ahv_server`; digits only; mode must be `mock` or `live`; `cd ... \|\| exit`; vars quoted | med |
| `tests/e2e/fake-source.sh` | loop mount of the lab test image, `kill` of the load loop, `umount` | mount point must be `/mnt/ev2ahv-*`; `$W/loop` must name a `/dev/loopN` block device; PID checked via `/proc` before `kill`; `cd` failure cannot reach `rm`; vars quoted; paths overridable for tests | med (lab use, root) |
| `tests/e2e/ui-fake/power.sh` | `rm -rf $D` on teardown, `kill` | `$D` must be absolute and end in `ev2ahv-uifake*`; `rm -rf -- "${D:?}"`; PID checked via `/proc`; new `dry-run` command prints what teardown would do | low |
| `tests/e2e/guest-load.sh` (in the source guest) | writes files, rewrites manifests, `kill` | load dirs must be absolute and not `/`; PID checked via `/proc`; manifests written inside `cd "$d" &&` subshells; dirs are an array (colon separated override) so spaces work | low |
| `tests/e2e/guest-load.ps1` (Windows guest) | `Stop-Process` of PowerShell processes | match narrowed to `guest-load.ps1` and `-Mode loop`, never the current process | low |
| `tools/win_check.sh` | read-only checks over WinRM | credentials file must exist; only `Get-*` and `guest-load.ps1 -Mode check` | low |
| `prep/prep-linux.sh` (root, on the guest) | removes the LVM devices file, writes initramfs/dracut/NM/udev config, `dnf/yum/apt remove` of packages **matched by name** (`stratus\|everrun`) | refuses `--apply` on AHV and `--cleanup` off AHV (tested); the LVM file is moved only after a successful backup; **new** `--dry-run` for `--apply` and `--cleanup` (changes nothing, creates no work dir); **new** the name match can no longer take system packages (`kernel*`, `glibc*`, `systemd*`, `bash*`, `openssh*`, `dracut*`, `grub*`, `qemu*`, `virtio*`, `lvm2*`, `sudo*`, `NetworkManager*`, `nutanix*`) with it, they are reported as a problem; work dir `/` refused | med |
| `prep/prep-windows.ps1` (admin, on the guest) | `Remove-Item -Recurse` of `ProgramData\ev2ahv`, registry `StartOverride`, ghost NIC removal, `msiexec /x` of products **matched by name** (`Stratus\|everRun`), NGT install | `Remove-Item -Recurse` only through `Invoke-OwnedTreeDelete` (non-empty path and leaf, leaf must be `ev2ahv`, never a drive root, `%SystemRoot%`, `%USERPROFILE%`, `%ProgramData%`, `%ProgramFiles%`, never a junction or symlink); `ProgramData` unset aborts; `-LiteralPath` everywhere; driver name validated; **new** `-DryRun` for Cleanup and Remove; Cleanup refuses off AHV, Prep refuses on AHV | med |

Not scripts that change a host (read only or lab-local): `tools/console_shot.py`, `tools/winrm_run.py`,
`scripts/scan_free_ips.py`, `tools/cbt_bench.py` (its `rmtree(w)` is a `tempfile.mkdtemp` dir), `server/ev2ahv_server/api.py`
(`os.remove(ui.reset)`, a constant path), `ev2ahv/guest.py` (writes one temp script to the guest and runs it).

## Real dangers found (and fixed)

1. **Writer or appliance VM could be deleted or powered off by the guard.** `workflow.writer_vm` and `appliance.py`
   added the writer's name to `extra_owned` with the comment "disks only", but `extra_owned` allowed everything.
   Fixed with `Prism.disk_only`.
2. **`ev2ahv-upgrade` `_restore` deleted the live app before checking the backup.** `rmtree(app)` then `copytree(backup/app)`:
   a backup dir without `app/` (partial backup, `rollback` picking the newest directory whatever it held) left the
   appliance without an app. Now validated first, and swapped in atomically.
3. **State and plan values reached `rm -f`/`truncate` as root on the writer** (`workflow.py` staging files). A plan
   name such as `../../x` or a corrupted state record could aim `rm -f` at another `*.raw` file. Names and paths are now validated.
4. **`server/run-dev.sh` and the e2e scripts `kill`ed whatever PID a stale or planted pidfile named** (`/tmp` is
   world-writable). Now verified through `/proc/<pid>/cmdline`.
5. **`ev2ahv-firstboot` deleted the SSH host keys on any manual re-run.**
6. **`prep-linux.sh --cleanup` removed packages by a case-insensitive name match** with no preview and no exclusion list.
   Same idea for Windows `msiexec /x`: both got `--dry-run` / `-DryRun`.
7. **Windows `Remove-Item -Recurse -Force` on an environment-derived path** with no check for an empty value, a junction
   or the wrong directory.
8. **`make_bundle.py` followed symlinks out of the repo** into the upgrade bundle (a leak, not a deletion).
9. `ev2ahv-gencert` left a private key in a `.gen.*` directory when openssl failed (and its `mktemp` directory was not
   traversable for non-root because of the `umask 0137`).
10. `mkimage.sh`, `smoke.sh`: privileged loop/mount/mkfs steps had no check that they run in a container or on the
    expected loop device; unquoted variables throughout.

No script was found that runs `rm -rf "$VAR/"*` with an unguarded empty variable; the only `rm -rf` calls on variables
were `power.sh teardown` (constant path), the upgrade tool, and the Windows script above.

## `--dry-run` coverage

| Where | Option |
|---|---|
| `python -m ev2ahv cleanup/discard/rollback ...` | existing `--dry-run` (Prism and workflow log `[dry-run]`, send nothing) |
| `prep/prep-linux.sh` | `--apply --dry-run`, `--cleanup --dry-run` |
| `prep/prep-windows.ps1` | `-Mode Cleanup -DryRun`, `-Mode Remove -DryRun` |
| `tests/e2e/ui-fake/power.sh` | `dry-run` |

## How it is tested

`python -m unittest tests.test_script_safety` (also part of `discover`, `tools/check.sh` and CI). Shell scripts run in
temp dirs with canary files; where a broken guard would be catastrophic (`/` as work dir) `rm` is replaced by a recording
shim. Root-only behaviour (`prep-linux.sh`, `ev2ahv-firstboot`) runs in a `python:3.12-slim` container and skips with a
reason when docker is missing. The PowerShell guard runs under `pwsh` (native, or the `mcr.microsoft.com/powershell`
image if already pulled) and skips otherwise.

Static checks: `tools/lint.sh` runs shellcheck (`-S warning`, every file with a bash shebang or `.sh`) and, where `pwsh`
exists, PSScriptAnalyzer with `tools/PSScriptAnalyzerSettings.psd1` (excludes `PSAvoidUsingWriteHost` and
`PSReviewUnusedParameter` on purpose). CI runs it in the `lint` job.

## Not covered (known gaps)

- A full `appliance/build.sh` run after these edits (needs docker privileged and ~20 minutes).
- `mkimage.sh` guards that need a real loop device (`losetup` backing-file check) run only in the builder container.
- `ev2ahv-config` (console menu) has no test; it needs root and NetworkManager.
- `blockd.py serve` against real `/dev/sdX` devices: only the decision logic is tested (fake baseline, claims, serials).
- `ev2ahv-firstboot` first-run path (key generation) is not exercised; only the "already initialised" guard is.
- The Windows prep scripts were not run on a Windows guest.
