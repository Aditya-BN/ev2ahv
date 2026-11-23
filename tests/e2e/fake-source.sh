#!/bin/bash
# Fake "running VM" for the A4 end-to-end test: a raw image on a loop device whose root
# filesystem is mounted and written to. stop = clean shutdown, start = boot it again.
set -u
W=/var/tmp/ev2ahv-a4; M=/mnt/ev2ahv-src; D=$M/var/ev2ahv-data; RUN=/run/ev2ahv-src-running; PIDF=/run/ev2ahv-src-load.pid
loopdev() { cat $W/loop; }
load() {
  n=$(ls "$D" 2>/dev/null | grep -c '^f-' || true)
  while [ -f $RUN ]; do
    n=$((n+1)); mb=$(( (RANDOM % 8) + 1 ))
    dd if=/dev/urandom of="$D/f-$n" bs=1M count=$mb conv=fsync status=none || break
    [ $((n % 5)) -eq 0 ] && f=$(( (RANDOM % n) + 1 )) && [ -f "$D/f-$f" ] && dd if=/dev/urandom of="$D/f-$f" bs=4k count=64 conv=notrunc,fsync status=none
    [ "$n" -ge "${MAX_FILES:-120}" ] && break
    sleep 0.5
  done
}
case "${1:-}" in
  status) [ -f $RUN ] && echo running || echo stopped ;;
  start)
    [ -f $RUN ] && exit 0
    mkdir -p $M; mount -o nouuid "$(loopdev)p4" $M && mkdir -p "$D" && touch $RUN
    nohup "$0" load >/dev/null 2>&1 & echo $! > $PIDF ;;
  load) load ;;
  stop)
    [ -f $RUN ] || exit 0
    rm -f $RUN; [ -f $PIDF ] && { kill "$(cat $PIDF)" 2>/dev/null; sleep 1; rm -f $PIDF; }
    ( cd "$D" && rm -f MANIFEST.sha256 && sha256sum f-* > $W/MANIFEST.expected && cp $W/MANIFEST.expected MANIFEST.sha256 )
    sync; umount $M ;;
  *) echo "usage: $0 status|start|stop"; exit 2 ;;
esac
