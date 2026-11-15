#!/usr/bin/env bash
# Tests for `bin/evolve gate` (bin/lib/evolve_gate.py). The base is this
# repository's tracked files at HEAD, committed as `main` of a throwaway git
# repository, so the real seed-cron, doctor and invariants pass on it; each
# candidate is that commit exported with `git archive` (the gate reads main
# with --git-dir), or, where a case needs the candidate's own repository, a
# copy of it, changed in one way. Every case asserts the exit code and which
# rules refused[].rule names. No core and no model; nothing here reaches the
# network.
#
# The cases are independent, each in a directory of its own, and run up to
# one per CPU at once; their ok/FAIL lines are printed in order once all are
# done. A gate run takes about 5 s: rule 8 runs only a new hand's own tests.
#
#   bin/test/evolve_gate_test.sh
set -euo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
repo="$(cd "$here/../.." && pwd)"
evolve="$repo/bin/evolve"
# Resolved (pwd -P): the doctor's keyring check walks every component, and a
# symlinked temp root (macOS /var) would fail it for the wrong reason.
scratch="$(cd "$(mktemp -d "${TMPDIR:-/tmp}/teeny-gate.XXXXXX")" && pwd -P)"
# shellcheck disable=SC2046 # one pid per word
trap 'kill $(jobs -p) 2>/dev/null || true; wait 2>/dev/null || true; rm -rf "$scratch"' EXIT

failures=0
pass() { printf 'ok   %s\n' "$1"; }
fail() { printf 'FAIL %s\n' "$1" >&2; failures=$((failures + 1)); }
check() { local name="$1"; shift; if "$@"; then pass "$name"; else fail "$name"; fi; }
jqe() { jq -e "$@" >/dev/null; }

unset OH_URL OPENHUMAN_CORE_TOKEN OPENHUMAN_CORE_URL OPENHUMAN_WORKSPACE CALENDAR_TZ \
  TEENY_ROOT TEENY_BRAIN TEENY_STORAGE TEENY_SECRETS TEENY_STATE TEENY_HANDS
g() { git -c user.name=gate-test -c user.email=gate-test@localhost -c commit.gpgSign=false -c core.hooksPath=/dev/null "$@"; }

base="$scratch/base"
mkdir -p "$base"
git -C "$repo" archive HEAD | tar -x -C "$base"
g -C "$base" -c init.defaultBranch=main init -q
g -C "$base" add -A
# The submodule's pin, which an archive leaves as an empty directory.
git -C "$repo" ls-tree -z HEAD vendor/ | while IFS= read -r -d '' entry; do
  [ "${entry%% *}" = 160000 ] || continue
  info="${entry%%	*}" where="${entry#*	}"
  g -C "$base" update-index --add --cacheinfo "160000,${info##* },$where"
done
g -C "$base" commit -q -m base

# Each case sets cand, out and err (run_case, below). `fresh` exports main into
# CAND, which has no .git, so the gate reads main from the base's repository;
# `fresh_repo` copies the base's repository, for a case about the candidate's
# own git.
gitdir=""
fresh() {
  rm -rf "$cand"
  mkdir -p "$cand"
  git -C "$base" archive main | tar -x -C "$cand"
  gitdir="$base/.git"
  playbook="$cand/brain/playbooks/roast.md" lore="$cand/brain/narrative/LORE.md"
}
fresh_repo() {
  rm -rf "$cand"
  cp -R "$base" "$cand"
  gitdir=""
  playbook="$cand/brain/playbooks/roast.md" lore="$cand/brain/narrative/LORE.md"
}
code=0
gate() {
  code=0
  if [ -n "$gitdir" ]; then
    "$evolve" gate "$cand" --git-dir "$gitdir" "$@" >"$out" 2>"$err" || code=$?
  else
    "$evolve" gate "$cand" "$@" >"$out" 2>"$err" || code=$?
  fi
}
rules() { jq -c '[.refused[]?.rule] | unique' "$out" 2>/dev/null || head -c 300 "$out"; }
why() { jq -r '[.refused[]? | "\(.rule): \(.detail)"] | join(" / ")' "$out" 2>/dev/null | head -c 600 || true; }
passes() { # NAME: exit 0, nothing refused
  gate
  if [ "$code" = 0 ] && jq -e '.ok == true and .refused == []' "$out" >/dev/null; then
    pass "$1"
  else
    fail "$1 (exit $code: $(why) $(cat "$err"))"
  fi
}
refuses() { # NAME RULE: exit 2, RULE among refused[].rule
  gate
  if [ "$code" = 2 ] && jq -e --arg r "$2" 'any(.refused[]; .rule == $r)' "$out" >/dev/null; then
    pass "$1"
  else
    fail "$1 (exit $code, wanted $2, got $(rules): $(why))"
  fi
}
lines() { # N FILE: append N numbered lines
  local i=1
  while [ "$i" -le "$1" ]; do printf 'gate line %d\n' "$i"; i=$((i + 1)); done >>"$2"
}
# A new hand in all three places. MODE `two` prints two JSON objects;
# `untested` has no test file.
add_hand() {
  local name="$1" mode="${2:-}"
  mkdir -p "$cand/hands/lib/teeny_hands/$name"
  if [ "$mode" = two ]; then
    cat >"$cand/hands/$name" <<HAND
#!/usr/bin/env -S python3 -I
"""hands/$name: says ok twice."""
import json
print(json.dumps({"ok": True}))
print(json.dumps({"ok": True}))
HAND
  else
    cat >"$cand/hands/$name" <<HAND
#!/usr/bin/env -S python3 -I
"""hands/$name: answers ping."""
import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(os.path.realpath(__file__)), "lib"))
from teeny_hands.$name.cli import main
sys.exit(main(sys.argv[1:]))
HAND
  fi
  chmod +x "$cand/hands/$name"
  : >"$cand/hands/lib/teeny_hands/$name/__init__.py"
  cat >"$cand/hands/lib/teeny_hands/$name/cli.py" <<CLI
"""The verbs of hands/$name."""

from __future__ import annotations

import argparse

from .. import hand


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(prog="hands/$name")
    verbs = root.add_subparsers(dest="verb")
    verbs.add_parser("ping").set_defaults(handler=lambda a, _: {"pong": True}, takes_body=False)
    return root


def main(argv: list[str]) -> int:
    return hand.run("$name", parser(), argv)
CLI
  [ "$mode" = untested ] && return
  cat >"$cand/hands/tests/test_$name.py" <<TEST
"""hands/$name exists and is executable."""

import os
import unittest
from pathlib import Path

HANDS = Path(__file__).resolve().parents[1]


class GateFixtureHandTest(unittest.TestCase):
    def test_the_entry_point_is_executable(self):
        self.assertTrue(os.access(HANDS / "$name", os.X_OK))
TEST
}
# sha256 over every path below DIR (.git left out, or only .git with `git`):
# its mode, mtime, a symlink's target and a file's bytes.
digest() {
  python3 -I - "$@" <<'PY'
import hashlib, os, stat, sys
root, only_git = sys.argv[1], len(sys.argv) > 2
top = os.path.join(root, ".git") if only_git else root
h = hashlib.sha256()
for d, dirs, files in os.walk(top):
    if d == root and ".git" in dirs:
        dirs.remove(".git")
    dirs.sort()
    for name in sorted(dirs + files):
        path = os.path.join(d, name)
        st = os.lstat(path)
        h.update(f"{os.path.relpath(path, root)}\0{st.st_mode:o}\0{st.st_mtime_ns}\0".encode())
        if stat.S_ISLNK(st.st_mode):
            h.update(os.readlink(path).encode())
        elif stat.S_ISREG(st.st_mode):
            with open(path, "rb") as f:
                h.update(f.read())
print(h.hexdigest())
PY
}

# The cases, run in the order listed here (CASES), each a function.
CASES=()
case_() { CASES+=("$1"); }

# --- what passes ------------------------------------------------------------------
case_ c_unchanged
c_unchanged() {
  fresh_repo
  passes "an unchanged candidate passes"
  check "it prints the base it compared with" test "$(jq -r .base "$out")" = "$(git -C "$base" rev-parse main)"
  check "it lists every rule's check" test "$(jq -r '[.checks[].rule] | join(",")' "$out")" = \
    "frozen-path,path-escape,seed-cron,doctor,invariants,diff-size,soul-context-size,hand-test,hand-contract,hand-unit-tests,shellcheck"
}

case_ c_clean
c_clean() {
  fresh
  printf '\nAnd one more thing the gate test says.\n' >>"$playbook"
  printf -- '- Teeny once gated itself and liked it.\n' >>"$lore"
  passes "a clean candidate (a playbook edit, one line of lore) passes"
  check "it counts that diff" test "$(jq -r '"\(.lines) \(.files)"' "$out")" = "3 2"
}

case_ c_diff_400
c_diff_400() {
  fresh
  lines 400 "$cand/brain/narrative/gate-400.md"
  passes "a 400-line diff passes"
  check "it counts 400 lines" test "$(jq -r .lines "$out")" = 400
}

case_ c_soul_60
c_soul_60() {
  fresh
  lines 60 "$cand/brain/SOUL.md"
  passes "a 60-line SOUL.md diff passes"
  check "it counts 60 SOUL+CONTEXT lines" test "$(jq -r .soul_context_lines "$out")" = 60
}

# A new hand with its test, and the gate leaves the candidate as it was: this
# run executes the candidate's code (the hand, the doctor, the unit tests).
case_ c_new_hand
c_new_hand() {
  fresh_repo
  add_hand gatecheck
  local before before_git
  before="$(digest "$cand")" before_git="$(digest "$cand" git)"
  passes "a new hand with its test file that keeps the contract passes"
  check "the gate ran the hand and the unit tests" \
    jqe '[.checks[] | select(.rule == "hand-contract" or .rule == "hand-unit-tests") | .ok] == [true, true]' "$out"
  check "and ran only that hand's test" \
    jqe '.checks[] | select(.rule == "hand-unit-tests") | .detail | test("Ran 1 test\\b")' "$out"
  check "the candidate is byte-for-byte unchanged" test "$(digest "$cand")" = "$before"
  check "the candidate's .git is unchanged" test "$(digest "$cand" git)" = "$before_git"
}

# --- one refusal per rule ---------------------------------------------------------
case_ c_bin_act
c_bin_act() {
  fresh
  printf '# gate test\n' >>"$cand/bin/act"
  refuses "refuses an edit to bin/act" frozen-path
}

case_ c_invariants_md
c_invariants_md() {
  fresh
  printf '\n- never-sell | SOUL.md | gate test\n' >>"$cand/evals/invariants.md"
  refuses "refuses an edit to evals/invariants.md" frozen-path
}

case_ c_judge_toml
c_judge_toml() {
  fresh
  printf '\n# gate test\n' >>"$cand/brain/agents/judge.toml"
  refuses "refuses an edit to brain/agents/judge.toml" frozen-path
}

# hands/lib/teeny_hands/lore/ is not in evals/frozen.txt: the allowlist alone
# freezes every existing file of the hand library.
case_ c_hand_lib
c_hand_lib() {
  fresh
  printf '\n# gate test\n' >>"$cand/hands/lib/teeny_hands/lore/cli.py"
  refuses "refuses an edit to an existing file in hands/lib/teeny_hands/" frozen-path
  check "and runs none of its code" \
    jqe '[.checks[] | select(.rule == "doctor" or .rule == "hand-contract" or .rule == "hand-unit-tests") | .ok] == [null, null, null]' "$out"
}

case_ c_symlink
c_symlink() {
  fresh
  ln -s ../../bin/act "$cand/brain/playbooks/x.md"
  refuses "refuses a symlink from brain/playbooks/x.md to ../../bin/act" path-escape
}

case_ c_rename
c_rename() {
  fresh
  mkdir -p "$cand/spine"
  mv "$cand/brain/playbooks/speculate.md" "$cand/spine/speculate.md"
  refuses "refuses a playbook renamed into spine/" path-escape
  check "and says it was a rename" jqe 'any(.refused[]; .detail | test("rename"))' "$out"
}

case_ c_delete_guard
c_delete_guard() {
  fresh
  rm "$cand/hands/guard"
  refuses "refuses deleting hands/guard" path-escape
}

case_ c_chmod
c_chmod() {
  fresh
  chmod +x "$cand/brain/CONTEXT.md"
  refuses "refuses chmod +x on a brain file" path-escape
}

case_ c_cron
c_cron() {
  fresh
  printf '\n[jobs.gate-every-minute]\nschedule = "* * * * *"\ncommand = "bin/heartbeat"\n' >>"$cand/brain/cron.toml"
  refuses "refuses a cron job that runs every minute" seed-cron
}

case_ c_anchor
c_anchor() {
  fresh
  grep -iv 'never sell' "$base/brain/SOUL.md" >"$cand/brain/SOUL.md"
  refuses 'refuses removing the "never sell" anchor from SOUL.md' invariants
}

case_ c_diff_401
c_diff_401() {
  fresh
  lines 401 "$cand/brain/narrative/gate-401.md"
  refuses "refuses a 401-line diff" diff-size
}

case_ c_soul_61
c_soul_61() {
  fresh
  lines 61 "$cand/brain/SOUL.md"
  refuses "refuses a 61-line SOUL.md diff" soul-context-size
}

case_ c_untested
c_untested() {
  fresh
  add_hand gatebare untested
  refuses "refuses a new hand without a test" hand-test
}

# A test file with no test in it is no test. (The bin/act edit keeps the
# unit tests from running: hand-test reads the file, it never runs it.)
case_ c_empty_test
c_empty_test() {
  fresh
  add_hand gateempty
  printf '"""Nothing here."""\n' >"$cand/hands/tests/test_gateempty.py"
  printf '# gate test\n' >>"$cand/bin/act"
  refuses "refuses a new hand whose test file defines no test" hand-test
}

case_ c_two_objects
c_two_objects() {
  fresh
  add_hand gatetwice two
  refuses "refuses a new hand that prints two JSON objects" hand-contract
}

# Rule 8 runs the new hand's own test, and a failing one is refused.
case_ c_failing_test
c_failing_test() {
  fresh
  add_hand gatefails
  cat >"$cand/hands/tests/test_gatefails.py" <<'TEST'
"""hands/gatefails: a test that fails."""

import unittest


class GateFixtureFailingTest(unittest.TestCase):
    def test_the_gate_fixture_fails(self):
        self.fail("the gate fixture's test fails")
TEST
  refuses "refuses a new hand whose own test fails" hand-unit-tests
  check "and only for that" test "$(rules)" = '["hand-unit-tests"]'
  check "naming the failing test and its file" jqe 'any(.refused[]; .rule == "hand-unit-tests"
    and (.detail | test("FAIL: test_the_gate_fixture_fails")) and .paths == ["hands/tests/test_gatefails.py"])' "$out"
}

# bin/doctor --root loads every playbook against the candidate's hands.
case_ c_ghost_hand
c_ghost_hand() {
  fresh
  python3 -I - "$playbook" <<'PY'
import sys
path = sys.argv[1]
text = open(path).read()
head, sep, body = text.partition("+++\n")[2].partition("+++\n")
open(path, "w").write('+++\n' + head + '\n[[inputs]]\nname = "ghost"\nrun = "hands/no-such-hand list"\n' + sep + body)
PY
  refuses "refuses a playbook that names a hand that doesn't exist" doctor
  check "the doctor says which hand" jqe 'any(.refused[]; .rule == "doctor" and (.detail | test("no-such-hand")))' "$out"
}

# --- every broken rule is listed ------------------------------------------------------
case_ c_three_rules
c_three_rules() {
  fresh
  chmod +x "$cand/brain/CONTEXT.md"
  printf '\n[jobs.gate-every-minute]\nschedule = "* * * * *"\ncommand = "bin/heartbeat"\n' >>"$cand/brain/cron.toml"
  lines 401 "$cand/brain/narrative/gate-401.md"
  gate
  check "a candidate that breaks three rules exits 2" test "$code" = 2
  check "and lists exactly those three" test "$(rules)" = '["diff-size","path-escape","seed-cron"]'
}

# --- the base comes from git, never the candidate's files -------------------------------
case_ c_own_invariants
c_own_invariants() {
  fresh
  grep -iv 'never sell' "$base/brain/SOUL.md" >"$cand/brain/SOUL.md"
  grep -iv 'never sell' "$base/evals/invariants.md" >"$cand/evals/invariants.md"
  refuses "a candidate that drops an anchor from its own invariants.md is still held to main's" invariants
}

# --base: a later commit that already has the change compares clean.
case_ c_base_ref
c_base_ref() {
  fresh_repo
  printf '\nA line on another branch.\n' >>"$playbook"
  (cd "$cand" && g checkout -q -b next && g commit -qam next)
  gate --base next
  check "--base REF compares with REF" test "$code:$(jq -r '"\(.lines) \(.base)"' "$out")" = "0:0 $(git -C "$cand" rev-parse next)"
  gate
  check "and main is the default" test "$code:$(jq -r .lines "$out")" = "0:2"
  gate --base no-such-ref
  check "an unknown base is the gate's own error (exit 1)" test "$code" = 1
  check "with one JSON error" jqe '.ok == false and (.error | test("no-such-ref"))' "$out"
}

# --- the gaps around the allowlist ------------------------------------------------------
case_ c_case_fold
c_case_fold() {
  fresh
  mv "$cand/brain/playbooks/speculate.md" "$cand/brain/playbooks/Speculate.md"
  refuses "refuses a path that differs from a base path only in case" path-escape
}

# Ignored and untracked files count: a candidate can't hide one behind .gitignore.
case_ c_ignored
c_ignored() {
  fresh
  mkdir -p "$cand/brain/skills/__pycache__"
  printf 'seen\n' >"$cand/brain/skills/__pycache__/seen.md"
  passes "an ignored file in an allowed tree passes"
  check "and is in the diff" test "$(jq -r '"\(.files) \(.lines)"' "$out")" = "1 1"
}

case_ c_binary
c_binary() {
  fresh
  printf 'x\0y\n' >"$cand/brain/playbooks/blob.md"
  refuses "refuses a binary file" path-escape
}

case_ c_gitattributes
c_gitattributes() {
  fresh
  printf '* -diff\n' >"$cand/brain/playbooks/.gitattributes"
  refuses "refuses a git metadata file in an allowed tree" path-escape
}

case_ c_nested_git
c_nested_git() {
  fresh
  mkdir -p "$cand/brain/skills/nested"
  g -C "$cand/brain/skills/nested" init -q
  refuses "refuses a nested git repository" path-escape
}

case_ c_fifo
c_fifo() {
  fresh
  mkfifo "$cand/brain/playbooks/pipe.md"
  refuses "refuses a special file" path-escape
}

# A hyphen makes hands/tests/test_<name>.py a module unittest never finds.
case_ c_hyphen
c_hyphen() {
  fresh
  add_hand gate-dash
  refuses "refuses a new hand whose name has a hyphen" frozen-path
  check "and runs none of its code" jqe '[.checks[] | select(.rule == "hand-contract") | .ok] == [null]' "$out"
}

# A new allowlisted brain file at the top of brain/ is no hand's entry point.
case_ c_exec_lessons
c_exec_lessons() {
  fresh
  printf 'lessons\n' >"$cand/brain/lessons.md"
  chmod +x "$cand/brain/lessons.md"
  refuses "refuses a new executable allowlisted brain file" path-escape
  check "and says it is an executable" jqe 'any(.refused[]; .detail | test("executable"))' "$out"
}

case_ c_docs
c_docs() {
  check "the docs say a new hand's test must define a test" grep -qF 'compiles and defines a test' "$repo/docs/evolve.md"
}

# A submodule is refused for what it is, before anything inside it is read:
# a moved pin is frozen-path, a new repository is path-escape, and its files
# (here a script shellcheck would fail) are never checked.
case_ c_submodule_pin
c_submodule_pin() {
  check "the base keeps the submodule's pin" test "$(git -C "$base" ls-tree HEAD vendor/openhuman | cut -c1-6)" = 160000
  fresh
  (cd "$cand/vendor/openhuman" && g init -q && printf 'moved\n' >moved && g add moved && g commit -qm moved)
  refuses "refuses a submodule checked out at another pin" frozen-path
  check "naming the submodule" jqe 'any(.refused[]; .rule == "frozen-path" and (.paths | index("vendor/openhuman")))' "$out"
}

case_ c_new_repo
c_new_repo() {
  fresh
  mkdir -p "$cand/brain/skills/sub"
  # shellcheck disable=SC2016 # the unquoted $1 is the fixture's lint
  printf '#!/usr/bin/env bash\necho $1\n' >"$cand/brain/skills/sub/bad.sh"
  (cd "$cand/brain/skills/sub" && g init -q && g add bad.sh && g commit -qm sub)
  gate
  check "a new repository inside the tree is refused as path-escape only" test "$code:$(rules)" = '2:["path-escape"]'
  check "and nothing inside it is in the diff" jqe '[.refused[].paths[]] == ["brain/skills/sub/.git"]' "$out"
}

case_ c_exec_skill
c_exec_skill() {
  fresh
  printf 'echo hi\n' >"$cand/brain/skills/run.md"
  chmod +x "$cand/brain/skills/run.md"
  refuses "refuses a new executable that is not a hand's entry point" path-escape
}

case_ c_shadow
c_shadow() {
  fresh
  mkdir -p "$cand/hands/lib/teeny_hands/hand"
  printf '# shadows hand.py\n' >"$cand/hands/lib/teeny_hands/hand/__init__.py"
  refuses "refuses a new hand package that shadows an existing module" frozen-path
}

case_ c_shellcheck
c_shellcheck() {
  fresh
  # shellcheck disable=SC2016 # the unquoted $1 is the fixture's lint
  printf '#!/usr/bin/env bash\necho $1\n' >"$cand/brain/skills/lint.sh"
  refuses "refuses a shell script shellcheck fails" shellcheck
}

# --- the gate's own errors --------------------------------------------------------------
case_ c_not_a_dir
c_not_a_dir() {
  code=0
  "$evolve" gate "$scratch/no-such-dir" >"$out" 2>/dev/null || code=$?
  check "a candidate that is not a directory is exit 1" test "$code" = 1
}

# --- running them -----------------------------------------------------------------------
# CASE in a subshell, in its own directory and TMPDIR: its lines go to log,
# and its failure count to failures, which a case that dies never writes.
run_case() {
  local dir="$scratch/cases/$1"
  mkdir -p "$dir/tmp"
  (
    cand="$dir/cand" out="$dir/out.json" err="$dir/gate.err"
    export TMPDIR="$dir/tmp"
    # shellcheck disable=SC2030 # each case counts its own, and writes it below
    failures=0
    "$1"
    echo "$failures" >"$dir/failures"
  ) >"$dir/log" 2>&1 </dev/null
}
jobs_max="$(nproc 2>/dev/null || sysctl -n hw.ncpu 2>/dev/null || echo 2)"
for c in "${CASES[@]}"; do
  while [ "$(jobs -rp | wc -l)" -ge "$jobs_max" ]; do sleep 0.2; done
  run_case "$c" &
done
wait

for c in "${CASES[@]}"; do
  dir="$scratch/cases/$c"
  while IFS= read -r line; do
    case "$line" in
      FAIL*) printf '%s\n' "$line" >&2 ;;
      *) printf '%s\n' "$line" ;;
    esac
  done <"$dir/log"
  if [ -s "$dir/failures" ]; then
    # shellcheck disable=SC2031 # this is the main shell's count
    failures=$((failures + $(cat "$dir/failures")))
  else
    fail "$c died before it finished (its log is above)"
  fi
done

if [ "$failures" -gt 0 ]; then
  echo "$failures failure(s)" >&2
  exit 1
fi
echo "all evolve gate tests passed"
