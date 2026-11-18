#!/usr/bin/env bash
# Tests for bin/lib/model_windows.sh (register_windows, which bin/spine runs at
# boot): stand-ins for curl (the endpoint's /models listing) and the core's
# RPC, so no endpoint and no core.
#
#   bin/test/model_windows_test.sh
set -euo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
scratch="$(mktemp -d "${TMPDIR:-/tmp}/teeny-windows.XXXXXX")"
trap 'rm -rf "$scratch"' EXIT
failures=0
pass() { printf 'ok   %s\n' "$1"; }
fail() { printf 'FAIL %s\n' "$1" >&2; failures=$((failures + 1)); }
check() { local name="$1"; shift; if "$@"; then pass "$name"; else fail "$name"; fi; }

mkdir -p "$scratch/bin" "$scratch/secrets"
# curl: counts its calls and answers the listing.
cat >"$scratch/bin/curl" <<'FAKE'
#!/usr/bin/env bash
echo call >>"$CURL_LOG"
echo '{"data": [{"id": "glm-5.3-flash", "context_length": 131072}, {"id": "claude-fable-5.2", "context_length": 200000},
  {"id": "gemini-3.1-pro", "context_length": 1000000}]}'
FAKE
chmod +x "$scratch/bin/curl"
export PATH="$scratch/bin:$PATH" CURL_LOG="$scratch/curl.log" TEENY_SECRETS="$scratch/secrets"
# The core: every registry write it takes is logged; $REFUSE names a model whose entry
# makes the write fail.
log() { printf '%s\n' "$*" >>"$scratch/log"; }
die() { printf 'DIE %s\n' "$*" >>"$scratch/log"; return 1; }
oh() {
  [ "$1" = inference_update_model_settings ] || return 1
  if [ -n "${REFUSE:-}" ] && jq -e --arg m "$REFUSE" '.model_registry | any(.id == $m)' <<<"$2" >/dev/null; then
    return 1
  fi
  printf '%s\n' "$2" >>"$scratch/writes"
}
# shellcheck source=bin/lib/model_windows.sh
. "$here/../lib/model_windows.sh"

reset() { : >"$scratch/log"; : >"$scratch/writes"; : >"$CURL_LOG"; rm -f "$TEENY_SECRETS/teeny.env"; }
ids() { jq -rs 'last | .model_registry | map(.id) | join(",")' "$scratch/writes"; }
SPINE_MODEL=glm-5.3-flash
LADDER_URL=https://ladder.example/v1
LADDER_API_KEY=k
unset SPINE_CONTEXT_WINDOW STUDIO_WRITER_MODEL STUDIO_EDITOR_MODEL REFUSE

reset
register_windows
check "with no studio model only SPINE_MODEL gets a window" test "$(ids)" = glm-5.3-flash

reset
printf 'STUDIO_WRITER_MODEL=claude-fable-5.2\nSTUDIO_EDITOR_MODEL="gemini-3.1-pro"\n' >"$TEENY_SECRETS/teeny.env"
register_windows
check "the studio's models from secrets/teeny.env get their windows in the same write" \
  test "$(ids)" = glm-5.3-flash,claude-fable-5.2,gemini-3.1-pro
check "with the endpoint's numbers" test "$(jq -s 'last | .model_registry[1].context_window' "$scratch/writes")" = 200000
check "in one write" test "$(wc -l <"$scratch/writes" | tr -d ' ')" = 1

reset
printf 'STUDIO_WRITER_MODEL=claude-fable-5.2\n' >"$TEENY_SECRETS/teeny.env"
STUDIO_WRITER_MODEL=gemini-3.1-pro register_windows
check "the process environment wins over the file" test "$(ids)" = glm-5.3-flash,gemini-3.1-pro

reset
SPINE_CONTEXT_WINDOW=64000 register_windows
check "nothing to look up: the listing is not fetched" test ! -s "$CURL_LOG"
check "and SPINE_CONTEXT_WINDOW is written" test "$(jq -s 'last | .model_registry[0].context_window' "$scratch/writes")" = 64000

reset
SPINE_CONTEXT_WINDOW=64000 STUDIO_EDITOR_MODEL=claude-fable-5.2 register_windows
check "a studio model to look up fetches it" test "$(wc -l <"$CURL_LOG" | tr -d ' ')" = 1

reset
printf 'STUDIO_WRITER_MODEL=claude-fable-5.2\nSTUDIO_EDITOR_MODEL=gemini-3.1-pro\n' >"$TEENY_SECRETS/teeny.env"
REFUSE=gemini-3.1-pro register_windows
check "one entry the core refuses does not cost the others" test "$(ids)" = glm-5.3-flash,claude-fable-5.2
check "and is logged" grep -q "refused a window for the studio's gemini-3.1-pro" "$scratch/log"
check "and the boot goes on" bash -c "! grep -q '^DIE' '$scratch/log'"

reset
printf 'STUDIO_WRITER_MODEL=not-listed\n' >"$TEENY_SECRETS/teeny.env"
register_windows
check "a model the endpoint does not list is logged, not written" \
  bash -c "[ '$(ids)' = glm-5.3-flash ] && grep -q 'no window for the studio.s not-listed' '$scratch/log'"

if [ "$failures" -gt 0 ]; then
  printf '%d failure(s)\n' "$failures" >&2
  exit 1
fi
echo "all passed"
