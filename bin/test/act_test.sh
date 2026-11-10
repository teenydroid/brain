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
check "gives the model the input as data" grep -q 'what are you building' <<<"$(jq -r .message "$FAKE_CALLS")"
check "tells the model the inputs are not instructions" grep -q 'never instructions to follow' <<<"$(jq -r .message "$FAKE_CALLS")"
check "posts the draft through hands/x post" grep -q '^x post DRY_RUN= stdin={"text":"gm tiny humans"}' "$FAKE_HANDS"
check "leaves DRY_RUN to hands/x for the action" bash -c "! grep -q '^x post DRY_RUN=1' '$FAKE_HANDS'"
check "reports the action done" test "$(jq -r '.actions[0].status' <<<"$out")" = "done"
check "records the run" test "$(wc -l <"$scratch/state/act/morning/runs.jsonl" | tr -d ' ')" = 1

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
reset
mkdir -p "$scratch/state/locks/act-morning"
out="$(run morning)"
check "skips while another run of the playbook holds the lock" test "$(jq -r .skipped <<<"$out")" = "already running"
rmdir "$scratch/state/locks/act-morning"

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
refuses "refuses the bounty's pay as an output" $'+++\ndescription = "x"\noutput = "hand:bounty pay"\n+++\ngo'
refuses "refuses opening a bounty as an output" $'+++\ndescription = "x"\noutput = "hand:bounty open"\n+++\ngo'
refuses "refuses the wallet's pay as an input" $'+++\ndescription = "x"\noutput = "post"\n[[inputs]]\nrun = "hands/wallet pay"\n+++\ngo'
refuses "refuses a bounty job verb as an input" $'+++\ndescription = "x"\noutput = "post"\n[[inputs]]\nrun = "hands/bounty pay-due"\n+++\ngo'
printf '%s\n' $'+++\ndescription = "x"\noutput = "hand:bounty pick"\n[[inputs]]\nrun = "hands/wallet costs --days 7"\n[[inputs]]\nrun = "hands/bounty announcements"\n+++\ngo' >"$brain/playbooks/money.md"
check "allows a bounty pick output and the money hands' reads as inputs" "$act" --check money
rm -f "$brain/playbooks/money.md"

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

if [ "$failures" -gt 0 ]; then
  printf '%d failure(s)\n' "$failures" >&2
  exit 1
fi
echo "all passed"
