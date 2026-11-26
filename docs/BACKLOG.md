# Backlog: debt and what to test

Updated 2026-11-26 WIB. Version 0.2.0 is the first iteration: it works end to end in the lab, but it is not a product yet.

Take an item by posting its ID on the shared board (see [AGENTS.md](../AGENTS.md)). When you finish one, move it to
"Done" with the date, the commit and the numbers.

## Status

| Area | State | Evidence |
|---|---|---|
| Engine: seed, resync, cutover, rollback | Verified on a fake source and on real everRun guests (Rocky FT, Windows HA) | docs/results.md |
| Web UI (Move-like) on the bastion, live mode | Verified with a Rocky FT cutover | docs/results.md C4.3 |
| Appliance qcow2 (Rocky 9, Python 3.12 venv) | Booted on AHV; self-test, Stage A and Stage B (Rocky) pass | docs/appliance.md |
| Upgrade bundle | Not built | — |

## Debt (known shortcuts and gaps)

| ID | Pri | Debt | Where |
|---|---|---|---|
| D1 | P1 | The root-helper BrokenPipe fix (`ev2ahv/helper/ev2ahv_helper.py`, 292e839) is not in `dist/ev2ahv-0.2.0.qcow2`. It is only hot-patched on `ev2ahv-appliance-2`. Rebuild before handing out the image. | appliance |
| D5 | P2 | Changed-block tracking is built and measured on a fake source but not wired in: needs an everRun check of read-write `virsh qemu-monitor-command` on an `ev2ahv-src-*` guest, then `sync.cbt: qemu-bitmap` in resync (docs/cbt.md, Next steps). Ask Stratus about bitmaps on FT guests. | engine, ev2ahv/cbt.py |
| D7 | P2 | Upgrade path is not built: a versioned bundle, DB migrations with backup and rollback, and UI/CLI apply. Deferred by the owner. | appliance, server |
| D8a | P2 | Prism project: the wizard's project list was hard-coded and the engine never applied the choice. Live mode now shows "None, not applied yet". To do: list projects from Prism and set the VM's project at create (v3 `metadata.project_reference`; needs a lab test). | web, ev2ahv/prism.py, builder |
| D8b | P2 | No "Edit Cutover Settings" (Move's button was never clicked in the reference, so its fields are unknown; see docs/move-reference). Needs a look at a real Move first. | web, server |
| D8c | P2 | Network step is VLAN only: subnets are listed without their type and overlay (VPC) subnets are untested as NIC targets. Needs a VPC subnet in the lab. | web, server/live.py, builder |
| D8d | P2 | "Test migration" works in mock mode only; the engine has no isolated-subnet test clone. | engine, server |
| D9 | P3 | Windows on SCSI costs downtime: the guest boots on PCI (virtio-blk) and is converted to SCSI after cutover, which added 182 s in the 2026-11-24 run. Binding `vioscsi` during guest prep, before cutover, could remove that step (not tried). | engine, ev2ahv/guest.py |
| D10 | P3 | Security scan leftovers after triage: rebuild and rescan to confirm the three CIS fixes (ipv6 forwarding, module blacklist, no `nullok`); check sysctl and modules on a booted instance; decide whether to drop pip from the venv for a clean trivy report; `ensure_pam_wheel_group_empty` open. | appliance, docs/security.md |
| D11 | P3 | Prism username/password auth has never been used with valid credentials (API key only). | ev2ahv/credentials.py |
| D13 | P3 | LDAP / AD sign-in is a stub (design in docs/security.md). The default certificate is self-signed (the Health page can replace it). | server |
| D14 | P3 | The image fallback (`target.transfer: image`) still needs the total disk size plus the largest disk free on the writer, and the upload adds to downtime. Going lower needs uploading straight from the raw file (no qcow2 copy). | engine |

## What to test

| ID | Test | How | Pass when |
|---|---|---|---|
| T1 | Fresh deploy of a rebuilt image (needs D1) | `bash appliance/build.sh`; upload as `ev2ahv-appliance-<ver>-<you>`; create a VM with a free static IP | Boots with no failed units, SELinux enforcing, forced password change, self-test PASS |
| T2 | Rollback and re-cutover from the appliance on a real guest | Plan `app-rocky-stageB` on `ev2ahv-appliance-2`; Rollback, then Cutover again | Source back up with data intact; second cutover verified (checksums) |
| T3 | Windows HA source from the appliance | `ev2ahv-src-win22`, `disk_bus: pci`, auto prep over WinRM | Boots on AHV, IP re-applied, checksums match |
| T4 | Stage B clicked through the UI (so far it was done through the API) | Browser on `ev2ahv-appliance-2`; screenshots to `docs/appliance-shots/` | Same result as the API run |
| T5 | UEFI: appliance boot, and a UEFI guest | Create the VM with UEFI boot | Boots, self-test PASS |
| T6 | Console TUI: static IP, hostname, NTP on AHV | `ev2ahv-config --console` on tty1 | Settings survive a reboot; UI reachable on the new IP |
| T7 | OVA export and ConfigDrive network | — | Deploys from OVA; ConfigDrive sets the network |
| T8 | Appliance reboot mid-seed (mid-cutover already passes) | Reboot during a seed | Seed resumes, nothing marked Failed |
| T9 | Several VMs in one plan at once | Two `ev2ahv-src-*` guests | Both complete; resyncs do not starve each other |
| T10 | Prism username/password auth (D11) | Environment with a user and password | Validate, refresh and migrate work |
| T11 | Unit tests (D12) | `tools/check.sh` or the GitHub `ci` workflow | All pass |
| T12 | FT/HA in discovery (D6) against the real everRun DOH API | `EV2AHV_EVERRUN_PASS=... python3 ev2ahv/everrun_api.py --host <portal> list`, then refresh the everRun environment in the UI | `ev2ahv-src-rocky9` shows FT and `ev2ahv-src-win22` HA; if the raw text is something else, extend `availability_level` |

## Done (recent)

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
