# shellcheck shell=bash
# Shared by every script under bin/. Sourced, never run.
#
# The layout (docs/architecture.md, "Spine"): the repository checkout is the
# root; `brain/` is OpenHuman's workspace (OPENHUMAN_WORKSPACE), `secrets/` and `state/` sit
# beside it and are gitignored. Every path can be moved with an environment
# variable, which is how the tests point the scripts at a scratch root.

TEENY_ROOT="${TEENY_ROOT:-$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)}"
TEENY_BRAIN="${OPENHUMAN_WORKSPACE:-$TEENY_ROOT/brain}"
TEENY_STORAGE="${TEENY_STORAGE:-$TEENY_BRAIN/storage.db}"
TEENY_SECRETS="${TEENY_SECRETS:-$TEENY_ROOT/secrets}"
TEENY_STATE="${TEENY_STATE:-$TEENY_ROOT/state}"
# Where the core answers. Cron shell jobs run with an allowlisted environment
# (no OPENHUMAN_* or TEENY_*), so bin/spine also leaves the URL in
# state/core.url for them; everything else a job needs it finds from the
# repository root it runs in.
if [ -n "${OPENHUMAN_CORE_URL:-}" ]; then
  OH_URL="$OPENHUMAN_CORE_URL"
elif [ -r "$TEENY_STATE/core.url" ]; then
  OH_URL="$(tr -d '[:space:]' <"$TEENY_STATE/core.url")"
else
  OH_URL="http://127.0.0.1:${OPENHUMAN_CORE_PORT:-7788}"
fi
export TEENY_ROOT TEENY_BRAIN TEENY_STORAGE TEENY_SECRETS TEENY_STATE OH_URL

log() { printf '[%s] %s\n' "${0##*/}" "$*" >&2; }
die() {
  log "$*"
  exit 1
}

need() {
  local tool
  for tool in "$@"; do
    command -v "$tool" >/dev/null 2>&1 || die "needs '$tool' on PATH"
  done
}

# The core's RPC bearer: the environment wins, then secrets/core.token. Never
# printed; a missing token is an error, not an unauthenticated call.
core_token() {
  if [ -n "${OPENHUMAN_CORE_TOKEN:-}" ]; then
    printf '%s' "$OPENHUMAN_CORE_TOKEN"
  elif [ -r "$TEENY_SECRETS/core.token" ]; then
    tr -d '[:space:]' <"$TEENY_SECRETS/core.token"
  else
    die "no core token: set OPENHUMAN_CORE_TOKEN or write $TEENY_SECRETS/core.token"
  fi
}

# `bin/oh` by default. The tests swap in a stand-in that keeps jobs in a file.
oh() { "${TEENY_OH:-$TEENY_ROOT/bin/oh}" "$@"; }

# How long one cron agent turn may run (OpenHuman `[agent] agent_timeout_secs`).
# The core bounds a whole scheduled agent turn by this one value, not just one
# tool call, and ignores an agent definition's own `timeout_secs` there
# (cron/scheduler/agent_run.rs). Its default of 120 s let the builder make six
# model calls and no test run (2026-11-12). 900 s winds down at 780 s, so a
# build started at ten past is over before build-ship collects at half past.
# It also caps any single tool call, which the turn deadline bounds anyway.
# bin/spine sets it at boot; `bin/doctor --live` checks it.
TEENY_AGENT_TURN_SECS=900
