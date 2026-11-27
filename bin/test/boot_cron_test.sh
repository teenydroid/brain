#!/usr/bin/env bash
# Tests for bin/lib/boot_cron.sh: the boot's wait for the Gateway's cron and its seeding stay inside one deadline,
# and each readiness probe is one bounded try.
#
#   bin/test/boot_cron_test.sh
# shellcheck disable=SC2016  # the single-quoted bodies are for the child shells to expand
set -euo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
scratch="$(mktemp -d "${TMPDIR:-/tmp}/teeny-boot-cron.XXXXXX")"
sleeper=""
trap 'rm -rf "$scratch"; [ -z "$sleeper" ] || kill "$sleeper" 2>/dev/null || true' EXIT

failures=0
pass() { printf 'ok   %s\n' "$1"; }
fail() { printf 'FAIL %s\n' "$1" >&2; failures=$((failures + 1)); }
check() { local name="$1"; shift; if "$@"; then pass "$name"; else fail "$name"; fi; }

# shellcheck source=bin/lib/teeny.sh
. "$here/../lib/teeny.sh"
# shellcheck source=bin/lib/boot_cron.sh
. "$here/../lib/boot_cron.sh"
log() { :; }

# A stand-in for bin/oh: ready once FAKE_READY_AFTER probes have been made; every call is recorded with its
# OH_TIMEOUT. FAKE_READY_AFTER=never keeps it down.
fake_oh="$scratch/oh"
cat >"$fake_oh" <<'FAKE'
#!/usr/bin/env bash
printf '%s timeout=%s %s\n' "$1" "${OH_TIMEOUT:-unset}" "${2:-}" >>"$FAKE_CALLS"
n="$(wc -l <"$FAKE_CALLS")"
[ "$FAKE_READY_AFTER" != never ] && [ "$n" -gt "$FAKE_READY_AFTER" ]
FAKE
chmod +x "$fake_oh"
export TEENY_OH="$fake_oh" FAKE_CALLS="$scratch/calls"

seed_ok="$scratch/seed-ok"
printf '#!/usr/bin/env bash\necho x >>"$SEED_LOG"\n' >"$seed_ok"
seed_flaky="$scratch/seed-flaky"
printf '#!/usr/bin/env bash\necho x >>"$SEED_LOG"\n[ "$(wc -l <"$SEED_LOG")" -ge 3 ]\n' >"$seed_flaky"
seed_hangs="$scratch/seed-hangs"
printf '#!/usr/bin/env bash\necho x >>"$SEED_LOG"\nsleep 60\n' >"$seed_hangs"
chmod +x "$seed_ok" "$seed_flaky" "$seed_hangs"
export SEED_LOG="$scratch/seeds"

reset() { : >"$FAKE_CALLS"; : >"$SEED_LOG"; }
sleep 300 &
sleeper=$!

run() { # run <ready-after> <core-pid> <seed>; prints the return code and the seconds taken
  reset
  local t0=$SECONDS rc=0
  FAKE_READY_AFTER="$1" boot_seed_cron "$2" "$3" || rc=$?
  printf '%s %s' "$rc" "$((SECONDS - t0))"
}

export SPINE_BOOT_SECS=2 SPINE_CRON_SECS=4

out="$(run never "$sleeper" "$seed_ok")"
check "a Gateway that never answers fails the boot in about SPINE_BOOT_SECS, not hours (got: $out)" \
  bash -c '[ "${1%% *}" = 3 ] && [ "${1##* }" -le 4 ]' _ "$out"
check "the probe asks for probe=true with a 10 s bound" grep -q 'cron_list timeout=10 {"probe":true}' "$FAKE_CALLS"
check "nothing is seeded into a Gateway that is down" test ! -s "$SEED_LOG"

out="$(run never 2147483 "$seed_ok")"
check "a core that exited stops the wait at once (got: $out)" bash -c '[ "${1%% *}" = 2 ]' _ "$out"

out="$(run 2 "$sleeper" "$seed_ok")"
check "a Gateway that comes up after a few probes is seeded (got: $out)" bash -c '[ "${1%% *}" = 0 ]' _ "$out"
check "...exactly once" bash -c '[ "$(wc -l <"$1")" = 1 ]' _ "$SEED_LOG"

out="$(run 0 "$sleeper" "$seed_flaky")"
check "a seed that fails twice is tried again and then succeeds (got: $out)" bash -c '[ "${1%% *}" = 0 ]' _ "$out"
check "...in three tries" bash -c '[ "$(wc -l <"$1")" = 3 ]' _ "$SEED_LOG"

out="$(run 0 "$sleeper" "$seed_hangs")"
check "a seed that hangs is cut off by the overall deadline (got: $out)" bash -c '[ "${1%% *}" = 3 ] && [ "${1##* }" -le 8 ]' _ "$out"

[ "$failures" = 0 ] || { printf '%s failed\n' "$failures" >&2; exit 1; }
printf 'all passed\n'
