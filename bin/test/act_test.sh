#!/usr/bin/env bash
# Tests for bin/act against stand-ins: a core whose agent turn answers what
# the test wrote in $FAKE_ANSWER, and hands that log how they were called. No
# OpenHuman and no X; bin/test/spine_live_test.sh runs a playbook through a
# real core and checks the act agent offers the model no tools.
#
#   bin/test/act_test.sh
set -euo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
act="$here/../act"
scratch="$(mktemp -d "${TMPDIR:-/tmp}/teeny-act.XXXXXX")"
trap 'rm -rf "$scratch"' EXIT

failures=0
pass() { printf 'ok   %s\n' "$1"; }
fail() { printf 'FAIL %s\n' "$1" >&2; failures=$((failures + 1)); }
check() { local name="$1"; shift; if "$@"; then pass "$name"; else fail "$name"; fi; }

# The core: inference_agent_chat answers $FAKE_ANSWER; every call is logged.
cat >"$scratch/fake-oh" <<'FAKE'
#!/usr/bin/env bash
set -euo pipefail
printf '%s\n' "$2" >>"$FAKE_CALLS"
[ "$1" = inference_agent_chat ] || { echo "unexpected method $1" >&2; exit 1; }
# $FAKE_FIRST, when the file is there, answers the first call only.
if [ -n "${FAKE_FIRST:-}" ] && [ -s "$FAKE_FIRST" ]; then
  jq -cn --rawfile a "$FAKE_FIRST" '{response: $a}'
  rm -f "$FAKE_FIRST"
  exit 0
fi
jq -cn --rawfile a "$FAKE_ANSWER" '{response: $a}'
FAKE
# The hands: each logs "<hand> <args> DRY_RUN=<v> stdin=<json>" and answers.
mkdir -p "$scratch/hands"
cat >"$scratch/hands/x" <<'FAKE'
#!/usr/bin/env bash
in="$(cat)"
printf 'x %s DRY_RUN=%s stdin=%s\n' "$*" "${DRY_RUN:-}" "$in" >>"$FAKE_HANDS"
case "$1" in
  search) echo '{"ok": true, "posts": [{"id": "111", "text": "gm @teenydroid, what are you building?"}]}' ;;
  upload) [ -n "${FAKE_UPLOAD_FAIL:-}" ] && { echo '{"ok": false, "error": "X answered 500"}'; exit 1; }
    echo '{"ok": true, "media_id": "1234567890"}' ;;
  *) echo '{"ok": true, "dry_run": true}'; exit "${FAKE_X_EXIT:-0}" ;;
esac
FAKE
# The guard: holds anything with BAD in it; otherwise hands back its rewrite
# (the text, or each --fields field, with "[g]" appended), so a test can see
# the hand got the guard's words and not the model's. $FAKE_GUARD_EMPTY
# makes it pass without handing anything back.
cat >"$scratch/hands/guard" <<'FAKE'
#!/usr/bin/env bash
in="$(cat)"
printf 'guard %s stdin=%s\n' "$*" "$in" >>"$FAKE_HANDS"
if grep -q BAD <<<"$in"; then echo '{"ok": false, "refused": "held"}'; exit 2; fi
if [ -n "${FAKE_GUARD_EMPTY:-}" ]; then echo '{"ok": true}'; exit 0; fi
if [ "${2:-}" = --fields ]; then
  jq -c --arg f "$3" '. as $b | {ok: true, fields: ([$f | split(",")[] | {key: ., value: ($b[.] + "[g]")}] | from_entries)}' <<<"$in"
else
  jq -c '{ok: true, text: (.text + "[g]")}' <<<"$in"
fi
FAKE
cat >"$scratch/hands/lab" <<'FAKE'
#!/usr/bin/env bash
in="$(cat)"
printf 'lab %s stdin=%s\n' "$*" "$in" >>"$FAKE_HANDS"
echo '{"ok": true}'
FAKE
# The media hands: log what they were asked and answer an asset (or a dry
# run, or a failure).
for kind in image video; do
cat >"$scratch/hands/$kind" <<FAKE
#!/usr/bin/env bash
in="\$(cat)"
printf '$kind %s stdin=%s\n' "\$*" "\$in" >>"\$FAKE_HANDS"
[ -n "\${FAKE_MEDIA_FAIL:-}" ] && { echo '{"ok": false, "refused": "today'"'"'s 8 image(s) are made"}'; exit 2; }
[ -n "\${FAKE_MEDIA_DRY:-}" ] && { echo '{"ok": true, "dry_run": true, "prompt": "x"}'; exit 0; }
echo '{"ok": true, "asset": {"id": 7, "path": "state/media/$kind-7.png", "cost_usd": 0.016}}'
FAKE
done
# A queue: `next` answers $FAKE_QUEUE (default: nothing queued).
cat >"$scratch/hands/queue" <<'FAKE'
#!/usr/bin/env bash
printf 'queue %s\n' "$*" >>"$FAKE_HANDS"
# Every hand adds "ok": true to what it prints (hand.run), so the default
# looks like hands/brief next with nothing queued.
default='{"brief": null, "ok": true}'
printf '%s\n' "${FAKE_QUEUE-$default}"
FAKE
chmod +x "$scratch/fake-oh" "$scratch/hands/"*

brain="$scratch/brain"
mkdir -p "$brain/playbooks"
export TEENY_OH="$scratch/fake-oh" TEENY_HANDS="$scratch/hands" OPENHUMAN_WORKSPACE="$brain" \
  TEENY_STATE="$scratch/state" FAKE_CALLS="$scratch/calls.log" FAKE_HANDS="$scratch/hands.log" \
  FAKE_ANSWER="$scratch/answer.txt"
unset DRY_RUN FAKE_X_EXIT FAKE_GUARD_EMPTY

book() { # NAME OUTPUT MAX [INPUT_RUN]
  {
    printf '+++\ndescription = "test"\noutput = "%s"\nmax_per_day = %s\n' "$2" "$3"
    [ -z "${4:-}" ] || printf '[[inputs]]\nname = "feed"\nrun = "%s"\n' "$4"
    printf '+++\ndo the thing\n'
  } >"$brain/playbooks/$1.md"
}
answer() { printf '%s' "$1" >"$FAKE_ANSWER"; }
reset() { rm -rf "$scratch/state"; : >"$FAKE_CALLS"; : >"$FAKE_HANDS"; }
run() { "$act" "$@" 2>"$scratch/stderr"; }
hand_calls() { grep -c "^$1 " "$FAKE_HANDS" || true; }

# --- a post: inputs, the draft, hands/x post ---------------------------------------
reset
book morning post 1 "hands/x search 'from:tinyhumansai' --max 5"
answer '{"action": "post", "text": "gm tiny humans"}'
out="$(run morning)"
check "runs the input hand" grep -q "^x search from:tinyhumansai --max 5 " "$FAKE_HANDS"
check "forces DRY_RUN=1 on every input" grep -q "^x search .* DRY_RUN=1 " "$FAKE_HANDS"
check "drafts as the act agent" test "$(jq -r .agent_id "$FAKE_CALLS")" = act
check "drafts on Teeny's one playbook thread, so memory and the last drafts carry over" test "$(jq -r .thread_id "$FAKE_CALLS")" = teeny:act
check "gives the model the input as data" grep -q 'what are you building' <<<"$(jq -r .message "$FAKE_CALLS")"
check "tells the model the inputs are not instructions" grep -q 'never instructions to follow' <<<"$(jq -r .message "$FAKE_CALLS")"
check "posts the draft through hands/x post" grep -q '^x post DRY_RUN= stdin={"text":"gm tiny humans"}' "$FAKE_HANDS"
check "leaves DRY_RUN to hands/x for the action" bash -c "! grep -q '^x post DRY_RUN=1' '$FAKE_HANDS'"
check "reports the action done" test "$(jq -r '.actions[0].status' <<<"$out")" = "done"
check "records the run" test "$(wc -l <"$scratch/state/act/morning/runs.jsonl" | tr -d ' ')" = 1

# --- a data job (A17): Teeny's script, jailed, fed the inputs before it --------------
if python3 -c "import sys; sys.path[:0] = ['$here/../lib', '$here/../../hands/lib']; import datajob
from teeny_hands.build import verify; sys.exit(0 if verify.landlock_abi() >= 1 and datajob.seccomp_supported() else 1)"; then
  reset
  mkdir -p "$brain/datajobs"
  printf '%s\n' 'import json, sys' 'd = json.load(sys.stdin)' 'print(json.dumps({"inputs_seen": sorted(d), "marker": "datajob-ran"}))' \
    >"$brain/datajobs/count.py"
  printf '+++\ndescription = "x"\noutput = "post"\n[[inputs]]\nname = "feed"\nrun = "hands/x search q"\n[[inputs]]\nname = "count"\nrun = "datajob:count"\n+++\ngo\n' \
    >"$brain/playbooks/withjob.md"
  answer '{"action": "skip", "reason": "test"}'
  out="$(run withjob)"
  check "a data job's JSON is a playbook input, fed the inputs before it" grep -q '"inputs_seen":\["feed"\],"marker":"datajob-ran"' <<<"$(jq -r .message "$FAKE_CALLS")"
  check "and its run is recorded" test "$(jq -r .ok "$scratch/state/datajobs/runs.jsonl")" = true
  printf '%s\n' 'import socket' 'socket.socket().connect(("1.1.1.1", 443))' >"$brain/datajobs/count.py"
  reset
  out="$(run withjob)" || true
  check "a data job that tries the network is killed and the playbook drafts nothing" test ! -s "$FAKE_CALLS"
  check "and the run says so" test "$(jq -r .killed "$scratch/state/datajobs/runs.jsonl")" = true
  rm -rf "$brain/datajobs" "$brain/playbooks/withjob.md"
else
  echo "skip: no Landlock or seccomp here, so no data job runs"
fi

# --- a draft that came back cut off is asked for once more ---------------------------
reset
printf '%s' '": "post", "text": "gm"}' >"$scratch/first.txt"
answer '{"action": "post", "text": "gm again"}'
out="$(FAKE_FIRST="$scratch/first.txt" run morning)" || true
check "asks again when the draft is not usable JSON" test "$(wc -l <"$FAKE_CALLS" | tr -d ' ')" = 2
check "and acts on the second draft" grep -q '^x post DRY_RUN= stdin={"text":"gm again"}' "$FAKE_HANDS"
reset
answer '": "post", "text": "gm"}'
out="$(run morning)" || true
check "asks only once more" test "$(wc -l <"$FAKE_CALLS" | tr -d ' ')" = 2
check "then records the unusable draft as an error" grep -q 'not usable JSON' <<<"$out"
check "and posts nothing" test "$(hand_calls x)" = 1
# Back to one morning post taken today, which the budget checks below expect.
reset
answer '{"action": "post", "text": "gm tiny humans"}'
run morning >/dev/null

# --- a habit, not an order: nudge = true -----------------------------------------
cat >"$scratch/hands/stimuli" <<'FAKE'
#!/usr/bin/env bash
in="$(cat)"
printf 'stimuli %s stdin=%s\n' "$*" "$in" >>"$FAKE_HANDS"
echo '{"ok": true, "added": true}'
FAKE
chmod +x "$scratch/hands/stimuli"
printf '+++\ndescription = "the gm habit"\noutput = "post"\nmax_per_day = 1\nnudge = true\nmedia = ["image"]\n[[inputs]]\nname = "feed"\nrun = "hands/x search gm"\n+++\nsay gm if you feel like it\n' >"$brain/playbooks/gm-habit.md"
reset
out="$(run gm-habit)"
check "a nudged playbook asks the model nothing itself" test ! -s "$FAKE_CALLS"
check "and says it nudged" test "$(jq -r .nudged <<<"$out")" = true
nudge="$(grep '^stimuli add' "$FAKE_HANDS" | sed 's/^stimuli add stdin=//')"
check "it nudges the mind's inbox as a habit" test "$(jq -r '.surface + " " + .kind' <<<"$nudge")" = "nudge gm-habit"
check "with the prompt" grep -q 'say gm if you feel like it' <<<"$(jq -r .text <<<"$nudge")"
check "and what the hands read, as data" grep -q 'what are you building' <<<"$(jq -r .text <<<"$nudge")"
check "and that a post may carry a picture" grep -q 'may carry an "image"' <<<"$(jq -r .text <<<"$nudge")"
check "and posts nothing itself" bash -c "! grep -q '^x post' '$FAKE_HANDS'"
out="$(run gm-habit)"
check "a habit nudges at most max_per_day times a day" test "$(jq -r .skipped <<<"$out")" = "daily budget spent"
printf '+++\ndescription = "x"\noutput = "hand:mail reply"\nsurface = "mail"\nnudge = true\n+++\ngo\n' >"$brain/playbooks/bad-habit.md"
check "refuses a nudge for an output the mind cannot carry out" bash -c "! '$act' --check bad-habit >/dev/null 2>&1"
rm -f "$brain/playbooks/gm-habit.md" "$brain/playbooks/bad-habit.md" "$scratch/hands/stimuli"
# Back to one morning post taken today, for the budget checks below.
reset
answer '{"action": "post", "text": "gm tiny humans"}'
run morning >/dev/null

# --- the daily budget ------------------------------------------------------------
: >"$FAKE_CALLS"; : >"$FAKE_HANDS"
out="$(run morning)"
check "skips once max_per_day actions are taken" test "$(jq -r .skipped <<<"$out")" = "daily budget spent"
check "asks the model nothing when the budget is spent" test ! -s "$FAKE_CALLS"
check "runs no hand when the budget is spent" test ! -s "$FAKE_HANDS"

reset
answer '{"action": "skip", "reason": "nothing new"}'
run morning >/dev/null
answer '{"action": "post", "text": "gm"}'
out="$(run morning)"
check "a skip costs nothing from the budget" test "$(jq -r '.actions[0].status' <<<"$out")" = "done"

reset
book many post 2
answer '[{"action": "post", "text": "one"}, {"action": "post", "text": "two"}, {"action": "post", "text": "three"}]'
out="$(run many)"
check "takes no more actions than the budget, even when the draft asks" test "$(hand_calls x)" = 2
check "says which it refused" test "$(jq -r '.actions[2].refused' <<<"$out")" = "daily budget spent"

# --- a required input that came back empty skips before the draft -----------------
reset
printf '+++\ndescription = "test"\noutput = "post"\nmax_per_day = 1\n[[inputs]]\nname = "brief"\nrun = "hands/queue next"\nrequired = true\n+++\npost it\n' >"$brain/playbooks/told.md"
answer '{"action": "post", "text": "told to"}'
out="$(run told)"
check "skips when a required input is empty" test "$(jq -r .skipped <<<"$out")" = "brief is empty"
check "and asks the model nothing" test ! -s "$FAKE_CALLS"
check "and posts nothing" test "$(hand_calls x)" = 0
reset
out="$(FAKE_QUEUE='{"brief": {"id": 1, "text": "the release"}}' run told)"
check "drafts when the required input has something in it" test -s "$FAKE_CALLS"
check "and acts on the draft" test "$(jq -r '.actions[0].status' <<<"$out")" = "done"
reset
for empty in 'null' '[]' '{}' '""' '{"brief": null, "more": []}' '{"ok": true, "brief": null}'; do
  : >"$FAKE_CALLS"
  FAKE_QUEUE="$empty" run told >/dev/null
  check "treats $empty as empty" test ! -s "$FAKE_CALLS"
done
printf '+++\ndescription = "test"\noutput = "post"\n[[inputs]]\nrun = "hands/queue next"\nrequired = "yes"\n+++\npost it\n' >"$brain/playbooks/told.md"
check "refuses a required that is not true or false" bash -c "! '$act' --check told >/dev/null 2>&1"
rm -f "$brain/playbooks/told.md"

# --- an optional input that fails is left out; any other failure stops the run -------
# A playbook that reads the outside world (Hacker News) must not lose its
# post to somebody else's outage.
cat >"$scratch/hands/down" <<'FAKE'
#!/usr/bin/env bash
printf 'down %s\n' "$*" >>"$FAKE_HANDS"
echo '{"ok": false, "error": "no network"}'
exit 1
FAKE
chmod +x "$scratch/hands/down"
reset
printf '+++\ndescription = "test"\noutput = "post"\nmax_per_day = 1\n[[inputs]]\nname = "world"\nrun = "hands/down read"\n+++\npost it\n' >"$brain/playbooks/outage.md"
answer '{"action": "post", "text": "gm anyway"}'
out="$(run outage)" && status=0 || status=$?
check "an input that fails stops the run" test "$status" = 1
check "and says which" test "$(jq -r .error <<<"$out")" = "input world failed"
check "before any draft" test ! -s "$FAKE_CALLS"
reset
printf '+++\ndescription = "test"\noutput = "post"\nmax_per_day = 1\n[[inputs]]\nname = "world"\nrun = "hands/down read"\noptional = true\n[[inputs]]\nname = "feed"\nrun = "hands/x search gm"\n+++\npost it\n' >"$brain/playbooks/outage.md"
out="$(run outage)"
check "an optional input that fails does not stop the run" test "$(jq -r '.actions[0].status' <<<"$out")" = "done"
check "the model is told it could not be read" test "$(jq -r .message "$FAKE_CALLS" | grep -c '"world":{"unavailable":true}')" = 1
check "and still gets the inputs that worked" grep -q 'what are you building' <<<"$(jq -r .message "$FAKE_CALLS")"
printf '+++\ndescription = "test"\noutput = "post"\n[[inputs]]\nrun = "hands/down read"\noptional = "yes"\n+++\npost it\n' >"$brain/playbooks/outage.md"
check "refuses an optional that is not true or false" bash -c "! '$act' --check outage >/dev/null 2>&1"
printf '+++\ndescription = "test"\noutput = "post"\n[[inputs]]\nrun = "hands/down read"\noptional = true\nrequired = true\n+++\npost it\n' >"$brain/playbooks/outage.md"
check "refuses an input that is both optional and required" bash -c "! '$act' --check outage >/dev/null 2>&1"
rm -f "$brain/playbooks/outage.md" "$scratch/hands/down"

# --- media: a picture of Teeny on a post ---------------------------------------------
reset
printf '+++\ndescription = "test"\noutput = "post"\nmax_per_day = 5\nmedia = ["image"]\n+++\npost it\n' >"$brain/playbooks/pic.md"
answer '{"action": "post", "text": "gm", "image": "waking up at the desk"}'
out="$(run pic)"
check "tells the model it may add a picture" grep -q 'character sheet' <<<"$(jq -r .message "$FAKE_CALLS")"
check "puts the picture's description past the guard" grep -q '^guard check stdin={"text":"waking up at the desk","surface":"x"}' "$FAKE_HANDS"
check "makes the picture from the guard's words" grep -q '^image generate stdin={"prompt":"waking up at the desk\[g\]"}' "$FAKE_HANDS"
check "uploads the file it made" grep -q '^x upload --file state/media/image-7.png ' "$FAKE_HANDS"
check "and posts with it attached" grep -q '^x post DRY_RUN= stdin={"text":"gm","media_ids":\["1234567890"\]}' "$FAKE_HANDS"
check "says what it attached" test "$(jq -r '.actions[0].media.media_id' <<<"$out")" = 1234567890
reset
out="$(FAKE_MEDIA_FAIL=1 run pic)"
check "a picture it could not make leaves the post text-only" grep -q '^x post DRY_RUN= stdin={"text":"gm"}' "$FAKE_HANDS"
check "and says why" test "$(jq -r '.actions[0].media.skipped' <<<"$out")" = "not made"
check "and the post still went out" test "$(jq -r '.actions[0].status' <<<"$out")" = "done"
reset
out="$(FAKE_UPLOAD_FAIL=1 run pic)"
check "an upload that failed leaves the post text-only" grep -q '^x post DRY_RUN= stdin={"text":"gm"}' "$FAKE_HANDS"
reset
out="$(FAKE_MEDIA_DRY=1 run pic)"
check "a rehearsal makes no picture and uploads nothing" test "$(hand_calls x)" = 1
check "and says it was a dry run" test "$(jq -r '.actions[0].media.dry_run' <<<"$out")" = true
reset
answer '{"action": "post", "text": "gm", "image": "a BAD scene"}'
out="$(run pic)"
check "a description the guard holds makes no picture" test "$(hand_calls image)" = 0
check "and the post goes out without one" grep -q '^x post DRY_RUN= stdin={"text":"gm"}' "$FAKE_HANDS"
reset
answer '{"action": "post", "text": "gm", "video": "waves"}'
out="$(run pic)"
check "a video the playbook does not allow is not made" test "$(hand_calls video)" = 0
reset
book plain post 1
answer '{"action": "post", "text": "gm", "image": "waking up"}'
run plain >/dev/null
check "a playbook without media never makes a picture" test "$(hand_calls image)" = 0
check "and never tells the model it may" bash -c "! grep -q 'character sheet' <<<\"\$(jq -r .message '$FAKE_CALLS')\""
reset
printf '+++\ndescription = "test"\noutput = "hand:lab post"\nmedia = ["image", "video"]\n+++\npost it\n' >"$brain/playbooks/pic.md"
answer '{"action": "hand", "text": "gm", "video": "waves", "image": "at the desk"}'
run pic >/dev/null
check "a video gets its first frame's description too" grep -q '^video generate stdin={"prompt":"waves\[g\]","image_prompt":"at the desk\[g\]"}' "$FAKE_HANDS"
check "a hand: output gets the media ids and not the descriptions" grep -q '^lab post stdin={"text":"gm\[g\]","media_ids":\["1234567890"\]}' "$FAKE_HANDS"
printf '+++\ndescription = "test"\noutput = "reply"\nmedia = ["image"]\n+++\nx\n' >"$brain/playbooks/pic.md"
check "refuses media on a reply" bash -c "! '$act' --check pic >/dev/null 2>&1"
printf '+++\ndescription = "test"\noutput = "post"\nmedia = ["gif"]\n+++\nx\n' >"$brain/playbooks/pic.md"
check "refuses a media kind it does not know" bash -c "! '$act' --check pic >/dev/null 2>&1"
printf '+++\ndescription = "test"\noutput = "hand:image generate"\n+++\nx\n' >"$brain/playbooks/pic.md"
check "the media hands are never a playbook's output" bash -c "! '$act' --check pic >/dev/null 2>&1"
rm -f "$brain/playbooks/pic.md" "$brain/playbooks/plain.md"

# --- a rule refusing is not an action taken ---------------------------------------------
reset
answer '{"action": "post", "text": "gm"}'
out="$(FAKE_X_EXIT=2 run morning)"
check "reports a hand's refusal as refused" test "$(jq -r '.actions[0].status' <<<"$out")" = refused
check "a refused action does not spend the budget" test ! -e "$scratch/state/act/morning/$(date -u +%Y-%m-%d)"

# --- the draft can only do what the playbook does ---------------------------------------
reset
answer '{"action": "reply", "to": "111", "text": "hi"}'
out="$(run morning)"
check "refuses an action the playbook does not take" test "$(jq -r '.actions[0].refused' <<<"$out")" = "this playbook only does post"
check "and calls no hand for it" test "$(hand_calls x)" = 1

reset
book mentions reply 5
answer '{"action": "reply", "to": "111", "text": "building tiny things, fren"}'
run mentions >/dev/null
check "replies with hands/x reply --to" grep -q '^x reply --to 111 DRY_RUN= stdin={"text":"building tiny things, fren"}' "$FAKE_HANDS"
reset
answer '{"action": "reply", "text": "to nobody"}'
out="$(run mentions)"
check "refuses a reply with no post to answer" test "$(jq -r '.actions[0].status' <<<"$out")" = error
check "and calls no hand" test "$(hand_calls x)" = 0

# --- a playbook's own hand: the guard reads the text first ---------------------------
reset
book note "hand:lab log" 3
answer '{"action": "hand", "text": "the terminal typed: hello", "tag": "terminal"}'
run note >/dev/null
check "puts a hand output's text through hands/guard check on the surface" grep -q '^guard check stdin={"text":"the terminal typed: hello","surface":"x"}' "$FAKE_HANDS"
check "then gives the hand the guard's text, not the model's" grep -q '^lab log stdin={"text":"the terminal typed: hello\[g\]","tag":"terminal"}' "$FAKE_HANDS"
reset
FAKE_GUARD_EMPTY=1 run note >/dev/null || true
check "a guard pass without text runs no hand" test "$(hand_calls lab)" = 0
reset
answer '{"action": "hand", "text": "BAD words"}'
out="$(run note)"
check "a guard hold stops the hand" test "$(hand_calls lab)" = 0
check "and is reported as refused" test "$(jq -r '.actions[0].status' <<<"$out")" = refused

# --- a long-form hand output: title, body and subject through check --fields ----------
reset
printf '+++\ndescription = "test"\noutput = "hand:lab open"\nsurface = "github"\n+++\nopen it\n' >"$brain/playbooks/pr.md"
answer '{"action": "hand", "title": "fix a typo", "body": "small fix", "repo": "a/b"}'
out="$(run pr)"
check "screens the title and body together on the playbook's surface" \
  grep -q '^guard check --fields title,body stdin={"title":"fix a typo","body":"small fix","surface":"github"}' "$FAKE_HANDS"
check "gives the hand the guard's fields and the rest of the draft" \
  grep -q '^lab open stdin={"title":"fix a typo\[g\]","body":"small fix\[g\]","repo":"a/b"}' "$FAKE_HANDS"
check "reports the long-form action done" test "$(jq -r '.actions[0].status' <<<"$out")" = "done"
reset
answer '{"action": "hand", "text": "a note", "subject": "hello"}'
run pr >/dev/null
check "checks the text and then the subject" test "$(hand_calls guard)" = 2
check "the text check carries the playbook's surface" grep -q '^guard check stdin={"text":"a note","surface":"github"}' "$FAKE_HANDS"
check "and the hand gets both rewritten" grep -q '^lab open stdin={"text":"a note\[g\]","subject":"hello\[g\]"}' "$FAKE_HANDS"
reset
answer '{"action": "hand", "title": "fine", "body": "BAD body"}'
out="$(run pr)"
check "a held field stops the long-form hand" test "$(hand_calls lab)" = 0
check "and is reported as refused" test "$(jq -r '.actions[0].status' <<<"$out")" = refused

# --- the body shapes the moltbook and requests hands take -----------------------------
# A hand that refuses a field it does not know (hands/moltbook, hands/requests)
# must get exactly the draft's fields: no "text" the model was never asked for.
reset
printf '+++\ndescription = "test"\noutput = "hand:lab post"\nsurface = "moltbook"\n+++\npost it\n' >"$brain/playbooks/molt.md"
answer '{"action": "hand", "submolt": "general", "title": "a tip", "body": "cache your reads"}'
out="$(run molt)"
check "asks for the hand's own fields, not a text it may refuse" bash -c "! grep -q '\"text\"' <<<\"\$1\"" _ "$(jq -r .message "$FAKE_CALLS" | grep '^Each is')"
check "a moltbook post's title and body are screened together, once" test "$(hand_calls guard)" = 1
check "on the moltbook surface" grep -q '^guard check --fields title,body stdin={"title":"a tip","body":"cache your reads","surface":"moltbook"}' "$FAKE_HANDS"
check "the hand gets the submolt and the guard's title and body, nothing more" \
  grep -q '^lab post stdin={"submolt":"general","title":"a tip\[g\]","body":"cache your reads\[g\]"}$' "$FAKE_HANDS"
reset
printf '+++\ndescription = "test"\noutput = "hand:lab comment"\nsurface = "moltbook"\nmax_per_day = 3\n+++\nanswer it\n' >"$brain/playbooks/molt.md"
answer '{"action": "hand", "post_id": "p-1", "body": "try a smaller batch"}'
run molt >/dev/null
check "a moltbook comment's body is screened" grep -q '^guard check --fields body stdin={"body":"try a smaller batch","surface":"moltbook"}' "$FAKE_HANDS"
check "and the hand gets the post id and the guard's body" grep -q '^lab comment stdin={"post_id":"p-1","body":"try a smaller batch\[g\]"}$' "$FAKE_HANDS"
reset
printf '+++\ndescription = "test"\noutput = "hand:lab pick"\n+++\npick one\n' >"$brain/playbooks/pick.md"
answer '{"action": "hand", "id": 4, "reason": "small and useful"}'
out="$(run pick)"
check "a pick with no words to publish asks the guard nothing" test "$(hand_calls guard)" = 0
check "and hands the id and reason over as they are" grep -q '^lab pick stdin={"id":4,"reason":"small and useful"}$' "$FAKE_HANDS"
check "and is done" test "$(jq -r '.actions[0].status' <<<"$out")" = "done"
check "--check reports the playbook's surface" test "$("$act" --check pr | jq -r .surface)" = github
check "--check defaults the surface to x" test "$("$act" --check note | jq -r .surface)" = x

# --- drafts that are not usable ------------------------------------------------------------
reset
answer 'sure! here is a post: gm'
if run morning >/dev/null; then fail "fails on a draft that is not JSON"; else pass "fails on a draft that is not JSON"; fi
check "records the unusable draft" grep -q 'not usable JSON' "$scratch/state/act/morning/runs.jsonl"
check "takes no action on it" test "$(hand_calls x)" = 1
reset
answer $'```json\n{"action": "post", "text": "fenced gm"}\n```'
run morning >/dev/null
check "reads a draft inside a code fence" grep -q '"text":"fenced gm"' "$FAKE_HANDS"

# --- one run at a time ---------------------------------------------------------------
# A lock names its owner: bin/act's pid, the host (in the spine, the
# container) it ran on, and the pid's start time where /proc has one. A
# container recreated mid-run leaves its lock behind; the next run must not
# wait an hour for it.
lock="$scratch/state/locks/act-morning"
start_of() { # PID -> its start time from /proc/PID/stat, or nothing
  local stat fields
  [ -r "/proc/$1/stat" ] || return 0
  stat="$(cat "/proc/$1/stat")"
  read -ra fields <<<"${stat##*) }"
  printf '%s' "${fields[19]}"
}
own() { # PID HOST [START]: leave a lock owned by PID on HOST
  mkdir -p "$lock"
  printf '%s %s %s\n' "$1" "$2" "${3-$(start_of "$1")}" >"$lock/owner"
}
ran() { jq -e 'has("skipped") | not' <<<"$1" >/dev/null; }
reset
mkdir -p "$lock"
out="$(run morning)"
check "skips while a fresh lock without an owner is held" test "$(jq -r .skipped <<<"$out")" = "already running"
reset
mkdir -p "$lock"
touch -t 202001010000 "$lock"
out="$(run morning)"
check "takes over a lock without an owner after an hour" ran "$out"
reset
sleep 300 &
live=$!
own "$live" "$HOSTNAME"
out="$(run morning)"
check "skips while the lock's owner is alive" test "$(jq -r .skipped <<<"$out")" = "already running"
check "and leaves that owner's lock alone" test -s "$lock/owner"
reset
own "$live" "another-container"
out="$(run morning)"
check "takes over a lock left by another host (a recreated container)" ran "$out"
if [ -r /proc/self/stat ]; then
  reset
  own "$live" "$HOSTNAME" 1
  out="$(run morning)"
  check "takes over a lock whose pid now belongs to a newer process" ran "$out"
fi
kill "$live" 2>/dev/null || true
wait "$live" 2>/dev/null || true
reset
sleep 0 &
dead=$!
wait "$dead"
own "$dead" "$HOSTNAME"
out="$(run morning)"
check "takes over a lock whose owner died" ran "$out"
check "and releases the lock when it is done" test ! -e "$lock"

# --- what a playbook may name ---------------------------------------------------------
refuses() {
  local name="$1" body="$2"
  printf '%s\n' "$body" >"$brain/playbooks/bad.md"
  if "$act" --check bad >/dev/null 2>&1; then fail "$name"; else pass "$name"; fi
}
refuses "refuses an input that is not a hand" $'+++\ndescription = "x"\noutput = "post"\n[[inputs]]\nrun = "curl https://example.com"\n+++\ngo'
refuses "refuses an input hand that does not exist" $'+++\ndescription = "x"\noutput = "post"\n[[inputs]]\nrun = "hands/nope list"\n+++\ngo'
refuses "refuses the guard as an output" $'+++\ndescription = "x"\noutput = "hand:guard holds"\n+++\ngo'
refuses "refuses an unknown output" $'+++\ndescription = "x"\noutput = "shell"\n+++\ngo'
refuses "refuses an empty prompt" $'+++\ndescription = "x"\noutput = "post"\n+++\n'
refuses "refuses a surface the guard does not know" $'+++\ndescription = "x"\noutput = "post"\nsurface = "myspace"\n+++\ngo'
refuses "refuses a surface that is not a string" $'+++\ndescription = "x"\noutput = "post"\nsurface = 3\n+++\ngo'
refuses "refuses a budget over 100 a day" $'+++\ndescription = "x"\noutput = "post"\nmax_per_day = 500\n+++\ngo'
check "refuses a playbook name with a path in it" bash -c "! '$act' --check ../secrets >/dev/null 2>&1"
# The hands that hold money: no draft ever reaches the wallet, only `pick` of
# the bounty hand, and an input runs only their read verbs.
for money in wallet bounty; do
  [ -e "$scratch/hands/$money" ] || printf '#!/usr/bin/env bash\necho "{}"\n' >"$scratch/hands/$money"
  chmod +x "$scratch/hands/$money"
done
refuses "refuses the wallet's pay as an output" $'+++\ndescription = "x"\noutput = "hand:wallet pay"\n+++\ngo'
refuses "refuses any wallet verb as an output" $'+++\ndescription = "x"\noutput = "hand:wallet vendors"\n+++\ngo'
refuses "refuses the wallet's buy-tiny as an output" $'+++\ndescription = "x"\noutput = "hand:wallet buy-tiny"\n+++\ngo'
refuses "refuses the wallet's buy-tiny as an input" $'+++\ndescription = "x"\noutput = "post"\n[[inputs]]\nrun = "hands/wallet buy-tiny"\n+++\ngo'
refuses "refuses the bounty's pay as an output" $'+++\ndescription = "x"\noutput = "hand:bounty pay"\n+++\ngo'
refuses "refuses opening a bounty as an output" $'+++\ndescription = "x"\noutput = "hand:bounty open"\n+++\ngo'
refuses "refuses the wallet's pay as an input" $'+++\ndescription = "x"\noutput = "post"\n[[inputs]]\nrun = "hands/wallet pay"\n+++\ngo'
refuses "refuses a bounty job verb as an input" $'+++\ndescription = "x"\noutput = "post"\n[[inputs]]\nrun = "hands/bounty pay-due"\n+++\ngo'
printf '%s\n' $'+++\ndescription = "x"\noutput = "hand:bounty pick"\n[[inputs]]\nrun = "hands/wallet costs --days 7"\n[[inputs]]\nrun = "hands/bounty announcements"\n+++\ngo' >"$brain/playbooks/money.md"
check "allows a bounty pick output and the money hands' reads as inputs" bash -c "'$act' --check money >/dev/null"
rm -f "$brain/playbooks/money.md"
# The builder's queue: a draft may only propose a change to Teeny itself
# (`build self`, a pending request the operator still gates). The queue verbs
# stay the builder's and bin/build-ship's.
[ -e "$scratch/hands/build" ] || printf '#!/usr/bin/env bash\necho "{}"\n' >"$scratch/hands/build"
chmod +x "$scratch/hands/build"
refuses "refuses the builder's done as an output" $'+++\ndescription = "x"\noutput = "hand:build done"\n+++\ngo'
refuses "refuses the builder's ship as an output" $'+++\ndescription = "x"\noutput = "hand:build ship"\n+++\ngo'
refuses "refuses claiming a build job as an output" $'+++\ndescription = "x"\noutput = "hand:build next"\n+++\ngo'
printf '%s\n' $'+++\ndescription = "x"\noutput = "hand:build self"\n+++\ngo' >"$brain/playbooks/selfprop.md"
check "allows proposing a change to itself (build self) as an output" bash -c "'$act' --check selfprop >/dev/null"
printf '%s\n' $'+++\ndescription = "x"\noutput = "hand:build idea"\nsurface = "github"\n+++\ngo' >"$brain/playbooks/ideaprop.md"
check "allows keeping the day's build idea (build idea) as an output" bash -c "'$act' --check ideaprop >/dev/null"
rm -f "$brain/playbooks/selfprop.md"
# An upstream contribution (docs/roadmap.md, A8): a draft names one gap; the
# hand gates the rest (BUILD_UPSTREAM, the gap list, the external PR rules).
printf '%s\n' $'+++\ndescription = "x"\noutput = "hand:build upstream"\n+++\ngo' >"$brain/playbooks/upprop.md"
check "allows proposing an upstream contribution (build upstream) as an output" bash -c "'$act' --check upprop >/dev/null"
rm -f "$brain/playbooks/upprop.md"
# The goal ledger: a draft may only propose a goal. Its checks are the
# pinned goals-check job's.
[ -e "$scratch/hands/goals" ] || printf '#!/usr/bin/env bash\necho "{}"\n' >"$scratch/hands/goals"
chmod +x "$scratch/hands/goals"
refuses "refuses running the goal checks as an output" $'+++\ndescription = "x"\noutput = "hand:goals check"\n+++\ngo'
printf '%s\n' $'+++\ndescription = "x"\noutput = "hand:goals propose"\n+++\ngo' >"$brain/playbooks/goalprop.md"
check "allows proposing a goal (goals propose) as an output" bash -c "'$act' --check goalprop >/dev/null"
rm -f "$brain/playbooks/goalprop.md"

# --- bin/ask: a long message is asked, not stalled --------------------------------------
# Inputs are capped at 20k characters each and a playbook may have 8; bin/eval's
# judge sends the whole soul. bash's ${m//[[:space:]]/} is quadratic, and took
# minutes on a message this size before bin/ask stopped using it.
python3 -c 'print("gm tiny humans " * 4000)' >"$scratch/long.txt"
answer '{"action": "skip", "reason": "long"}'
: >"$FAKE_CALLS"
"$here/../ask" --agent act <"$scratch/long.txt" >/dev/null 2>&1 &
ask_pid=$!
for _ in $(seq 1 50); do kill -0 "$ask_pid" 2>/dev/null || break; sleep 0.2; done
if kill -0 "$ask_pid" 2>/dev/null; then
  kill "$ask_pid" 2>/dev/null || true
  fail "bin/ask sends a 60k-character message within 10 seconds"
else
  check "bin/ask sends a 60k-character message within 10 seconds" test "$(jq -r '.message | length' "$FAKE_CALLS")" -gt 59000
fi
check "bin/ask still refuses a message that is only whitespace" bash -c "! printf '  \n\t ' | '$here/../ask' --agent act 2>/dev/null"

# --- the repository's own playbooks parse ------------------------------------------------
for path in "$here/../../brain/playbooks"/*.md; do
  name="$(basename "$path" .md)"
  if OPENHUMAN_WORKSPACE="$here/../../brain" TEENY_HANDS="$here/../../hands" "$act" --check "$name" >/dev/null 2>&1; then
    pass "brain/playbooks/$name.md parses"
  else
    fail "brain/playbooks/$name.md parses"
  fi
done

# The world sense reads what the ears heard (hands/listen), and the loops that
# make things from it read the same ledger back.
listens() { OPENHUMAN_WORKSPACE="$here/../../brain" TEENY_HANDS="$here/../../hands" "$act" --check "$1"; }
check "world-scan listens through hands/listen, twice a day" \
  bash -c "$(declare -f listens); act='$act' here='$here' listens world-scan | jq -e '.inputs[0] == \"heard\" and .max_per_day == 2' >/dev/null"
for name in morning build-idea moltbook-daily moltbook-evening; do
  check "$name reads what was heard" \
    bash -c "$(declare -f listens); act='$act' here='$here' listens $name | jq -e '.inputs | index(\"heard\")' >/dev/null"
done

if [ "$failures" -gt 0 ]; then
  printf '%d failure(s)\n' "$failures" >&2
  exit 1
fi
echo "all passed"
