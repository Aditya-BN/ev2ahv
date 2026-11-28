# CHECKPOINT 2026-11-28 14:30 WIB (owner paused the swarm; resume tonight)

All 9 agents stopped at the usage limit (reset 14:20 WIB). Unfinished work is committed and pushed as WIP on these branches (none merged, all untested):

| Branch | Work | WIP head |
|---|---|---|
| adit/perf | parallel disks + faster writes (lab: Rocky pair, appliance-2) | bbcf40c |
| adit/report | migration report + notifications | 6c331de |
| adit/nic | D29 per-NIC subnet mapping | 2352c12 |
| adit/policy | D28 bandwidth policies + D30 name guard / test fallback | c4f3478 |
| adit/agent | D25 agent-based migration (lab: ev2ahv-agentsrc-1, appliance-4) | 7f45f0c |
| adit/ngt | D26 NGT install (lab: ev2ahv-ngt-linux 10.12.136.177) | d71e58c |
| adit/stratus | D27 CBT warning, non-root host account, snapshot-export mode | 27d1921 |
| adit/cutover | D8b scheduled cutover, cutover window | 522ad05 |
| (main tree) | D15/T13 backups and production impact (win22, appliance-3); its numbers so far are on the board | n/a |

Lab state at the pause (checked):
- AHV ON: appliance-2, -3, -4, writer-root, rocky9-app2 (.162), win22-app3 (.161), ngt-linux, agentsrc-1.
- everRun: no guests running on either node, so both sources are stopped and no VM has both copies on.

Resume: re-enable the scheduled task "ev2ahv swarm auto-continue" (trig_016G221qSDhaRkX9GTSrUJC7), or tell adit's session "resume the swarm". It sends each agent (ids in ~/.config/ev2ahv/swarm-agents.txt) "limit reset, continue", merges finished branches one by one, then releases.
Worktrees: ~/ev2ahv-<slug>.

# ev2ahv status (2026-11-27 WIB, after v0.3.6: final_verify)

Start with AGENTS.md, docs/architecture.md and docs/BACKLOG.md. Detailed numbers are in docs/results.md.

## v0.3.6 (2026-11-27)

| Item | Result | Verified |
| --- | --- | --- |
| Final sync with the dirty bitmap | Tried and **rejected** on everRun: the guest's QEMU exits with the guest, a QMP pause does not hold, and the guest writes after disk silence (2 chunks missed). The fallback worked | Yes, on the lab (docs/cbt.md) |
| `sync.final_verify: full\|sample\|none` (per-VM setting, API, wizard) | Downtime on the 40 GiB FT guest: stock 422-474 s, sample 208.0 s, none 161.0 s; data 438/438 x2, same IP and MAC each time. Default stays full | Yes, idle guest |
| Lab end state | appliance-2 on 0.3.6; AHV `ev2ahv-rocky9-app2` ON, everRun `ev2ahv-src-rocky9` STOPPED; plan cbt off, final_verify full | Yes |

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
| `ev2ahv-appliance-2` | https://10.12.135.154/, 0.3.6, ON. Holds the everRun and Prism environments and plan `app-rocky-stageB` (Completed). UI password `~/.config/ev2ahv/appliance2-ui.pw` |
| `ev2ahv-appliance-3` | https://10.12.135.160/, 0.3.5, ON. Prism and everRun (`everrun-lab`) environments, plan `win22-ui-t4`; app key is the bastion key (lab shortcut). UI password `~/.config/ev2ahv/appliance3-ui.pw` |
| `ev2ahv-rocky9-app2` (AHV) | ON, 10.12.136.162 (cut over again 2026-11-27 06:26 UTC with final_verify none, id 5d23a4f5...). The everRun source `ev2ahv-src-rocky9` is **STOPPED**; never run both. The guest still has the test load files and `/root/guest-load2.sh`; plan CBT setting is off |
| `ev2ahv-win22-app3` (AHV) | ON, 10.12.136.161, migrated from appliance-3 through the UI on 2026-11-27 (plan `win22-ui-t4`, Completed, downtime 877 s). Never start the everRun source |
| `ev2ahv-src-win22` (everRun) | **STOPPED** (since the T3/T4 cutover). Never run both. It still holds test files in C:\ev2ahv-root and F:\ev2ahv-data and the load script C:\ev2ahv\guest-load.ps1 |
| `ev2ahv-writer` | **Deleted** on purpose (2026-11-27); bastion live mode (:8444) has no writer until a new one is made |
| `ev2ahv-writer-root` | New lab writer (2026-11-27, D14): Rocky 9, 2 vCPU, 4 GiB, 20 GiB `/data` (`/data/staging` for image mode), VLAN136 10.12.136.211, user `nutanix` (sudo, bastion key authorized). Unclaimed |
| Bastion | Mock UI :8443, live UI :8444; shared board `/var/tmp/ev2ahv-collab/` |
| Prism images | `ev2ahv-appliance-0.3.2` and `-0.3.5` (the old 0.2.0 images were deleted) |

Admin over ssh: key `~/.config/ev2ahv/id_ed25519`, admin passwords in `appliance{2,3}-admin.pw`, known_hosts `~/.config/ev2ahv/known_hosts`.

## Decisions for the owner

1. D5: a real everRun failover of the FT guest (node maintenance moves both lab guests, `ev2ahv-src-win22` included), and Stratus' view, before anyone turns `sync.cbt` on for a customer guest. Done: release 0.3.5, the FT test, the cleanup.
2. A properly scoped Prism key for the colleague (the prism.env key sees nothing); he currently uses adit's credentials.
3. Still open from the backlog: D8a-d (UI gaps against Move), D9 (Windows SCSI boot), D11 (Prism user/password auth), D13 LDAP, D15/T13 (backup agents, snapshots), T3-T9, and applying a changed `cbt` while the engine plan is reused (D5).

appliance-3 app key (2026-11-27): its own `ev2ahv-appliance` key, authorized on both everRun nodes (from 10.12.135.160 only). Revert: copy `id_ed25519.bastion-lab` back over `id_ed25519` in `/var/lib/ev2ahv/config`.
