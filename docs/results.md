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

## Backlog batch, 2026-11-26 (code, docs and offline checks; no lab objects touched)

| Item | Measured | Not verified |
|---|---|---|
| D12 CI | `tools/check.sh`: 59 of 59 unit tests pass (Python 3.9.25 venv), `web/openapi.json` current, `tsc --noEmit` clean | the GitHub workflow has not run yet (branch not pushed) |
| D3 name guard setting | 6 unit tests (default `ev2ahv-`, custom prefix, empty = any, bad value, env override, engine refuses) | browser click-through, the warning event on change |
| D2 appliance key | docs + Health page line; tsc clean | pasting the line on real everRun nodes |
| D6 FT/HA | 5 unit tests on a sample DOH `watch` response | the real `availability` text on everRun 8.1.1 (T12) |
| D10 trivy | trivy 0.76.2 on the 0.2.0 tree: 1 HIGH (setuptools 70.3.0) with no file path, package list identical to pip 26.3 `vendor.txt`; 0 HIGH with pip's directory skipped. Both `scan.sh` trivy runs exit 0 with the shared cache | rescan of a rebuilt image |
| D10 CIS | static check of the tree: 3 real gaps among the "false negative" rules (ipv6 forwarding, module `blacklist`, PAM `nullok`), fixed in `appliance/` | rebuild + oscap rescan; sysctl/modules on a booted instance |
| D14 image mode | worst case for 40+10+10 GiB: 100 GiB (was 120 GiB); 4 unit tests incl. qcow2 removal after a failed upload | a run on the lab writer |

## D5 changed-block tracking on a fake source, 2026-11-26 (builder container, QEMU 10.1)

`docker run --rm -v $PWD:/src:ro -w /src ev2ahv-builder python3 tools/cbt_bench.py <GiB> <dirty %>`; details in docs/cbt.md.

| Disk | Dirty | Full resync | Dirty-bitmap resync | Speed-up |
|---|---|---|---|---|
| 2 GiB | 2 % (10 of 512 chunks) | 10.07 s, 512 chunks read | 0.86 s, 10 read | 11.7x |
| 8 GiB | 1 % (20 of 2048 chunks) | 38.33 s, 2048 chunks read | 1.57 s, 20 read | 24.4x |

Both passes left the target byte-identical to the source (`cmp`). Not run on everRun.

## T2 attempt, 2026-11-26 (stopped before any change)

State before: AHV `ev2ahv-rocky9-app2` ON and answering on 10.12.136.162 (sys_vendor Nutanix); manifests on the AHV copy
`/ext4/ev2ahv` and `/span/ev2ahv` 438 lines each, `sha256sum -c` OK. everRun `ev2ahv-src-rocky9` stopped.
Finding: everRun undefines a stopped guest in libvirt (`virsh -r list --all` on both nodes shows only
`ev2ahv-src-win22`), so a refreshed environment drops the source, and 0.2.0 rebuilds the engine plan from discovery
before every step: rollback from the appliance would fail. Fixed on branch `root/T2-rollback-stopped-source`; not yet
deployed to `ev2ahv-appliance-2`, so rollback and re-cutover were not run.

## D1 rebuild 0.3.0 and D10 rescan, 2026-11-26 (bastion, image not yet on AHV)

Built with `bash appliance/build.sh` from branch `root/D1-rebuild-0.3.0` (main + T2 fix + D13 + D5). The first build
failed on a clean clone: `*.env` in .gitignore had kept `appliance/rootfs/etc/ev2ahv/ev2ahv.env` out of the repo; it is
now committed (byte-identical to the copy the 0.2.0 builds used).

| Measure | Result |
|---|---|
| Image | `dist/ev2ahv-0.3.0.qcow2`, 306,970,624 bytes, sha256 a32f842f318a490a..., MANIFEST.json and SPDX SBOM |
| Smoke boot (qemu TCG, `appliance/scripts/smoke.sh`) | all checked units active, SELinux Enforcing, first-boot SSH host keys, TLS cert and instance key generated with the expected owners and modes, `/api/v2/appinfo` HTTP 200 through nginx (`version 0.3.0`, live, appliance), console menu shows 0.3.0. cloud-init "degraded" only because qemu has no datasource |
| trivy (HIGH, CRITICAL) | 1 HIGH, the known pip-vendored setuptools 70.3.0 false positive (docs/security.md); both scan.sh trivy runs exit 0 |
| pip-audit | no known vulnerabilities |
| OpenSCAP CIS L1 | 195 pass, 61 fail (0.2.0: 187 / 69). Now passing: no_empty_passwords, sysctl_net_ipv6_conf_all_forwarding, kernel_module_{cramfs,freevxfs,hfs,hfsplus,jffs2,usb-storage}_disabled |

Not done: T1 on AHV (upload as `ev2ahv-appliance-0.3.0-root` and deploy), because the Prism API key in prism.env sees
no VMs or images.

## Overnight lab run 2026-11-27 (WIB; logs UTC): D7 real upgrade, T2, T1 (version 0.3.2)

Everything below ran on the real lab (Prism `NTNX-DC`, everRun `everrun-lab`) from the bastion, through the appliance API
and the ev2ahv-upgrade CLI. Real, not staged. Nothing outside `ev2ahv-*` objects was touched.

### Builds (bastion, `bash appliance/build.sh`, then `tools/make_bundle.py`)

| Build | Result |
|---|---|
| 0.3.1 from main 727863d | 6 min 30 s; `ev2ahv-0.3.1.qcow2` 307,101,696 B; bundle 18,577,680 B (73 files) |
| 0.3.2 from main 80b0f71 (after the bug below) | 6 min 16 s; `ev2ahv-0.3.2.qcow2` 307,298,304 B (293 MiB), sha256 fd0d36b0...5619; `ev2ahv-upgrade-0.3.2.tar.gz` 18,577,913 B, sha256 3776b828...cf85 |

### D7 for real: `ev2ahv-appliance-2` 0.2.0 (py312 + hot patches) -> 0.3.1 -> 0.3.2

Before: 2 environments Connected (Prism Central 91 VMs, everRun 1), plan `app-rocky-stageB` Completed, SELinux Enforcing,
no failed units. The bundle was copied with scp; 0.2.0 has no `ev2ahv-upgrade`, so the tool was extracted from the bundle
and run as `sudo python3 ./ev2ahv-upgrade apply` (it installed itself to /usr/local/sbin).

| Step | Result |
|---|---|
| 0.2.0 -> 0.3.1 | bundle verified (73 files), 23 s total (17:43:22 to 17:43:45 UTC) including the venv rebuild from the bundled wheels; backup `20261126T174322Z-0.2.0` |
| Problem found | `/api/v2/plans` returned HTTP 500. The old plan stored the raw engine cutover result (`vm`, `timings`, `verify`) with no `completed_at`, and 0.3.x's `CutoverResult` requires it. The upgrade's own health check (`/api/v2/appinfo`) passed, so it did not roll back. |
| Fix | `PlanVm` now reads the legacy shape (`completed_at` 0 = unknown), with a unit test; version 0.3.2 (80b0f71). The old plan keeps its numbers (downtime 421.8 s, final sync 364.2 s). |
| 0.3.1 -> 0.3.2 | 2 s (17:52:26 to 17:52:28 UTC, venv unchanged); backup `20261126T175226Z-0.3.1` |
| After | version 0.3.2, venv Python 3.12.16, no failed units, SELinux Enforcing, 0 AVC, UI login with the existing password, both environments Connected (secrets decrypt, so the instance key and database survived), plan intact, selftest PASS |

The automatic restore was not exercised (no upgrade failed its own check).

### T2: rollback and re-cutover from the appliance (plan `app-rocky-stageB`, `ev2ahv-src-rocky9`)

| Step | Result |
|---|---|
| Before | AHV `ev2ahv-rocky9-app2` ON, everRun source stopped (absent from discovery, as everRun undefines a stopped guest) |
| Rollback (UI action, 17:53:14 UTC) | the engine step finished in about 12 s using the seeded engine plan (the T2-rollback-stopped-source fix works on the real system: no "no longer in" error from rollback itself). AHV VM powered off and discarded; everRun source started and answered ping about 75 s after the request, `sys_vendor` Stratus, boot 17:54:30 UTC |
| Source data | `sha256sum -c MANIFEST.sha256` in `/ext4/ev2ahv` and `/span/ev2ahv`: 438 of 438 OK in each, 0 failures |
| Post-rollback resync | failed once: `ev2ahv-src-rocky9 is no longer in everrun-lab. Refresh the environment` (the plan went to Ready to Cutover and resync rebuilt the plan from the stale discovery list). Refresh environment + Retry cleared it; resync took about 70 s (69.6 s) |
| Cutover again (17:59:23 UTC) | Completed after 531 s wall (resync, final sync, stop source, create, power on, verify) |
| Stored result (D4) | `downtime_seconds` 422.1, `final_sync_seconds` 364.4, `last_resync_seconds` 69.6, `verify` {power ON, reachable 10.12.136.162:22}, new `target_vm_id` 006cf2a0-b1c1-48c3-5cb4-ef5872465437 (rollback deletes the old AHV VM; the cutover creates a new one) |
| AHV check | ON; same IP 10.12.136.162 and MAC 00:e0:09:a4:d7:40; `sys_vendor` Nutanix; manifests 438/438 OK in both directories; everRun source stopped again. The two were never running together. |

Compare: the first cutover (2026-11-26) had downtime about 421.8 s; this one 422.1 s.

### T1: fresh deploy of 0.3.2 as `ev2ahv-appliance-3`

| Step | Result |
|---|---|
| Image | Prism image `ev2ahv-appliance-0.3.2` (daabb60c-fb58-483c-8123-d03069189f1d), upload 39.6 s through the v3 file API |
| VM | `ev2ahv-appliance-3` (a0090f8b-2db6-484b-7aa1-f9aa927064ca): 2 vCPU, 4 GiB, 20 GiB disk cloned from the image (no `storageContainer`), BIOS, NIC on VLAN135-IPAM, static 10.12.135.160 |
| IP proof | 10.12.135.160: no ping (3 probes, twice), neighbour entry INCOMPLETE, in no NIC of any of the Prism VMs, outside every IPAM pool of VLAN135-IPAM, no DNS name |
| First boot | ping after ~15 s, HTTPS 200 after ~24 s; 19.8 s to multi-user (4.5 kernel + 3.6 initrd + 11.7 userspace); `systemctl --failed` empty, system state running, SELinux Enforcing, 0 AVC, Python 3.12.16, version 0.3.2, ~500 MiB RAM used idle |
| Forced password change | default login gave `must_change`, API 403 until changed, then 200; new UI password in `~/.config/ev2ahv/appliance3-ui.pw` (0600). The expired `admin` password was changed over ssh (new one in `~/.config/ev2ahv/appliance3-admin.pw`, 0600) and the bastion key added to admin's authorized_keys |
| AOS environment | Nutanix-lab-PC added with the lab API key: Connected, 89 VMs visible |
| Health and self-test | health finds itself (`ev2ahv-appliance-3`); self-test PASS (1 GiB hot-add, root helper, offline, remove); no failed units afterwards (the BrokenPipe fix works in the shipped image) |

Not done on appliance-3: the everRun environment, a migration, the console static-IP menu (the VM got its address from IPAM).

## D5 and T13 on real everRun: `ev2ahv-src-win22`, 2026-11-27 (branch adit/d5-everrun)

Read-only SSH on node1 (10.12.136.152) and `virsh qemu-monitor-command` on `ev2ahv-src-win22` only. The guest was running
when found and was left running. Nothing was written to the guest disks except test files that were deleted afterwards
(C:\ev2ahv-d5*, F:\ev2ahv-d5*: 2 x 32 MiB of random data per round, WinRM).

| Measure | Result |
|---|---|
| QEMU | `qemu-unity` reporting 6.2.0; plain raw over file nodes, no block job, mirror, COLO, migration or bitmap before |
| Bitmap add / rotate / remove | in memory, non-persistent, 64 KiB granularity; add instant, rotate 1.0 s over ssh |
| NBD export of the frozen bitmap | unix socket, read-only; `qemu-img map` 0.05 s; dirty bytes equal the bitmap count (34.4 MB for 32 MiB written) |
| Full hash of the 60 GiB boot disk | 149.9 s |
| Dirty-only (67 chunks, 39.4 MB dirty) | 7.6 s read + 2.8 s tracker |
| T13 reader load | see docs/runbook.md section 7: sdb await 2.0 ms idle, 5.1-5.6 ms reading; about 1.1 cores; guest probe unchanged within noise; recommended `max_mbps: 100` |

Left on the lab afterwards: no bitmaps, no NBD server or socket, no exports, test files removed from the guest. The
temporary helper scripts the engine copies to the node's `/tmp` are the usual ones. Unit tests: see commit.

## Release 0.3.3 (2026-11-27 WIB; logs UTC): D16, D17, D7 notes, flaky test, both appliances upgraded

Real, run from the bastion on the lab. No migration, rollback or cutover was run; the everRun and AHV Rocky guests were not touched.

### Build

`bash appliance/build.sh`: 7 min 35 s, `ev2ahv-0.3.3.qcow2` 307,625,984 B, sha256 7f150b3f...e5b8. `tools/make_bundle.py`: `ev2ahv-upgrade-0.3.3.tar.gz` 18,582,690 B, 73 files. Unit tests 162 of 162 pass (3.9 and 3.12).

### Upgrades with `ev2ahv-upgrade apply` over ssh as admin (sudo), 0.3.2 -> 0.3.3

| Appliance | Apply (UTC / WIB 27 Nov) | Backup | Verified after |
|---|---|---|---|
| `ev2ahv-appliance-2` 10.12.135.154 | 19:47:35 to 19:47:37 UTC (02:47 WIB), 2 s, venv unchanged | `20261126T194735Z-0.3.2` | version 0.3.3, Python 3.12.16, ev2ahv/nginx/blockd.socket/chronyd/sshd/firewalld/auditd active, SELinux Enforcing, 0 AVC since boot, UI login 200, both environments Connected, `/api/v2/plans` 200 with `app-rocky-stageB` Completed (downtime 422.1 s, completed_at unchanged), plans/vms, events, settings 200, self-test PASS (5 s) |
| `ev2ahv-appliance-3` 10.12.135.160 | 19:48:15 to 19:48:17 UTC (02:48 WIB), 2 s | `20261126T194815Z-0.3.2` | same list: AOS environment Connected, no plans (none created), no failed units, 0 AVC, self-test PASS |

- The upgrade ran with the installed 0.3.2 tool, so its health check was the old one (appinfo only) and it still printed the tarfile `RuntimeWarning` (the 0.3.2 tool predates the safety audit's `filter="data"`). I ran the new `data_check` from the installed 0.3.3 tool against both live apps afterwards: OK (all five list endpoints 200 with the app's health session).
- `/usr/sbin/ev2ahv-upgrade` was not created by these upgrades (the old tool does not link). I created the symlink by hand on both; `sudo ev2ahv-upgrade status` then works. New images have the link from `mkimage.sh`, and `ev2ahv-upgrade apply` from 0.3.3 on creates it.
- appliance-2 shows 7 failed `ev2ahv-blockd@...` per-connection units (PIDs 2807-3007, UID 996) from before the upgrade (state `degraded` also before). They are leftovers of earlier resync and rollback jobs on that appliance; not investigated, not caused by 0.3.3. appliance-3 has none.
- The automatic restore on a failing health check was exercised only in unit tests (a plans 500 after upgrade restores), not on the lab.

### Flaky unit test found

About 10 percent of full-suite runs in `python:3.12-slim` (3 failures in about 30 runs, plain and shuffled order) failed in `test_script_safety.RunDev.test_stale_pidfile_never_kills_an_unrelated_process` with `OSError: [Errno 26] Text file busy`: the test rewrote its fake `py` script while the previous run's background process was still executing it. Fix: `Sandbox.write` writes beside the file and renames it. After the fix (repo state of 0.3.3, py3.12-slim container): 25 plain runs and 15 shuffled-order runs, 40 of 40 passed, 0 failures.


### 0.3.4: the same plus the D5 merge (main 87250d9), upgraded the same night

0.3.3 was built before the D5 CBT merge (`sync.cbt`, default off), so main was rebased and a second release built: `ev2ahv-0.3.4.qcow2` 307,494,912 B (sha256 5b77586d...325d, build 6 min 47 s), `ev2ahv-upgrade-0.3.4.tar.gz` 18,583,972 B. 170 unit tests pass. The 0.3.3 image was not deployed anywhere; only its bundle was applied.

| Appliance | Apply 0.3.3 -> 0.3.4 | Verified |
|---|---|---|
| `ev2ahv-appliance-2` | 20:10:21 to 20:10:24 UTC (03:10 WIB), 3 s, backup `20261126T201021Z-0.3.3` | version 0.3.4, 7 units active, SELinux Enforcing, 0 AVC, login 200, both environments Connected, `/api/v2/plans` 200 (`app-rocky-stageB` Completed), self-test PASS |
| `ev2ahv-appliance-3` | 20:10:26 to 20:10:28 UTC, 2 s, backup `20261126T201026Z-0.3.3` | same; AOS environment Connected, self-test PASS, no failed units |

This was the first upgrade run by the 0.3.3 tool on the lab: `sudo ev2ahv-upgrade apply` worked (symlink), no tarfile warning, and the new health check (appinfo plus plans, plans/vms, providers, events, settings with the app's health session) passed on the real apps. The 7 old `ev2ahv-blockd@` failed units on appliance-2 are unchanged. Nothing migrated, rolled back or cut over; D5 is off by default and was not exercised on the appliances.


## 2026-11-27 morning run (WIB 08:00-09:10; logs UTC): v0.3.5 and D5 on the FT guest

Real lab, from the bastion, through the appliance API and `ev2ahv-upgrade`. Only `ev2ahv-*` objects were touched.

### v0.3.5 release

- Contents: the blockd exit-flush fix (fa865b8), `ev2ahv-upgrade prune --keep N [--dry-run]`, and a per-VM changed-block tracking setting (API, UI checkbox, default off; see the D5 part below). 175 unit tests, openapi current, tsc and shellcheck clean. Commit 3585d22, tag `v0.3.5`.
- Build (`appliance/build.sh`): 6 min 32 s. `ev2ahv-0.3.5.qcow2` 306,905,088 B; `ev2ahv-upgrade-0.3.5.tar.gz` 18,585,497 B (73 files), sha256 c32e2f4d...0d99 (checked on the appliance after scp).
- Image uploaded to Prism as `ev2ahv-appliance-0.3.5` (b409f2bb-082e-4c91-9a59-8796d31df9fd), 38.5 s. `ev2ahv-appliance-0.3.2` kept.

| Appliance | Apply 0.3.4 -> 0.3.5 | Verified after |
|---|---|---|
| `ev2ahv-appliance-2` | 01:06:57 to 01:07:00 UTC (08:06 WIB), 3 s, backup `20261127T010657Z-0.3.4`. Before it: `systemctl reset-failed` cleared the 7 old failed blockd units (state was `degraded`) | version 0.3.5, `is-system-running` running, SELinux Enforcing, 0 AVC since boot, login 200, `/api/v2/plans` and `/providers` 200, self-test PASS, then 0 failed units |
| `ev2ahv-appliance-3` | 01:08:27 to 01:08:30 UTC, 3 s, backup `20261127T010827Z-0.3.4` | same list; self-test PASS, 0 failed units |

The blockd fix is confirmed on both appliances: after a self-test (which hot-adds a disk and talks to the helper) there are 0 failed `ev2ahv-blockd@` units. On appliance-2 this came on top of a full day of jobs: the rollback, 8 resyncs and a cutover in the D5 run below also left 0 failed units (checked at the end, state `running`).

Prune: `ev2ahv-upgrade prune --keep 2` on appliance-2, dry run first, removed `20261126T174322Z-0.2.0`, `...175226Z-0.3.1` and `...194735Z-0.3.2`; kept `...201021Z-0.3.3` and `...010657Z-0.3.4`. Only directories named like the ones `apply` creates, holding an `app/` and not symlinks, are considered; `--keep` below 1 is refused; deletes go through the same `rm_tree` guard (2 unit tests, see docs/script-safety.md).

### D16, first real test: resync after rollback

Plan `app-rocky-stageB` (Rocky 9 FT `ev2ahv-src-rocky9`, 4 disks, 40 GiB, resync every 5 min) on appliance-2.

| Step | Result |
|---|---|
| Rollback from the appliance (AHV `ev2ahv-rocky9-app2` off, everRun source started) | 01:10:33 to 01:10:45 UTC, 12 s |
| First resync after the rollback | Started 01:10:45, finished 01:11:57 (72 s), 0 bytes sent. **It succeeded without refreshing the environment.** The log shows the D16 path: "ev2ahv-src-rocky9 is no longer in everrun-lab. Refresh the environment; using the engine plan from the seed". |
| Source data | `sha256sum -c MANIFEST.sha256`: `/ext4/ev2ahv` 438 of 438 OK, `/span/ev2ahv` 438 of 438 OK, 0 failures (guest up 3 min, `sys_vendor` Stratus Technologies) |

Finding: while the engine plan is reused, per-VM setting changes (the new `cbt` one) are not applied, because the plan is not rebuilt. After a rollback the environment has to be refreshed once before the change takes effect (done at 01:17 UTC; the 01:23 resync rebuilt the plan). Recorded as a follow-up in BACKLOG.

### D5 on the FT guest (everRun 8.1.1, qemu-unity 6.2)

Read-only QMP checks on both nodes before enabling (`virsh qemu-monitor-command ev2ahv-src-rocky9`): node0 `running` (primary), node1 `paused` (partner). On both: `query-block-jobs` `[]`, `query-colo-status` mode `none`, `query-migrate` `{}`, `query-block-exports` `[]`, no dirty bitmaps, plain raw nodes over file nodes (4 disks, libvirt-2..5). This is the same picture as the HA guest (win22): FT replication is not a QEMU block job, COLO or migration that a bitmap could clash with. So tracking was enabled.

Enabling: the new `PATCH /api/v2/plans/{id}/vms/{vm}/settings` with `{"cbt": "auto"}`, then an environment refresh (see the finding above). Write load: `tests/e2e/guest-load.sh` (current version, copied to the guest) on `/ext4/ev2ahv`, `/span/ev2ahv` and `/data/ev2ahv` (about 1 file per second per directory, 1-4 MiB each, plus random 128 KiB rewrites of older files). Per-disk numbers are in the engine log (`.../ui/<plan>/ev2ahv-src-rocky9.log`); wall time is from the plan events. "Sent" is the sum over the 4 disks.

| # | Start UTC | Mode | Load | Wall | Chunks read | Sent |
|---|---|---|---|---|---|---|
| R1 | 01:10:45 | full scan, cbt off (after rollback) | none | 72 s | 10,240 | 0 B |
| R2 | 01:16:57 | full scan, cbt off | none | 73 s | 10,240 | 348 MB |
| R3 | 01:23:14 | full scan, tracking starts (first cbt resync) | heavy | 140 s | 10,240 | 2,068 MB |
| R4 | 01:30:38 | dirty only (697 of 10,240 chunks) | heavy | 126 s | 697 | 2,793 MB |
| R5 | 01:37:48 | dirty only (354 chunks) | moderate (60 files) | 73 s | 354 | 1,418 MB |
| R6 | 01:44:04 | dirty only (11 chunks) | none | 26 s | 11 | 29 MB |
| R7 | 01:49:35 | bitmaps removed by hand: fallback full scan, tracking restarts | moderate | 110 s | 10,240 | 705 MB |
| R8 | 01:56:28 | dirty only (17 chunks) | none | 27 s | 17 | 54 MB |

- Idle, CBT against a full scan: 26-27 s against 72-73 s (2.7x), 11-17 chunks read instead of 10,240. The floor is about 5-6 s per disk (rotate over ssh and virsh about 1 s, NBD export and extent query, teardown), so on a small 40 GiB guest the gain is smaller than the 14x seen on the 60 GiB win22 boot disk.
- Under heavy load the transfer to the target dominates (about 23 MB/s), so a dirty pass saves little (126 s against 140 s). Reading was never the limit there. CBT pays off when the dirty set is small against the allocated size.
- Time per disk, vda (20 GiB) idle: 26-29 s full scan, 1.5-2.6 s dirty.
- Fallback: R7 logged, per disk, "dirty-bitmap pass failed (no ev2ahv-cbt-* bitmap on libvirt-N-format: tracking was interrupted), full pass instead", completed correctly, and R8 was a dirty pass again. This **simulated** the bitmap loss of a failover or restart by removing the bitmaps over QMP on node0; it is not a real failover.
- Not run: a real everRun failover or migration of the guest. The everRun API has no per-VM failover for an FT guest; the way to force one is node maintenance on a node, which moves both lab guests (`ev2ahv-src-win22` too, which was off limits) and needs the everRun password (kept only in the appliance's store, not on the bastion). Skipped on purpose.
- Cleanup: setting back to `off` (PATCH), then every `ev2ahv-cbt-*` bitmap removed over QMP on node0. Afterwards on both nodes: no bitmaps, `query-block-exports` `[]`, `query-block-jobs` `[]`, COLO `none`, no `/var/tmp/ev2ahv-cbt-*` socket. The guest never paused or lost connectivity during the 8 resyncs.

### Cutover again from the appliance

Load stopped, manifests written (`stop`): `/ext4/ev2ahv` 438 files, `/span/ev2ahv` 438 files, both `check` OK on the source. Cutover started 01:59:01 UTC (08:59 WIB) and the plan reached Completed at 02:08:30.

| Item | Result |
|---|---|
| Stored cutover result | downtime **439.1 s**, final sync 377.9 s, last resync 93.8 s, power ON, reachable 10.12.136.162:22. No image upload or conversion (Rocky, SCSI) |
| AHV VM | `ev2ahv-rocky9-app2`, new id 645b6dfa-5cc9-4f21-4b58-a4fe57bef1b4, ON; the previous (rolled back) copy was deleted by the cutover, so there is one VM of that name |
| Guest | same IP 10.12.136.162, same MAC 00:e0:09:a4:d7:40, `sys_vendor` Nutanix, kernel 5.14.0-570.17.1.el9_6, 4 disks 20/10/5/5 GiB, `is-system-running` running |
| Data | `sha256sum -c MANIFEST.sha256`: `/ext4/ev2ahv` 438 of 438 OK, `/span/ev2ahv` 438 of 438 OK, 0 failures |
| everRun | `ev2ahv-src-rocky9` stopped (not defined on node0 or node1); the two were never running together |

The final sync is unchanged (a verified full pass), so the downtime is in line with T2 (422.1 s). The dirty pass was not used for the final sync.

## T3/T4 Windows via the UI from appliance-3, 2026-11-27 (logs UTC; WIB = +7)

`ev2ahv-src-win22` (everRun HA, 60 GiB C: + 10 GiB F:, 10.12.136.161) migrated to `ev2ahv-win22-app3` from `ev2ahv-appliance-3` (v0.3.5)
by driving the real UI with headless Chromium (`web/tools/t4.mjs`, deviceScaleFactor 2, 49 screenshots in `docs/appliance-shots/t4/`).
The only non-UI calls were read-only checks (plan JSON, Prism, WinRM) and the test harness: `tests/e2e/guest-load.ps1` started a write load
on C: and F: before the resync, then `-Mode stop` wrote the SHA-256 manifests (400 + 399 files) on the source just before cutover.

Lab shortcut (same as appliance-2): the bastion keypair was installed as the appliance app key,
`/var/lib/ev2ahv/config/id_ed25519` (owner ev2ahv, 0600, restorecon, with the `.pub`); the appliance's generated key was kept as `*.generated`.
A real deployment would authorize the appliance's own key on the everRun nodes.

| Step (all in the UI) | Result |
|---|---|
| Add everRun environment `everrun-lab` (10.12.136.153, nodes .151/.152), refresh | Connected, 2 VMs listed |
| Wizard: target AOS `Nutanix-lab-PC` / NTNX-DC / VM_Container; `sharednetwork` to `VLAN136`; automatic prep (WinRM, Administrator); retain IP and MAC; `disk_bus: pci` with convert to SCSI (default); guest IP 10.12.136.161; name `ev2ahv-win22-app3` | Saved and started as `win22-ui-t4` |
| Prep and prep-check | about 3 min (02:25 to 02:28:40); badge PASS |
| Seed (70 GiB, sparse skipped) | about 12.6 min (02:28:40 to 02:41:16) |
| Resync under load | 188.3 s (02:56 to 02:59). Needed the plan edited to the 15 min interval, see UI problems |
| Cutover (UI, 03:09:15 UTC) | Completed; screenshots 40 to 49 |
| **Stored cutover result** | downtime **877.2 s (14.6 min)**, final sync **678.6 s**, PCI to SCSI conversion **78.9 s**, last resync 188.3 s, verify power ON and 10.12.136.161:5985 reachable |

Verified on AHV (Prism v4 and WinRM): VM `ev2ahv-win22-app3` (8e487f94-c10f-4c83-69ea-5f5e561ae305) ON, 2 vCPU, 6 GiB, MAC 00:e0:09:a4:d7:41, two SCSI disks
("NUTANIX VDISK", online, writable), C: and F: healthy, `Manufacturer Nutanix / AHV`, IP 10.12.136.161 answers ping, ports 3389 and 5985 open,
F: 400 of 400 and C: 399 of 399 files match, manifest hashes identical to the source's. The everRun source is **stopped** (`vm-info` state stopped).
Not done: RDP login (port check only), rollback (not needed).

UI problems found (no code change was needed to finish; none fixed yet, see BACKLOG D18 to D21):
1. No "Resync now". The first resync waits for the interval (default 1 hour, smallest option 15 min). To see a resync inside the window the plan had to be edited through the wizard, which asks for the Windows password again.
2. Environment details show memory as `5.990234375 GiB` and version as just `everRun`.
3. Seed ETA is unreliable on sparse disks (34 min shown, finished in 12.6 min).
4. The prep-check badge shows WARN while the check is still running.
5. Cutover shows no stage text beyond "cutover NN%"; a 15 min wait with no hint of final sync versus conversion.
