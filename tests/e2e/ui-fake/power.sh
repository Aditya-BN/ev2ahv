#!/bin/bash
# Throwaway non-bootable "VM" for the C4.2 UI test: a 2 GiB raw file on the writer. While "running", a
# background loop rewrites a few random 4 MiB blocks every few seconds so resync has something to send.
set -u
D=/var/tmp/ev2ahv-uifake; F=$D/disk.raw; RUN=$D/running; PIDF=$D/load.pid
case "${1:-}" in
  setup)
    mkdir -p $D && [ -f $F ] || { truncate -s 2G $F; for i in $(seq 0 7 300); do dd if=/dev/urandom of=$F bs=4M count=1 seek=$i conv=notrunc status=none; done; }
    "$0" start ;;
  status) [ -f $RUN ] && echo running || echo stopped ;;
  start)
    [ -f $RUN ] && exit 0
    touch $RUN
    nohup "$0" load >/dev/null 2>&1 & echo $! > $PIDF ;;
  load)
    while [ -f $RUN ]; do dd if=/dev/urandom of=$F bs=4M count=1 seek=$((RANDOM % 500)) conv=notrunc status=none; sleep 5; done ;;
  stop)
    rm -f $RUN; [ -f $PIDF ] && { kill "$(cat $PIDF)" 2>/dev/null; rm -f $PIDF; }; sync ;;
  teardown) "$0" stop; rm -rf $D ;;
  *) echo "usage: $0 setup|status|start|stop|teardown"; exit 2 ;;
esac
