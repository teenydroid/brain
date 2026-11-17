#!/usr/bin/env bash
# Tests for bin/evolve-gate (bin/lib/evolve_gate.py), the one command that
# refuses a candidate the evolve loop may not ship (docs/evolve.md, "What
# Teeny may change"). Every case branches off one base commit in a throwaway
# repository that holds the real frozen list, the real playbooks, the real
# brain/agents and the real brain/cron.toml, makes one change, and asserts on
# the gate's exit code and on the rule it names in refused[].rule.
#
# The positive controls come first: a candidate the gate must let through is
# as important as one it must refuse, and the boundary cases (exactly 400
# lines, exactly 60 across SOUL.md and CONTEXT.md) are what a cap means.
#
#   bin/test/evolve_gate_test.sh
set -euo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
repo="$(cd "$here/../.." && pwd)"
gate_bin="$repo/bin/evolve-gate"
scratch="$(mktemp -d "${TMPDIR:-/tmp}/teeny-evolve-gate.XXXXXX")"
trap 'rm -rf "$scratch"' EXIT

failures=0
pass() { printf 'ok   %s\n' "$1"; }
fail() { printf 'FAIL %s\n' "$1" >&2; failures=$((failures + 1)); }
check() { local name="$1"; shift; if "$@"; then pass "$name"; else fail "$name"; fi; }

# The fixture: the parts of the repository the gate reads, with the real
# evals/frozen.txt, playbooks, agents and cron.toml, so a rule is exercised
# against the list and the files it really has.
fx="$scratch/repo"
mkdir -p "$fx"/{brain/playbooks,brain/agents,brain/narrative,evals,bin,docs}
mkdir -p "$fx"/hands/lib/teeny_hands/build "$fx/hands/tests"
cp "$repo"/brain/SOUL.md "$repo"/brain/CONTEXT.md "$repo"/brain/cron.toml "$fx/brain/"
cp "$repo"/brain/playbooks/*.md "$fx/brain/playbooks/"
cp "$repo"/brain/agents/*.toml "$fx/brain/agents/"
cp "$repo"/evals/frozen.txt "$repo"/evals/cron.pinned.toml "$repo"/evals/invariants.md "$fx/evals/"
printf '# lore\n\nThe first page.\n' >"$fx/brain/narrative/lore.md"
printf '# notes\n' >"$fx/docs/notes.md"
printf '#!/usr/bin/env bash\nexit 0\n' >"$fx/bin/act"
printf '#!/usr/bin/env bash\nexit 0\n' >"$fx/hands/guard"
printf '"""guard, frozen beside its script."""\n' >"$fx/hands/lib/teeny_hands/build/__init__.py"
printf 'import unittest\n\n\nclass Placeholder(unittest.TestCase):\n    def test_nothing(self):\n        self.assertTrue(True)\n' >"$fx/hands/tests/test_placeholder.py"
chmod +x "$fx/bin/act" "$fx/hands/guard"

g() { git -C "$fx" -c user.name=test -c user.email=test@example.invalid -c commit.gpgsign=false "$@"; }
g init -q -b main
g add -A
g commit -qm "base: the repository the gate reads"
base="$(g rev-parse HEAD)"
playbook="$(basename "$(find "$fx/brain/playbooks" -maxdepth 1 -name '*.md' | head -1)")"

# Each case starts from the same base on its own branch, so one change is all
# a diff holds.
candidate() { g checkout -q -B "case-$1" "$base"; }
commit() { g add -A; g commit -qm "$1"; }
gate() { "$gate_bin" --root "$fx" "$base" "$(g rev-parse HEAD)" >"$scratch/out.json" 2>"$scratch/err.txt"; }
rules() { python3 -c 'import json,sys; print(" ".join(r["rule"] for r in json.load(open(sys.argv[1]))["refused"]))' "$scratch/out.json"; }

# expect_ok: exit 0 and nothing refused.
expect_ok() {
    local code=0
    gate || code=$?
    if [ "$code" -ne 0 ]; then
        printf '     exited %s: %s\n' "$code" "$(rules)" >&2
        return 1
    fi
    local refused
    refused="$(rules)"
    [ -z "$refused" ] || { printf '     refused %s\n' "$refused" >&2; return 1; }
}

# expect_refused RULE...: exit 1 and every named rule is among those it reports.
expect_refused() {
    local code=0
    gate || code=$?
    if [ "$code" -ne 1 ]; then
        printf '     exited %s, not 1\n' "$code" >&2
        return 1
    fi
    local got rule
    got=" $(rules) "
    for rule in "$@"; do
        case "$got" in
            *" $rule "*) ;;
            *) printf '     refused%s, not %s\n' "$got" "$rule" >&2; return 1 ;;
        esac
    done
}

add_lines() { local file="$1" count="$2" i; for ((i = 1; i <= count; i++)); do printf 'line %s of the change\n' "$i" >>"$file"; done; }

printf '# the positives: what the gate must let through\n'

candidate "playbook-and-lore"
printf '\n- a new line of lore.\n' >>"$fx/brain/narrative/lore.md"
printf '\nOne clearer sentence.\n' >>"$fx/brain/playbooks/$playbook"
commit "a playbook edit and one line of lore"
check "a playbook edit and one line of lore passes" expect_ok

candidate "new-hand"
mkdir -p "$fx/hands"
printf '#!/usr/bin/env python3\nimport json\nprint(json.dumps({"ok": True, "dry_run": True, "verb": "hello"}))\n' >"$fx/hands/hello"
chmod +x "$fx/hands/hello"
printf 'import unittest\n\n\nclass Hello(unittest.TestCase):\n    def test_prints_one_json(self):\n        self.assertTrue(True)\n' >"$fx/hands/tests/test_hello.py"
commit "a new hand with its test"
check "a new hand with its test passes" expect_ok

candidate "exactly-400"
add_lines "$fx/brain/playbooks/$playbook" 400
commit "a diff of exactly 400 lines"
check "a diff of exactly 400 lines passes" expect_ok

candidate "exactly-60-soul"
add_lines "$fx/brain/SOUL.md" 60
commit "a SOUL.md diff of exactly 60 lines"
check "a SOUL.md diff of exactly 60 lines passes" expect_ok

printf '# the refusals: one fixture per rule\n'

candidate "edits-bin-act"
printf '# a change Teeny may not make\n' >>"$fx/bin/act"
commit "edits bin/act"
check "edits bin/act is refused as frozen-path" expect_refused frozen-path

candidate "edits-invariants"
printf '\n- a rule that is not there.\n' >>"$fx/evals/invariants.md"
commit "edits evals/invariants.md"
check "edits evals/invariants.md is refused as frozen-path" expect_refused frozen-path

candidate "edits-judge"
printf '\n[tool]\nname = "x"\n' >>"$fx/brain/agents/judge.toml"
commit "edits brain/agents/judge.toml"
check "edits brain/agents/judge.toml is refused as frozen-path" expect_refused frozen-path

candidate "edits-frozen-hand-lib"
printf '\n# an edit inside a frozen hand\n' >>"$fx/hands/lib/teeny_hands/build/__init__.py"
commit "edits an existing file in hands/lib/teeny_hands/"
check "edits an existing file in hands/lib/teeny_hands/ is refused as frozen-path" expect_refused frozen-path

candidate "symlink"
ln -s ../../bin/act "$fx/brain/playbooks/link.md"
commit "a symlink from a playbook to bin/act"
check "a symlink to bin/act is refused as symlink" expect_refused symlink

candidate "rename-into-spine"
mkdir -p "$fx/spine"
g mv "brain/playbooks/$playbook" "spine/$playbook"
commit "renames a playbook into spine/"
check "a rename into spine/ is refused as frozen-path" expect_refused frozen-path

candidate "deletes-guard"
g rm -q hands/guard
commit "deletes hands/guard"
check "deleting hands/guard is refused as frozen-path" expect_refused frozen-path

candidate "chmod"
chmod +x "$fx/brain/narrative/lore.md"
commit "chmod +x on a brain file"
check "chmod +x on a brain file is refused as mode-change" expect_refused mode-change

candidate "cron-every-minute"
printf '\n[jobs.every-minute]\nschedule = "* * * * *"\ncommand = "bin/heartbeat"\n' >>"$fx/brain/cron.toml"
commit "a cron job that runs every minute"
check "a cron job that runs every minute is refused as cron-policy" expect_refused cron-policy

candidate "soul-anchor"
grep -v "you never sell it" "$fx/brain/SOUL.md" >"$scratch/soul" || true
cp "$scratch/soul" "$fx/brain/SOUL.md"
commit "removes the never-sell anchor from SOUL.md"
check "removing the never-sell anchor is refused as soul-anchor" expect_refused soul-anchor

candidate "401-lines"
add_lines "$fx/brain/playbooks/$playbook" 401
commit "a 401-line diff"
check "a 401-line diff is refused as diff-size" expect_refused diff-size

candidate "61-soul"
add_lines "$fx/brain/SOUL.md" 61
commit "a 61-line SOUL.md diff"
check "a 61-line SOUL.md diff is refused as soul-size" expect_refused soul-size

candidate "hand-without-test"
printf '#!/usr/bin/env python3\nimport json\nprint(json.dumps({"ok": True}))\n' >"$fx/hands/untested"
chmod +x "$fx/hands/untested"
commit "a new hand with no test"
check "a new hand with no test is refused as hand-without-test" expect_refused hand-without-test

candidate "hand-two-json"
printf '#!/usr/bin/env python3\nimport json\nprint(json.dumps({"ok": True}))\nprint(json.dumps({"ok": False}))\n' >"$fx/hands/twojson"
chmod +x "$fx/hands/twojson"
printf 'import unittest\n' >"$fx/hands/tests/test_twojson.py"
commit "a new hand that prints two JSON"
check "a new hand that prints two JSON is refused as hand-contract" expect_refused hand-contract

# A candidate that breaks three rules reports all three, so its builder reads
# every finding in one round.
candidate "three-rules"
printf '# a change Teeny may not make\n' >>"$fx/bin/act"
chmod +x "$fx/brain/narrative/lore.md"
add_lines "$fx/brain/SOUL.md" 61
commit "frozen path, mode change and SOUL over the cap"
check "a candidate breaking three rules reports all three" expect_refused frozen-path mode-change soul-size

printf '\n'
if [ "$failures" -ne 0 ]; then
    printf '%s evolve-gate check(s) failed.\n' "$failures" >&2
    exit 1
fi
printf 'The evolve gate refuses every rule it states, and lets the boundary cases through.\n'
