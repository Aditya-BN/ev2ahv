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
| D2 | P1 | Lab shortcut: `ev2ahv-appliance-2` uses the bastion's SSH keypair as its app key. In a real deployment the user authorizes the appliance's own key (shown on the Health page) on the everRun nodes; this needs a documented step or a UI flow. | appliance, docs |
| D5 | P2 | No changed-block tracking: every resync and final sync reads the whole disk (about 110 MiB/s). Downtime grows with disk size. | engine |
| D6 | P2 | Discovery uses `virsh -r` over SSH and cannot tell FT from HA (availability is blank). The everRun DOH API has it. | server/live.py, ev2ahv/everrun_api.py |
| D7 | P2 | Upgrade path is not built: a versioned bundle, DB migrations with backup and rollback, and UI/CLI apply. Deferred by the owner. | appliance, server |
| D8 | P2 | UI gaps against Move: no Prism project list, no "Edit Cutover Settings", the network step is VLAN-only (no VPC), and "test migration" works in mock mode only. | web, server |
| D9 | P3 | Windows guests need `disk_bus: pci` (virtio-blk); virtio-scsi boot did not work. | engine, docs |
| D10 | P3 | Security scan leftovers: trivy reports 1 HIGH, setuptools 70.3.0 (probably a false positive, since the venv has 84.2.1). CIS L1 has 69 fails, not triaged. `scan.sh`'s second trivy run fails on a cold DB cache. | appliance/scripts/scan.sh, docs/security.md |
| D11 | P3 | Prism username/password auth has never been used with valid credentials (API key only). | ev2ahv/credentials.py |
| D13 | P3 | UI has a single local user; no RBAC, no LDAP. The default certificate is self-signed (the Health page can replace it). | server |
| D14 | P3 | The image fallback (`target.transfer: image`) needs up to twice the disk size free on the writer. | engine |

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

## Done (recent)

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
