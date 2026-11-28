# Autopilot (unattended checks)

`tools/autopilot.sh` runs from adit's crontab every 2 hours while nobody is in a Claude session. It runs no AI and changes nothing in the lab, on main or on any branch. It stops at the first unexpected error and records it, so the next Claude session reads the error and fixes it.

Steps, in order:

| Step | What it checks | Stops the run when |
|---|---|---|
| fetch | `git fetch origin` | git or the network fails |
| lab | ev2ahv-* VMs that are ON in Prism, and guests running on both everRun nodes (`virsh -r list`) | a VM runs on AHV and on everRun at the same time (same IP and MAC), or Prism or SSH fail |
| ci | the latest GitHub Actions run on main | the API fails; a red run is only flagged |
| merge | trial-merges every unmerged `adit/*` branch onto main, in order, with `git merge-tree` (objects only, no refs) | git fails; conflicts are only listed |
| tests | unit tests of each unmerged `adit/*` branch, in a temporary worktree that is removed afterwards | a worktree can't be created; a failing test is only flagged |

Results:
- `~/.local/state/ev2ahv-autopilot/latest.md`: the summary. The first line is `OK`, `ATTENTION: ...` or `FAILED at <step>`.
- `latest/`: the logs of each step.
- Runs are kept for 7 days.
- Each run posts one line on the shared board.

To resume work, a Claude session reads `latest.md` first.

Install, as adit: `(crontab -l 2>/dev/null; echo '17 */2 * * * $HOME/ev2ahv/tools/autopilot.sh >/dev/null 2>&1') | crontab -`
Remove: `crontab -e` and delete the line.
