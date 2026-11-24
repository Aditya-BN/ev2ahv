#!/bin/bash
# Stage B write load inside a Linux source guest. Writes files to the data disk and the root LV.
# stop ends the load, writes checksum manifests, syncs, and prints them for the bastion to keep.
set -u
DIRS="/data/ev2ahv /var/ev2ahv-root"; PIDF=/run/ev2ahv-load.pid
load() {
  n=0
  while :; do
    n=$((n+1))
    for d in $DIRS; do
      dd if=/dev/urandom of="$d/f-$n" bs=1M count=$(( (RANDOM % 4) + 1 )) conv=fsync status=none
      if [ $((n % 4)) -eq 0 ]; then t=$(( (RANDOM % n) + 1 )); [ -f "$d/f-$t" ] && dd if=/dev/urandom of="$d/f-$t" bs=4k count=32 conv=notrunc,fsync status=none; fi
    done
    [ "$n" -ge "${MAX_FILES:-400}" ] && break
    sleep 1
  done
}
case "${1:-}" in
  start) mkdir -p $DIRS; [ -f $PIDF ] && kill -0 "$(cat $PIDF)" 2>/dev/null && exit 0
         nohup "$0" load >/dev/null 2>&1 & echo $! > $PIDF ;;
  load) load ;;
  stop) [ -f $PIDF ] && { kill "$(cat $PIDF)" 2>/dev/null; sleep 2; rm -f $PIDF; }
        for d in $DIRS; do ( cd "$d" && rm -f MANIFEST.sha256 && sha256sum f-* > /run/m.$$ && mv /run/m.$$ MANIFEST.sha256 && sync MANIFEST.sha256 ); done
        sync; for d in $DIRS; do echo "$d $(sha256sum < "$d/MANIFEST.sha256" | cut -d' ' -f1) $(wc -l < "$d/MANIFEST.sha256")"; done ;;
  check) for d in $DIRS; do ( cd "$d" && sha256sum -c --quiet MANIFEST.sha256 && echo "$d OK $(wc -l < MANIFEST.sha256) files $(sha256sum < MANIFEST.sha256 | cut -d' ' -f1)" ) || echo "$d FAILED"; done ;;
  *) echo "usage: $0 start|stop|check"; exit 2 ;;
esac
