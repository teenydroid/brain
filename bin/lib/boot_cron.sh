# shellcheck shell=bash
# The boot's wait for the Gateway's cron and the seeding that follows it, under one wall-clock deadline.
# Sourced by bin/spine (after lib/teeny.sh, which gives `oh` and `log`).
#
# Each probe is one try, cut off after SPINE_PROBE_SECS (the core's `cron_list` with probe=true does not wait
# out a Gateway that is down, and OH_TIMEOUT bounds the call itself), so a loop of them takes about as long as
# it counts. The whole of it, the waits and every seeding attempt, ends at SPINE_CRON_SECS (default 600):
# a boot that cannot seed fails and falls back instead of hanging for hours.

# Does the Gateway take a cron_list right now? One bounded try.
boot_gateway_ready() {
  OH_TIMEOUT="${SPINE_PROBE_SECS:-10}" oh cron_list '{"probe":true}' >/dev/null 2>&1
}

# boot_seed_cron CORE_PID SEED_COMMAND...
# Returns 0 when seeded; 2 when the core exited; 3 when the deadline passed (the log says where).
boot_seed_cron() {
  local core_pid="$1"
  shift
  local boot_secs="${SPINE_BOOT_SECS:-120}" total="${SPINE_CRON_SECS:-600}"
  local end=$((SECONDS + total)) attempt wait_end remaining
  for attempt in 1 2 3; do
    wait_end=$((SECONDS + boot_secs))
    [ "$wait_end" -le "$end" ] || wait_end="$end"
    until boot_gateway_ready; do
      kill -0 "$core_pid" 2>/dev/null || { log "the core exited while the Gateway was starting"; return 2; }
      if [ "$SECONDS" -ge "$wait_end" ]; then
        log "the OpenClaw Gateway did not take a cron_list in time (try $attempt of 3)"
        return 3
      fi
      sleep 1
    done
    [ "$attempt" = 1 ] && log "gateway: up; cron answers"
    remaining=$((end - SECONDS))
    [ "$remaining" -ge 1 ] || { log "no time left to seed cron (${total}s)"; return 3; }
    # A seed that lands in a Gateway restart fails on a connection the core already retries for a while; seeding is a
    # reconciliation (a second run only finishes what the first did), so try again, inside the same deadline.
    if timeout "$remaining" "$@"; then return 0; fi
    log "seeding cron failed (try $attempt of 3); waiting for the Gateway"
  done
  return 3
}
