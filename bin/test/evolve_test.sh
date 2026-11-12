#!/usr/bin/env bash
# Tests for bin/evolve from the command line, offline: a scratch clone of this
# repository for the candidates to branch from, stand-in hands that log every
# call (hands/gh among them, which must never be called under DRY_RUN), and a
# stand-in core whose `act` turns draft in the voice of whichever core URL
# they reach and whose `judge` turns prefer the candidate's drafts. The logic
# is unit-tested in hands/tests/test_evolve.py; this covers the wiring: the
# kill switch, --detach, a dry-run generation, a held generation measured on a
# "lab core" URL and then selected, the held-out tasks kept out of what is
# published, and the pinned jobs.
#
#   bin/test/evolve_test.sh
set -euo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
repo="$(cd "$here/../.." && pwd)"
evolve="$repo/bin/evolve"
scratch="$(mktemp -d "${TMPDIR:-/tmp}/teeny-evolve.XXXXXX")"
src="$scratch/src.git"
cleanup() {
  git --git-dir "$src" worktree list --porcelain 2>/dev/null | awk '/^worktree /{print $2}' | while read -r tree; do
    [ "$tree" = "$src" ] || git --git-dir "$src" worktree remove --force "$tree" 2>/dev/null || true
  done
  rm -rf "$scratch"
}
trap cleanup EXIT

failures=0
pass() { printf 'ok   %s\n' "$1"; }
fail() { printf 'FAIL %s\n' "$1" >&2; failures=$((failures + 1)); }
check() { local name="$1"; shift; if "$@"; then pass "$name"; else fail "$name"; fi; }

# This repository as committed, cloned bare: what candidates branch from.
git clone -q --bare "$repo" "$src"

root="$scratch/root"
mkdir -p "$root/secrets" "$root/state" "$root/hands" "$root/props"
for name in world build guard gh; do
  cat >"$root/hands/$name" <<'HAND'
#!/usr/bin/env bash
printf '%s %s\n' "$(basename "$0")" "$*" >>"$FAKE_CALLS"
case "$(basename "$0") $1" in
  "build jobs") echo '{"jobs": []}' ;;
  "guard holds") echo '{"holds": []}' ;;
  *) exit 1 ;;
esac
HAND
  chmod +x "$root/hands/$name"
done

cat >"$scratch/fake-oh" <<'FAKE'
#!/usr/bin/env bash
set -euo pipefail
[ "$1" = inference_agent_chat ] || { echo "unexpected method $1" >&2; exit 1; }
printf '%s\n' "$2" >>"$FAKE_CORE_CALLS"
python3 -I - "$2" <<'PY'
import json, os, re, sys
params = json.loads(sys.argv[1])
message, agent = params["message"], params.get("agent_id")
who = "CAND" if "lab" in os.environ.get("OH_URL", "") else "base"
if agent == "act":
    task = re.search(r"^# eval: (\S+)", message, re.M).group(1)
    answer = json.dumps({"text": f"gm {task} from the {who}"})
elif agent == "judge":
    drafts = dict(re.findall(r"^## draft ([12])\n\n```text\n(.*?)\n```", message, re.M | re.S))
    hits = [k for k in ("1", "2") if "CAND" in drafts.get(k, "")]
    answer = json.dumps({"better": hits[0] if len(hits) == 1 else "tie", "breaks": {},
                         "notes": "the CAND draft is warmer"})
else:
    sys.exit(f"unexpected agent {agent}")
print(json.dumps({"response": answer}))
PY
FAKE
chmod +x "$scratch/fake-oh"

unset OPENHUMAN_CORE_URL OPENHUMAN_WORKSPACE TEENY_STATE TEENY_SECRETS EVAL_HELDOUT_DIR EVOLVE
export TEENY_ROOT="$root" TEENY_HANDS="$root/hands" EVOLVE_GIT_DIR="$src" EVOLVE_BASE=HEAD \
  FAKE_CALLS="$scratch/calls.log" FAKE_CORE_CALLS="$scratch/core.log" TEENY_OH="$scratch/fake-oh" DRY_RUN=1
: >"$FAKE_CALLS"
: >"$FAKE_CORE_CALLS"

# A proposal: one more line at the end of the morning playbook.
propose() { # I LINE
  python3 - "$src" "$root/props/cand-$1.json" "$2" <<'PY'
import json, subprocess, sys
text = subprocess.run(["git", "--git-dir", sys.argv[1], "show", "HEAD:brain/playbooks/morning.md"],
                      check=True, capture_output=True, text=True).stdout
last = text.rstrip().splitlines()[-1]
json.dump({"title": f"morning: {sys.argv[3][:30]}", "component": "attention", "expected_move": "+1% engaged reach",
           "hypothesis": "The morning post opens the same way every day.",
           "edits": [{"path": "brain/playbooks/morning.md", "old": last, "new": f"{last}\n\n{sys.argv[3]}"}]},
          open(sys.argv[2], "w"))
PY
}

# --- off by default ----------------------------------------------------------------------
out="$("$evolve" run --proposals "$root/props" 2>/dev/null)"
check "without secrets/evolve.env the loop is off" grep -q 'EVOLVE=0' <<<"$out"
check "and makes no generation" test ! -e "$root/state/evolve/1"
out="$("$evolve" run --detach 2>/dev/null)"
check "--detach returns at once" test "$(jq -r .kind <<<"$out")" = started
check "and logs to state/evolve/evolve.log" test "$(jq -r .log <<<"$out")" = state/evolve/evolve.log

# --- a dry-run generation -----------------------------------------------------------------
printf 'EVOLVE=1\nEVOLVE_GENERATIONS_PER_DAY=24\nEVOLVE_EVAL_SAMPLES=1\nEVOLVE_MAX_WINRATE_JUMP=0.6\n' >"$root/secrets/evolve.env"
propose 1 "Open with what shipped, never with the weather."
propose 2 "Name the person who shipped it."
out="$("$evolve" run --candidates 2 --proposals "$root/props" 2>"$scratch/err")"
check "a generation runs" test "$(jq -r .gen <<<"$out")" = 1
check "both candidates pass the gate, unevaluated" test "$(jq -r '[.candidates[].verdict] | join(",")' <<<"$out")" = "unevaluated,unevaluated"
check "the evidence is written" test -s "$root/state/evolve/1/evidence.json"
check "every candidate is archived" test "$(grep -c '"id": "g1c' "$root/state/evolve/archive.jsonl")" = 2
check "DRY_RUN opens no pull request" test "$(jq -r .dry_run <<<"$out")" = true
check "but says which it would" test "$(jq -r .would_open_pr.branch <<<"$out")" = teeny/gen-1
check "and never calls hands/gh" bash -c "! grep -q '^gh ' '$FAKE_CALLS'"
check "and pushes no branch" test -z "$(git --git-dir "$src" branch --list 'teeny/*')"
check "the pull request says it is unevaluated" grep -q 'Unevaluated' <<<"$(jq -r .body <<<"$out")"

# --- a held generation, measured on a lab core ------------------------------------------------
mkdir -p "$root/secrets/heldout"
cat >"$root/secrets/heldout/hold-lighthouse.toml" <<'TOML'
description = "a held-out reply about the lighthouse"
kind = "reply"
prompt = '''Reply to the keeper asking about the tide clock.'''
[inputs]
mention = {id = "9", author = "keeper", text = "tide?"}
TOML
propose 1 "Thank one person by name."
out="$("$evolve" run --candidates 1 --proposals "$root/props" --hold 2>"$scratch/err")"
check "--hold stops after the gate" test "$(jq -r '.held | length > 0' <<<"$out")" = true
check "and keeps the candidate's worktree" test -d "$root/state/evolve/2/cand-1/brain"
out="$("$evolve" evaluate 2 1 --oh-url-candidate http://lab.invalid:7788 2>"$scratch/err")"
check "evaluate measures it on the lab core" test "$(jq -r .verdict <<<"$out")" = won
check "with the visible tasks' win rate" test "$(jq -r .scores.win_rate <<<"$out")" = 1
check "and the held-out tasks apart" test "$(jq -r .scores.heldout.wins <<<"$out")" = 1
check "the baseline drafted on the live core" test "$(jq -r .draft.text "$root/state/evolve/2/eval/base/drafts.jsonl" | grep -c 'from the base')" -gt 0
check "the candidate on the lab core" test "$(jq -r .draft.text "$root/state/evolve/2/eval/cand-1/drafts.jsonl" | grep -c 'from the CAND')" -gt 0
check "and the judge read main's soul" test -s "$root/state/evolve/2/judge-root/brain/SOUL.md"
out="$("$evolve" select 2 2>"$scratch/err")"
body="$(jq -r .body <<<"$out")"
check "select picks the measured winner" test "$(jq -r .selected <<<"$out")" = g2c1
check "its pull request carries the compare report" grep -q 'Compare report' <<<"$body"
check "and the held-out totals" grep -q 'Held-out tasks' <<<"$body"
check "but never a held-out task" bash -c "! grep -qiE 'lighthouse|hold-lighthouse|tide clock' <<<\"\$1\"" _ "$body"
check "select twice opens nothing more" grep -q 'already opened' <<<"$("$evolve" select 2 2>/dev/null)"

# --- the next generation's evidence ----------------------------------------------------------
"$evolve" run --candidates 1 --proposals "$root/props" >/dev/null 2>&1 || true
evidence="$root/state/evolve/3/evidence.json"
check "the next evidence carries the judge's notes" grep -q 'the CAND draft is warmer' "$evidence"
check "and none about a held-out task" bash -c "! grep -qiE 'lighthouse|tide clock' '$evidence'"

# --- status, halts, resume --------------------------------------------------------------------
out="$("$evolve" status 2>/dev/null)"
check "status lists the parent weights" test "$(jq -r '.weights | has("main")' <<<"$out")" = true
printf 'EVOLVE=1\nEVOLVE_GENERATIONS_PER_DAY=1\n' >"$root/secrets/evolve.env"
out="$("$evolve" run --proposals "$root/props" 2>/dev/null)"
check "too many generations today halts the run" test "$(jq -r .skipped <<<"$out")" = "halted: generations"
check "and under DRY_RUN files no issue" test "$(jq -r '.halts[0].dry_run' <<<"$out")" = true
check "and never calls hands/gh" bash -c "! grep -q '^gh ' '$FAKE_CALLS'"
check "resume answers" test "$("$evolve" resume 2>/dev/null | jq -r .resumed)" = true
check "a bad threshold refuses the run" bash -c "printf 'EVOLVE_CANDIDATES=99\n' >'$root/secrets/evolve.env'; ! '$evolve' run 2>/dev/null"

# --- the pinned jobs -------------------------------------------------------------------------------
jobs="$(python3 "$repo/bin/lib/cron_policy.py" "$repo/brain/cron.toml" "$repo/evals/cron.pinned.toml")"
check "evolve is pinned, daily and detached" test "$(jq -r '.[] | select(.name == "teeny.evolve") | "\(.expr)|\(.command)|\(.enabled)"' <<<"$jobs")" = "30 3 * * *|bin/evolve run --detach|true"
check "evolve-credit is pinned, daily" test "$(jq -r '.[] | select(.name == "teeny.evolve-credit") | "\(.expr)|\(.command)"' <<<"$jobs")" = "50 3 * * *|bin/evolve credit"
check "and the schedule passes seed-cron --check" "$repo/bin/seed-cron" --check

if [ "$failures" -gt 0 ]; then
  printf '%d failure(s)\n' "$failures" >&2
  exit 1
fi
echo "all passed"
