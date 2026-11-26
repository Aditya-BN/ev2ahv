#!/bin/bash
# Stage B write load inside a Linux source guest. Writes files to the data disk and the root LV.
# stop ends the load, writes checksum manifests, syncs, and prints them for the bastion to keep.
# EV2AHV_LOAD_DIRS (colon separated, so paths may contain spaces) and EV2AHV_LOAD_PIDF override the defaults (tests).
set -u
IFS=: read -r -a DIRS <<< "${EV2AHV_LOAD_DIRS:-/data/ev2ahv:/var/ev2ahv-root}"
PIDF=${EV2AHV_LOAD_PIDF:-/run/ev2ahv-load.pid}
for d in "${DIRS[@]}"; do
  case "$d" in /|"") echo "refusing: load dir '$d'" >&2; exit 2 ;; /*) ;; *) echo "refusing: load dir '$d' is not absolute" >&2; exit 2 ;; esac
done
ours() {   # only signal our own load loop; a stale pidfile may name a recycled pid
  case "$1" in ''|*[!0-9]*) return 1 ;; esac
  tr '\0' ' ' < "/proc/$1/cmdline" 2>/dev/null | grep -q 'guest-load.sh'
}
load() {
  n=0
  while :; do
    n=$((n+1))
    for d in "${DIRS[@]}"; do
      dd if=/dev/urandom of="$d/f-$n" bs=1M count=$(( (RANDOM % 4) + 1 )) conv=fsync status=none
      if [ $((n % 4)) -eq 0 ]; then t=$(( (RANDOM % n) + 1 )); [ -f "$d/f-$t" ] && dd if=/dev/urandom of="$d/f-$t" bs=4k count=32 conv=notrunc,fsync status=none; fi
    done
    [ "$n" -ge "${MAX_FILES:-400}" ] && break
    sleep 1
  done
}
case "${1:-}" in
  start) mkdir -p "${DIRS[@]}"
         if [ -f "$PIDF" ] && kill -0 "$(cat "$PIDF")" 2>/dev/null; then exit 0; fi
         nohup "$0" load >/dev/null 2>&1 & echo $! > "$PIDF" ;;
  load) load ;;
  stop) if [ -f "$PIDF" ]; then
          pid=$(cat "$PIDF" 2>/dev/null || true)
          if ours "$pid"; then kill "$pid" 2>/dev/null || true; sleep 2; fi
          rm -f "$PIDF"
        fi
        for d in "${DIRS[@]}"; do ( cd "$d" && rm -f MANIFEST.sha256 && sha256sum f-* > "$PIDF.manifest" && mv "$PIDF.manifest" MANIFEST.sha256 && sync MANIFEST.sha256 ); done
        sync; for d in "${DIRS[@]}"; do echo "$d $(sha256sum < "$d/MANIFEST.sha256" | cut -d' ' -f1) $(wc -l < "$d/MANIFEST.sha256")"; done ;;
  check) for d in "${DIRS[@]}"; do ( cd "$d" && sha256sum -c --quiet MANIFEST.sha256 && echo "$d OK $(wc -l < MANIFEST.sha256) files $(sha256sum < MANIFEST.sha256 | cut -d' ' -f1)" ) || echo "$d FAILED"; done ;;
  *) echo "usage: $0 start|stop|check"; exit 2 ;;
esac
