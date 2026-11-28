#!/bin/bash
# Unattended checks while nobody is in a Claude session. Deterministic, no AI, and it changes nothing in the lab or on main.
#   tools/autopilot.sh            (cron: every 2 h; see docs/autopilot.md)
# Steps, in order:
#   1. fetch;
#   2. lab safety (no VM running on everRun and AHV at once, appliances answer);
#   3. CI on main;
#   4. trial-merge every adit/* branch onto main (conflicts reported, nothing written);
#   5. unit tests of every adit/* branch in a throwaway worktree.
# Any unexpected error stops the run. The error and its step are written to $STATE/latest.md so a later Claude session can pick them up.
set -euo pipefail
export PATH=/usr/local/bin:/usr/bin:/bin:$HOME/.local/bin
REPO=${EV2AHV_REPO:-$HOME/ev2ahv}
STATE=${EV2AHV_AUTOPILOT_STATE:-$HOME/.local/state/ev2ahv-autopilot}
PY=$REPO/.venv/bin/python
KEY=$HOME/.config/ev2ahv/id_ed25519
KH=$HOME/.config/ev2ahv/known_hosts
NODES=${EV2AHV_EVERRUN_NODES:-"10.12.136.151 10.12.136.152"}
APPLIANCES=${EV2AHV_APPLIANCES:-"10.12.135.154 10.12.135.160 10.12.135.164"}
# AHV copy name = everRun source name; a pair must never run on both sides.
PAIRS=${EV2AHV_PAIRS:-"ev2ahv-rocky9-app2=ev2ahv-src-rocky9 ev2ahv-win22-app3=ev2ahv-src-win22"}

TS=$(date -u +%Y%m%dT%H%M%SZ)
RUN=$STATE/runs/$TS
mkdir -p "$RUN"
exec 9>"$STATE/lock"
flock -n 9 || { echo "another run is active" >&2; exit 0; }
STEP=init
REPORT=$RUN/report.md
note() { echo "$*" >> "$REPORT"; }

finish() {
  rc=$?
  if [ $rc -ne 0 ]; then
    { echo; echo "## FAILED at step: $STEP (exit $rc)"; echo '```'; tail -40 "$RUN/$STEP.log" 2>/dev/null; echo '```'; } >> "$REPORT"
    status="FAILED at $STEP"
  else
    status=${ATTENTION:+ATTENTION: $ATTENTION}
    status=${status:-OK}
  fi
  sed -i "1s|.*|# ev2ahv autopilot $TS: $status|" "$REPORT"
  cp "$REPORT" "$STATE/latest.md"
  ln -sfn "$RUN" "$STATE/latest"
  find "$STATE/runs" -mindepth 1 -maxdepth 1 -type d -mtime +7 -exec rm -r -- {} + 2>/dev/null || true   # keep a week
  [ -x /var/tmp/ev2ahv-collab/post ] && /var/tmp/ev2ahv-collab/post "autopilot (adit, unattended): $status. Details: $STATE/latest.md" >/dev/null 2>&1 || true
  exit $rc
}
trap finish EXIT
echo "# ev2ahv autopilot $TS: running" > "$REPORT"
ATTENTION=""
attention() { ATTENTION="${ATTENTION:+$ATTENTION; }$*"; note "- ATTENTION: $*"; }

# ---- 1. fetch
STEP=fetch
{ git -C "$REPO" fetch -q --prune origin; git -C "$REPO" rev-parse --short origin/main; } > "$RUN/$STEP.log" 2>&1
note "main: $(git -C "$REPO" log --oneline -1 origin/main)"

# ---- 2. lab safety (read-only)
STEP=lab
{
  "$PY" - "$REPO" <<'P' > "$RUN/ahv-on.txt"
import sys; sys.path.insert(0, sys.argv[1])
from ev2ahv.credentials import load
from ev2ahv.prism import Prism
for v in Prism(load()).list_vms():
    if v.get("name", "").startswith("ev2ahv-") and v.get("powerState") == "ON":
        print(v["name"])
P
  : > "$RUN/everrun-running.txt"
  for n in $NODES; do
    ssh -o BatchMode=yes -o ConnectTimeout=10 -o UserKnownHostsFile="$KH" -i "$KEY" "root@$n" 'virsh -r list --name' \
      | sed '/^$/d' >> "$RUN/everrun-running.txt"
  done
  cat "$RUN/ahv-on.txt" "$RUN/everrun-running.txt"
} > "$RUN/$STEP.log" 2>&1
note "AHV ev2ahv VMs ON: $(paste -sd' ' "$RUN/ahv-on.txt")"
note "everRun guests running: $(paste -sd' ' "$RUN/everrun-running.txt")"
for p in $PAIRS; do
  ahv=${p%%=*}; src=${p##*=}
  if grep -qx "$ahv" "$RUN/ahv-on.txt" && grep -qx "$src" "$RUN/everrun-running.txt"; then
    echo "DUAL RUN: $ahv is ON on AHV and $src runs on everRun (same IP and MAC)" >> "$RUN/$STEP.log"
    false
  fi
done
for a in $APPLIANCES; do
  code=$(curl -sk -o /dev/null -m 15 -w '%{http_code}' "https://$a/api/v2/appinfo" || true)
  [ "$code" = 200 ] || attention "appliance $a answered HTTP $code"
done

# ---- 3. CI on main
STEP=ci
curl -s -m 30 "https://api.github.com/repos/Aditya-BN/ev2ahv/actions/runs?branch=main&per_page=1" > "$RUN/$STEP.log"
ci=$("$PY" -c "import json,sys; r=json.load(open(sys.argv[1]))['workflow_runs'][0]; print(r['head_sha'][:7], r['status'], r['conclusion'])" "$RUN/$STEP.log")
note "CI main: $ci"
case "$ci" in *" completed success") ;; *" completed "*) attention "CI on main: $ci";; *) note "  (CI still running)";; esac

# ---- 4. trial merge of every adit/* branch, in order, onto main (writes nothing to refs)
STEP=merge
branches=$(git -C "$REPO" for-each-ref --format='%(refname:short)' 'refs/remotes/origin/adit/*' \
  | while read -r b; do git -C "$REPO" merge-base --is-ancestor "$b" origin/main || echo "$b"; done)
note ""; note "## Unmerged branches: trial merge onto main, in order"
base=$(git -C "$REPO" rev-parse origin/main)
{
  for b in $branches; do
    if out=$(git -C "$REPO" merge-tree --write-tree --name-only --no-messages "$base" "$b" 2>&1); then
      # chain the next trial merge on top of this one: a dangling commit object, no ref is changed
      base=$(git -C "$REPO" commit-tree "$(echo "$out" | head -1)" -p "$base" -p "$b" -m "autopilot trial merge")
      echo "clean  $b"
    else
      echo "CONFLICT $b: $(echo "$out" | tail -n +2 | paste -sd' ')"
    fi
  done
} > "$RUN/$STEP.log" 2>&1
sed 's/^/- /' "$RUN/$STEP.log" >> "$REPORT"
grep -q '^CONFLICT' "$RUN/$STEP.log" && attention "merge conflicts (see the list)"

# ---- 5. unit tests per branch (throwaway worktree, removed afterwards)
STEP=tests
note ""; note "## Unit tests per branch"
: > "$RUN/$STEP.log"
for b in $branches; do
  wt=$(mktemp -d /tmp/ev2ahv-ap.XXXXXX)
  git -C "$REPO" worktree add -q --detach "$wt" "$b" >> "$RUN/$STEP.log" 2>&1
  if (cd "$wt" && timeout 900 "$PY" -m unittest discover -s tests) > "$RUN/tests-${b//\//_}.log" 2>&1; then r=PASS; else r=FAIL; fi
  summary=$(grep -E '^(Ran|OK|FAILED)' "$RUN/tests-${b//\//_}.log" | paste -sd' ')
  note "- $r $b: $summary"
  [ $r = PASS ] || attention "tests fail on $b"
  git -C "$REPO" worktree remove --force "$wt" >> "$RUN/$STEP.log" 2>&1
done
STEP="finished"
