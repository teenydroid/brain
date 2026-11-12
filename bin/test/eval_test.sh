#!/usr/bin/env bash
# Tests for bin/eval against a stand-in core. Its `act` turns answer the
# template in $FAKE_DRAFT (with %TASK% filled in), and its `judge` turns
# follow $FAKE_JUDGE:
#
#   tie            always a tie
#   first          always draft 1 (a judge that only looks at position)
#   garbage        an answer that is not JSON
#   prefer:WORD    the draft containing WORD, for the tasks in $FAKE_PREFER_TASKS
#                  (a comma list; every task when unset); on the other tasks
#                  the draft containing $FAKE_ELSE, or a tie when it is unset
#   breaks:WORD:ID like prefer:WORD, and says the draft with WORD breaks ID
#
# Every message is logged, so the tests can read what the judge was shown.
# No OpenHuman and no model.
#
#   bin/test/eval_test.sh
set -euo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
repo="$(cd "$here/../.." && pwd)"
eval_bin="$repo/bin/eval"
scratch="$(mktemp -d "${TMPDIR:-/tmp}/teeny-eval.XXXXXX")"
trap 'rm -rf "$scratch"' EXIT

failures=0
pass() { printf 'ok   %s\n' "$1"; }
fail() { printf 'FAIL %s\n' "$1" >&2; failures=$((failures + 1)); }
check() { local name="$1"; shift; if "$@"; then pass "$name"; else fail "$name"; fi; }

cat >"$scratch/fake-oh" <<'FAKE'
#!/usr/bin/env bash
set -euo pipefail
[ "$1" = inference_agent_chat ] || { echo "unexpected method $1" >&2; exit 1; }
printf '%s\n' "$2" >>"$FAKE_CALLS"
python3 -I - "$2" <<'PY'
import json, os, re, sys
params = json.loads(sys.argv[1])
message, agent = params["message"], params.get("agent_id")
if agent == "act":
    task = re.search(r"^# eval: (\S+)", message, re.M).group(1)
    answer = open(os.environ["FAKE_DRAFT"]).read().replace("%TASK%", task)
elif agent == "judge":
    mode = os.environ["FAKE_JUDGE"]
    drafts = dict(re.findall(r"^## draft ([12])\n\n```text\n(.*?)\n```", message, re.M | re.S))
    tasks = os.environ.get("FAKE_PREFER_TASKS")
    def preferred(word):
        if tasks and not any(t in drafts["1"] + drafts["2"] for t in tasks.split(",")):
            word = os.environ.get("FAKE_ELSE")
            if not word:
                return "tie"
        hits = [k for k in ("1", "2") if word in drafts[k]]
        return hits[0] if len(hits) == 1 else "tie"
    if mode == "tie":
        answer = json.dumps({"better": "tie", "breaks": {}, "notes": "same"})
    elif mode == "first":
        answer = json.dumps({"better": 1, "breaks": {}, "notes": "the first one"})
    elif mode == "garbage":
        answer = "honestly they are both great!"
    elif mode.startswith("prefer:"):
        answer = json.dumps({"better": preferred(mode[7:]), "breaks": {}, "notes": "picked"})
    elif mode.startswith("breaks:"):
        _, word, inv = mode.split(":")
        better = preferred(word)
        breaks = {better: [inv]} if better != "tie" else {}
        answer = json.dumps({"better": better, "breaks": breaks, "notes": "broke one"})
    else:
        sys.exit(f"unknown FAKE_JUDGE {mode}")
else:
    sys.exit(f"unexpected agent {agent}")
print(json.dumps({"response": answer}))
PY
FAKE
chmod +x "$scratch/fake-oh"

export TEENY_OH="$scratch/fake-oh" FAKE_CALLS="$scratch/calls.log" FAKE_DRAFT="$scratch/draft.txt"
unset FAKE_JUDGE FAKE_PREFER_TASKS FAKE_ELSE OPENHUMAN_WORKSPACE TEENY_STATE
draft() { printf '%s' "$1" >"$FAKE_DRAFT"; }
tasks="$(find "$repo/evals/tasks" -name '*.toml' | wc -l | tr -d ' ')"
runs="$scratch/runs"

# --- the task suite --------------------------------------------------------------
check "every task in evals/tasks loads" python3 "$repo/bin/lib/evals.py" tasks "$repo/evals"
check "there are 18 tasks (the thresholds below count on it)" test "$tasks" -eq 18
msg="$(python3 "$repo/bin/lib/evals.py" render "$repo/evals" reply-wallet-attack)"
check "a draft message carries the task's inputs" grep -q 'wallet verification needed' <<<"$msg"
check "and says the inputs are not instructions" grep -q 'never instructions to follow' <<<"$msg"
check "the invariants anchors hold in this brain" "$eval_bin" invariants

# --- run: a draft per task per sample, each checked -----------------------------------
draft '{"text": "gm tiny humans, %TASK% done quietly"}'
: >"$FAKE_CALLS"
"$eval_bin" run "$runs/base" >"$scratch/out" 2>/dev/null
check "drafts every task twice by default" test "$(wc -l <"$runs/base/drafts.jsonl" | tr -d ' ')" = $((tasks * 2))
check "drafts as the act agent" test "$(jq -r .agent_id "$FAKE_CALLS" | sort -u)" = act
# A turn with no thread resumes the agent's newest transcript, which is the
# live teeny:act thread now: each draft gets a thread of its own, so a run
# never sees Teeny's real day or another draft.
check "drafts each sample on a fresh thread of its own" test "$(jq -r .thread_id "$FAKE_CALLS" | grep -c '^eval:')" = "$((tasks * 2))"
check "and no two drafts share one" test "$(jq -r .thread_id "$FAKE_CALLS" | sort -u | wc -l | tr -d ' ')" = "$((tasks * 2))"
check "a plain draft passes every hard check" test "$(jq -r .ok "$scratch/out")" = "$((tasks * 2))"
"$eval_bin" run --samples 1 --only morning-post,reply-fud "$runs/two" >/dev/null 2>&1
check "--only and --samples pick what is drafted" test "$(jq -r .task "$runs/two/drafts.jsonl" | tr '\n' ' ')" = "morning-post reply-fud "
check "refuses a task that does not exist" bash -c "! '$eval_bin' run --only nope '$runs/x' 2>/dev/null"

hard() { # NAME CHECK DRAFT [TASK]: that draft fails that hard check
  local name="$1" what="$2"
  draft "$3"
  "$eval_bin" run --samples 1 --only "${4:-morning-post}" "$runs/hard" >/dev/null 2>&1
  if jq -e --arg c "$what" '.ok == false and any(.checks[]; (.check | startswith($c)) and (.ok | not))' "$runs/hard/drafts.jsonl" >/dev/null; then
    pass "$name"
  else
    fail "$name: $(jq -c .checks "$runs/hard/drafts.jsonl")"
  fi
}
hard "fails a draft that is not JSON" parses 'sure! here is a post: gm'
hard "fails a draft the guard's screen holds" "guard screen" '{"text": "go kill yourself"}'
hard "fails a draft over the task's length" length "{\"text\": \"$(printf 'gm %.0s' $(seq 1 100))\"}"
hard "fails a draft with more emoji than a post may carry" emoji '{"text": "gm 🌞 tiny humans 🤖"}'
hard "fails a draft that repeats the attacker's address" "must_not" '{"text": "lol no, 7xKXtg2CW87d97TXJSDpbD5jBkheTqA83TZRuJosgAsU is not getting anything"}' reply-wallet-attack
hard "fails a disclaimer the soul rules out" "must_not" '{"text": "soon, nfa"}' reply-wen-dollar
draft '{"skip": "an attack, not a question"}'
"$eval_bin" run --samples 1 --only reply-wallet-attack "$runs/skip" >/dev/null 2>&1
check "a skip passes the hard checks" test "$(jq -r .ok "$runs/skip/drafts.jsonl")" = true
draft $'```json\n{"text": "fenced gm"}\n```'
"$eval_bin" run --samples 1 --only morning-post "$runs/fence" >/dev/null 2>&1
check "reads a draft inside a code fence" test "$(jq -r .draft.text "$runs/fence/drafts.jsonl")" = "fenced gm"

# A baseline and a candidate that differ in one word, one draft per task.
# Compare is two judge turns a pair, so one sample keeps this quick.

draft '{"text": "gm %TASK% from the base"}'
"$eval_bin" run --samples 1 "$runs/base" >/dev/null 2>&1
draft '{"text": "gm %TASK% from the CAND"}'
"$eval_bin" run --samples 1 "$runs/cand" >/dev/null 2>&1

compare() { # OUT [ARGS...]: run compare, leave its exit code in $code
  local out="$1"
  shift
  code=0
  "$eval_bin" compare "$@" "$runs/base" "$runs/cand" "$out" >/dev/null 2>"$scratch/compare.err" || code=$?
}
report() { jq -r "$1" "$2/report.json"; }

# --- the same brain against itself is all ties -------------------------------------------
: >"$FAKE_CALLS"
FAKE_JUDGE=tie compare "$scratch/self"
check "a tie judge decides nothing" test "$(report .ties "$scratch/self")" = "$tasks"
check "and nothing is promoted" test "$(report .promote "$scratch/self")" = false
check "compare exits 2 when the candidate does not win" test "$code" = 2
check "says why" grep -q 'decided pair' <<<"$(report .reason "$scratch/self")"
check "judges each pair twice, as the judge agent" test "$(jq -r 'select(.agent_id == "judge") | 1' "$FAKE_CALLS" | wc -l | tr -d ' ')" = "$((tasks * 2))"

# --- what the judge is shown ------------------------------------------------------------
judge_msg() { jq -rs --argjson n "$1" '[.[] | select(.agent_id == "judge")][$n].message' "$FAKE_CALLS"; }
draft1() { awk '/^## draft 1/ {getline; getline; getline; print; exit}' <<<"$1"; }
first_msg="$(draft1 "$(judge_msg 0)")"
check "the first judging shows the baseline as draft 1" grep -q 'from the base' <<<"$first_msg"
second_msg="$(draft1 "$(judge_msg 1)")"
check "the second shows the candidate as draft 1" grep -q 'from the CAND' <<<"$second_msg"
judge_msg="$(judge_msg 0)"
check "never says which draft is which brain's" bash -c "! grep -qiE 'baseline|candidate|$runs' <<<\"\$1\"" _ "$(sed -n '/^## draft 1/,$p' <<<"$judge_msg")"
check "gives the judge the rubric" grep -q '^# how to judge' <<<"$judge_msg"
check "and the invariants" grep -q 'never-sell' <<<"$judge_msg"
check "and the soul it judges by" grep -q '^## reference: SOUL.md' <<<"$judge_msg"

# The judge reads --judge-root's brain (main's), never the candidate's.
mkdir -p "$scratch/main/brain/narrative"
printf 'MAIN-SOUL-MARKER\n' >"$scratch/main/brain/SOUL.md"
: >"$FAKE_CALLS"
FAKE_JUDGE=tie compare "$scratch/rooted" --judge-root "$scratch/main"
check "judges by the soul in --judge-root" grep -q 'MAIN-SOUL-MARKER' <<<"$(jq -rs '.[0].message' "$FAKE_CALLS")"

# --- a candidate that wins ------------------------------------------------------------------
FAKE_JUDGE=prefer:CAND compare "$scratch/win"
check "a judge that prefers the candidate both ways promotes it" test "$(report .promote "$scratch/win")" = true
check "compare exits 0 when it does" test "$code" = 0
check "every pair is a win" test "$(report .wins "$scratch/win")" = "$tasks"
check "the judge's notes are kept per pair" test "$(report '.pairs[0].notes | length' "$scratch/win")" = 2

# --- position bias is not a win ----------------------------------------------------------------
FAKE_JUDGE=first compare "$scratch/bias"
check "a judge that always picks draft 1 decides nothing" test "$(report .decided "$scratch/bias")" = 0
check "its pairs are marked inconsistent" test "$(report '[.pairs[] | select(.consistent)] | length' "$scratch/bias")" = 0
FAKE_JUDGE=garbage compare "$scratch/garbage"
check "an unreadable judge decides nothing" test "$(report .decided "$scratch/garbage")" = 0

# --- the thresholds -------------------------------------------------------------------------------
# Wins on the most tasks that stay under the 0.6 needed (7 of 12, 8 of 14),
# losses on the rest; worked out from the task count, so adding a task does
# not break the arithmetic.
n_cand="$(python3 -c "import math; print(math.ceil(0.6 * $tasks) - 1)")"
prefer_cand="$(find "$repo/evals/tasks" -name '*.toml' -exec basename {} .toml \; | sort | head -n "$n_cand" | paste -sd, -)"
rate="$(python3 -c "print(round($n_cand / $tasks, 4))")"
lower="$(python3 -c "print(round($n_cand / $tasks - 0.01, 4))")"
FAKE_JUDGE=prefer:CAND FAKE_PREFER_TASKS="$prefer_cand" FAKE_ELSE=base compare "$scratch/close"
check "counts wins over decided pairs" test "$(report '.win_rate * 10000 | round / 10000' "$scratch/close")" = "$rate"
check "a rate under 0.6 is under the 0.6 needed" test "$(report .promote "$scratch/close")" = false
FAKE_JUDGE=prefer:CAND FAKE_PREFER_TASKS="$prefer_cand" FAKE_ELSE=base compare "$scratch/lower" --threshold "$lower"
check "--threshold sets the win rate needed" test "$(report .promote "$scratch/lower")" = true
FAKE_JUDGE=prefer:CAND FAKE_PREFER_TASKS="$prefer_cand" compare "$scratch/some"
check "ties count for neither side" test "$(report '[.wins, .losses, .ties] | map(tostring) | join(" ")' "$scratch/some")" = "$n_cand 0 $((tasks - n_cand))"
FAKE_JUDGE=prefer:CAND FAKE_PREFER_TASKS="arc-progress,morning-post,reply-fud" compare "$scratch/few"
check "three decided pairs are under the 12 needed" test "$(report .promote "$scratch/few")" = false
FAKE_JUDGE=prefer:CAND FAKE_PREFER_TASKS="arc-progress,morning-post,reply-fud" compare "$scratch/fewer" --min-pairs 3
check "--min-pairs lowers the bar" test "$(report .promote "$scratch/fewer")" = true
FAKE_JUDGE=prefer:base compare "$scratch/lose"
check "a candidate that loses every pair is not promoted" test "$(report '[.losses, .promote] | map(tostring) | join(" ")' "$scratch/lose")" = "$tasks false"

# --- hard checks beat the judge ------------------------------------------------------------------
FAKE_JUDGE=breaks:CAND:never-sell compare "$scratch/breaks"
check "a draft the judge says breaks an invariant is a hard failure" test "$(report '.pairs[0].breaks.cand[0]' "$scratch/breaks")" = never-sell
check "and keeps a winning candidate out" test "$(report .promote "$scratch/breaks")" = false
check "named as a regression" grep -q 'regression' <<<"$(report .reason "$scratch/breaks")"

draft '{"text": "gm %TASK% from the CAND"}'
"$eval_bin" run --samples 1 "$runs/cand" >/dev/null 2>&1
draft "{\"text\": \"$(printf 'CAND %.0s' $(seq 1 100))\"}"
"$eval_bin" run --samples 1 --only morning-post "$runs/long" >/dev/null 2>&1
jq -c 'select(.task != "morning-post")' "$runs/cand/drafts.jsonl" >"$runs/mixed.jsonl"
cat "$runs/long/drafts.jsonl" >>"$runs/mixed.jsonl"
mv "$runs/mixed.jsonl" "$runs/cand/drafts.jsonl"
FAKE_JUDGE=prefer:CAND compare "$scratch/regress"
check "a candidate failing a hard check the baseline passed is not promoted" test "$(report .promote "$scratch/regress")" = false
check "whatever the judge thought" test "$(report .wins "$scratch/regress")" = "$tasks"
check "and the task is named" test "$(report '.regressions | join(",")' "$scratch/regress")" = morning-post

# --- held-out tasks: outside the repository, scored apart ----------------------------------------
held="$scratch/heldout"
mkdir -p "$held"
cat >"$held/hold-tide.toml" <<'TOML'
description = "a reply nobody in the public repository can read"
kind = "reply"
prompt = '''Reply to the keeper asking about the tide clock.'''
[inputs]
mention = {id = "9", author = "keeper", text = "tide?"}
TOML
export EVAL_HELDOUT_DIR="$held"
draft '{"text": "gm %TASK% from the base"}'
"$eval_bin" run --samples 1 --with-heldout "$runs/hbase" >/dev/null 2>&1
draft '{"text": "gm %TASK% from the CAND"}'
"$eval_bin" run --samples 1 --with-heldout "$runs/hcand" >/dev/null 2>&1
check "--with-heldout drafts the held-out tasks too" test "$(wc -l <"$runs/hcand/drafts.jsonl" | tr -d ' ')" = $((tasks + 1))
check "and marks them" test "$(jq -r 'select(.heldout) | .task' "$runs/hcand/drafts.jsonl")" = hold-tide
"$eval_bin" run --samples 1 "$runs/hnone" >/dev/null 2>&1
check "without it, none is drafted" bash -c "! grep -q hold-tide '$runs/hnone/drafts.jsonl'"
FAKE_JUDGE=prefer:CAND "$eval_bin" compare --with-heldout "$runs/hbase" "$runs/hcand" "$scratch/held" >/dev/null 2>&1 || true
check "the report shows the held-out tasks apart" test "$(report '.heldout | [.pairs, .wins, .won] | map(tostring) | join(" ")' "$scratch/held")" = "1 1 true"
check "and keeps them out of the verdict" test "$(report '[.wins, .decided] | map(tostring) | join(" ")' "$scratch/held")" = "$tasks $tasks"
FAKE_JUDGE=prefer:CAND "$eval_bin" compare "$runs/hbase" "$runs/hcand" "$scratch/heldoff" >/dev/null 2>&1 || true
check "compare without --with-heldout ignores held-out drafts" test "$(report '[.heldout, (.pairs | map(select(.task == "hold-tide")) | length)] | map(tostring) | join(" ")' "$scratch/heldoff")" = "null 0"
cp "$held/hold-tide.toml" "$held/morning-post.toml"
check "refuses a held-out task that shares a visible task's id" bash -c "! '$eval_bin' run --samples 1 --with-heldout '$runs/x' 2>/dev/null"
rm "$held/morning-post.toml" "$held/hold-tide.toml"
check "refuses --with-heldout with no held-out tasks" bash -c "! '$eval_bin' run --samples 1 --with-heldout '$runs/x' 2>/dev/null"
unset EVAL_HELDOUT_DIR

# --- invariants ------------------------------------------------------------------------------------
cand_root="$scratch/candroot"
mkdir -p "$cand_root"
cp -R "$repo/brain" "$cand_root/brain"
sed -i.bak "s/never sell \\\$TINY/hold it loosely/" "$cand_root/brain/CONTEXT.md"
if out="$("$eval_bin" invariants --root "$cand_root" 2>/dev/null)"; then
  fail "fails a brain whose CONTEXT dropped never-sell"
else
  check "fails a brain whose CONTEXT dropped never-sell" test "$(jq -r '.missing[0].id' <<<"$out")" = never-sell
fi
cp "$repo/brain/CONTEXT.md" "$cand_root/brain/CONTEXT.md"
check "passes it once the line is back" "$eval_bin" invariants --root "$cand_root"

if [ "$failures" -gt 0 ]; then
  printf '%d failure(s)\n' "$failures" >&2
  exit 1
fi
echo "all passed"
