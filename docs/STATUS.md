# ev2ahv status (2026-11-27 WIB, after v0.3.5 and the D5 FT test)

Start with AGENTS.md, docs/architecture.md and docs/BACKLOG.md. Detailed numbers are in docs/results.md.

## Since the overnight run (2026-11-27 morning WIB)

| Item | Result | Verified |
|---|---|---|
| Release **v0.3.5** (tag pushed) | Both appliances on 0.3.5: `running`, SELinux Enforcing, 0 AVC, login and plans/environments 200, self-test PASS, then 0 failed blockd units (the fix works). Image `ev2ahv-appliance-0.3.5` in Prism. New: `ev2ahv-upgrade prune --keep N`, per-VM changed-block tracking setting (default off) | Yes, on the lab |
| Backups pruned | appliance-2 keeps the 2 newest (0.3.3, 0.3.4 backups) | Yes |
| D16 first real test | First resync after the rollback ran at once, 72 s, no environment refresh needed | Yes |
| D5 on the FT guest | No block jobs, COLO or migration, so tracking was enabled. 8 resyncs under load: dirty pass idle 26 s against 72 s full; heavy load is transfer-bound (126 s against 140 s). Fallback after bitmap loss works. Real failover **not** run (simulated by removing the bitmaps) | Yes, except failover |
| Cutover again from the appliance | Downtime 439.1 s, final sync 377.9 s; 438/438 files OK in both directories, same IP and MAC, `sys_vendor` Nutanix | Yes |

## Overnight results (2026-11-27, all on the real lab unless marked)

| Item | Result | Verified |
|---|---|---|
| D7 upgrade bundle, real | appliance-2: 0.2.0 → 0.3.1 (23 s) → 0.3.2 → 0.3.3 → 0.3.4 (2-3 s each). The 0.3.1 upgrade broke `/api/v2/plans` without being caught. Since 0.3.3 the health check reads plans, VMs, providers, events and settings, and any failure restores the old version | Upgrades: yes. Automatic restore: unit tests only |
| T2 rollback and re-cutover from the appliance (Rocky FT, 40 GiB) | Rollback about 12 s; source back with 438/438 files OK in each folder. Re-cutover: **422 s downtime**, final sync 364 s; 438/438 OK in each folder on AHV, same IP and MAC | Yes |
| T1 fresh deploy | `ev2ahv-appliance-3` from image 0.3.2: boot 19.8 s, forced password change, self-test PASS | Yes |
| D16 resync after rollback | Reuses the seeded plan; retries instead of failing | Unit tests; next real rollback will show |
| D17, legacy cutover results | `completed_at` is null instead of 1970 | Unit tests |
| Safety audit | 10 real dangers fixed (see docs/script-safety.md): writer/appliance VM deletable, upgrade restore deleted the app before checking the backup, unvalidated staging paths in rm, stale-pid kills, firstboot wiped host keys, prep cleanup without dry-run | 60 tests, shellcheck and PSScriptAnalyzer in CI |
| D5 changed-block tracking on everRun (win22) | QEMU dirty bitmap + NBD. Resync of a 60 GiB disk takes 10.4 s instead of 150 s (**14x**). Does not interfere with everRun replication (no QEMU block jobs). Option `sync.cbt: auto`, default off | Yes on HA win22, read side only. Not tested: FT, failover |
| T13 load on the everRun node | One reader: about 130 MiB/s, about 1.1 core, +3 ms disk latency. Recommended `max_mbps: 100` (docs/runbook.md section 7) | Single 30 s samples |
| Flaky CI | "Text file busy" race in test_script_safety; fixed, 40/40 runs pass | Yes |
| blockd failed units (status 120) | Exit-time stdout flush after the client hangs up; fixed in fa865b8, shipped in 0.3.5. The 7 old failed units on appliance-2 were cleared with `systemctl reset-failed` and none came back | Yes (0.3.5, lab) |

Tests: 175 unit tests, CI green on main (see the commit status below).

## Running now

| Object | State |
|---|---|
| `ev2ahv-appliance-2` | https://10.12.135.154/, 0.3.5, ON. Holds the everRun and Prism environments and plan `app-rocky-stageB` (Completed). UI password `~/.config/ev2ahv/appliance2-ui.pw` |
| `ev2ahv-appliance-3` | https://10.12.135.160/, 0.3.5, ON. Prism environment only. UI password `~/.config/ev2ahv/appliance3-ui.pw` |
| `ev2ahv-rocky9-app2` (AHV) | ON, 10.12.136.162 (cut over again 2026-11-27 02:08 UTC, id 645b6dfa...). The everRun source `ev2ahv-src-rocky9` is **STOPPED**; never run both. The guest still has the test load files and `/root/guest-load2.sh`; plan CBT setting is off |
| `ev2ahv-src-win22` (everRun) | Running, as before. Its AHV copy was deleted, so there is no conflict |
| `ev2ahv-writer` | **Deleted** on purpose (2026-11-27); bastion live mode (:8444) has no writer until a new one is made |
| Bastion | Mock UI :8443, live UI :8444; shared board `/var/tmp/ev2ahv-collab/` |
| Prism images | `ev2ahv-appliance-0.3.2` and `-0.3.5` (the old 0.2.0 images were deleted) |

Admin over ssh: key `~/.config/ev2ahv/id_ed25519`, admin passwords in `appliance{2,3}-admin.pw`, known_hosts `~/.config/ev2ahv/known_hosts`.

## Decisions for the owner

1. D5: a real everRun failover of the FT guest (node maintenance moves both lab guests, `ev2ahv-src-win22` included), and Stratus' view, before anyone turns `sync.cbt` on for a customer guest. Done: release 0.3.5, the FT test, the cleanup.
2. A properly scoped Prism key for the colleague (the prism.env key sees nothing); he currently uses adit's credentials.
3. Still open from the backlog: D8a-d (UI gaps against Move), D9 (Windows SCSI boot), D11 (Prism user/password auth), D13 LDAP, D15/T13 (backup agents, snapshots), T3-T9, and applying a changed `cbt` while the engine plan is reused (D5).
