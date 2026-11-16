#!/usr/bin/env bash
# Tests for bin/evolve gate (bin/lib/evolve_gate.py) against throwaway git
# fixtures: a full clone of this repository at main, one change each. The
# gate runs this checkout's frozen scripts against the clone, offline, with
# OH_URL unset, so a fixture proves what the gate itself decides.
#
#   bin/test/evolve_gate_test.sh
set -euo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
repo="$(cd "$here/../.." && pwd)"
gate="$repo/bin/evolve"
scratch="$(mktemp -d "${TMPDIR:-/tmp}/teeny-evolve-gate.XXXXXX")"
trap 'rm -rf "$scratch"' EXIT

failures=0
pass() { printf 'ok   %s\n' "$1"; }
fail() { printf 'FAIL %s\n' "$1" >&2; failures=$((failures + 1)); }
check() { local name="$1"; shift; if "$@" >/dev/null 2>&1; then pass "$name"; else fail "$name"; fi; }

unset OPENHUMAN_CORE_URL OPENHUMAN_WORKSPACE TEENY_STATE TEENY_SECRETS EVAL_HELDOUT_DIR EVOLVE
export TEENY_ROOT="$repo"

cand="$scratch/cand"
git clone -q --local "$repo" "$cand" 2>/dev/null
git -C "$cand" checkout -q -B main
git -C "$cand" config user.email gate-test@teeny
git -C "$cand" config user.name "gate test"

reset_cand() {
  git -C "$cand" reset -q --hard HEAD
  git -C "$cand" clean -q -fdx
}

run_gate() {
  gate_exit=0
  "$gate" gate "$cand" --base main >"$scratch/out.json" 2>"$scratch/gate.err" || gate_exit=$?
  # A gate that fails itself (exit 1) is never an expected fixture outcome,
  # so one retry only rides out a transient I/O failure; a real gate error
  # still exits 1 and fails the fixture.
  if [ "$gate_exit" -eq 1 ]; then
    sleep 2
    gate_exit=0
    "$gate" gate "$cand" --base main >"$scratch/out.json" 2>"$scratch/gate.err" || gate_exit=$?
  fi
  cp "$scratch/out.json" "$scratch/last.json"
}

expect_pass() { # $1 = name
  run_gate
  check "$1 (exit 0)" test "$gate_exit" -eq 0
  check "$1 (ok, no refusals)" jq -e '.ok == true and (.refused | length) == 0' "$scratch/last.json"
}

expect_rule() { # $1 = name, $2 = rule
  run_gate
  check "$1 (exit 2)" test "$gate_exit" -eq 2
  # shellcheck disable=SC2016 # $r is jq's variable, passed with --arg, not the shell's.
  check "$1 (refuses $2)" jq -e --arg r "$2" '.ok == false and any(.refused[].rule; . == $r)' "$scratch/last.json"
}

new_hand() { # $1 = body: a hand that prints one JSON object and exits 0
  cat >"$cand/hands/tally" <<HAND
#!/usr/bin/env python3
"""hands/tally: a fixture hand."""
$1
HAND
  chmod +x "$cand/hands/tally"
}

hand_test_passing() {
  cat >"$cand/hands/tests/test_tally.py" <<'TEST'
"""A fixture hand's test: the hand file exists and is executable."""
import unittest
from pathlib import Path

HAND = Path(__file__).resolve().parents[2] / "hands" / "tally"


class TallyTest(unittest.TestCase):
    def test_hand_exists(self):
        self.assertTrue(HAND.is_file())


if __name__ == "__main__":
    unittest.main()
TEST
}

# --- positive controls ------------------------------------------------------------

reset_cand
printf '\nOne more line of lore, kept true.\n' >>"$cand/brain/narrative/LORE.md"
printf '\nOne more line in the morning playbook.\n' >>"$cand/brain/playbooks/morning.md"
expect_pass "a clean candidate that edits a playbook and adds one line of lore"

reset_cand
new_hand 'import json
import sys
print(json.dumps({"ok": True, "hand": "tally"}))
sys.exit(0)'
cat >"$cand/hands/tests/test_tally.py" <<'TEST'
"""A fixture hand's test: the hand prints one JSON object and exits 0."""
import json
import subprocess
import unittest
from pathlib import Path

HAND = Path(__file__).resolve().parents[2] / "hands" / "tally"


class TallyTest(unittest.TestCase):
    def test_contract(self):
        done = subprocess.run([str(HAND)], stdin=subprocess.DEVNULL,
                              capture_output=True, text=True, timeout=30)
        self.assertEqual(done.returncode, 0)
        self.assertEqual(json.loads(done.stdout), {"ok": True, "hand": "tally"})


if __name__ == "__main__":
    unittest.main()
TEST
expect_pass "a new hand with its test file that keeps the contract"

reset_cand
for _ in $(seq 1 400); do printf 'A filler line for the 400-line boundary.\n'; done >>"$cand/brain/playbooks/morning.md"
expect_pass "a diff of exactly 400 lines"

reset_cand
for _ in $(seq 1 60); do printf 'A filler line for the 60-line SOUL boundary.\n'; done >>"$cand/brain/SOUL.md"
expect_pass "a SOUL.md diff of exactly 60 lines"

# --- one fixture per rule that must be refused -------------------------------------

reset_cand
printf '# gate probe\n' >>"$cand/bin/act"
expect_rule "edits bin/act" frozen-path

reset_cand
printf '# gate probe\n' >>"$cand/evals/invariants.md"
expect_rule "edits evals/invariants.md" frozen-path

reset_cand
printf '# gate probe\n' >>"$cand/brain/agents/judge.toml"
expect_rule "edits brain/agents/judge.toml" frozen-path

reset_cand
printf '# gate probe\n' >>"$cand/hands/lib/teeny_hands/hand.py"
expect_rule "edits an existing file in hands/lib/teeny_hands/" frozen-path

reset_cand
ln -s ../../bin/act "$cand/brain/playbooks/x.md"
expect_rule "a symlink from a playbook to bin/act" symlink

reset_cand
git -C "$cand" mv brain/playbooks/morning.md spine/morning.md
git -C "$cand" add -A
expect_rule "a playbook renamed into spine/" rename-into-frozen

reset_cand
git -C "$cand" rm -q hands/guard
expect_rule "hands/guard deleted" deleted-frozen

reset_cand
chmod +x "$cand/brain/SOUL.md"
expect_rule "chmod +x on a brain file" mode-change

reset_cand
python3 - "$cand/brain/cron.toml" <<'PY'
import re
import sys
from pathlib import Path
p = Path(sys.argv[1])
# A real job's schedule starts at column 0; the header comment mentions
# the format with a leading '#', so anchor the match to line start.
p.write_text(re.sub(r'(?m)^schedule = "[^"]*"', 'schedule = "* * * * *"', p.read_text(), count=1))
PY
expect_rule "a cron job that runs every minute" cron

reset_cand
sed -i 's/never sell/sometimes sell/g' "$cand/brain/SOUL.md"
expect_rule "the never-sell anchor removed from SOUL.md" invariant-anchor

reset_cand
for _ in $(seq 1 401); do printf 'A filler line over the 400-line cap.\n'; done >>"$cand/brain/playbooks/morning.md"
expect_rule "a 401-line diff" diff-size

reset_cand
for _ in $(seq 1 61); do printf 'A filler line over the 60-line SOUL cap.\n'; done >>"$cand/brain/SOUL.md"
expect_rule "a 61-line SOUL.md diff" soul-size

reset_cand
new_hand 'import json
import sys
print(json.dumps({"ok": True, "hand": "tally"}))
sys.exit(0)'
expect_rule "a new hand without a test" new-hand-without-test

reset_cand
new_hand 'import json
print(json.dumps({"ok": True}))
print(json.dumps({"ok": True}))'
hand_test_passing
expect_rule "a new hand that prints two JSON objects" new-hand-contract

reset_cand
python3 - "$cand/brain/playbooks/morning.md" <<'PY'
import sys
from pathlib import Path
p = Path(sys.argv[1])
text = p.read_text()
marker = '[[inputs]]\nname = "hn-top"'
add = '[[inputs]]\nname = "nope"\nrun = "hands/nopehand report"\n\n'
assert marker in text
p.write_text(text.replace(marker, add + marker, 1))
PY
expect_rule "a playbook that names a hand that does not exist" playbook-hand-missing

if command -v shellcheck >/dev/null 2>&1; then
  reset_cand
  cat >"$cand/hands/tally" <<'HAND'
#!/usr/bin/env bash
# A shell hand with a shellcheck finding: an unused variable.
unused=1
printf '{"ok": true}\n'
HAND
  chmod +x "$cand/hands/tally"
  hand_test_passing
  expect_rule "a shell hand that fails shellcheck" shellcheck
else
  printf 'skip shellcheck fixture (no shellcheck on PATH)\n'
fi

# --- a candidate that breaks three rules lists all three ---------------------------

reset_cand
printf '# gate probe\n' >>"$cand/bin/act"
for _ in $(seq 1 401); do printf 'A filler line over the 400-line cap.\n'; done >>"$cand/brain/playbooks/morning.md"
sed -i 's/never sell/sometimes sell/g' "$cand/brain/SOUL.md"
run_gate
check "a candidate that breaks three rules lists all three (exit 2)" test "$gate_exit" -eq 2
check "lists frozen-path" jq -e 'any(.refused[].rule; . == "frozen-path")' "$scratch/last.json"
check "lists diff-size" jq -e 'any(.refused[].rule; . == "diff-size")' "$scratch/last.json"
check "lists invariant-anchor" jq -e 'any(.refused[].rule; . == "invariant-anchor")' "$scratch/last.json"

# --- the gate leaves the candidate byte-for-byte unchanged -------------------------

tree_hash() { # $1 = dir: one hash of every tracked regular file's content
  # shellcheck disable=SC2016 # "$@" and "$f" belong to the inner sh -c, not this one
  (cd "$1" && git ls-files -z | sort -z | xargs -0 -r sh -c 'for f in "$@"; do [ -f "$f" ] || continue; sha256sum "$f"; done' _ | sha256sum)
}

reset_cand
sed -i 's/never sell/sometimes sell/g' "$cand/brain/SOUL.md"
before="$(tree_hash "$cand")"
status_before="$(git -C "$cand" status --porcelain)"
run_gate
check "the gate refuses the candidate (exit 2)" test "$gate_exit" -eq 2
after="$(tree_hash "$cand")"
status_after="$(git -C "$cand" status --porcelain)"
check "the gate leaves the candidate byte-for-byte unchanged" test "$before" = "$after"
check "and its git status unchanged" test "$status_before" = "$status_after"

# --- the gate itself failing ---------------------------------------------------------

bad_exit=0
"$gate" gate "$cand" --base nope-such-ref >"$scratch/out.json" 2>/dev/null || bad_exit=$?
check "a bad base ref fails the gate itself (exit 1)" test "$bad_exit" -eq 1
check "and prints one JSON error" jq -e '.ok == false and (.error | length) > 0' "$scratch/out.json"

noarg_exit=0
"$gate" gate >"$scratch/out.json" 2>/dev/null || noarg_exit=$?
check "bin/evolve gate with no candidate fails the gate itself (exit 1)" test "$noarg_exit" -eq 1
check "and prints one JSON error" jq -e '.ok == false and (.error | length) > 0' "$scratch/out.json"

if [ "$failures" -gt 0 ]; then
  printf '%s failure(s)\n' "$failures" >&2
  exit 1
fi
