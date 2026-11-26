# ev2ahv status (morning of 2026-11-27 WIB)

Start with AGENTS.md, docs/architecture.md and docs/BACKLOG.md. Detailed numbers are in docs/results.md.

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
| blockd failed units (status 120) | Exit-time stdout flush after the client hangs up; fixed in fa865b8. **Not in 0.3.4**: appliance-2 shows 7 old failed `ev2ahv-blockd@` units (cosmetic, `systemctl reset-failed` clears them) | Reproduced and fixed locally; needs the next bundle |

Tests: 170 unit tests, CI green on main.

## Running now

| Object | State |
|---|---|
| `ev2ahv-appliance-2` | https://10.12.135.154/, 0.3.4, ON. Holds the everRun and Prism environments and plan `app-rocky-stageB` (Completed). UI password `~/.config/ev2ahv/appliance2-ui.pw` |
| `ev2ahv-appliance-3` | https://10.12.135.160/, 0.3.4, ON. Prism environment only. UI password `~/.config/ev2ahv/appliance3-ui.pw` |
| `ev2ahv-rocky9-app2` (AHV) | ON, 10.12.136.162. The everRun source `ev2ahv-src-rocky9` is **STOPPED**; never run both |
| `ev2ahv-src-win22` (everRun) | Running, as before. Its AHV copy was deleted, so there is no conflict |
| `ev2ahv-writer` | ON. Only needed for bastion live mode |
| Bastion | Mock UI :8443, live UI :8444; shared board `/var/tmp/ev2ahv-collab/` |
| Prism images | `ev2ahv-appliance-0.2.0`, `-0.2.0-py312`, `-0.3.2` (the last is current) |

Admin over ssh: key `~/.config/ev2ahv/id_ed25519`, admin passwords in `appliance{2,3}-admin.pw`, known_hosts `~/.config/ev2ahv/known_hosts`.

## Decisions for the owner

1. Release 0.3.5 (blockd fix plus the safety audit, which 0.3.4 already has) and tag it? It takes about 10 minutes plus 2 upgrades.
2. D5: test it on the FT guest (needs a Rocky rollback, so the source runs) and with an everRun failover, before anyone turns `sync.cbt` on.
3. Cleanup: the old images `ev2ahv-appliance-0.2.0` and `-0.2.0-py312`, `ev2ahv-writer` (if bastion live mode is not needed), and pruning the appliance-2 backups.
4. A properly scoped Prism key for the colleague (the prism.env key sees nothing); he currently uses adit's credentials.
5. Still open from the backlog: D8a-d (UI gaps against Move), D9 (Windows SCSI boot), D11 (Prism user/password auth), D13 LDAP, D15/T13 (backup agents, snapshots), T3-T9.
