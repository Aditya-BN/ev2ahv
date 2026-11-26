#!/bin/bash
# Fake "running VM" for the A4 end-to-end test: a raw image on a loop device whose root
# filesystem is mounted and written to. stop = clean shutdown, start = boot it again.
set -u
W=${EV2AHV_A4_DIR:-/var/tmp/ev2ahv-a4}; M=${EV2AHV_A4_MNT:-/mnt/ev2ahv-src}; D=$M/var/ev2ahv-data
RUN=${EV2AHV_A4_RUN:-/run/ev2ahv-src-running}; PIDF=${EV2AHV_A4_PIDF:-/run/ev2ahv-src-load.pid}
# The mount target must be our own mount point, never "/" or a system directory.
case "$M" in /mnt/ev2ahv-*|/tmp/*/ev2ahv-*) ;; *) echo "refusing: mount point '$M' is not /mnt/ev2ahv-*" >&2; exit 2 ;; esac
loopdev() {   # the loop device recorded by the setup; refuse anything that is not a loop block device
  local d; d=$(cat "$W/loop" 2>/dev/null) || return 1
  case "$d" in /dev/loop[0-9]*) [ -b "$d" ] || [ -n "${EV2AHV_A4_ALLOW_FILE:-}" ] || return 1 ;; *) return 1 ;; esac
  echo "$d"
}
ours() {   # only signal our own load loop (a stale pidfile may name a recycled pid)
  case "$1" in ''|*[!0-9]*) return 1 ;; esac
  tr '\0' ' ' < "/proc/$1/cmdline" 2>/dev/null | grep -q 'fake-source.sh'
}
load() {
  n=$(find "$D" -maxdepth 1 -name 'f-*' 2>/dev/null | wc -l)
  while [ -f "$RUN" ]; do
    n=$((n+1)); mb=$(( (RANDOM % 8) + 1 ))
    dd if=/dev/urandom of="$D/f-$n" bs=1M count=$mb conv=fsync status=none || break
    [ $((n % 5)) -eq 0 ] && f=$(( (RANDOM % n) + 1 )) && [ -f "$D/f-$f" ] && dd if=/dev/urandom of="$D/f-$f" bs=4k count=64 conv=notrunc,fsync status=none
    [ "$n" -ge "${MAX_FILES:-120}" ] && break
    sleep 0.5
  done
}
case "${1:-}" in
  status) [ -f "$RUN" ] && echo running || echo stopped ;;
  start)
    [ -f "$RUN" ] && exit 0
    dev=$(loopdev) || { echo "refusing: $W/loop does not name a loop device" >&2; exit 2; }
    mkdir -p "$M"; mount -o nouuid "${dev}p4" "$M" && mkdir -p "$D" && touch "$RUN"
    nohup "$0" load >/dev/null 2>&1 & echo $! > "$PIDF" ;;
  load) load ;;
  stop)
    [ -f "$RUN" ] || exit 0
    rm -f "$RUN"
    if [ -f "$PIDF" ]; then
      pid=$(cat "$PIDF" 2>/dev/null || true)
      if ours "$pid"; then kill "$pid" 2>/dev/null || true; sleep 1; fi
      rm -f "$PIDF"
    fi
    ( cd "$D" && rm -f MANIFEST.sha256 && sha256sum f-* > "$W/MANIFEST.expected" && cp "$W/MANIFEST.expected" MANIFEST.sha256 )
    sync; umount "$M" ;;
  *) echo "usage: $0 status|start|stop"; exit 2 ;;
esac
