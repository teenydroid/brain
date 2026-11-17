#!/usr/bin/env bash
# Tests for bin/evolve from the command line, offline: a scratch clone of this
# repository for the candidates to branch from, stand-in hands that log every
# call (hands/gh among them, which must never be called under DRY_RUN), a
# page of learnings (brain/MEMORY.md), and the real bin/eval: the changes here
# edit a playbook no eval task drafts from, so they are unmeasurable and no
# lab boots. The logic is unit-tested in hands/tests/test_evolve.py;
# this covers the wiring: the kill switch, --detach, a dry-run generation from
# the page's learnings, a learning not asked about twice, nothing to do when
# none is open, status, the halts, and the pinned jobs.
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

# This repository's HEAD, pushed into a scratch bare repository: what the
# candidates branch from. A push, not a clone, so CI's detached, shallow
# checkout works too.
git init -q --bare "$src"
git --git-dir "$src" config receive.shallowUpdate true
git -C "$repo" -c core.hooksPath=/dev/null push -q --no-verify "$src" HEAD:refs/heads/main

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

mkdir -p "$root/brain"
cat >"$root/brain/MEMORY.md" <<'PAGE'
# MEMORY.md

## people

- [m1] **ana** (x): builds a cli for crabs.

## what i have decided

- [m36] before retrying anything, check whether it already went through.
- [m44] **no likes on X, ever**: X's rules don't allow automated likes.
PAGE

unset OPENHUMAN_CORE_URL OPENHUMAN_WORKSPACE TEENY_STATE TEENY_SECRETS EVAL_HELDOUT_DIR EVOLVE
export TEENY_ROOT="$root" TEENY_HANDS="$root/hands" EVOLVE_GIT_DIR="$src" EVOLVE_BASE=main \
  FAKE_CALLS="$scratch/calls.log" TEENY_OH="$scratch/no-core" DRY_RUN=1
: >"$FAKE_CALLS"

# A proposal: one more line at the end of the moltbook-follow playbook (no eval task drafts from it).
propose() { # I LINE
  python3 - "$src" "$root/props/cand-$1.json" "$2" <<'PY'
import json, subprocess, sys
text = subprocess.run(["git", "--git-dir", sys.argv[1], "show", "main:brain/playbooks/moltbook-follow.md"],
                      check=True, capture_output=True, text=True).stdout
last = text.rstrip().splitlines()[-1]
json.dump({"title": f"morning: {sys.argv[3][:30]}", "component": "attention", "expected_move": "+1% engaged reach",
           "hypothesis": "The morning post opens the same way every day.",
           "edits": [{"path": "brain/playbooks/moltbook-follow.md", "old": last, "new": f"{last}\n\n{sys.argv[3]}"}]},
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
# Wait for it to finish before the loop is switched on below.
for _ in $(seq 1 50); do
  grep -q 'EVOLVE=0' "$root/state/evolve/evolve.log" 2>/dev/null && break
  sleep 0.2
done
check "and the detached run found the loop off" grep -q 'EVOLVE=0' "$root/state/evolve/evolve.log"

# --- a dry-run generation -----------------------------------------------------------------
printf 'EVOLVE=1\nEVOLVE_GENERATIONS_PER_DAY=24\nEVOLVE_EVALUATOR=none\nEVOLVE_PROBES=0\n' >"$root/secrets/evolve.env"
propose 1 "Open with what shipped, never with the weather."
propose 2 "Name the person who shipped it, and check it went out."
out="$("$evolve" run --candidates 2 --proposals "$root/props" 2>"$scratch/err")"
check "a generation runs" test "$(jq -r .gen <<<"$out")" = 1
check "one candidate per open learning, the newest first" test "$(jq -r '[.candidates[].learning] | join(",")' <<<"$out")" = "m44,m36"
check "never one for a person on the page" bash -c "! grep -q '\"m1\"' '$root/state/evolve/archive.jsonl'"
check "both pass the gate, unmeasurable, and would open" test "$(jq -r '[.candidates[].verdict] | join(",")' <<<"$out")" = "dry-run,dry-run"
check "the evidence is written" test -s "$root/state/evolve/1/evidence.json"
check "every candidate is archived with its learning" test "$(grep -c '"learning": {' "$root/state/evolve/archive.jsonl")" -ge 2
check "DRY_RUN says which pull request each would open" test "$(jq -r .would_open_pr.branch "$root/state/evolve/1/cand-2.pr.json")" = teeny/gen-1-c2
check "with its teeny/eval status" test "$(jq -r .would_set_status.context "$root/state/evolve/1/cand-1.pr.json")" = teeny/eval
check "and its learning in Teeny's words" grep -q 'no likes on X, ever' <<<"$(jq -r .body "$root/state/evolve/1/cand-1.pr.json")"
check "and says it is unmeasurable" grep -q 'Unmeasurable' <<<"$(jq -r .body "$root/state/evolve/1/cand-1.pr.json")"
check "and never calls hands/gh" bash -c "! grep -q '^gh ' '$FAKE_CALLS'"
check "and pushes no branch" test -z "$(git --git-dir "$src" branch --list 'teeny/*')"
check "and no worktree is left behind" test -z "$(find "$root/state/evolve/1" -maxdepth 1 -type d -name 'cand-*')"

# --- the same learnings are not asked about again --------------------------------------------
out="$("$evolve" run --proposals "$root/props" 2>/dev/null)"
check "with every learning carried out, a run does nothing" grep -q 'no open learnings' <<<"$(jq -r .skipped <<<"$out")"
check "and makes no generation" test ! -e "$root/state/evolve/2"
cat >>"$root/brain/MEMORY.md" <<'PAGE'
- [m57] no dotted tokens in outbound telegram text.
PAGE
propose 1 "Write telegram names without dots."
out="$("$evolve" run --proposals "$root/props" 2>/dev/null)"
check "a new learning is taken up at the next run" test "$(jq -r '[.candidates[].learning] | join(",")' <<<"$out")" = m57

# --- evolve-watch -------------------------------------------------------------------------------
out="$("$evolve" watch 2>/dev/null)"
check "evolve-watch with nothing merged watches nothing" test "$(jq -r .watching <<<"$out")" = null
out="$("$evolve" watch --detach 2>/dev/null)"
check "evolve-watch --detach logs to state/evolve/watch.log" test "$(jq -r .log <<<"$out")" = state/evolve/watch.log
for _ in $(seq 1 50); do
  grep -q '"watching"' "$root/state/evolve/watch.log" 2>/dev/null && break
  sleep 0.2
done
check "and the detached watch ran" grep -q '"watching": null' "$root/state/evolve/watch.log"
check "and never calls hands/gh" bash -c "! grep -q '^gh ' '$FAKE_CALLS'"

# --- status, halts, resume --------------------------------------------------------------------
cat >>"$root/brain/MEMORY.md" <<'PAGE'
- [m61] reach is a wave, commitment is a drip.
PAGE
out="$("$evolve" status 2>/dev/null)"
check "status lists the open learnings" test "$(jq -r '[.learnings.open[].id] | join(",")' <<<"$out")" = m61
check "and each candidate's state" test "$(jq -r '.candidates[0].verdict' <<<"$out")" = dry-run
check "and the open evolve pull requests" test "$(jq -r '.open_prs | length' <<<"$out")" = 0
printf 'EVOLVE=1\nEVOLVE_GENERATIONS_PER_DAY=1\nEVOLVE_EVALUATOR=none\n' >"$root/secrets/evolve.env"
out="$("$evolve" run --scheduled --proposals "$root/props" 2>/dev/null)"
check "too many scheduled generations today halts the run" test "$(jq -r .skipped <<<"$out")" = "halted: generations"
check "and under DRY_RUN files no issue" test "$(jq -r '.halts[0].dry_run' <<<"$out")" = true
check "and never calls hands/gh" bash -c "! grep -q '^gh ' '$FAKE_CALLS'"
check "resume answers" test "$("$evolve" resume 2>/dev/null | jq -r .resumed)" = true
check "a bad threshold refuses the run" bash -c "printf 'EVOLVE_CANDIDATES=99\n' >'$root/secrets/evolve.env'; ! '$evolve' run 2>/dev/null"

# --- the pinned jobs -------------------------------------------------------------------------------
jobs="$(python3 "$repo/bin/lib/cron_policy.py" "$repo/brain/cron.toml" "$repo/evals/cron.pinned.toml")"
check "evolve is pinned, after each dream, and detached" test "$(jq -r '.[] | select(.name == "teeny.evolve") | "\(.expr)|\(.tz)|\(.command)|\(.enabled)"' <<<"$jobs")" = "40 2,14 * * *|Asia/Kolkata|bin/evolve run --scheduled --detach|true"
check "each dream comes before it" test "$(jq -r '[.[] | select(.name == "teeny.dream" or .name == "teeny.dream-midday") | .expr] | sort | join(";")' <<<"$jobs")" = "0 14 * * *;0 2 * * *"
check "evolve-credit is pinned, daily" test "$(jq -r '.[] | select(.name == "teeny.evolve-credit") | "\(.expr)|\(.command)"' <<<"$jobs")" = "50 3 * * *|bin/evolve credit"
check "evolve-watch is pinned, hourly and detached" test "$(jq -r '.[] | select(.name == "teeny.evolve-watch") | "\(.expr)|\(.command)"' <<<"$jobs")" = "20 * * * *|bin/evolve watch --detach"
check "and the schedule passes seed-cron --check" env -u TEENY_ROOT "$repo/bin/seed-cron" --check --file "$repo/brain/cron.toml"

if [ "$failures" -gt 0 ]; then
  printf '%d failure(s)\n' "$failures" >&2
  exit 1
fi
echo "all passed"
