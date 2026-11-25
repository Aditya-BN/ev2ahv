#!/bin/bash
# Dev helper: (re)start the mock server on 8443 and/or the live server on 8444. Logs in /tmp.
cd "$(dirname "$0")"
for mode in "$@"; do
  port=$([ "$mode" = mock ] && echo 8443 || echo 8444)
  pidf=/tmp/ev2ahv-$mode.pid
  [ -f $pidf ] && kill "$(cat $pidf)" 2>/dev/null && sleep 1
  nohup ../.venv/bin/python -m ev2ahv_server --$mode --port $port > /tmp/ev2ahv-$mode.log 2>&1 &
  echo $! > $pidf
done
