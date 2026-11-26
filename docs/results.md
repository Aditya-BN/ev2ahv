# Stage B results

Times are from the ev2ahv logs. WIB is UTC+7.

## Rocky 9 FT guest (`ev2ahv-src-rocky9` to `ev2ahv-rocky9-ahv`), 2026-11-24

Source: 2 vCPU, 4 GiB, BIOS, 20 GiB LVM root + 10 GiB XFS data, static IP 10.12.136.162, on everRun 8.1.1 FT.
A write load ran on both disks during seed and resync.

| Measure | Result |
|---|---|
| Seed, with live writes | 172.7 s, 2.13 GiB sent of 30 GiB |
| Resync | about 53 s per round, 0.27 to 0.50 GiB sent |
| Guest shutdown | 104 s |
| Final sync | 335 s (0.32 GiB sent, then a full verified pass on both sides) |
| AHV power-on to SSH | 39 s |
| **Total downtime** | **495 s (8.3 min), shutdown request to verified on AHV** |

The tool recorded 391 s for this run because it did not yet count the guest shutdown. That is fixed, and
495 s is taken from the log timestamps (03:13:04 to 03:21:19 UTC).

**Verified on AHV:** platform Nutanix, same IP and MAC, LVM root, `/boot` and `/data` mounted, sshd and
NetworkManager active, no failed units, system state `running`, and 359 of 359 files match their checksums.

**Rollback (B4):** AHV VM powered off, source booted on everRun, SSH back in 199 s, and the same 359 files
and manifest hashes verified on the source.

## Windows Server 2022 HA guest (`ev2ahv-src-win22` to `ev2ahv-win22-ahv`), 2026-11-24

Source: 2 vCPU, 6 GiB, BIOS, 60 GiB C: (MBR) + 10 GiB F: (GPT), static IP 10.12.136.161, everRun HA.
Only virtio-blk (`viostor`) was in use as a boot driver, so the plan used `disk_bus: pci`.

| Measure (first run, before the addendum) | Result |
|---|---|
| Seed, with live writes on C: and F: | 561.7 s, 9.9 GiB sent of 70 GiB |
| Resync | 2.6 to 2.8 min, 0.9 to 1.3 GiB |
| Shutdown (includes about 4 min of test-manifest hashing) | 5.3 min |
| Final sync | 12.6 min, mostly the verified pass over 60 GiB |
| Boot and IP re-applied by the startup task | 2 min |
| **Downtime to IP back** | **about 19.9 min** (06:40:41 to 07:00:34 UTC) |
| Rollback | AHV VM off in 45 s, source answering after 674 s (nested Windows boot) |

Verified on AHV: platform Nutanix AHV, both disks online and writable, C: and F: healthy, IP re-applied to
the adapter with the same MAC, RDP and WinRM up, and 537 of 537 files match.

## Addendum: Move-like guest prep, both modes (2026-11-24)

| Run | prep-check | Result on AHV | Rollback |
|---|---|---|---|
| Rocky, **auto** | 16 PASS after `guest-prep` | Downtime 467 s. NIC kept the name `ens3f0` (pinned by MAC). 359 of 359 files match. `guest-cleanup` found nothing to remove | Source back, data intact |
| Rocky, **manual** | FAIL "prep.json not found" before, 16 PASS after the operator ran the bundle | Downtime 457 s. 382 of 382 files match. Operator ran the cleanup bundle on AHV | Source back, data intact |
| Windows, **auto**, `scsi` | 16 PASS at the time (a false pass, fixed below) | **Did not boot**: recovery environment. Nothing lost | AHV VM off first, source booted normally |
| Windows, **auto**, `pci` | `scsi` now FAILs correctly. `pci`: 16 PASS, 1 WARN | 799 of 799 files match. IP re-applied by MAC. `guest-cleanup` ran, no ghost NICs existed | Source back |
| Windows, **manual** | FAIL "prep.json not found" before, 15 PASS + 1 WARN after the operator ran the bundle (the remaining FAIL was the existing AHV copy's name) | Prep and verification only, not migrated in manual mode | |

Recorded downtime for the Windows `pci` run was 1,512 s (25 min). It includes about 4 min of test-manifest
hashing before shutdown, which a real migration does not have.

### What the Windows `scsi` failure showed

Staging `vioscsi` and making it boot-start is not enough on Server 2022 when the VM has never had a
virtio-scsi controller: no device is bound to the driver, so Windows cannot use it for the boot disk. The
prep script now treats a boot driver as ready only when a present device uses it. For guests like this,
prep-check FAILs `disk_bus: scsi` and says to use `pci` and convert after cutover. The driver stays
staged, so Windows can install it by itself once a SCSI controller exists on AHV. The conversion itself
is now automated (see below).

### PCI to SCSI conversion (default since 2026-11-24)

Windows run on `pci` with `convert_to_scsi` at its default (on):

| Step | Result |
|---|---|
| Temporary 1 GiB SCSI disk hot-added, Windows installed `vioscsi` by itself | 4 s to add, then bound |
| Guest shutdown, disks cloned to SCSI.0 and SCSI.1, boot set to SCSI.0, PCI disks removed | |
| Boot on SCSI and full verification | passed |
| **Added downtime for the conversion** | **182 s** |

On AHV afterwards: both disks SCSI (`NUTANIX VDISK`), online and writable, C: and F: healthy, 799 of 799
files match. Rollback afterwards was clean. The recorded total downtime for this run (2,211 s) is inflated
by the test harness: about 10 min of checksumming 800 test files before the shutdown.

## Rocky multi-disk layout (REQ-002), 2026-11-25

Four disks: 20 GiB boot (XFS + LVM root), 10 GiB XFS data, and two new 5 GiB disks holding a plain ext4
partition, an LVM PV on a partition, a whole-disk PV with no partition table, and one ext4 LV (8 GiB)
spanning both PVs. All mounted by UUID. Auto mode, write load on all four filesystems.

| Measure | Result |
|---|---|
| prep-check | 18 PASS, 0 WARN, 0 FAIL |
| Seed | 258 s |
| Final sync | 464 s |
| Downtime | 636 s |
| On AHV | `/ext4` on sdc1, `/span` on vgspan across sdd and sdc2, 759 of 759 files match |
| Rollback | source back, all four manifests identical |

## Issues found and fixed

| Issue | Effect if missed | Fix |
|---|---|---|
| 2 KiB everRun metadata trailer in each disk file | Stray metadata on the AHV disk | Copy guest-visible bytes only |
| LVM devices file pinned to `/dev/vda2` | Risk of LVM not finding the PV on AHV | `prep-linux.sh --apply` |
| NIC profile bound to `ens3f0` | No network on AHV, where the NIC is `ens3` | Name pinned by MAC, profile matches by MAC |
| Power status read the wrong column | Cutover never saw the source stop | Parser fixed, unreadable status is now an error |
| Downtime excluded guest shutdown | Understated downtime | Timer starts before the shutdown |
| Full hash of sparse disks | Slow syncs | Holes are detected and skipped on the source |
| WinRM check hung while Windows reset its NIC | Cutover stuck at verify | Shorter timeout with retries |
| Windows `vioscsi` counted as ready without a device | Unbootable AHV VM | Ready only when a device uses it |
| PowerShell writes one-element arrays as objects | prep-check crashed | Accept both forms |
| Power commands limited to 5 min | Long Windows shutdown hook aborted cutover safely | `source.power.timeout` |

## Not tested

- Ghost NIC removal on a real ghost adapter (none appeared), and the optional NGT install.
- Windows guest cleanup in manual mode on AHV.

## C4.3: Rocky 9 FT cutover through the web UI (live mode), 2026-11-25 (UTC)

everRun environment registered in the UI server (discovery over ssh, read-only), plan created with automatic
prep, seed (4 disks, 40 GiB), cutover from the API the UI uses. On AHV: sys_vendor Nutanix, load-generator
manifests OK on /ext4 (189 files) and /span (188 files). Before it, the old CLI-test copy ev2ahv-rocky9-multi
was discarded (owner-approved) because it held the guest's MAC; prep-check had correctly failed on that.
The C4.2 fake-source VM ev2ahv-ui-fake and its staging disk were deleted, and the fake file removed from the writer.

## Stage B from the appliance (py312 build on AHV), 2026-11-26

Run on `ev2ahv-appliance-2` (image `ev2ahv-appliance-0.2.0-py312`, https://10.12.135.154/), through the same
`/api/v2` calls the UI makes (driven with curl). Times below are UTC (WIB = +7).

Setup: everRun environment `everrun-lab` (10.12.136.153, nodes .151/.152) and the Prism environment were added in the
appliance. This lab shortcut was used for SSH: the bastion keypair `~/.config/ev2ahv/id_ed25519` was copied to
`/var/lib/ev2ahv/config/id_ed25519` (owner ev2ahv, 0600, restorecon), so the appliance's app key is the bastion's key, which is
already authorized on the everRun nodes and in the guest. No key was added to the everRun nodes. A real deployment would
authorize the appliance's own generated key instead.

Preparation on the lab systems (owner-approved): the AHV copy of the earlier C4.3 run (`ev2ahv-rocky9-ui`, extId
9f074705-4157-4fb7-5a6a-524f00322306, description "Migrated by ev2ahv, plan ui-ui-rocky-c43-ev2ahv-src-rocky9", same MAC
00:e0:09:a4:d7:40 as the guest) was powered off and deleted; the brief called it `ev2ahv-src-rocky9`, but no AHV VM had that name.
The everRun source `ev2ahv-src-rocky9` was then started (it was stopped) and SSH on 10.12.136.162 answered. Only one of the two
was ever running with that IP.

Plan `app-rocky-stageB`: network `sharednetwork` to `VLAN136`, automatic prep over SSH (root, key), retain IP and MAC,
disk bus scsi, target name `ev2ahv-rocky9-app2`, resync every 5 min.

| Measure | Result |
|---|---|
| prep-check | 18 PASS, 0 WARN, 0 FAIL (writer helper on Python 3.12; source disks read from the everRun nodes over SSH) |
| Guest prep + prep-check | 57 s (01:58:02 to 01:58:59) |
| Seed (4 disks, 40 GiB) | 205.6 s (01:59:04 to 02:02:30) |
| Resync while waiting | 1 run, 118 s (02:07:34 to 02:09:32), driven by a write load on /ext4 and /span; ~2.4 GiB sent in total on vdd/vde |
| Cutover (start to verified) | 448 s (02:09:59 to 02:17:27); source stopped by 02:10:48; final sync 364 s to 02:16:52 |
| Downtime (source shutdown requested to target verified) | about 448 s (the engine did not store `cutover_result` timings for this run; derived from the events and job log) |
| Result on AHV | `ev2ahv-rocky9-app2` (8e75066d-a0cb-4d79-69c2-0bf01223efa2) ON, 2 vCPU, 4 GiB, 4 SCSI disks, MAC 00:e0:09:a4:d7:40, IP 10.12.136.162, ssh works, sys_vendor Nutanix, SELinux enforcing |
| Data check | manifests written on the source just before cutover: /ext4/ev2ahv 438 files and /span/ev2ahv 438 files, sha256 of both manifests identical on AHV, `sha256sum -c` OK on both |
| everRun source after | stopped (not running); not deleted |

Found and fixed during the run: the root helper's final `print("ok N")` after a write raised `BrokenPipeError` because the
client hangs up after the END frame, leaving 10 failed `ev2ahv-blockd@...` units in `systemctl --failed` (data was already
fsynced and verified). `ev2ahv/helper/ev2ahv_helper.py` now ignores that error; hot-patched onto the appliance, failed units cleared,
self-test rerun PASS. The patch is not in the shipped py312 qcow2; rebuild to include it.

Not verified: the UI itself was not clicked through (API only), rollback and re-cutover from this appliance, and a Windows
source from the appliance.

## D4: cutover result kept on the plan VM, 2026-11-26 (code and unit tests only)

Cause: live mode already wrote `cutover_result` into the plan in SQLite (`Live._cut`), but `models.PlanVm` had no such field,
so pydantic dropped it from every `/api/v2` response. That is why the Stage B numbers above had to be worked out from events.
Now `PlanVm.cutover_result` (`models.CutoverResult`) holds `completed_at`, `target_vm_id`, `downtime_seconds`,
`final_sync_seconds`, `image_upload_seconds`, `conversion_seconds`, `last_resync_seconds` and the short `verify` fields (power,
reachable; command output is left out). Rollback clears it. Mock mode fills it. The Progress page shows downtime and final sync
under "View Target VM".

| Measure | Result |
|---|---|
| Unit tests (`python -m unittest discover -s tests`, Python 3.9.25, pydantic 2.13.5; pytest is not installed) | 44 run, 44 pass (4 new in `tests/test_server.py`) |
| New tests against the old `models.py` | 3 of 4 fail (the field is missing), so they catch the bug |
| `tsc --noEmit` on `web/` | no errors |

Not verified: a real cutover on the appliance showing the stored numbers (needs the D1 rebuild, or a redeploy of `server/`
onto `ev2ahv-appliance-2`, which adit holds), `npm run build`, and a browser view of the Progress page.
