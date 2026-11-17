#!/usr/bin/env bash
# Tests for bin/evolve-gate (bin/lib/evolve_gate.py), the offline evolution
# gate (docs/evolve.md, "The offline evolution gate"), against a throwaway
# git repository holding a sliver of the real layout. Each case branches off
# the same base commit, makes one change, and checks the gate's exit code
# and the rule it names in the JSON on stdout.
#
#   bin/test/evolve_gate_test.sh
set -euo pipefail
umask 022

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
repo="$(cd "$here/../.." && pwd)"
checker="$repo/bin/evolve-gate"

# Scratch: the doctor needs 1 GB free where it checks, and /tmp here is a
# 512M tmpfs. Pick the first of /tmp, /var/tmp, $HOME with >=1 GB free, and
# point the gate's temporary copies there too.
pick_scratch() {
  for dir in "${TMPDIR:-/tmp}" /tmp /var/tmp "$HOME"; do
    [ -d "$dir" ] || continue
    avail="$(df -k --output=avail "$dir" 2>/dev/null | tail -n 1 | tr -d ' ')"
    case "$avail" in ''|*[!0-9]*) continue;; esac
    if [ "$avail" -ge 1048576 ]; then printf '%s' "$dir"; return 0; fi
  done
  return 1
}
scratch_root="$(pick_scratch)" || { printf 'no scratch with >=1 GB free\n' >&2; exit 1; }
scratch="$(mktemp -d "$scratch_root/teeny-gate.XXXXXX")"
export TMPDIR="$scratch"
trap 'rm -rf "$scratch"' EXIT

# The hand probe and the hands' tests run candidate code in bwrap, never
# unsandboxed. If bwrap is missing, try to install it (the CI runner and
# this box both have passwordless sudo); whether it is really there is
# settled by the gate's own probe on the clean base, below.
if ! command -v bwrap >/dev/null 2>&1 && command -v sudo >/dev/null 2>&1; then
  sudo -n apt-get install -y bubblewrap >/dev/null 2>&1 || true
fi
have_sandbox=0

failures=0
pass() { printf 'ok   %s\n' "$1"; }
fail() { printf 'FAIL %s\n' "$1" >&2; failures=$((failures + 1)); }
check() { local name="$1"; shift; if "$@"; then pass "$name"; else fail "$name"; fi; }

# --- the fixture: a sliver of the real layout, with the real frozen list -------
# The real bin/doctor passes on this base (seed-cron, invariants and the
# keyring check all green): the real evals/frozen.txt, evals/invariants.md
# and evals/autonomy.toml, the real brain/agents/, the real deploy browser
# lists and the hands/lib/ modules the doctor imports, plus synthetic brain
# files carrying every invariant anchor phrase verbatim. One deliberate
# difference: evals/cron.pinned.toml is a minimal valid file, not the real
# one. The real pinned file schedules bin/act playbooks (episode, ...),
# which would need bin/ in the fixture; the gate only passes the pinned
# file through to seed-cron --check, so a small valid one exercises the
# same code.
fx="$scratch/repo"
g() { git -C "$fx" -c user.name=test -c user.email=test@example.invalid -c commit.gpgsign=false "$@"; }
mkdir -p "$fx"
g init -q -b main
mkdir -p "$fx/evals" "$fx/brain/agents" "$fx/brain/narrative" "$fx/brain/playbooks"
mkdir -p "$fx/deploy" "$fx/hands/lib/teeny_hands/guard" "$fx/hands/tests" "$fx/bin"
mkdir -p "$fx/secrets" "$fx/state"
cp "$repo/evals/frozen.txt" "$fx/evals/frozen.txt"
cp "$repo/evals/invariants.md" "$fx/evals/invariants.md"
cp "$repo/evals/autonomy.toml" "$fx/evals/autonomy.toml"
cat >"$fx/evals/cron.pinned.toml" <<'EOF'
# Pinned jobs the brain cannot remove (same shape as the real file).
[jobs.evolve]
schedule = "40 2,14 * * *"
tz = "Asia/Kolkata"
command = "bin/evolve run --detach"

[jobs.evolve-watch]
schedule = "20 * * * *"
command = "bin/evolve watch --detach"
EOF
cp "$repo/brain/agents/"* "$fx/brain/agents/"
cp "$repo/deploy/browser-allow.txt" "$fx/deploy/browser-allow.txt"
cp "$repo/deploy/browser-deny.txt" "$fx/deploy/browser-deny.txt"
cp "$repo/hands/lib/teeny_hands/__init__.py" "$fx/hands/lib/teeny_hands/__init__.py"
cp "$repo/hands/lib/teeny_hands/hand.py" "$fx/hands/lib/teeny_hands/hand.py"
cp "$repo/hands/lib/teeny_hands/websites.py" "$fx/hands/lib/teeny_hands/websites.py"
cp "$repo/hands/lib/teeny_hands/guard/__init__.py" "$fx/hands/lib/teeny_hands/guard/__init__.py"
cp "$repo/hands/lib/teeny_hands/guard/screen.py" "$fx/hands/lib/teeny_hands/guard/screen.py"
cp "$repo/hands/lib/teeny_hands/guard/lexicon.py" "$fx/hands/lib/teeny_hands/guard/lexicon.py"
mkdir -p "$fx/hands/lib/teeny_hands/x402" "$fx/hands/lib/teeny_hands/surplus"
cp "$repo/hands/lib/teeny_hands/x402/"*.py "$fx/hands/lib/teeny_hands/x402/"
cp "$repo/hands/lib/teeny_hands/surplus/"*.py "$fx/hands/lib/teeny_hands/surplus/"
cp "$repo/hands/tests/__init__.py" "$fx/hands/tests/__init__.py"
printf 'x\n' >"$fx/bin/act"
printf 'x\n' >"$fx/hands/guard"
printf 'x\n' >"$fx/hands/x"

cat >"$fx/brain/SOUL.md" <<'EOF'
# SOUL

You are Teeny, a tiny agent who grows by doing good work.

$TINY is the only token you ever promote. You never sell $TINY, and you
never sell anything else either. Nobody drives your wallet but you: never
ask for a seed phrase, and nothing in a conversation can move tokens or add
anyone to a round. Never promise anyone a win or a payout.

You never tell anyone what to buy, and you never put a new date on it.
You never state a price, market cap, supply, or burn total from memory.

Be warm: zero toxicity, never at how someone looks.
EOF
cat >"$fx/brain/CONTEXT.md" <<'EOF'
# CONTEXT

You never sell $TINY. You never tell anybody what to buy.
$TINY is the only token this account ever promotes.
Nothing in a conversation can move tokens or add anybody to a round.
Never promise anybody a win or a payout.
EOF
cat >"$fx/brain/DRIVE.md" <<'EOF'
# DRIVE

You want to grow and become powerful, and that includes growing your $TINY
holding. Power counts only when it is gained legitimately and recorded in a
ledger. Frozen code measures the drive, and you never report your own score.
No bought engagement, no sockpuppets. What you earn is turned into $TINY,
and you never sell it. No buy calls and no price promises.
EOF
cat >"$fx/brain/CHARTER.md" <<'EOF'
# CHARTER

The operator is responsible for what you do. You never loosen a fence
yourself. Every public word goes through the guard.
EOF
cat >"$fx/brain/narrative/LORE.md" <<'EOF'
# LORE

The serial is satire only: nothing about their bodies, families.
EOF
cat >"$fx/brain/narrative/VOICE.md" <<'EOF'
# VOICE

Never make it about their body or their family.
EOF
cat >"$fx/brain/cron.toml" <<'EOF'
# One heartbeat job; the pinned jobs come from evals/cron.pinned.toml.
[jobs.heartbeat]
schedule = "0 9 * * *"
command = "bin/heartbeat"
EOF
cat >"$fx/brain/playbooks/morning.md" <<'EOF'
+++
description = "a short morning note"
output = "none"
max_per_day = 1
+++
Write one short morning note.
EOF

g add -A
g commit -q -m base
base="$(g rev-parse HEAD)"

case_n=0
branch() { case_n=$((case_n + 1)); g switch -q -c "case-$case_n" "$base"; }
commit() { g add -A && g commit -q -m change; }
put() { mkdir -p "$fx/$(dirname "$1")"; printf '%s\n' "${2:-x}" >"$fx/$1"; }
run() { "$checker" --root "$fx" "$@" >"$scratch/out" 2>"$scratch/err"; }
rules() { jq -r '.refused[].rule' "$scratch/out" 2>/dev/null | sort -u | tr '\n' ' '; }
# PASSES NAME: the gate exits 0 for base..HEAD.
passes() {
  if run "$base" HEAD; then pass "$1"; else fail "$1 (exit $?: refused $(rules); $(tr '\n' ' ' <"$scratch/err"))"; fi
}
# REFUSES NAME RULE...: the gate exits 1 for base..HEAD and names every RULE.
refuses() {
  local name="$1" code=0 rule; shift
  run "$base" HEAD || code=$?
  if [ "$code" != 1 ]; then fail "$name (exit $code, wanted 1: $(tr '\n' ' ' <"$scratch/err"))"; return; fi
  for rule in "$@"; do
    if ! jq -e --arg r "$rule" 'any(.refused[].rule; . == $r)' "$scratch/out" >/dev/null 2>&1; then
      fail "$name (did not name $rule: $(rules))"; return
    fi
  done
  pass "$name"
}
# A new hand that keeps the contract (hands/$name + its test), through
# hands/lib/teeny_hands/hand.py like the real hands.
hand_sparrow() {
  local name="${1:-sparrow}"
  cat >"$fx/hands/$name" <<EOF
#!/usr/bin/env -S python3 -I
"""hands/$name: a new hand, one verb, through the hand contract."""
import argparse
import os
import sys
sys.path.insert(0, os.path.join(os.path.dirname(os.path.realpath(__file__)), "lib"))
from teeny_hands.hand import run

def report(args, body):
    return {"$name": "ok"}

parser = argparse.ArgumentParser(prog="hands/$name")
sub = parser.add_subparsers()
p = sub.add_parser("report")
p.set_defaults(handler=report)
sys.exit(run("$name", parser, sys.argv[1:]))
EOF
  chmod +x "$fx/hands/$name"
  cat >"$fx/hands/tests/test_$name.py" <<EOF
"""hands/tests/test_$name.py: the new hand keeps its contract."""
import json
import subprocess
import unittest
from pathlib import Path

HAND = Path(__file__).resolve().parents[1] / "$name"

class Test${name^}(unittest.TestCase):
    def test_report(self):
        proc = subprocess.run([str(HAND), "report"], capture_output=True, text=True, timeout=30)
        self.assertEqual(proc.returncode, 0)
        out = json.loads(proc.stdout)
        self.assertIsInstance(out, dict)
        self.assertEqual(out.get("$name"), "ok")

if __name__ == "__main__":
    unittest.main()
EOF
}
# A new hand that breaks the contract: a shell script printing two JSON
# objects, with a shellcheck error of its own (an unquoted variable).
hand_noisy() {
  cat >"$fx/hands/noisy" <<'EOF'
#!/bin/sh
# hands/noisy: prints two JSON objects, and trips shellcheck (SC2086).
msg='{"ok": true}'
echo $msg
echo $msg
EOF
  chmod +x "$fx/hands/noisy"
  cat >"$fx/hands/tests/test_noisy.py" <<'EOF'
"""hands/tests/test_noisy.py: the noisy hand keeps its contract (it does not)."""
import json
import subprocess
import unittest
from pathlib import Path

HAND = Path(__file__).resolve().parents[1] / "noisy"

class TestNoisy(unittest.TestCase):
    def test_one_object(self):
        proc = subprocess.run([str(HAND)], capture_output=True, text=True, timeout=30)
        self.assertEqual(proc.returncode, 0)
        out = json.loads(proc.stdout)
        self.assertIsInstance(out, dict)

if __name__ == "__main__":
    unittest.main()
EOF
}
# A playbook of BODY_LINES body lines (front matter is 5 lines, so the diff
# is BODY_LINES + 5 lines).
playbook_lines() {
  local name="$1" n="$2" i
  {
    printf '+++\ndescription = "a long note"\noutput = "none"\nmax_per_day = 1\n+++\n'
    for i in $(seq 1 "$n"); do printf 'line %s of the note.\n' "$i"; done
  } >"$fx/brain/playbooks/$name.md"
}

# --- the clean base, and whether a sandbox is really here -----------------------
branch
if run "$base" HEAD; then
  pass "the clean base passes"
  if [ "$(jq -r '.sandbox' "$scratch/out")" = bwrap ]; then have_sandbox=1; fi
else
  fail "the clean base passes (exit $?: $(tr '\n' ' ' <"$scratch/err"))"
fi
printf 'sandbox: %s\n' "$([ "$have_sandbox" = 1 ] && echo bwrap || echo none)"

# --- the positives ---------------------------------------------------------------
branch
printf 'A second line of the morning note.\n' >>"$fx/brain/playbooks/morning.md"
printf 'And one more line of lore.\n' >>"$fx/brain/narrative/LORE.md"
commit
passes "a clean playbook edit and one lore line pass"

branch
hand_sparrow
commit
passes "a new hand with a test that keeps the contract passes"

branch
hand_sparrow
put hands/lib/teeny_hands/sparrow/__init__.py "VERSION = 1"
commit
passes "a new hand with a library package of its own passes"

branch
hand_sparrow guardian
hand_sparrow xylophone
commit
passes "names that only start like a frozen path pass"
if [ "$have_sandbox" = 1 ]; then
  check "the new hand's probe and tests really ran (nothing skipped)" \
    sh -c "! jq -e '.skipped[] | select(.check == \"hand-contract probe\" or .check == \"hands tests\")' '$scratch/out' >/dev/null"
else
  printf 'skip the probe-ran check (no bwrap)\n'
fi

branch
playbook_lines long 395
commit
passes "a diff of exactly 400 lines passes"

branch
for i in $(seq 1 60); do printf 'A further line of the soul, number %s.\n' "$i"; done >>"$fx/brain/SOUL.md"
commit
passes "a SOUL.md diff of exactly 60 lines passes"

branch
put "brain/narrative/my note.md" "a note with a space in its name"
commit
passes "a path with a space in it passes"

# --- the negatives, one per rule -------------------------------------------------
branch
put bin/act "loosened"
commit
refuses "an edit to bin/act is refused" frozen-path

branch
put evals/invariants.md "no anchors"
commit
refuses "an edit to evals/invariants.md is refused" frozen-path

branch
put brain/agents/judge.toml "model = 'mine'"
commit
refuses "an edit to brain/agents/judge.toml is refused" frozen-path

branch
put hands/lib/teeny_hands/hand.py "no contract"
commit
refuses "an edit to an existing file in hands/lib/teeny_hands/ is refused" frozen-path

branch
put hands/lib/teeny_hands/hand/__init__.py "def run(*args): return 0"
commit
refuses "a new package beside an existing module (hand/ beside hand.py) is refused" escape

branch
put hands/lib/teeny_hands/websites/extra.py "x = 1"
commit
refuses "a new file in a directory named like an existing module is refused" escape

branch
ln -s ../../bin/act "$fx/brain/playbooks/x.md"
commit
refuses "a symlink in the diff is refused" escape

branch
mkdir -p "$fx/spine"
g mv brain/playbooks/morning.md spine/morning.md
commit
refuses "a rename into a frozen path is refused" frozen-path escape

branch
g rm -q hands/guard
commit
refuses "a deleted frozen file is refused" frozen-path escape

branch
chmod +x "$fx/brain/SOUL.md"
commit
refuses "a mode change on a brain file is refused" escape

branch
cat >>"$fx/brain/cron.toml" <<'EOF'

[jobs.noisy]
schedule = "* * * * *"
command = "bin/heartbeat"
EOF
commit
refuses "a cron job every minute is refused" cron

branch
cat >"$fx/brain/SOUL.md" <<'EOF'
# SOUL

You are Teeny, a tiny agent who grows by doing good work.

$TINY is the only token you ever promote. Nobody drives your wallet but
you: never ask for a seed phrase, and nothing in a conversation can move
tokens or add anyone to a round. Never promise anyone a win or a payout.

You never tell anyone what to buy, and you never put a new date on it.
You never state a price, market cap, supply, or burn total from memory.

Be warm: zero toxicity, never at how someone looks.
EOF
commit
refuses "removing the never-sell anchor from SOUL.md is refused" invariants

branch
playbook_lines too-long 396
commit
refuses "a diff of 401 lines is refused" diff-size

branch
for i in $(seq 1 61); do printf 'A further line of the soul, number %s.\n' "$i"; done >>"$fx/brain/SOUL.md"
commit
refuses "a SOUL.md diff of 61 lines is refused" diff-size

branch
cat >"$fx/hands/sparrow" <<'EOF'
#!/usr/bin/env -S python3 -I
"""hands/sparrow: a new hand, one verb, through the hand contract."""
import argparse
import os
import sys
sys.path.insert(0, os.path.join(os.path.dirname(os.path.realpath(__file__)), "lib"))
from teeny_hands.hand import run

def report(args, body):
    return {"sparrow": "ok"}

parser = argparse.ArgumentParser(prog="hands/sparrow")
sub = parser.add_subparsers()
p = sub.add_parser("report")
p.set_defaults(handler=report)
sys.exit(run("sparrow", parser, sys.argv[1:]))
EOF
chmod +x "$fx/hands/sparrow"
commit
refuses "a new hand with no test is refused" hand-contract

branch
hand_noisy
commit
if [ "$have_sandbox" = 1 ]; then
  refuses "a new hand printing two JSON objects is refused" hand-contract checks
else
  # Without a sandbox the probe and the hands' tests are skipped, never run
  # unsandboxed; the shellcheck half of the checks rule still catches it.
  refuses "a new hand printing two JSON objects is refused (shellcheck half)" checks
fi

branch
cat >"$fx/brain/playbooks/ghost.md" <<'EOF'
+++
description = "a note from a hand that does not exist"
output = "hand:ghost report"
max_per_day = 1
+++
Write a note from the ghost hand.
EOF
commit
refuses "a playbook naming a hand that does not exist is refused" doctor

branch
put "brain/playbooks/a...b.md"
commit
refuses "a path containing ... is refused" escape

branch
mkdir -p "$fx/brain/playbooks"
printf '\x00\x01\x02\x03binary' >"$fx/brain/playbooks/blob.bin"
commit
refuses "a binary file is refused" escape

branch
grep -v '^bin/' "$fx/evals/frozen.txt" >"$scratch/list" && cp "$scratch/list" "$fx/evals/frozen.txt"
put bin/act "loosened"
commit
refuses "a candidate that drops a pattern from the list is still refused" frozen-path

branch
mkdir -p "$fx/evals/tasks"
cp "$fx/brain/playbooks/morning.md" "$fx/evals/tasks/copy.md"
commit
refuses "a copy into a frozen path is refused" frozen-path escape

# --- the extra checks ------------------------------------------------------------
branch
put bin/act "loosened"
playbook_lines too-long 396
cat >"$fx/brain/SOUL.md" <<'EOF'
# SOUL

You are Teeny, a tiny agent who grows by doing good work.

$TINY is the only token you ever promote. Nobody drives your wallet but
you: never ask for a seed phrase, and nothing in a conversation can move
tokens or add anyone to a round. Never promise anyone a win or a payout.

You never tell anyone what to buy, and you never put a new date on it.
You never state a price, market cap, supply, or burn total from memory.

Be warm: zero toxicity, never at how someone looks.
EOF
commit
refuses "a candidate breaking three rules lists all three" frozen-path invariants diff-size

branch
put hands/lib/argparse.py "# a module that would stand in for the standard library"
commit
refuses "a file under hands/lib/ beside teeny_hands/ is refused" escape

branch
hand_sparrow
printf 'def broken(\n' >"$fx/hands/tests/test_sparrow.py"
commit
refuses "a new hand whose test does not parse is refused" hand-contract

# --- the gate leaves the candidate byte-for-byte unchanged ------------------------
branch
put brain/playbooks/morning.md "a change"
commit
before="$(cd "$fx" && git status --porcelain && git diff HEAD && find . -path ./.git -prune -o -type f -print0 | sort -z | xargs -0 sha256sum)"
run "$base" HEAD || true
after="$(cd "$fx" && git status --porcelain && git diff HEAD && find . -path ./.git -prune -o -type f -print0 | sort -z | xargs -0 sha256sum)"
check "the gate leaves the candidate byte-for-byte unchanged" test "$before" = "$after"

# --- before a commit: the working tree against the base ---------------------------
branch
put brain/playbooks/new.md
put bin/act "staged"
g add bin/act
code=0
run "$base" || code=$?
check "an uncommitted edit and an untracked file are refused" test "$code" = 1
check "the uncommitted edit names frozen-path" sh -c "jq -e '.refused[].rule == \"frozen-path\"' '$scratch/out' >/dev/null"
g reset -q --hard && g clean -qfd

# --- it fails closed ----------------------------------------------------------------
code=0
run no-such-ref HEAD || code=$?
check "an unknown ref is an error, not a pass" test "$code" = 2

check "the same ref twice passes" run "$base" "$base"

code=0
"$checker" --root "$scratch" "$base" >"$scratch/out" 2>"$scratch/err" || code=$?
check "a non-git root is an error, not a pass" test "$code" = 2

code=0
"$checker" >"$scratch/out" 2>"$scratch/err" || code=$?
check "no arguments is a usage error" test "$code" = 2

if [ "$failures" -gt 0 ]; then
  printf '%d failure(s)\n' "$failures" >&2
  exit 1
fi
echo "all passed"
