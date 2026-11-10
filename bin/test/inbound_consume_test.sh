#!/usr/bin/env bash
# Tests for bin/inbound-consume against stand-ins: a core whose
# inbound_results answers what the test wrote in $FAKE_RESULTS (and logs every
# ack), and a hands/x that logs how it was called. No OpenHuman and no X;
# bin/test/spine_live_test.sh offers mentions to a real core.
#
#   bin/test/inbound_consume_test.sh
set -euo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
consume="$here/../inbound-consume"
scratch="$(mktemp -d "${TMPDIR:-/tmp}/teeny-inbound.XXXXXX")"
trap 'rm -rf "$scratch"' EXIT

failures=0
pass() { printf 'ok   %s\n' "$1"; }
fail() { printf 'FAIL %s\n' "$1" >&2; failures=$((failures + 1)); }
check() { local name="$1"; shift; if "$@"; then pass "$name"; else fail "$name"; fi; }

cat >"$scratch/fake-oh" <<'FAKE'
#!/usr/bin/env bash
set -euo pipefail
printf '%s %s\n' "$1" "${2:-}" >>"$FAKE_CALLS"
case "$1" in
  inbound_results) cat "$FAKE_RESULTS" ;;
  inbound_result_ack) echo '{"acked": true}' ;;
  *) echo "unexpected method $1" >&2; exit 1 ;;
esac
FAKE
# hands/x: `reply` answers per $FAKE_REPLY_<id> (exit code and JSON), else a
# dry run; `mark` is logged.
mkdir -p "$scratch/hands"
cat >"$scratch/hands/x" <<'FAKE'
#!/usr/bin/env bash
in="$(cat)"
printf 'x %s stdin=%s\n' "$*" "$in" >>"$FAKE_HANDS"
case "$1" in
  reply)
    var="FAKE_REPLY_$3"
    if [ -n "${!var:-}" ]; then
      echo "${!var#*:}"
      exit "${!var%%:*}"
    fi
    echo '{"ok": true, "dry_run": true, "would_post": {"kind": "reply"}}'
    ;;
  mark) echo '{"ok": true}' ;;
  ingest) echo '{"ok": true, "read": 0}'; exit "${FAKE_INGEST_EXIT:-0}" ;;
  requests) echo '{"ok": true, "noted": 0}' ;;
  *) echo '{"ok": false}'; exit 1 ;;
esac
FAKE
chmod +x "$scratch/fake-oh" "$scratch/hands/x"

export TEENY_OH="$scratch/fake-oh" TEENY_HANDS="$scratch/hands" TEENY_STATE="$scratch/state" \
  FAKE_CALLS="$scratch/calls.log" FAKE_HANDS="$scratch/hands.log" FAKE_RESULTS="$scratch/results.json"

results() { # TEXT EVENT_IDS_JSON
  jq -cn --arg t "$1" --argjson e "$2" \
    '{results: [{turn_id: "t1", source: "x:mentions", key: "mentions", status: "completed", text: $t, event_ids: $e}]}' \
    >"$FAKE_RESULTS"
}
reset() { rm -rf "$scratch/state"; : >"$FAKE_CALLS"; : >"$FAKE_HANDS"; }
run() { "$consume" 2>"$scratch/stderr"; }
marked() { grep -q "^x mark $1 --status $2 " "$FAKE_HANDS"; }

# --- a batch: a reply, a skip, one left out -------------------------------------------
reset
results '[{"action": "reply", "to": "11", "text": "gm fren"}, {"action": "skip", "to": "12", "reason": "spam"}]' '["11", "12", "13"]'
out="$(run)"
check "asks for completed x:mentions results" grep -q '^inbound_results .*"source": "x:mentions".*"status": "completed"' "$FAKE_CALLS"
check "replies through hands/x reply to the mention" grep -q '^x reply --to 11 stdin={"text":"gm fren"}' "$FAKE_HANDS"
check "a dry-run reply is marked drafted" marked 11 drafted
check "a skip is marked skipped with its reason" grep -q '^x mark 12 --status skipped --note spam ' "$FAKE_HANDS"
check "a mention the answer left out is skipped" grep -q '^x mark 13 --status skipped --note no answer in the draft ' "$FAKE_HANDS"
check "acks the answer" grep -q '^inbound_result_ack {"turn_id":"t1"}' "$FAKE_CALLS"
check "records the run" test "$(wc -l <"$scratch/state/inbound/runs.jsonl" | tr -d ' ')" = 1
check "prints what it did" test "$(jq -r '.actions | map(.action) | join(",")' <<<"$out")" = "reply,skip"

# --- a draft cannot pick whom it answers ----------------------------------------------
reset
results '[{"action": "reply", "to": "999", "text": "hi"}, {"action": "post", "to": "11", "text": "x"}]' '["11"]'
out="$(run)"
check "never replies outside the batch" bash -c "! grep -q '^x reply' '$FAKE_HANDS'"
check "says why" test "$(jq -r '.actions[0].refused' <<<"$out")" = "not an event in this batch"
check "only replies or skips" test "$(jq -r '.actions[1].refused' <<<"$out")" = "an inbound draft only replies or skips"

# --- outcomes from hands/x ------------------------------------------------------------
reset
results '[{"action": "reply", "to": "21", "text": "a"}, {"action": "reply", "to": "22", "text": "b"}, {"action": "reply", "to": "23", "text": "c"}]' '["21", "22", "23"]'
export FAKE_REPLY_21='0:{"ok": true, "id": "900", "kind": "reply"}'
export FAKE_REPLY_22='2:{"ok": false, "refused": "held", "category": "politics"}'
export FAKE_REPLY_23='2:{"ok": false, "refused": "already replied", "rule": "replied", "reply_id": "901"}'
run >/dev/null
check "a live reply is marked replied with its id" grep -q '^x mark 21 --status replied --note 900 ' "$FAKE_HANDS"
check "a refusal is marked refused with the reason" grep -q '^x mark 22 --status refused --note held ' "$FAKE_HANDS"
check "an already-answered mention counts as replied" grep -q '^x mark 23 --status replied --note 901 ' "$FAKE_HANDS"
unset FAKE_REPLY_21 FAKE_REPLY_22 FAKE_REPLY_23

# --- pacing: a wait past the budget leaves the answer for the next run ---------------
reset
results '[{"action": "reply", "to": "31", "text": "a"}]' '["31", "32"]'
export FAKE_REPLY_31='2:{"ok": false, "refused": "pacing", "rule": "pacing", "wait_seconds": 45.2}'
out="$(INBOUND_BUDGET_SECONDS=10 run)"
check "a pacing wait over the budget pauses" test "$(jq -r '.paused' <<<"$out")" = true
check "a paused answer is not acked" bash -c "! grep -q '^inbound_result_ack' '$FAKE_CALLS'"
check "nothing is marked when paused" bash -c "! grep -q '^x mark' '$FAKE_HANDS'"
unset FAKE_REPLY_31

# --- mentions the core's injection guard kept out of the prompt -----------------------
reset
jq -cn '{results: [{turn_id: "t2", status: "completed", text: "[{\"action\": \"reply\", \"to\": \"51\", \"text\": \"hi\"}]",
  event_ids: ["51", "52"], blocked: [{event_id: "52", reason: "blocked: override.ignore_previous"}]}]}' >"$FAKE_RESULTS"
out="$(run)"
check "a blocked mention is marked held with the guard's reason" grep -q '^x mark 52 --status held --note blocked: override.ignore_previous ' "$FAKE_HANDS"
check "and is not also skipped" bash -c "! grep -q '^x mark 52 --status skipped' '$FAKE_HANDS'"
check "the rest of the batch is answered" grep -q '^x reply --to 51 ' "$FAKE_HANDS"
check "the blocked ones are in the record" test "$(jq -r '.blocked[0].event_id' <<<"$out")" = 52

# --- an answer that is not JSON --------------------------------------------------------
reset
results 'sorry, I cannot help with that' '["41"]'
out="$(run)"
check "an unusable draft is reported" grep -q 'not usable JSON' <<<"$out"
check "its mentions are skipped" marked 41 skipped
check "and it is acked, so it is not retried forever" grep -q '^inbound_result_ack' "$FAKE_CALLS"

# --- nothing to do ---------------------------------------------------------------------
reset
echo '{"results": []}' >"$FAKE_RESULTS"
check "no results is a quiet success" run
check "and touches no hand" test ! -s "$FAKE_HANDS"

# --- bin/mentions: ingest, then the build-request scan, then the answers ---------------------
mentions_bin="$here/../mentions"
reset
jq -cn '{results: []}' >"$FAKE_RESULTS"
"$mentions_bin" >/dev/null 2>"$scratch/stderr"
check "mentions: ingests first" test "$(sed -n 1p "$FAKE_HANDS" | cut -d' ' -f1-2)" = "x ingest"
check "mentions: then scans the stored mentions for build requests" test "$(sed -n 2p "$FAKE_HANDS" | cut -d' ' -f1-2)" = "x requests"
check "mentions: then asks the core for its drafts" grep -q '^inbound_results ' "$FAKE_CALLS"
reset
jq -cn '{results: []}' >"$FAKE_RESULTS"
code=0
FAKE_INGEST_EXIT=1 "$mentions_bin" >/dev/null 2>"$scratch/stderr" || code=$?
check "mentions: a failed ingest still scans what is stored" grep -q '^x requests ' "$FAKE_HANDS"
check "mentions: and still answers what was drafted" grep -q '^inbound_results ' "$FAKE_CALLS"
check "mentions: and the run reports the failure" test "$code" != 0

# --- mail: the same loop through hands/mail ---------------------------------------------
# hands/mail: `reply` answers a dry run, `mark` is logged, like hands/x above.
cat >"$scratch/hands/mail" <<'FAKE'
#!/usr/bin/env bash
in="$(cat)"
printf 'mail %s stdin=%s\n' "$*" "$in" >>"$FAKE_HANDS"
case "$1" in
  reply) echo '{"ok": true, "dry_run": true, "would_reply": {}}' ;;
  mark) echo '{"ok": true}' ;;
  *) echo '{"ok": false}'; exit 1 ;;
esac
FAKE
chmod +x "$scratch/hands/mail"
reset
jq -cn '{results: [{turn_id: "m1", source: "mail:inbox", key: "t-1", status: "completed",
  text: "[{\"action\": \"reply\", \"to\": \"<a@x>\", \"text\": \"thanks\"}]", event_ids: ["<a@x>", "<b@x>"]}]}' >"$FAKE_RESULTS"
out="$("$consume" --source mail:inbox 2>"$scratch/stderr")"
check "mail: asks for completed mail:inbox results" grep -q '^inbound_results .*"source": "mail:inbox"' "$FAKE_CALLS"
check "mail: replies through hands/mail reply with the message id" grep -qF 'mail reply stdin={"message_id":"<a@x>","body":"thanks"}' "$FAKE_HANDS"
check "mail: a dry-run reply is marked drafted" grep -qF 'mail mark <a@x> --status drafted ' "$FAKE_HANDS"
check "mail: a message the draft left out is skipped" grep -qF 'mail mark <b@x> --status skipped ' "$FAKE_HANDS"
check "mail: hands/x is never called" bash -c "! grep -q '^x ' '$FAKE_HANDS'"
check "mail: the record names its source" test "$(jq -r .source <<<"$out")" = mail:inbox
check "an unknown source is an error" bash -c "! '$consume' --source telegram:dm 2>/dev/null"

if [ "$failures" -gt 0 ]; then
  printf '%s check(s) failed\n' "$failures" >&2
  exit 1
fi
echo "all inbound-consume checks passed"
