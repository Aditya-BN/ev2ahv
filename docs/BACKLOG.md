# Backlog: debt and what to test

Updated 2026-11-27 WIB (after 0.3.6). Version 0.3.6: still a first iteration. It works end to end in the lab, but it is not a product yet.

Take an item by posting its ID on the shared board (see [AGENTS.md](../AGENTS.md)). When you finish one, move it to
"Done" with the date, the commit and the numbers.

## Status

| Area | State | Evidence |
|---|---|---|
| Engine: seed, resync, cutover, rollback | Verified on a fake source and on real everRun guests (Rocky FT, Windows HA) | docs/results.md |
| Web UI (Move-like) on the bastion, live mode | Verified with a Rocky FT cutover | docs/results.md C4.3 |
| Appliance qcow2 (Rocky 9, Python 3.12 venv) | Booted on AHV; self-test, Stage A and Stage B (Rocky) pass | docs/appliance.md |
| Upgrade bundle | Built; applied for real on appliance-2 (0.2.0 -> 0.3.1 -> 0.3.2) | docs/results.md |

## Debt (known shortcuts and gaps)

| ID | Pri | Debt | Where |
|---|---|---|---|
| D5 | P3 | Changed-block tracking survived a real everRun FT failover in the lab (2026-11-27, see results): bitmap loss is detected and the resync falls back to a full pass, then tracking restarts on the new node. Still default off for customer guests; Stratus has not been asked. | engine, docs/cbt.md |
| D7 | P3 | Upgrades: production signing key not made yet (owner holds it, later); the lab key is not installed on the lab appliances (would refuse unsigned bundles); backups are pruned only by hand (`ev2ahv-upgrade prune`). | appliance, server |
| D8b | P2 | (owner 2026-11-27: interesting; research the Nutanix Move docs first, a real Move later.) No "Edit Cutover Settings" (Move's button was never clicked in the reference, so its fields are unknown; see docs/move-reference). Needs a look at a real Move first. | web, server |
| D9 | P3 | Windows on SCSI costs downtime (PCI boot + conversion, 69-79 s). The direct SCSI boot (vioscsi boot-start + CriticalDeviceDatabase, no bound device) does NOT boot Server 2022 on AHV: it ends in the recovery environment (lab 2026-11-27). Now opt-in (`target.try_direct_scsi`); the automatic fallback to PCI works but costs the verify timeout. A working variant needs the driver bound to a real device before cutover (e.g. a temporary SCSI disk hot-added to the source, as the conversion does on AHV). | engine, prep/prep-windows.ps1 |
| D13 | P3 | LDAP sign-in is built and tested against OpenLDAP, not yet against the lab Active Directory (needs a bind account). The default certificate is self-signed (the Health page can replace it). | server |
| D15 | P2 | Production impact and backups are untested. ev2ahv takes no snapshots and does not use VSS, but: (a) seeds and full-disk resyncs add read I/O and about one CPU core per disk on the everRun node, and that load has never been measured; (b) reading while an everRun snapshot exists is unverified; (c) D5 dirty bitmaps may clash with any QEMU-level backup on the same VM; (d) after cutover the everRun-side backup job fails, and in-guest agents usually need one full backup because disk IDs change; (e) running scripts as root on the nodes may not be covered by Stratus support. Update 2026-11-27: (a) measured, see docs/runbook.md section 7 (about 1 core, +3 ms disk latency, recommended `max_mbps: 100`); (b) (c) (d) (e) still untested. | docs/runbook.md, engine |
| D25 | P1 | Agent-based migration is not implemented. Today ev2ahv reads disks on the everRun node over SSH (root, helper in /tmp), which Stratus support may not allow on customer systems. An in-guest agent mode (read the guest's own disks from inside the VM, Linux and Windows, VSS on Windows, send to the appliance) avoids touching the everRun nodes and gives application-consistent seeds. Owner 2026-11-28: high priority, root. | engine, guest, new agent |
| D26 | P1 | NGT install (`install_ngt`) has never been tested on a real guest. Test it on Linux and Windows at cutover (NGT ISO through Prism, install, NGT shown in Prism), and a failed install must not fail the cutover. Owner 2026-11-28: high priority, root. | guest, engine |
| D27 | P1 | Stratus research (docs/research/stratus-support.md): QEMU monitor commands are unsupported in spirit ("make no changes to the QEMU virtualization engine"), so CBT (`sync.cbt`) must stay opt-in with a clear warning in the UI and docs; root SSH plus a helper is a gray area. Add a supported-path source mode: everRun snapshot + `avcli vm-snapshot-export` (VHD/VHDX to NFS/CIFS) for seeding, plus a non-root read-only host account option. Send the drafted questions to Stratus support. | engine, docs, UI |
| D28 | P1 | Bandwidth page policies are stored but never applied; plans use a fixed cap. Apply the active policy (schedule, Mbps) to seeds and resyncs. Found while writing the product guide. | server/live.py, engine |
| D29 | P1 | Every NIC of a VM is attached to the first mapped subnet. Map each source NIC to its own target subnet (wizard, plan, builder). | web, server, builder |
| D30 | P2 | The Source VM name prefix guard defaults to `ev2ahv-`, which blocks every customer VM until changed. Make it empty by default on a customer appliance (keep the lab default via config), and show it clearly in Settings. Also: the Test action falls back to the production subnet when no test network is set; refuse instead. | server, web |

## What to test

| ID | Test | How | Pass when |
|---|---|---|---|
| T5 | UEFI guest (the appliance itself boots under UEFI, 2026-11-27) | Migrate an everRun UEFI guest | Boots on AHV with UEFI, checksums match |
| T6 | Console TUI: static IP, hostname, NTP on AHV | `ev2ahv-config --console` on tty1 | Settings survive a reboot; UI reachable on the new IP |
| T7 | OVA export and ConfigDrive network | — | Deploys from OVA; ConfigDrive sets the network |
| T9 | Several VMs in one plan at once | Two `ev2ahv-src-*` guests | Both complete; resyncs do not starve each other |
| T11 | Unit tests (D12) | `tools/check.sh` or the GitHub `ci` workflow | All pass, every run (flaky `Text file busy` fixed in 0.3.3, see Done) |
| T13 | Production impact and backups (D15) | On lab everRun: seed and resync a production-sized disk while watching node I/O latency and CPU at several bandwidth limits; run an in-guest backup agent during a seed; seed while an everRun snapshot exists; cutover, then back up the AHV VM | Node latency acceptable at a documented bandwidth limit; backup job and ev2ahv both succeed; data read under a snapshot is correct; runbook has a Backups and throttling section |
| T14 | Later: Microsoft SQL Server on Windows: impact of seed/resync on SQL latency and a consistent database after cutover (DBCC CHECKDB, transaction test) | SQL Server Express or Developer on a Windows test guest with a write load | DB consistent on AHV; latency impact measured at a documented max_mbps |
| T15 | Later: MySQL / MariaDB on Linux: same as T14 (InnoDB crash recovery clean, CHECKSUM TABLE) | MariaDB on a Rocky test guest with sysbench | DB consistent on AHV; latency impact measured |

## Done (recent)

- 2026-11-27: D11/T10 done: Prism username/password works end to end (CLI test, engine create/disk/delete with ETag, appliance environment validate/refresh with matching counts, disk hot-add self-test PASS, password stored as `v1:` and absent from logs, journal and support bundle). Self-test now takes `provider`. Not run: a full migration with it. See docs/results.md.

- 2026-11-27: D21 done for appliance-2 too (owner approved). Its own key (`ev2ahv-appliance`, SHA256:eXBo8F...) is
  authorized with `from="10.12.135.154"` and no forwarding on both everRun nodes and in the Rocky guest (backups
  `authorized_keys.bak-ev2ahv-<UTC>`); swapped after a test as the `ev2ahv` user (bastion key kept as
  `id_ed25519.bastion-lab`). Environment refresh Connected; prep-check: source disks and all guest checks PASS.
- 2026-11-27: prep-check after a rollback no longer FAILs on the plan's own earlier AHV copy (same extId as the stored
  `target_vm`; the next cutover discards it first). Before, a prep-check after a rollback made the UI refuse the
  re-cutover without force. Found on appliance-2 (0.3.8, plan app-rocky-stageB): now 0 FAIL. 3 tests.
- 2026-11-27: D7 lab signing key (owner decision). Ed25519 pair made by root on the bastion; private key only at
  `/root/.config/ev2ahv/upgrade-signing/lab.key` (0600), public key `appliance/keys/ev2ahv-lab-upgrade.pub`; custody and
  rotation in docs/security.md. Checked: a 0.3.8 bundle signed with it verifies against the public key, and a changed
  manifest is refused.
- 2026-11-27: D24. Deleting a plan whose VMs were seeded is refused unless `cleanup=true`; then each such VM runs a
  new staging-only step (`cleanup-staging`, never the migrated VM) and the worker deletes the plan when all are done,
  starting nothing else for it meanwhile. The dashboard and plan page offer "Remove and delete". D23: a NIC without a
  known MAC (plan-file sources) never gets keep_mac, whatever "Retain MAC" says. 2 tests (the API one runs the real
  worker tick in live mode); not yet clicked on an appliance.
- 2026-11-27: T8 passed. Appliance rebooted mid-seed (`ev2ahv-appliance-3`, 0.3.7, plan-file source 12 GiB with 10 GiB
  written on `ev2ahv-writer-root`): reboot at 31.2 %, UI back 30 s later, seed resumed from 31.2 % (not from 0), VM Ready
  to Cutover 179 s after the reboot; events "seed interrupted by a service stop; it resumes on restart" then "seed
  finished"; nothing marked Failed; no failed units, SELinux enforcing, 0 AVC. Found D23 and D24 on the way.
- 2026-11-27: D21 for appliance-3. Its own key (`ev2ahv-appliance`, SHA256:CzvWC2PB...) was appended to root's
  authorized_keys on both everRun nodes with `from="10.12.135.160",no-agent-forwarding,no-port-forwarding,no-X11-forwarding`
  (backups `authorized_keys.bak-ev2ahv-<UTC>` on each node), tested as the `ev2ahv` user, then made the app key; the
  bastion key is kept on the appliance as `id_ed25519.bastion-lab` for a revert. everRun environment refresh: Connected.
- 2026-11-27: D10 closed. pip is no longer shipped in the venv (mkimage.sh and ev2ahv-upgrade remove it after
  installing), so trivy's only HIGH (pip's vendored setuptools 70.3.0) is gone: rebuilt image, trivy 0 HIGH/CRITICAL,
  pip-audit clean, CIS L1 195 pass / 61 fail (all 61 explained in docs/security.md; `ensure_pam_wheel_group_empty`
  accepted: root is locked, wheel is the admin sudo group).
- 2026-11-27: D7 UI upgrade and signing (0.3.7). Health page uploads a bundle; a root path unit applies it
  (`ev2ahv-upgrade apply-request`) and writes status for the page. Bundles can be Ed25519-signed; signatures are
  required once `/etc/ev2ahv/upgrade-keys/*.pub` exist. Lab on `ev2ahv-appliance-3`: CLI 0.3.5 -> 0.3.7 (5 s), then the
  same bundle through `PUT /api/v2/upgrade`: applied by the path unit, status "done" 6 s after the trigger, 0 AVC,
  no failed units. Found and fixed on the way: files copied out of `/var/lib` kept the `var_lib_t` label, so systemd
  could not run the tool (now `restorecon`); a misplaced block had left the routes unregistered. 12 new tests.
- 2026-11-27: D13 LDAP. Directory sign-in (Appliance Settings): service bind, user search with escaped filter, user
  bind, role from admin/viewer group membership, write-only bind password, clear text refused. 7 tests on ldap3's mock
  directory; end to end against an OpenLDAP 2.4 container over LDAPS and StartTLS: admin and viewer roles right, user
  in no group, wrong password and `*` refused, self-signed server refused with verification on.
- 2026-11-27: D14 closed. `target.image_format: raw` uploads the staging file itself (no qemu-img, no second copy);
  qcow2 stays the default. Same fake source (6 GiB/3 GiB written + 2 GiB/1 GiB) on `ev2ahv-writer-root`: space check
  8 GiB vs 14 GiB, peak use +4.00 vs +7.00 GiB, image upload 224.2 vs 246.4 s, downtime 276.2 vs 299.4 s. raw sends the
  full virtual size, so qcow2 can still win on very sparse disks or slow links. 2 new tests.
- 2026-11-27: D8d. Test migration works in live mode and the CLI (`ev2ahv test` / `discard-test`): the staging disks
  (hot-add) are flushed and offlined on the writer and cloned into `<target>-test` with new MACs on the plan's test
  subnet, then powered on; the source keeps running. Discard deletes only that VM; the next resync brings the staging
  disks back. Lab on `ev2ahv-writer-root`: test VM ON 15 s after the request with both disks (2 GiB, 1 GiB) and a new
  MAC; discard removed it; a resync after an 8 MiB source change sent exactly 2 chunks. Image mode is refused (nothing
  to clone). 4 unit tests.
- 2026-11-27: D8c. Target refresh reports each subnet's type and, for overlay subnets, its VPC name; the wizard's
  network step labels them ("app (VPC vpc-prd)", "VLAN136 (VLAN)"). Lab: the engine's unchanged NIC payload (with a kept
  MAC) attached a VM to overlay subnet `overlay` (VPC vpc-prd) on the first try; test VM deleted right away. Not
  covered: keeping the source IP on an overlay subnet (needs VPC IPAM reservation) and a guest booted in a VPC.
- 2026-11-27: D14 run on a real writer (`ev2ahv-writer-root`): prep-check asked 14 GiB (old rule 16), peak use 7.00 GiB
  (raw staging + one qcow2), never two qcow2 at once, none left behind, staging sha256-identical to the source.
  Downtime 299.4 s, of which 246.4 s image upload: the upload, not the space, is now the cost of image mode.
- 2026-11-27: T12 passed. everRun 8.1.1 DOH `watch` gives `availability` as `FT` for `ev2ahv-src-rocky9` and `HA` for
  `ev2ahv-src-win22`; `availability_level` maps both. A refresh on `ev2ahv-appliance-3` (0.3.5) shows rocky9 as FT. Found:
  a stopped guest is missing from discovery (everRun undefines it in libvirt), so its FT/HA and the guest itself do not
  show in the wizard until it runs (D22).
- 2026-11-27: D18. "Resync now" action for VMs that are Ready to Cutover or Tested (API action `resync`, VM menus in the
  UI): it marks the VM as never synced, so the worker starts a resync on its next tick (2 s); a resync already running
  absorbs the request. Resync interval now has a 5 min option (minimum 5). 2 tests; not yet clicked on an appliance.
- 2026-11-27: D22. A stopped everRun guest (undefined in libvirt, so invisible to `virsh -r`) is now listed from the
  web API's `watch` during refresh: power Off, FT/HA filled, with a warning that its disks are unknown until it runs
  (and the name-guard warning when it applies). Running guests still come only from virsh. 3 new tests; the state text
  `stopped` was read from the real everRun on 2026-11-27 (T12).
- 2026-11-27: D8a. Prism projects: refreshing a target environment lists its projects (v3 `projects/list`, paged);
  the wizard offers them (None by default); the engine puts the new AHV VM in the chosen project right after create
  (v3 `metadata.project_reference`, owned VMs only, no-op if already there). Lab: `ev2ahv-d8a-test` was put in project
  `Testing` and read back through v3; a second call changed nothing. 4 unit tests. Not yet run as part of a migration.
- 2026-11-27: T5 (appliance part). `ev2ahv-appliance-uefi` from image `ev2ahv-appliance-0.3.5` with v4 `UefiBoot`
  (secure boot off) booted and served the UI 60 s after power-on; forced password change (403 before, 200 after);
  Prism environment Connected; self-test PASS (5 steps: found itself, hot-added 1 GiB as /dev/sdb, helper read it,
  offlined, removed via Prism). Not checked from inside (failed units, SELinux) and not with secure boot.
- 2026-11-27: D19. Environment details show memory with the shared formatter (`6 GiB`, not `5.990234375 GiB`), and
  an everRun environment shows its release (`everRun 8.1.1-285`, read from `supernova/version` in the web API's watch
  output, seen on the lab system). 1 new test.
- 2026-11-27: D20. Cutover shows its stage next to the phase (stopping source, final sync, image upload or preparing
  disks, creating AHV VM, powering on, verifying, converting to SCSI): the runner emits it after each engine step and
  live mode stores it as `stage`. The prep-check badge shows "Checking…" while prep-check or guest prep runs instead
  of the old result. ETA uses the rate of the last 120 s instead of the since-start average, which was far off on
  sparse disks (holes pass fast). 4 tests; not yet watched on a real cutover.
- 2026-11-27: T3 and T4 done together: win22 HA migrated from appliance-3 by clicking through the UI (Playwright, 49 screenshots in docs/appliance-shots/t4/). Downtime 877.2 s, final sync 678.6 s, PCI to SCSI 78.9 s, seed about 12.6 min, resync 188 s; boots, same IP and MAC, 3389/5985 open, 400+399 of 400+399 manifest files match. See docs/results.md. D9 note: the conversion cost 79 s this time. New debt D18 to D21.
- 2026-11-27: v0.3.5 released (commit 3585d22, tag v0.3.5). Both appliances upgraded 0.3.4 -> 0.3.5 in 3 s each; self-test PASS, `running`, 0 AVC, 0 failed units afterwards (the blockd exit-flush fix, fa865b8, is confirmed). Image `ev2ahv-appliance-0.3.5` in Prism (0.3.2 kept; 0.2.0 images and `ev2ahv-writer` were deleted the same morning on purpose).
  - D7 prune: `ev2ahv-upgrade prune --keep N [--dry-run]`, guarded like the rest (own backup names only, needs `app/`, no symlinks, keep >= 1), 2 unit tests. Used on appliance-2: 5 backups -> 2.
  - D16 real result: after a rollback from the appliance the first resync started at once, took 72 s and succeeded without refreshing the environment (log: "using the engine plan from the seed"). Side effect: a changed per-VM setting is not applied while that plan is reused; refresh the environment once.
  - D5 on FT: per-VM `cbt` setting (API `PATCH /api/v2/plans/{id}/vms/{vm}/settings`, wizard checkbox, default off). FT guest Rocky 9: no block jobs, COLO none, no migration, no exports, no bitmaps before; 8 resyncs, dirty passes idle 26-27 s against 72-73 s full, fallback after bitmap removal works; cutover afterwards 439.1 s downtime, 438/438 files OK in both directories, same IP and MAC. A real failover was not run (see D5). Numbers in docs/results.md and docs/cbt.md.
- 2026-11-27: D5 on real everRun and the load part of T13/D15 (branch adit/d5-everrun, numbers in docs/cbt.md, docs/runbook.md section 7, docs/results.md). Bitmap, NBD export and dirty extents all work on win22; boot disk 149.9 s full vs about 10 s dirty. `sync.cbt: auto|off` added to plans. T13 stays open for backup agents, snapshots and cutover-then-backup.
- 2026-11-27: 0.3.3 and 0.3.4 (details in docs/results.md). Both appliances upgraded 0.3.2 -> 0.3.3 -> 0.3.4 (0.3.4 adds the D5 merge, off by default; 2-3 s each). The sudo link is now present on both; the health check ran for real in the 0.3.4 upgrade.
  - D16: the resync of a rolled-back VM reuses the seeded engine plan when the source is not in discovery, and a failed one is retried every 60 s up to 20 times (VM stays Ready to Cutover, warning events) instead of going to Failed. 6 unit tests. The first real use is the next rollback; not run on the lab.
  - D17: `cutover_result.completed_at` is now optional; legacy results and stored 0 are returned as null (never 1970). The OpenAPI file and `web/src/api.ts` follow. 2 unit tests.
  - D7 notes: the upgrade health check now also GETs `/api/v2/plans`, `plans/vms`, `providers`, `events`, `settings` (the app writes a read-only health session token to `/run/ev2ahv/health.cookie`, mode 0600), and any non-200 restores the old version (unit test: plans 500 after upgrade restores). `ev2ahv-upgrade` is linked into `/usr/sbin` (image build and `apply`), so `sudo ev2ahv-upgrade` works; the 0.3.2 tool still needed the full path and was linked by hand on both appliances. The Python 3.9 tarfile warning was already fixed by the audit (`filter="data"` kept with the member checks); the 0.3.2 tool predates it. Unit test runs unpack with warnings as errors.
  - Flaky unit test (T11): `test_stale_pidfile_never_kills_an_unrelated_process` failed with `Text file busy` in about 10 percent of runs (it rewrote a script that a background process still executed). Fixed in `tests/test_script_safety.py` (write and rename). After the fix: 40 of 40 runs pass (25 plain, 15 shuffled, py3.12-slim).
- 2026-11-27: D1, D7 (real upgrade), T1, T2, overnight lab run (details and numbers in docs/results.md, commit 80b0f71 and the docs commit after it).
  - D1/T1: 0.3.2 image (307,298,304 B) uploaded as `ev2ahv-appliance-0.3.2` and deployed as `ev2ahv-appliance-3` at 10.12.135.160: boot 19.8 s, no failed units, SELinux Enforcing, forced password change, self-test PASS.
  - D7: appliance-2 upgraded 0.2.0 -> 0.3.1 (23 s) -> 0.3.2 (2 s). It broke `/api/v2/plans` (legacy cutover result without `completed_at`); fixed in 0.3.2 with a test. New debt D16, D17 and the D7 notes.
  - T2: rollback powered off the AHV copy and started the everRun source (reachable in ~75 s; both data manifests 438/438 OK); re-cutover downtime 422.1 s, D4 result stored.

- 2026-11-26: D7 (CLI). `tools/make_bundle.py` builds a versioned bundle (app, web, wheels, the tool, manifest with
  sha256 of every file); `ev2ahv-upgrade apply` verifies it, refuses downgrades and running jobs, backs up app, venv,
  state and env, installs, health-checks and restores automatically on failure; `rollback` and `status`. Schema
  migrations via `PRAGMA user_version`. 7 unit tests on a fake appliance tree (success, failed health check restores
  app, venv and database, tampered bundle, downgrade, busy, rollback, newer schema refused).
- 2026-11-26: D1 build and D10 rescan. `dist/ev2ahv-0.3.0.qcow2` built from a clean clone (after committing the env
  template that `*.env` had kept out of git) and smoke-booted: units active, SELinux enforcing, API 200. CIS L1 187/69 ->
  195/61, with all 8 targeted rules now passing; trivy 1 HIGH (known false positive); pip-audit clean.
- 2026-11-26: D5 design and prototype (docs/cbt.md). QEMU dirty bitmaps, rotated atomically and read over NBD, plus
  `engine.sync_dirty`. Fake source, same writes: 2 GiB / 2 % dirty, 10.07 s full vs 0.86 s (11.7x); 8 GiB / 1 %,
  38.33 s vs 1.57 s (24.4x); target identical to source in every run. 7 unit tests. Not on everRun yet.
- 2026-11-26: D13 (local part). Local users with admin and viewer roles; viewers are read only (one middleware refuses
  every non-GET call). Users page for admins; the 0.2.0 login is imported as admin. 11 unit tests, 26 end-to-end checks
  on a mock server, UI build and headless-browser check (admin sees Users, viewer does not, no page errors).
- 2026-11-26: D9 reviewed; the boot failure itself is handled. The wizard picks PCI for Windows guests with virtio-blk disks,
  prep-check FAILs SCSI for them (`api.py` boot driver check), and the engine converts PCI to SCSI after cutover
  (verified 2026-11-24, docs/results.md). The row now tracks only the extra downtime.
- 2026-11-26: D14 (partly). The image-mode prep-check asked for twice the total disk size; the real worst case is the total
  plus the largest disk (one qcow2 at a time), for example 100 GiB instead of 120 GiB for 40+10+10 GiB. A failed upload no longer
  leaves its qcow2 on the writer (a retry needed room for two copies). 4 new unit tests; not run on the lab writer.
- 2026-11-26: D10 triage. The trivy HIGH (setuptools 70.3.0) is a confirmed false positive from pip's vendored package list
  (0 HIGH with pip's directory skipped). `scan.sh` shares one trivy cache, so the second run no longer fails cold (both runs
  exit 0). CIS: a static check of the tree found 3 real gaps among the "false negatives"; they are fixed in `appliance/` for
  the next build (up to 8 of the 69 fails; sysctl rules may still fail under oscap-chroot). Details in docs/security.md.
- 2026-11-26: D8 split into D8a-D8d (each needs the lab or a real Move to finish). In live mode, the wizard's project picker
  now says the project is not applied, instead of offering a hard-coded list that the engine ignored.
- 2026-11-26: D6 (code; real values not yet seen, see T12). Discovery fills FT/HA from the everRun web API
  (`EverRun.availability()`, the same `watch` call as `everrun_api.py list`), normalized by `availability_level`. If the
  everRun password is missing or the API fails, the column stays blank and a warning is logged; discovery still works.
  5 new unit tests on a sample `watch` response.
- 2026-11-26: D2. Documented step "Authorize the appliance SSH key" in docs/appliance.md (what root access is used for,
  per-node steps, rotation, revocation). The Health page shows a ready-to-paste `authorized_keys` line limited with `from=`
  and no forwarding, and the everRun validation error points there. `ev2ahv-appliance-2` still runs the lab shortcut (the
  bastion's key); switching it is for its holder (adit). Not clicked through in a browser.
- 2026-11-27: script safety audit (docs/script-safety.md, tests/test_script_safety.py, tools/lint.sh). Guards on every rm/rmtree, kill, loop/mkfs and Prism delete; `--dry-run` for guest cleanup; shellcheck and PSScriptAnalyzer in CI. Branch adit/script-safety, not merged; image build not re-run.
- 2026-11-26: D3. The source name guard is a setting: Appliance Settings, "Source VM name prefix" (`source_name_prefix`,
  default `ev2ahv-`, empty allows any VM after a confirm). A change is logged as a warning event. Older databases get the
  default. `EV2AHV_ALLOW_ANY_SOURCE=1` still overrides it (deprecated). 6 new unit tests; not clicked through in a browser.
- 2026-11-26: D12. CI in `.github/workflows/ci.yml`: unit tests on Python 3.12 with the appliance pins, a check that
  `web/openapi.json` matches the models (`tools/gen_openapi.py --check`), and `npm ci && npm run build` on Node 20.
  `tools/check.sh` runs the same checks on the bastion (44 of 44 tests pass, openapi current, tsc clean). The workflow
  itself has not run on GitHub yet.
- 2026-11-26: D4. The cutover result (downtime, final sync, verify) is now kept on the plan VM and returned by `/api/v2`
  (`PlanVm.cutover_result`) and shown on the Progress page. It was already saved in SQLite, but the response model dropped it.
  Rollback clears it. Unit tests: 44 of 44 pass, 4 new. Not yet seen on a real appliance cutover (see docs/results.md, D4).
- 2026-11-26: the venv moved to Python 3.12 and pip-audit went from 19 findings to 0 (eacdbdc). The py312 image was booted on AHV, and Stage B
  (Rocky FT, 40 GiB) ran from the appliance: downtime about 448 s, checksums match (292e839).
- 2026-11-25: Stage D appliance, first iteration, tagged v0.2.0. Stage C: UI and live mode.
- 2026-11-24: Stage B from the CLI. Rocky FT downtime 495 s; Windows HA downtime 307.5 s.
