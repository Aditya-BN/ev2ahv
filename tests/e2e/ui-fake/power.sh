#!/bin/bash
# Throwaway non-bootable "VM" for the C4.2 UI test: a 2 GiB raw file on the writer. While "running", a
# background loop rewrites a few random 4 MiB blocks every few seconds so resync has something to send.
# EV2AHV_UIFAKE_DIR may override the work dir (tests); its last component must start with ev2ahv-uifake.
set -u
D=${EV2AHV_UIFAKE_DIR:-/var/tmp/ev2ahv-uifake}
case "$D" in
  /*) ;;
  *) echo "refusing: work dir '$D' is not absolute" >&2; exit 2 ;;
esac
case "${D##*/}" in
  ev2ahv-uifake*) ;;
  *) echo "refusing: work dir '$D' must end in ev2ahv-uifake*" >&2; exit 2 ;;
esac
F=$D/disk.raw; RUN=$D/running; PIDF=$D/load.pid
ours() {   # $1 pid: only signal a process that is this script's load loop
  case "$1" in ''|*[!0-9]*) return 1 ;; esac
  tr '\0' ' ' < "/proc/$1/cmdline" 2>/dev/null | grep -q 'power.sh'
}
case "${1:-}" in
  setup)
    mkdir -p "$D"
    if [ ! -f "$F" ]; then
      truncate -s 2G "$F"
      for i in $(seq 0 7 300); do dd if=/dev/urandom of="$F" bs=4M count=1 seek="$i" conv=notrunc status=none; done
    fi
    "$0" start ;;
  status) [ -f "$RUN" ] && echo running || echo stopped ;;
  start)
    [ -f "$RUN" ] && exit 0
    touch "$RUN"
    nohup "$0" load >/dev/null 2>&1 & echo $! > "$PIDF" ;;
  load)
    while [ -f "$RUN" ]; do dd if=/dev/urandom of="$F" bs=4M count=1 seek=$((RANDOM % 500)) conv=notrunc status=none; sleep 5; done ;;
  stop)
    rm -f "$RUN"
    if [ -f "$PIDF" ]; then
      pid=$(cat "$PIDF" 2>/dev/null || true)
      if ours "$pid"; then kill "$pid" 2>/dev/null || true; fi
      rm -f "$PIDF"
    fi
    sync ;;
  teardown) "$0" stop; rm -rf -- "${D:?}" ;;
  dry-run) echo "would stop the load loop and remove $D"; ls -la "$D" 2>/dev/null || true ;;
  *) echo "usage: $0 setup|status|start|stop|teardown|dry-run"; exit 2 ;;
esac
