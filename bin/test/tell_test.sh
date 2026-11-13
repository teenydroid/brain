#!/usr/bin/env bash
# Tests for deploy/tell.sh against a stand-in docker: `exec ... hands/stimuli
# add` keeps the body it was given, and `exec ... bin/tick --detach` writes the
# beat record $FAKE_BEAT asks for into state/tick/runs.jsonl. No compose, no
# core.
#
#   bin/test/tell_test.sh
set -euo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
scratch="$(mktemp -d "${TMPDIR:-/tmp}/teeny-tell.XXXXXX")"
trap 'rm -rf "$scratch"' EXIT

failures=0
pass() { printf 'ok   %s\n' "$1"; }
fail() { printf 'FAIL %s\n' "$1" >&2; failures=$((failures + 1)); }
check() { local name="$1"; shift; if "$@"; then pass "$name"; else fail "$name"; fi; }

mkdir -p "$scratch/bin"
cat >"$scratch/bin/docker" <<'FAKE'
#!/usr/bin/env bash
set -euo pipefail
printf '%s\n' "$*" >>"$FAKE_CALLS"
case "$*" in
  *"hands/stimuli add") cat >"$FAKE_ROOT/stimulus.json"; echo '{"added": true}' ;;
  *"bin/tick --detach")
    mkdir -p "$FAKE_ROOT/state/tick"
    ref="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["ref"])' "$FAKE_ROOT/stimulus.json")"
    case "${FAKE_BEAT:-answer}" in
      answer) printf '{"ok": true, "kind": "beat", "tick": "t1", "host": ["%s"], "thought": "posting it", "results": [{"do": "post_x", "status": "done", "result": {"id": "42"}}]}\n' "$ref" ;;
      failed) printf '{"ok": false, "kind": "failed", "tick": "t1", "host": ["%s"], "error": "the turn failed: boom"}\n' "$ref" ;;
      none) : ;;
    esac >>"$FAKE_ROOT/state/tick/runs.jsonl"
    ;;
  *) echo "unexpected docker call: $*" >&2; exit 1 ;;
esac
FAKE
chmod +x "$scratch/bin/docker"

# Each case gets a checkout of its own: tell.sh finds state/ beside deploy/.
fresh() {
  root="$scratch/$1"
  mkdir -p "$root/deploy" "$root/state/tick"
  cp "$here/../../deploy/tell.sh" "$root/deploy/tell.sh"
  echo '{"ok": true, "kind": "idle"}' >"$root/state/tick/runs.jsonl"  # an older beat: never ours
  export FAKE_ROOT="$root" FAKE_CALLS="$root/calls.log"
}
tell() { PATH="$scratch/bin:$PATH" TELL_POLL_SECS=0 "$root/deploy/tell.sh" "$@"; }

# --- the message, the kick, the answer ---------------------------------------
fresh answer
set +e; out="$(tell "tweet that the paywall is live" 2>&1)"; code=$?; set -e
check "an answered beat exits 0" [ "$code" = 0 ]
check "it prints the thought" grep -q "thought: posting it" <<<"$out"
check "it prints what each intent did" grep -q -- '- post_x: done' <<<"$out"
check "the stimulus is a host instruction with the text" python3 -c '
import json, sys
s = json.load(open(sys.argv[1]))
assert (s["surface"], s["kind"], s["who"], s["text"]) == ("host", "instruction", "operator", "tweet that the paywall is live"), s
assert s["ref"].startswith("host-"), s' "$root/stimulus.json"
check "it adds then starts a beat in the spine" diff <(sed 's/^.*exec -T spine //' "$root/calls.log") <(printf 'hands/stimuli add\nbin/tick --detach\n')
check "the ledger counts it without the text" python3 -c '
import json, sys
lines = open(sys.argv[1]).read().splitlines()
assert len(lines) == 1, lines
said = json.loads(lines[0])
assert said["chars"] == len("tweet that the paywall is live") and len(said["sha256"]) == 64, said
assert "paywall" not in lines[0], said' "$root/state/host/said.jsonl"

# --- stdin -------------------------------------------------------------------
fresh stdin
echo "set up a browser task" | tell --no-wait >/dev/null
check "the message can come on stdin" grep -q '"text": "set up a browser task' "$root/stimulus.json"
check "--no-wait still starts a beat" grep -q "bin/tick --detach" "$root/calls.log"

# --- failure and timeout -----------------------------------------------------
fresh failed
set +e; out="$(FAKE_BEAT=failed tell "post it" 2>&1)"; code=$?; set -e
check "a failed beat exits 1" [ "$code" = 1 ]
check "and says why" grep -q "the turn failed: boom" <<<"$out"

fresh timeout
set +e; out="$(FAKE_BEAT=none tell --timeout 0 "post it" 2>&1)"; code=$?; set -e
check "no beat in time exits 2" [ "$code" = 2 ]
check "and says it is still in the inbox" grep -q "still in the inbox" <<<"$out"

fresh empty
set +e; tell "   " >/dev/null 2>&1; code=$?; set -e
check "an empty message is refused" [ "$code" = 1 ]
check "and reaches nothing" [ ! -e "$root/calls.log" ]

if [ "$failures" -gt 0 ]; then
  printf '%d failure(s)\n' "$failures" >&2
  exit 1
fi
echo "all passed"
