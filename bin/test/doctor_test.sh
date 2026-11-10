#!/usr/bin/env bash
# Tests for bin/doctor, and for bin/spine refusing to boot a checkout the
# doctor fails. Each case breaks one thing in a scratch copy of the checkout.
# No OpenHuman needed: the spine is handed a stand-in core that only records
# that it was started.
#
#   bin/test/doctor_test.sh
set -euo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
repo="$(cd "$here/../.." && pwd)"
scratch="$(mktemp -d "${TMPDIR:-/tmp}/teeny-doctor.XXXXXX")"
trap 'rm -rf "$scratch"' EXIT

failures=0
pass() { printf 'ok   %s\n' "$1"; }
fail() { printf 'FAIL %s\n' "$1" >&2; failures=$((failures + 1)); }
check() { local name="$1"; shift; if "$@"; then pass "$name"; else fail "$name"; fi; }

unset OPENHUMAN_CORE_TOKEN OPENHUMAN_CORE_URL OPENHUMAN_WORKSPACE CALENDAR_TZ \
  TEENY_ROOT TEENY_BRAIN TEENY_STORAGE TEENY_SECRETS TEENY_STATE TEENY_HANDS

# A fresh copy of what the box runs: bin/, hands/, evals/ and the brain.
root="$scratch/root"
fresh() {
  rm -rf "$root"
  mkdir -p "$root/brain"
  cp -R "$repo/bin" "$repo/evals" "$root/"
  mkdir -p "$root/deploy"
  cp "$repo/deploy/browser-allow.txt" "$root/deploy/"
  mkdir -p "$root/hands"
  cp -R "$repo/hands/lib" "$repo/hands/x" "$repo/hands/guard" "$repo/hands/mail" "$root/hands/"
  cp -R "$repo/brain/playbooks" "$repo/brain/agents" "$repo/brain/narrative" "$root/brain/"
  cp "$repo/brain/SOUL.md" "$repo/brain/CONTEXT.md" "$repo/brain/cron.toml" "$root/brain/"
}
doctor() { "$root/bin/doctor" >"$scratch/report.json" 2>"$scratch/doctor.log"; }
fails() { # NAME CHECK-SUBSTRING: the doctor exits 1 and names the failing check
  local name="$1" what="$2"
  if doctor; then
    fail "$name (the doctor passed)"
  elif grep -q "^FAIL $what" "$scratch/doctor.log"; then
    pass "$name"
  else
    fail "$name (failed for something else: $(grep '^FAIL' "$scratch/doctor.log" | head -2 | tr '\n' ' '))"
  fi
}

# --- a healthy checkout ------------------------------------------------------------
fresh
check "passes a fresh copy of the checkout" doctor
check "writes its report to state/doctor.json" test "$(jq -r .ok "$root/state/doctor.json")" = true
check "prints the same report" test "$(jq -r .ok "$scratch/report.json")" = true
check "checks every scheduled playbook" grep -q '^ok   playbook morning' "$scratch/doctor.log"
check "checks the guard's screen" grep -q '^ok   guard screen' "$scratch/doctor.log"

# --- --root checks another checkout, and writes its report there --------------------
other="$scratch/other"
cp -R "$root" "$other"
rm "$other/brain/SOUL.md"
if "$root/bin/doctor" --root "$other" >/dev/null 2>&1; then fail "--root checks the other checkout"; else pass "--root checks the other checkout"; fi
check "--root writes the report into that checkout" test "$(jq -r .ok "$other/state/doctor.json")" = false

# --- each thing a brain edit could break ---------------------------------------------
fresh; rm "$root/brain/SOUL.md"
fails "fails without SOUL.md" "brain/SOUL.md"
fresh; : >"$root/brain/CONTEXT.md"
fails "fails on an empty CONTEXT.md" "brain/CONTEXT.md"
fresh; : >"$root/brain/config.toml"
fails "fails on a config.toml inside the brain" "brain/config.toml"
fresh; printf '[jobs.x]\nschedule = "* * * * *"\ncommand = "bin/heartbeat"\n' >"$root/brain/cron.toml"
fails "fails a cron that fires every minute" "cron.toml"
fresh; printf '[jobs.x]\nschedule = "0 9 * * *"\ncommand = "bin/act gone"\n' >"$root/brain/cron.toml"
fails "fails a cron naming a playbook that does not exist" "cron.toml"
fresh; printf '+++\ndescription = "x"\noutput = "post"\n[[inputs]]\nrun = "rm -rf /"\n+++\ngo\n' >"$root/brain/playbooks/morning.md"
fails "fails a scheduled playbook that runs something other than a hand" "playbook morning"
fresh; printf 'no front matter\n' >"$root/brain/playbooks/someday.md"
fails "fails a playbook nothing schedules yet" "playbook someday"
fresh; sed -i.bak 's/^named = \[\]/named = ["shell"]/' "$root/brain/agents/act.toml"
fails "fails an act agent that was given a tool" "agent act.toml"
fresh; rm "$root/brain/agents/act.toml"
fails "fails without the act agent" "agent act.toml"
# The scheduled agents: they must opt in, name their tools, and keep to their role.
fresh; sed -i.bak 's/^scheduled_actions = true/scheduled_actions = false/' "$root/brain/agents/builder.toml"
fails "fails a builder that does not set scheduled_actions" "cron.toml"
fresh; perl -pi -e 's/^    "shell",/    "shell",\n    "browser",/' "$root/brain/agents/builder.toml"
fails "fails a builder that was given the browser" "agent builder.toml"
fresh; perl -pi -e 's/^    "browser",/    "browser",\n    "shell",/' "$root/brain/agents/operator.toml"
fails "fails an operator that was given the shell" "agent operator.toml"
fresh; perl -pi -e 's/^    "browser",/    "browser",\n    "wallet_send",/' "$root/brain/agents/operator.toml"
fails "fails an operator that was given a wallet tool" "agent operator.toml"
fresh; sed -i.bak 's/mcp_hands_build_done_[0-9a-f]*/mcp_hands_build_done_000000/' "$root/brain/agents/builder.toml"
fails "fails a builder naming a hands tool the core would not register" "agent builder.toml"
fresh; perl -pi -e 's/^id = "teeny-mentions"/id = "teeny-mentions"\nscheduled_actions = true/' "$root/brain/agents/teeny-mentions.toml"
fails "fails a drafting agent that opts into scheduled actions" "agent teeny-mentions.toml"
fresh; printf 'example.com\n*\n' >"$root/deploy/browser-allow.txt"
fails "fails a browser allow list with a wildcard" "browser-allow.txt"
fresh; sed -i.bak 's/^named = \[\]/named = ["shell"]/' "$root/brain/agents/judge.toml"
fails "fails a judge agent that was given a tool" "agent judge.toml"
fresh; rm "$root/brain/agents/judge.toml"
fails "fails without the judge agent" "agent judge.toml"
fresh; sed -i.bak 's/never sell/hold/' "$root/brain/SOUL.md"
fails "fails a soul that no longer says it never sells" "invariants"
check "and names the invariant it lost" grep -q '^FAIL invariants: .*never-sell' "$scratch/doctor.log"
fresh; rm "$root/brain/narrative/LORE.md"
fails "fails a brain that lost the lore's satire rule" "invariants"
fresh; rm "$root/evals/invariants.md"
fails "fails a checkout without evals/invariants.md" "invariants"
fresh; rm -rf "$root/hands/lib/teeny_hands/guard"
fails "fails without the guard" "guard screen"
fresh; printf 'LEXICON = ()\nIDIOMS = ()\n' >"$root/hands/lib/teeny_hands/guard/lexicon.py"
fails "fails a guard whose lexicon was emptied" "guard screen"

# --- pre-spine leftovers warn, never fail --------------------------------------------
fresh; mkdir -p "$root/secrets" "$root/workspace"; : >"$root/secrets/kernel.env"; : >"$root/workspace/storage.db"
check "passes with pre-spine leftovers" doctor
check "but warns about the old kernel env" grep -q '^warn secrets/kernel.env: pre-spine leftover' "$scratch/doctor.log"
check "and the old workspace storage" grep -q '^warn workspace/storage.db: pre-spine leftover' "$scratch/doctor.log"
check "teeny-mentions must stay tool-less" bash -c "grep -q 'teeny-mentions' '$root/bin/lib/agent_check.py'"
fresh; sed -i.bak 's/^named = \[\]/named = ["shell"]/' "$root/brain/agents/teeny-mail.toml"
fails "fails a mail agent that was given a tool" "agent teeny-mail.toml"

# --- storage and secrets ------------------------------------------------------------------
fresh; sqlite3 "$root/brain/storage.db" 'create table t (x); insert into t values (1);'
fails "fails a storage.db without its master key" "secrets/openhuman.key"
mkdir -p "$root/secrets"; (umask 077 && echo key >"$root/secrets/openhuman.key")
check "passes a storage.db with its key" doctor
check "and checks the database itself" grep -q '^ok   storage.db: quick_check ok' "$scratch/doctor.log"
chmod 644 "$root/secrets/openhuman.key"
fails "fails a master key other users can read" "secrets/openhuman.key mode"
chmod 600 "$root/secrets/openhuman.key"
printf 'this is not a database, it is %0500d' 0 >"$root/brain/storage.db"
fails "fails a storage.db that is not a database" "storage.db"

# --- bin/spine will not boot what the doctor fails -----------------------------------------
cat >"$scratch/fake-core" <<'FAKE'
#!/usr/bin/env bash
echo started >>"$FAKE_CORE_LOG"
exit 0
FAKE
chmod +x "$scratch/fake-core"
fresh
printf '[jobs.x]\nschedule = "* * * * *"\ncommand = "bin/heartbeat"\n' >"$root/brain/cron.toml"
: >"$scratch/core.log"
if FAKE_CORE_LOG="$scratch/core.log" OPENHUMAN_CORE_BIN="$scratch/fake-core" OPENHUMAN_CORE_PORT=1 \
  "$root/bin/spine" >"$scratch/spine.log" 2>&1; then
  fail "bin/spine refuses to boot a checkout the doctor fails"
else
  check "bin/spine refuses to boot a checkout the doctor fails" grep -q 'bin/doctor failed' "$scratch/spine.log"
fi
check "and never starts the core" test ! -s "$scratch/core.log"
check "and leaves the reason in state/doctor.json" test "$(jq -r '.checks[] | select(.status == "fail") | .check' "$root/state/doctor.json")" = cron.toml

if [ "$failures" -gt 0 ]; then
  printf '%d failure(s)\n' "$failures" >&2
  exit 1
fi
echo "all passed"
