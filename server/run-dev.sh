#!/bin/bash
# Dev helper: (re)start the mock server on 8443 and/or the live server on 8444. Logs in /tmp.
# Usage: run-dev.sh mock|live [mock|live]
# Safety: the pidfile lives in world-writable /tmp, so a PID is only signalled when /proc says it is our server.
set -u
cd "$(dirname "$0")" || exit 1
PY=${EV2AHV_PY:-../.venv/bin/python}
RUN=${EV2AHV_RUN_DIR:-/tmp}   # pidfiles and logs
is_ours() {   # $1 pid: true only for a process whose command line is the ev2ahv_server module
  case "$1" in ''|*[!0-9]*) return 1 ;; esac
  [ "$1" -gt 1 ] && tr '\0' ' ' < "/proc/$1/cmdline" 2>/dev/null | grep -q 'ev2ahv_server'
}
[ $# -gt 0 ] || { echo "usage: $0 mock|live ..." >&2; exit 2; }
for mode in "$@"; do
  case "$mode" in mock|live) ;; *) echo "unknown mode '$mode' (use mock or live)" >&2; exit 2 ;; esac
done
for mode in "$@"; do
  port=$([ "$mode" = mock ] && echo 8443 || echo 8444)
  pidf=$RUN/ev2ahv-$mode.pid
  if [ -f "$pidf" ]; then
    old=$(cat "$pidf" 2>/dev/null || true)
    if is_ours "$old"; then kill "$old" 2>/dev/null && sleep 1; else echo "$pidf is stale or not ours; not signalling '$old'" >&2; fi
  fi
  # the dev server is the lab: keep the ev2ahv- name guard unless the caller sets EV2AHV_DEFAULT_NAME_PREFIX (D30a)
  EV2AHV_DEFAULT_NAME_PREFIX=${EV2AHV_DEFAULT_NAME_PREFIX-ev2ahv-} nohup "$PY" -m ev2ahv_server "--$mode" --port "$port" > "$RUN/ev2ahv-$mode.log" 2>&1 &
  echo $! > "$pidf"
done
