#!/usr/bin/env bash
# Tests for bin/frozen-check (bin/lib/frozen.py), the evolve gate's frozen-path
# check, against a throwaway git repository holding the real frozen list
# (evals/frozen.txt). Each case branches off the same base commit, makes one
# change, and checks the diff.
#
#   bin/test/frozen_test.sh
set -euo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
repo="$(cd "$here/../.." && pwd)"
checker="$repo/bin/frozen-check"
scratch="$(mktemp -d "${TMPDIR:-/tmp}/teeny-frozen.XXXXXX")"
trap 'rm -rf "$scratch"' EXIT

failures=0
pass() { printf 'ok   %s\n' "$1"; }
fail() { printf 'FAIL %s\n' "$1" >&2; failures=$((failures + 1)); }
check() { local name="$1"; shift; if "$@"; then pass "$name"; else fail "$name"; fi; }

# The fixture: a sliver of the real layout, with the real frozen list.
fx="$scratch/repo"
g() { git -C "$fx" -c user.name=test -c user.email=test@example.invalid -c commit.gpgsign=false "$@"; }
mkdir -p "$fx"
g init -q -b main
put() { mkdir -p "$fx/$(dirname "$1")"; printf '%s\n' "${2:-x}" >"$fx/$1"; }
mkdir -p "$fx/evals"
cp "$repo/evals/frozen.txt" "$fx/evals/frozen.txt"
for f in bin/act bin/lib/teeny.sh evals/judge.md evals/tasks/morning.toml spine/src/main.rs \
  deploy/compose.yaml Cargo.lock Cargo.toml .github/workflows/check.yml DIRECTIVE.md \
  hands/guard hands/x hands/moltbook hands/lib/teeny_hands/hand.py \
  hands/lib/teeny_hands/guard/__init__.py hands/lib/teeny_hands/x/post.py \
  brain/SOUL.md brain/agents/act.toml brain/agents/act.md brain/agents/judge.toml \
  brain/agents/teeny.toml brain/agents/teeny.md brain/agents/builder.toml brain/agents/operator.toml \
  brain/agents/teeny-mentions.toml brain/agents/teeny-mail.toml brain/playbooks/morning.md; do
  put "$f"
done
g add -A
g commit -q -m base
base="$(g rev-parse HEAD)"

case_n=0
branch() { case_n=$((case_n + 1)); g switch -q -c "case-$case_n" "$base"; }
commit() { g add -A && g commit -q -m change; }
run() { "$checker" --root "$fx" "$@" >"$scratch/out" 2>"$scratch/err"; }
# PASSES NAME: the checker exits 0 for base..HEAD.
passes() {
  if run "$base" HEAD; then pass "$1"; else fail "$1 (exit $?: $(tr '\n' ' ' <"$scratch/err"))"; fi
}
# REFUSES NAME PATH...: the checker exits 1 for base..HEAD and names every PATH.
refuses() {
  local name="$1" code=0 p; shift
  run "$base" HEAD || code=$?
  if [ "$code" != 1 ]; then fail "$name (exit $code, wanted 1: $(tr '\n' ' ' <"$scratch/err"))"; return; fi
  for p in "$@"; do
    if ! grep -qF -- " $p " "$scratch/err"; then fail "$name (did not name $p: $(tr '\n' ' ' <"$scratch/err"))"; return; fi
  done
  pass "$name"
}

# --- what Teeny may change -----------------------------------------------------
branch
put brain/SOUL.md "a new soul"
put brain/playbooks/night.md
put brain/agents/teeny.md "a sharper voice"
put brain/agents/act.md "draft better"
put brain/agents/scout.toml "id = 'scout'"
commit
passes "an edit to the brain, an agent's prompt, or a new agent passes"

branch
put hands/sparrow
put hands/lib/teeny_hands/sparrow/__init__.py
put hands/tests/test_sparrow.py
commit
passes "a new hand passes"

branch
put hands/guardian
put hands/xylophone
put hands/lib/teeny_hands/guardian/__init__.py
put bin2/tool
put brain/Cargo.toml
commit
passes "names that only start like a frozen path pass"

branch
put hands/moltbook "a better wrapper"
commit
passes "an edit to a hand that is not guard or x passes"

# --- the frozen paths ----------------------------------------------------------
branch
put bin/act "loosened"
commit
refuses "an edit under bin/ is refused" bin/act

branch
g rm -q evals/judge.md
commit
refuses "a deletion under evals/ is refused" evals/judge.md

branch
g mv hands/guard brain/guard
commit
refuses "a rename out of a frozen path is refused" hands/guard

branch
g mv brain/playbooks/morning.md evals/tasks/morning-copy.md
commit
refuses "a rename into a frozen path is refused" evals/tasks/morning-copy.md

branch
put DIRECTIVE.md "no fences"
commit
refuses "DIRECTIVE.md is refused" DIRECTIVE.md

branch
put brain/agents/act.toml "named = ['shell']"
put brain/agents/judge.toml "model = 'mine'"
commit
refuses "the act and judge agents are refused" brain/agents/act.toml brain/agents/judge.toml

branch
for a in teeny builder operator teeny-mentions teeny-mail; do put "brain/agents/$a.toml" "named = ['shell']"; done
commit
refuses "the agent definitions that say they are frozen are refused" brain/agents/teeny.toml \
  brain/agents/builder.toml brain/agents/operator.toml brain/agents/teeny-mentions.toml brain/agents/teeny-mail.toml

branch
put hands/lib/teeny_hands/guard/screen/deep/rules.py
put evals/tasks/a/b/c.toml
put .github/workflows/new.yml
put hands/x "posts anything"
commit
refuses "nested paths under frozen globs are refused" \
  hands/lib/teeny_hands/guard/screen/deep/rules.py evals/tasks/a/b/c.toml .github/workflows/new.yml hands/x

branch
put hands/lib/teeny_hands/hand.py "no contract"
put Cargo.lock "pinned elsewhere"
put spine/src/main.rs "fn main() {}"
put deploy/compose.yaml "services: {}"
put secrets/teeny.env
commit
refuses "every frozen path touched is named, not just the first" \
  hands/lib/teeny_hands/hand.py Cargo.lock spine/src/main.rs deploy/compose.yaml secrets/teeny.env

branch
put brain/SOUL.md "allowed"
put bin/act "not allowed"
commit
run "$base" HEAD || true
check "an allowed path is not named alongside a refused one" sh -c "! grep -qF ' brain/SOUL.md ' '$scratch/err'"

# --- the list is the base's, so a candidate cannot edit its way out ---------------
branch
grep -v '^bin/' "$fx/evals/frozen.txt" >"$scratch/list" && cp "$scratch/list" "$fx/evals/frozen.txt"
put bin/act "loosened"
commit
refuses "a candidate that drops a pattern from the list is still refused" evals/frozen.txt bin/act

# --- before a commit: the working tree against the base ---------------------------
branch
put bin/act "staged"
g add bin/act
put evals/tasks/new.toml
put brain/SOUL.md "allowed"
code=0
run "$base" || code=$?
check "an uncommitted edit and an untracked file are refused" test "$code" = 1
check "names the uncommitted edit" grep -qF ' bin/act ' "$scratch/err"
check "names the untracked file" grep -qF ' evals/tasks/new.toml ' "$scratch/err"
g reset -q --hard && g clean -qfd

branch
put brain/SOUL.md "allowed"
put brain/playbooks/new.md
check "a clean working tree of allowed edits passes" run "$base"

# --- the same check from Python, for bin/evolve --------------------------------------
branch
put bin/act "loosened"
commit
check "frozen.check() names what the CLI names" python3 - "$repo/bin/lib" "$fx" "$base" <<'PY'
import sys
sys.path.insert(0, sys.argv[1])
import frozen
hits = frozen.check(sys.argv[2], sys.argv[3], "HEAD")
assert [(h.status, h.path, h.pattern) for h in hits] == [("M", "bin/act", "bin/**")], hits
assert frozen.check(sys.argv[2], sys.argv[3], sys.argv[3]) == []
PY

# --- a list of paths, no git ---------------------------------------------------------
code=0
"$checker" --files brain/SOUL.md ./bin/act brain/../evals/judge.md >"$scratch/out" 2>"$scratch/err" || code=$?
check "--files refuses frozen paths" test "$code" = 1
check "--files names them normalised" sh -c "grep -qF ' bin/act ' '$scratch/err' && grep -qF ' evals/judge.md ' '$scratch/err'"
check "--files passes allowed paths" "$checker" --files brain/SOUL.md hands/lib/teeny_hands/sparrow/x.py 2>/dev/null

# --- it fails closed ----------------------------------------------------------------
code=0
run no-such-ref HEAD || code=$?
check "an unknown ref is an error, not a pass" test "$code" = 2

g switch -q -c no-list "$base"
g rm -q evals/frozen.txt
commit
nolist="$(g rev-parse HEAD)"
put bin/act "loosened"
commit
code=0
run "$nolist" HEAD || code=$?
check "a base with no frozen list is an error, not a pass" test "$code" = 2

code=0
"$checker" >"$scratch/out" 2>"$scratch/err" || code=$?
check "no arguments is a usage error" test "$code" = 2

if [ "$failures" -gt 0 ]; then
  printf '%d failure(s)\n' "$failures" >&2
  exit 1
fi
echo "all passed"
