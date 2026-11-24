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
is not automated yet.

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
- Converting a `pci` Windows guest to `scsi` after cutover.
