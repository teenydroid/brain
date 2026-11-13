#!/usr/bin/env bash
# Diagnostic only: log each Chrome launch, its stderr with timestamps, and
# what the machine is doing while it starts.
log="${CHROME_DIAG_LOG:-/tmp/chrome-diag.log}"
echo "$(date +%T.%N) START pid=$$ HOME=$HOME DBUS=${DBUS_SESSION_BUS_ADDRESS:-unset} DISPLAY=${DISPLAY:-unset} real=$REAL_CHROME args: $*" >>"$log"
(
  for _ in $(seq 1 20); do
    sleep 2
    {
      echo "$(date +%T.%N) PS for launch $$"
      ps -eo pid,ppid,stat,etimes,pcpu,wchan:24,args --sort=-pcpu | head -15 | cut -c1-220
      uptime
    } >>"$log"
  done
) &
exec "$REAL_CHROME" "$@" 2> >(while IFS= read -r l; do echo "$(date +%T.%N) [$$] $l" >>"$log"; echo "$l" >&2; done)
