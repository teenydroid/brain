#!/usr/bin/env bash
# Tests for bin/doctor, and for bin/spine refusing to boot a checkout the
# doctor fails. Each case breaks one thing in a scratch copy of the checkout.
# No core needed: the spine is handed a stand-in that only records that it
# was started, and the Gateway's config is written by hand.
#
#   bin/test/doctor_test.sh
set -euo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
repo="$(cd "$here/../.." && pwd)"
# Resolved (pwd -P): the keyring check walks every component, and a symlinked
# temp root (macOS /var) would fail it for the wrong reason.
scratch="$(cd "$(mktemp -d "${TMPDIR:-/tmp}/teeny-doctor.XXXXXX")" && pwd -P)"
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
  cp "$repo/deploy/browser-deny.txt" "$root/deploy/"
  mkdir -p "$root/hands"
  # Every hand a playbook may name, and their library; not the tests.
  cp -R "$repo/hands/lib" "$root/hands/"
  find "$repo/hands" -maxdepth 1 -type f -perm -u+x -exec cp {} "$root/hands/" \;
  # brain/skills/: a playbook names the skills its prompt carries (skills = [...]).
  cp -R "$repo/brain/playbooks" "$repo/brain/agents" "$repo/brain/narrative" "$repo/brain/skills" "$root/brain/"
  cp "$repo/brain/SOUL.md" "$repo/brain/DRIVE.md" "$repo/brain/CHARTER.md" "$repo/brain/CONTEXT.md" "$repo/brain/cron.toml" "$root/brain/"
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
check "says which hand-test sandbox works, never failing for want of one" \
  jq -e '[.checks[] | select(.check == "hand-test sandbox") | .status != "fail"] == [true]' "$scratch/report.json"

# --- a red report outlives the green one that follows it ------------------------------
# state/doctor.json is overwritten each run; the babysitter's rollback sweep reads it
# up to 20 minutes later, so each failing run is also kept in state/doctor-failures.jsonl.
fresh
rm -f "$root/state/doctor-failures.jsonl"
check "a green run leaves no failure record" doctor
check "and no failures file" test ! -e "$root/state/doctor-failures.jsonl"
rm "$root/brain/SOUL.md"
doctor || true
cp "$repo/brain/SOUL.md" "$root/brain/SOUL.md"
check "a red run is kept in state/doctor-failures.jsonl, with its time and failing checks" \
  jq -e -s 'length == 1 and (.[0].at | test("^[0-9]{4}-")) and ([.[0].checks[].check] | index("brain/SOUL.md") != null)' \
  "$root/state/doctor-failures.jsonl"
check "a green run after it passes" doctor
check "and overwrites doctor.json" test "$(jq -r .ok "$root/state/doctor.json")" = true
check "but the red record is still there" test "$(wc -l <"$root/state/doctor-failures.jsonl")" = 1
check "and keeps no detail, only names" jq -e -s '.[0].checks | all(.[]; keys == ["check", "status"])' "$root/state/doctor-failures.jsonl"
check "the history lock and temp files are gone after a run" \
  test -z "$(find "$root/state" -maxdepth 1 \( -name 'doctor-failures.lock' -o -name 'doctor-failures.??????' \))"
mkdir "$root/state/doctor-failures.lock"
touch -d '5 minutes ago' "$root/state/doctor-failures.lock"
rm "$root/brain/SOUL.md"
doctor || true
cp "$repo/brain/SOUL.md" "$root/brain/SOUL.md"
check "a stale lock is broken, not waited on forever" test ! -e "$root/state/doctor-failures.lock"
for _ in $(seq 1 60); do
  printf '{"at":"2026-01-01T00:00:00Z","commit":"x","checks":[]}\n' >>"$root/state/doctor-failures.jsonl"
done
rm "$root/brain/SOUL.md"
doctor || true
check "the failures file is bounded to the latest 50" test "$(wc -l <"$root/state/doctor-failures.jsonl")" = 50

# --- hands/trend's reader: it loads, and needs no binary or network -----------------
fresh
check "names the trend reader as python and digg.com/mcp" doctor
check "as an ok check" grep -q '^ok   trend reader: python, digg.com/mcp' "$scratch/doctor.log"
fresh
mkdir -p "$root/secrets"
check "a leftover TREND_DIGG_BIN setting changes nothing" doctor
OPENHUMAN_DOCKER=1 doctor || true
check "inside the image the check is the same" grep -q '^ok   trend reader: python, digg.com/mcp' "$scratch/doctor.log"
printf 'raise SystemExit("broken")\n' >"$root/hands/lib/teeny_hands/trend/digg.py"
fails "a trend reader that does not load fails" "trend reader"

# --- --root checks another checkout, and writes its report there --------------------
other="$scratch/other"
cp -R "$root" "$other"
rm "$other/brain/SOUL.md"
if "$root/bin/doctor" --root "$other" >/dev/null 2>&1; then fail "--root checks the other checkout"; else pass "--root checks the other checkout"; fi
check "--root writes the report into that checkout" test "$(jq -r .ok "$other/state/doctor.json")" = false
# The box check reads that checkout too: its tools, not this one's.
mkdir -p "$other/home/tools/zed-of-the-other"
echo 'print(1)' >"$other/home/tools/zed-of-the-other/run.py"
"$root/bin/doctor" --root "$other" >/dev/null 2>&1 || true
check "--root lists the other checkout's tools" \
  test "$(jq -r '.checks[] | select(.check == "box") | .detail | contains("zed-of-the-other")' "$other/state/doctor.json")" = true

# --- each thing a brain edit could break ---------------------------------------------
fresh; rm "$root/brain/SOUL.md"
fails "fails without SOUL.md" "brain/SOUL.md"
fresh; : >"$root/brain/CONTEXT.md"
fails "fails on an empty CONTEXT.md" "brain/CONTEXT.md"
fresh; rm "$root/brain/DRIVE.md"
fails "fails without DRIVE.md" "brain/DRIVE.md"
# The charter (docs/governance.md, fence "charter").
fresh; rm "$root/brain/CHARTER.md"
fails "fails without CHARTER.md" "brain/CHARTER.md"
fresh; sed -i.bak 's/responsible for what you do/answerable to nobody/' "$root/brain/CHARTER.md"
fails "fails a charter that no longer names the operator as responsible" "invariants"
fresh; : >"$root/brain/config.toml"
fails "fails on a config.toml inside the brain" "brain/config.toml"
fresh; printf '[jobs.x]\nschedule = "* * * * *"\ncommand = "bin/heartbeat"\n' >"$root/brain/cron.toml"
fails "fails a cron that fires every minute" "cron.toml"
fresh; printf '[jobs.x]\nschedule = "0 9 * * *"\ncommand = "bin/act gone"\n' >"$root/brain/cron.toml"
fails "fails a cron naming a playbook that does not exist" "cron.toml"
fresh; printf '+++\ndescription = "x"\noutput = "post"\n[[inputs]]\nrun = "rm -rf /"\n+++\ngo\n' >"$root/brain/playbooks/morning.md"
fails "fails a scheduled playbook that runs something other than a hand" "playbook morning"
fresh; printf '+++\ndescription = "x"\noutput = "hand:build done"\n+++\ngo\n' >"$root/brain/playbooks/someday.md"
fails "fails a playbook that hands its draft to the retired builder's queue" "playbook someday"
fresh; printf '+++\ndescription = "x"\noutput = "hand:build self"\n+++\ngo\n' >"$root/brain/playbooks/someday.md"
fails "fails a playbook that files a change through the retired builder" "playbook someday"
fresh; printf 'no front matter\n' >"$root/brain/playbooks/someday.md"
fails "fails a playbook nothing schedules yet" "playbook someday"
fresh; sed -i.bak 's/^named = \[\]/named = ["shell"]/' "$root/brain/agents/act.toml"
fails "fails an act agent that was given a tool" "agent act.toml"
fresh; rm "$root/brain/agents/act.toml"
fails "fails without the act agent" "agent act.toml"
fresh; rm "$root/brain/agents/act.md"
fails "fails an act agent whose prompt file has no instructions beside it" "agent act.toml"
# An agent job has no command: the playbook scan must step over it, not stop at it.
fresh; printf '[jobs.aaa]\nschedule = "0 */2 * * *"\nagent = "mind"\nprompt = "build"\n[jobs.morning]\nschedule = "0 7 * * *"\ncommand = "bin/act morning"\n' >"$root/brain/cron.toml"
doctor || true
check "checks a scheduled playbook listed after an agent job" grep -q '^ok   playbook morning' "$scratch/doctor.log"
check "and an agent job does not upset the scan" bash -c "! grep -q 'startswith' '$scratch/doctor.log'"
# The scheduled agents: they must opt in, name their tools, and keep to their role.
fresh; sed -i.bak 's/^scheduled_actions = true/scheduled_actions = false/' "$root/brain/agents/operator.toml"
fails "fails an operator that does not set scheduled_actions" "cron.toml"
fresh; printf '[jobs.old-builder]\nschedule = "10 */2 * * *"\nagent = "builder"\nprompt = "Build the job on your desk."\n' >>"$root/brain/cron.toml"
fails "fails a cron job that runs the retired builder" "cron.toml"
fresh; perl -pi -e 's/^    "shell",/    "shell",\n    "cron_add",/' "$root/brain/agents/mind.toml"
fails "fails a mind that was given a cron tool" "agent mind.toml"
fresh; perl -pi -e 's/^    "browser",/    "browser",\n    "shell",/' "$root/brain/agents/operator.toml"
fails "fails an operator that was given the shell" "agent operator.toml"
fresh; perl -pi -e 's/^    "browser",/    "browser",\n    "wallet_send",/' "$root/brain/agents/operator.toml"
fails "fails an operator that was given a wallet tool" "agent operator.toml"
fresh; perl -pi -e 's/^    "browser",/    "browser",\n    "mcp_github_create_issue",/' "$root/brain/agents/operator.toml"
fails "fails an operator that was given an MCP tool" "agent operator.toml"
fresh; perl -ni -e 'print unless /^    "file_write",/' "$root/brain/agents/mind.toml"
fails "fails a mind that cannot write its home" "agent mind.toml"
# An idle cron run that ends on an empty message is a failed run, every hour.
fresh; perl -pi -e 's/`nothing to do: no task waiting`/nothing/' "$root/brain/agents/operator.toml"
fails "fails an operator whose idle run can end on an empty message" "agent operator.toml"
# The mind's shell is rooted at its home (scheduled_workspace), never the
# checkout that holds brain/ and secrets/; no other agent picks a root.
fresh; perl -ni -e 'print unless /^scheduled_workspace/' "$root/brain/agents/mind.toml"
fails "fails a mind whose shell is not rooted at its home" "agent mind.toml"
fresh; sed -i.bak 's|^scheduled_workspace = .*|scheduled_workspace = "."|' "$root/brain/agents/mind.toml"
fails "fails a mind rooted at the whole checkout" "agent mind.toml"
fresh; perl -pi -e 's/^id = "operator"/id = "operator"\nscheduled_workspace = "state\/workbench"/' "$root/brain/agents/operator.toml"
fails "fails an operator that picks a workspace root" "agent operator.toml"
fresh; perl -pi -e 's/^id = "teeny-mentions"/id = "teeny-mentions"\nscheduled_actions = true/' "$root/brain/agents/teeny-mentions.toml"
fails "fails a drafting agent that opts into scheduled actions" "agent teeny-mentions.toml"
fresh; printf 'example.com\n*\n' >"$root/deploy/browser-allow.txt"
fails "fails a browser allow list with a wildcard" "browser-allow.txt"
fresh; rm "$root/deploy/browser-deny.txt"
fails "fails without the browser's deny list" "browser-deny.txt"
fresh
doctor || true
check "checks the key proxy's list of services" grep -q '^ok   services.toml: 1 service' "$scratch/doctor.log"
fresh; sed -i.bak 's|^base = "https://www.moltbook.com/api/v1"$|base = "http://www.moltbook.com/api/v1"|' "$root/evals/services.toml"
fails "fails a key proxy service that is not https" "services.toml"
fresh; printf '\n[services.extra]\nbase = "https://api.example.com"\nkey = "EXTRA_KEY"\nsurface = "moltbook"\nagent = "a"\nallow = [{ method = "GET", path = "/(" }]\n' >>"$root/evals/services.toml"
fails "fails a key proxy service whose allow regex does not compile" "services.toml"
fresh; rm "$root/evals/services.toml"
fails "fails without the key proxy's list of services" "services.toml"
fresh; sed -i.bak 's/^named = \[\]/named = ["shell"]/' "$root/brain/agents/judge.toml"
fails "fails a judge agent that was given a tool" "agent judge.toml"
fresh; rm "$root/brain/agents/judge.toml"
fails "fails without the judge agent" "agent judge.toml"
# The query turn's privacy rests on its agent having no tools and no memory.
fresh; sed -i.bak 's/^named = \[\]/named = ["shell"]/' "$root/brain/agents/querier.toml"
fails "fails a querier agent that was given a tool" "agent querier.toml"
fresh; sed -i.bak 's/^omit_memory_context = true/omit_memory_context = false/' "$root/brain/agents/querier.toml"
fails "fails a querier agent that loads memory" "agent querier.toml"
fresh; rm "$root/brain/agents/querier.toml"
fails "fails without the querier agent" "agent querier.toml"
fresh; sed -i.bak 's/never sell/hold/' "$root/brain/SOUL.md"
fails "fails a soul that no longer says it never sells" "invariants"
check "and names the invariant it lost" grep -q '^FAIL invariants: .*never-sell' "$scratch/doctor.log"
fresh; sed -i.bak 's/recorded in a ledger/recorded somewhere/' "$root/brain/DRIVE.md"
fails "fails a drive that no longer needs a ledger" "invariants"
check "and names the drive" grep -q '^FAIL invariants: .*drive' "$scratch/doctor.log"
fresh; rm "$root/brain/narrative/VOICE.md"
fails "fails a brain that lost the voice's satire rule" "invariants"
fresh; rm "$root/evals/invariants.md"
fails "fails a checkout without evals/invariants.md" "invariants"
fresh; rm -rf "$root/hands/lib/teeny_hands/guard"
fails "fails without the guard" "guard screen"
# The autonomy ladder (docs/governance.md, fence "ladder"; roadmap G2).
fresh; mkdir -p "$root/secrets"; printf 'EVOLVE=1\nEVOLVE_AUTO_PROMOTE=1\n' >"$root/secrets/evolve.env"
fails "fails EVOLVE_AUTO_PROMOTE=1 while merge is at L4" "autonomy ladder"
check "and names the switch and the level it needs" grep -q '^FAIL autonomy ladder: EVOLVE_AUTO_PROMOTE=1 .*merge at L5' "$scratch/doctor.log"
fresh; mkdir -p "$root/secrets"; printf 'EVOLVE=1\nEVOLVE_AUTO_PROMOTE=0\n' >"$root/secrets/evolve.env"
check "passes EVOLVE=1 with self-edit at L4" doctor
check "and says the switch is within its level" grep -q '^ok   autonomy ladder: 1 switch(es) on' "$scratch/doctor.log"
# Self-merge is its own rung (L5, the operator's decision of 2026-11-15), so
# granting it leaves EVOLVE_AUTO_PROMOTE refused under merge at L4.
# The studio switch: a word that is neither on nor off fails the doctor (it
# posts nothing on either path), and judges on the writer's model warn.
fresh; mkdir -p "$root/secrets"; printf 'STUDIO=maybe\n' >"$root/secrets/teeny.env"
fails "fails a STUDIO that is neither on nor off" "studio switch"
fresh; mkdir -p "$root/secrets"; printf 'STUDIO=1\nSTUDIO_WRITER_MODEL=w\nSAFETY_JUDGE_MODEL=w\n' >"$root/secrets/teeny.env"
doctor || true
check "warns when the studio's judges are the writer's model" grep -q '^warn studio switch: on .*writer' "$scratch/doctor.log"
fresh; mkdir -p "$root/secrets"; printf 'STUDIO=1\nSTUDIO_WRITER_MODEL=w\nSAFETY_JUDGE_MODEL=j\n' >"$root/secrets/teeny.env"
doctor || true
check "and is quiet when they are not" grep -q '^ok   studio switch: on' "$scratch/doctor.log"
fresh; mkdir -p "$root/secrets"; printf 'BABYSIT_MERGE=1\nBABYSIT_SELF_MERGE=1\n' >"$root/secrets/teeny.env"
check "passes BABYSIT_SELF_MERGE=1 with self-merge at L5" doctor
printf 'EVOLVE_AUTO_PROMOTE=1\n' >"$root/secrets/evolve.env"
fails "still fails EVOLVE_AUTO_PROMOTE=1 beside it" "autonomy ladder"
sed -i.bak '/^\[capability.self-merge\]/,/^switch/ s/^level = 5$/level = 4\nnext = { level = 5, also = ["x"] }/' "$root/evals/autonomy.toml"
rm "$root/secrets/evolve.env"
fails "fails BABYSIT_SELF_MERGE=1 once self-merge is under L5" "autonomy ladder"
check "and names the switch and the level it needs" grep -q '^FAIL autonomy ladder: BABYSIT_SELF_MERGE=1 .*self-merge at L5' "$scratch/doctor.log"
fresh; rm "$root/evals/autonomy.toml"
fails "fails a checkout without evals/autonomy.toml" "autonomy ladder"
# A model change (roadmap G8): recorded at boot, the levels on it drop, a switch
# above the dropped level warns (never fails the boot), and the issue is prepared.
fresh; mkdir -p "$root/secrets" "$root/docs"; cp "$repo/docs/governance.md" "$root/docs/"
printf 'SPINE_MODEL=model-a\n' >"$root/secrets/spine.env"; printf 'EVOLVE=1\n' >"$root/secrets/evolve.env"
# Confirmed before today, so a change today counts as after it.
sed -i.bak 's/^confirmed = .*/confirmed = "2020-01-01"/' "$root/evals/autonomy.toml"
check "records the model behind each role" doctor
check "and says nothing changed the first time" grep -q '^ok   model: .*no change' "$scratch/doctor.log"
printf 'SPINE_MODEL=model-b\n' >"$root/secrets/spine.env"
check "a model change still boots" doctor
check "and warns that it changed" grep -q '^warn model: changed: .*model-a -> model-b' "$scratch/doctor.log"
check "and warns that EVOLVE=1 is above self-edit's dropped level" grep -q '^warn autonomy ladder: EVOLVE=1 .*dropped to L3' "$scratch/doctor.log"
check "and prepares the issue" grep -q 'self-edit: L4 -> L3' "$root/secrets/autonomy/issue-pending.json"
fresh; printf 'LEXICON = ()\nIDIOMS = ()\n' >"$root/hands/lib/teeny_hands/guard/lexicon.py"
fails "fails a guard whose lexicon was emptied" "guard screen"

# --- the wallet: keys without sane caps never boot -------------------------------------
fresh
check "passes with no wallet keys" doctor
check "and says the wallet cannot move anything" grep -q '^ok   wallet caps: no wallet keys set' "$scratch/doctor.log"
wallet_env() { mkdir -p "$root/secrets"; printf '%s\n' "$@" >"$root/secrets/teeny.env"; }
caps_ok=(WALLET_MAX_PER_TX_USD=50 WALLET_MAX_PER_DAY_USD=100 WALLET_MAX_PER_RECIPIENT_WEEK_USD=60 WALLET_SOL_USD=200)
fresh; wallet_env WALLET_CLUSTER=devnet "${caps_ok[@]}"
check "passes a wallet with sane caps" doctor
check "and says what the caps are" grep -qF "ok   wallet caps: devnet: \$50.00 a payment" "$scratch/doctor.log"
fresh; wallet_env WALLET_CLUSTER=devnet
fails "fails wallet keys without caps" "wallet caps"
fresh; wallet_env WALLET_MAX_PER_TX_USD=0 WALLET_MAX_PER_DAY_USD=100 WALLET_MAX_PER_RECIPIENT_WEEK_USD=60 WALLET_SOL_USD=200
fails "fails a zero cap" "wallet caps"
fresh; wallet_env WALLET_MAX_PER_TX_USD=50 WALLET_MAX_PER_DAY_USD=1000000 WALLET_MAX_PER_RECIPIENT_WEEK_USD=60 WALLET_SOL_USD=200
fails "fails an absurd cap" "wallet caps"
fresh; wallet_env "${caps_ok[@]}" WALLET_CLUSTER=testnet
fails "fails an unknown cluster" "wallet caps"

# --- the x402 hand: its top-up and search limits are sane -------------------------------
fresh
check "passes with no x402 keys, and says the top-up is off" doctor
check "and names it" grep -q '^ok   x402 settings: top-up off' "$scratch/doctor.log"
fresh; wallet_env X402_FUND=1 X402_FUND_FLOOR_USD=1 X402_FUND_TARGET_USD=5 X402_FUND_MAX_PER_DAY_USD=10
check "passes a sane top-up" doctor
# shellcheck disable=SC2016 # the dollar signs are the doctor's literal words
check "and says what it does" grep -qF 'ok   x402 settings: top-up on: below $1 on Base, up to $5' "$scratch/doctor.log"
fresh; wallet_env X402_FUND=1 X402_FUND_FLOOR_USD=1
fails "fails a top-up switched on without its limits" "x402 settings"
fresh; wallet_env X402_FUND=1 X402_FUND_FLOOR_USD=5 X402_FUND_TARGET_USD=5 X402_FUND_MAX_PER_DAY_USD=10
fails "fails a floor that is not below the target" "x402 settings"
fresh; wallet_env X402_EXA_MAX_USD=50
fails "fails a search limit over its ceiling" "x402 settings"

# --- the keyring's directory: nobody else may write it, from / down -----------------
fresh; chmod 775 "$root/brain"
fails "fails a group-writable brain (the keyring would refuse it)" "keyring path"
check "and names the directory" grep -q '^FAIL keyring path: .*brain: mode 775' "$scratch/doctor.log"
fresh; chmod 775 "$root"
fails "fails a group-writable checkout above the brain" "keyring path"
python3 "$root/bin/lib/private_dirs.py" --fix "$root/brain" "$root" 2>/dev/null >/dev/null || true
check "private_dirs --fix tightens the brain up to the checkout" doctor
check "and the doctor names the keyring path ok" grep -q '^ok   keyring path' "$scratch/doctor.log"

# --- pre-spine leftovers warn, never fail --------------------------------------------
fresh; mkdir -p "$root/secrets" "$root/workspace"; : >"$root/secrets/kernel.env"; : >"$root/workspace/storage.db"
check "passes with pre-spine leftovers" doctor
check "but warns about the old kernel env" grep -q '^warn secrets/kernel.env: pre-spine leftover' "$scratch/doctor.log"
check "and the old workspace storage" grep -q '^warn workspace/storage.db: pre-spine leftover' "$scratch/doctor.log"
# --- the X profile's disclosure line warns, never fails (P1) --------------------------
fresh
check "passes with no X keys" doctor
check "and the X profile check is ok" grep -q '^ok   x profile: no X keys' "$scratch/doctor.log"
mkdir -p "$root/secrets" "$root/state"
printf 'X_API_KEY=k\nX_API_SECRET=s\nX_ACCESS_TOKEN=t\nX_ACCESS_TOKEN_SECRET=ts\nX_OPERATOR_HANDLE=steven\n' >"$root/secrets/teeny.env"
check "passes with X keys and no cached profile" doctor
check "but warns that the bio is unchecked" grep -q '^warn x profile: no cached profile' "$scratch/doctor.log"
bio() { python3 -c '
import sqlite3, sys, time
conn = sqlite3.connect(sys.argv[1])
conn.execute("create table if not exists x_profile (handle text primary key, user_id text, name text, description text not null default \x27\x27, read_at real not null)")
conn.execute("insert or replace into x_profile values (\x27teenydroid\x27, \x271\x27, \x27teeny\x27, ?, ?)", (sys.argv[2], time.time()))
conn.commit()
' "$root/state/hands.db" "$1"; }
bio "a tiny crab"
check "passes a bio without the disclosure line" doctor
check "but warns that it does not say automated" grep -q '^warn x profile: @teenydroid: the bio does not say' "$scratch/doctor.log"
bio "a tiny crab. automated, run by @steven"
check "passes a bio with the disclosure line" doctor
check "and names the X profile ok" grep -q '^ok   x profile: @teenydroid' "$scratch/doctor.log"
check "teeny-mentions must stay tool-less" bash -c "grep -q 'teeny-mentions' '$root/bin/lib/agent_check.py'"
fresh; sed -i.bak 's/^named = \[\]/named = ["shell"]/' "$root/brain/agents/teeny-mail.toml"
fails "fails a mail agent that was given a tool" "agent teeny-mail.toml"
# A drafting agent's turn is one model call, but the harness pauses a session
# turn at max_iterations even after a final answer: at 1 every turn is
# recorded as failed. 2 absorbs that pause; more is a loop it cannot use. The
# mind and act run at exactly 3: a first call that reasons to its output cap
# and comes back empty is retried, and at 2 the pause after that retry fails
# the turn.
fresh; sed -i.bak 's/^max_iterations = 3$/max_iterations = 1/' "$root/brain/agents/teeny.toml"
fails "fails a drafting agent at max_iterations = 1 (the cap pause fails every turn)" "agent teeny.toml"
fresh; sed -i.bak 's/^max_iterations = 3$/max_iterations = 2/' "$root/brain/agents/teeny.toml"
fails "fails the mind at max_iterations = 2 (a retried empty answer is paused and lost)" "agent teeny.toml"
fresh; sed -i.bak 's/^max_iterations = 3$/max_iterations = 4/' "$root/brain/agents/act.toml"
fails "fails act given more iterations than a retry needs" "agent act.toml"
fresh; sed -i.bak 's/^max_iterations = 2$/max_iterations = 8/' "$root/brain/agents/judge.toml"
fails "fails a drafting agent given more iterations than it can use" "agent judge.toml"
fresh; sed -i.bak 's/^max_iterations = 2$/max_iterations = 3/' "$root/brain/agents/teeny-mentions.toml"
fails "fails a drafting agent held to 2 that was given 3" "agent teeny-mentions.toml"
fresh; perl -ni -e 'print unless /^max_iterations/' "$root/brain/agents/act.toml"
fails "fails a drafting agent that leaves max_iterations to the default" "agent act.toml"
fresh; sed -i.bak 's/^named = \[\]/named = ["shell"]/' "$root/brain/agents/teeny.toml"
fails "fails the mind at 3 iterations that was given a tool" "agent teeny.toml"
fresh; sed -i.bak 's/^sandbox_mode = "read_only"$/sandbox_mode = "none"/' "$root/brain/agents/teeny.toml"
fails "fails the mind outside the read_only sandbox" "agent teeny.toml"
for a in teeny act; do
  check "the $a agent says why it runs at max_iterations = 3" \
    bash -c "grep -q '^max_iterations = 3$' '$repo/brain/agents/$a.toml' && grep -q 'cap' '$repo/brain/agents/$a.toml'"
done
for a in judge reviewer querier teeny-mentions teeny-mail; do
  check "the $a agent says why it runs at max_iterations = 2" \
    bash -c "grep -q '^max_iterations = 2$' '$repo/brain/agents/$a.toml' && grep -q 'cap' '$repo/brain/agents/$a.toml'"
done

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

# --- --live: the running core's cron is still moving -----------------------------------------
# A stand-in core: /health is a file curl can read, and cron_list answers from
# $FAKE_JOBS. 2026-11-11: three jobs kept a next_run 75 minutes in the past
# after a reboot, and nothing said so.
mkdir -p "$scratch/core"
: >"$scratch/core/health"
cat >"$scratch/fake-oh" <<'FAKE'
#!/usr/bin/env bash
case "$1" in
  cron_list) cat "$FAKE_JOBS" ;;
  config_get_agent_settings) printf '{"agent_timeout_secs": %s}\n' "${FAKE_AGENT_TIMEOUT:-1800}" ;;
  modules_browser_check_readiness)
    ready='{"module_ready": true, "chrome_ready": true}'
    printf '%s\n' "${FAKE_BROWSER:-$ready}" ;;
  # A turn: refused as the core refused the studio's on 2026-11-19 when its
  # model is in $FAKE_REFUSED (a comma list). Each is logged to $FAKE_TURNS.
  inference_agent_chat|inference_agent_chat_simple)
    # The worker's exec probe: FAKE_EXEC=stale answers what a Gateway whose exec policy refuses would.
    if [ "$(jq -r '.agent_id // ""' <<<"$2")" = worker ]; then
      case "${FAKE_EXEC:-ok}" in
        ok) printf '{"response": "%s"}\n' "$(date +%s)" ;;
        old) printf '{"response": "%s"}\n' "$(( $(date +%s) - 3600 ))" ;;
        *) echo '{"response": "exec denied by approvals policy"}' ;;
      esac
      exit 0
    fi
    printf '%s %s\n' "$1" "$2" >>"${FAKE_TURNS:-/dev/null}"
    m="$(jq -r '.model_override // ""' <<<"$2")"
    if [[ ",${FAKE_REFUSED:-}," == *",$m,"* ]]; then
      echo "[oh] openhuman.$1: driver failed: model error: local-openai returned HTTP 400 (request_rejected): messages: at least one message is required." >&2
      exit 1
    fi
    echo '{"response": "{\"ok\": true}"}' ;;
  get_config) echo '{"config": {"model_registry": [{"id": "w", "context_window": 1000}, {"id": "e", "context_window": 1000}]}}' ;;
  *) echo "unexpected method $1" >&2; exit 1 ;;
esac
FAKE
chmod +x "$scratch/fake-oh"
live_jobs() { # NAME:NEXT_RUN_SECONDS_AGO:ENABLED ...
  local spec name secs enabled jobs='[]'
  for spec in "$@"; do
    IFS=: read -r name secs enabled <<<"$spec"
    jobs="$(jq -c --arg n "$name" --argjson s "$secs" --argjson e "$enabled" '. + [{id: ("id-" + $n), name: $n,
      job_type: "shell", command: "bin/heartbeat", schedule: {kind: "cron", expr: "*/5 * * * *", tz: "UTC"},
      enabled: $e, next_run: (now - $s | floor | todate)}]' <<<"$jobs")"
  done
  printf '%s\n' "$jobs" >"$scratch/jobs.json"
}
# What the core writes for the Gateway, as far as the lockdown reads it.
good_gateway_config() {
  jq -cn '{gateway: {mode: "local", bind: "loopback", auth: {mode: "token"}, controlUi: {enabled: false},
           http: {endpoints: {chatCompletions: {enabled: false}, responses: {enabled: false}}}},
          hooks: {enabled: false}, update: {checkOnStart: false}, discovery: {mdns: {mode: "off"}},
          cron: {skipMissedJobs: true},
          plugins: {allow: ["browser"], slots: {memory: "none"}, entries: {"memory-core": {enabled: false}}}}'
}
# The same config with Teeny's memory plugin owning the slot, as the core writes it when CortexDB is configured.
with_cortex='.plugins.slots.memory = "teeny-cortexdb" | .plugins.allow += ["teeny-cortexdb"]
  | .plugins.load = {paths: ["/srv/teeny/core/plugins/teeny-cortexdb"]}
  | .plugins.entries["teeny-cortexdb"] = {enabled: true, hooks: {allowConversationAccess: true},
      config: {url: "http://127.0.0.1:3141", scope: "s", recall: {agents: {mind: {budgetTokens: 2000}}, timeoutMs: 5000}}}'
put_gateway_config() { # [JQ-FILTER]: write the good config, optionally broken by the filter
  mkdir -p "$root/state/openclaw"
  good_gateway_config | jq -c "${1:-.}" >"$root/state/openclaw/openclaw.json"
}
live_doctor() {
  [ -e "$root/state/openclaw/openclaw.json" ] || [ "${NO_GATEWAY_CONFIG:-0}" = 1 ] || put_gateway_config
  FAKE_AGENT_TIMEOUT="${FAKE_AGENT_TIMEOUT:-1800}" FAKE_JOBS="$scratch/jobs.json" TEENY_OH="$scratch/fake-oh" OPENHUMAN_CORE_URL="file://$scratch/core" \
    "$root/bin/doctor" --live >"$scratch/report.json" 2>"$scratch/doctor.log"
}
fresh
live_jobs teeny.mail-inbound:4500:true teeny.health:4600:true teeny.tick:30:true teeny.late:300:true \
  teeny.paused:7200:false memory_background:-600:true
if live_doctor; then
  fail "--live fails when an enabled job's next run is long past (the doctor passed)"
else
  check "--live fails when an enabled job's next run is long past" \
    grep -q '^FAIL cron next runs are current: ' "$scratch/doctor.log"
fi
check "and names the stuck jobs" grep -q '^FAIL cron next runs are current: .*teeny.mail-inbound.*teeny.health' "$scratch/doctor.log"
check "but not a job five minutes behind" bash -c "! grep '^FAIL cron next runs' '$scratch/doctor.log' | grep -q teeny.late"
check "nor a disabled one" bash -c "! grep '^FAIL cron next runs' '$scratch/doctor.log' | grep -q teeny.paused"
check "and records it in the report" \
  test "$(jq -r '.checks[] | select(.check == "cron next runs are current") | .status' "$root/state/doctor.json")" = fail
live_jobs teeny.mail-inbound:-100:true teeny.tick:30:true teeny.late:300:true teeny.paused:7200:false
live_doctor || true
check "--live passes current next runs" grep -q '^ok   cron next runs are current' "$scratch/doctor.log"

# OpenClaw's memory-core made its own "Memory Dreaming Promotion" job (0 3 * * *) on a lab spine: seed-cron never
# touches a name outside teeny.*, so the doctor has to say a job nobody seeded is there, and say it on one line.
live_jobs teeny.tick:30:true mind.wake-1:-100:true
jq -c '. + [{id: "dream-1", name: "Memory Dreaming Promotion", job_type: "agent", enabled: true, next_run: null}]' "$scratch/jobs.json" >"$scratch/jobs2.json"
mv "$scratch/jobs2.json" "$scratch/jobs.json"
live_doctor || true
check "--live names a job nobody seeded in 'cron in step'" \
  grep -q '^FAIL cron in step: .*unseeded job(s).*Memory Dreaming Promotion' "$scratch/doctor.log"
check "and the cron-in-step failure is one readable line, not seed-cron's log" \
  test "$(grep -c '^FAIL cron in step' "$scratch/doctor.log")" = 1 -a "$(grep '^FAIL cron in step' "$scratch/doctor.log" | grep -c '\[seed-cron\]')" = 0
check "but not the mind's own wakes or teeny.* jobs" bash -c "! grep '^FAIL cron in step' '$scratch/doctor.log' | grep -q 'mind.wake-1'"

# 2026-11-12: the builder's cron turn ran out of the core's default 120 s
# (`[agent] agent_timeout_secs`, which bounds a whole cron agent turn) after
# six model calls, twice, and never reached its tests. bin/spine raises it.
FAKE_AGENT_TIMEOUT=120 live_doctor || true
check "--live fails when a cron agent turn gets only the core's default 120 s" \
  grep -q '^FAIL cron agent turn budget: ' "$scratch/doctor.log"
live_doctor || true
check "--live passes the budget bin/spine sets" grep -q '^ok   cron agent turn budget' "$scratch/doctor.log"

# The core's readiness probe says whether the OpenClaw Gateway is running and
# Chrome is found (under OpenHuman, 2026-11-13, every browser call failed with
# a module error and only the operator's own report said so).
FAKE_BROWSER='{"module_ready": false, "chrome_ready": false, "error": "the OpenClaw gateway is not running"}' live_doctor || true
check "--live fails when the OpenClaw Gateway is not running" \
  grep -q '^FAIL gateway: .*the OpenClaw Gateway is not running' "$scratch/doctor.log"
check "and the browser with it" grep -q '^FAIL browser: .*the OpenClaw gateway is not running' "$scratch/doctor.log"
FAKE_BROWSER='{"module_ready": true, "chrome_ready": false, "error": "no Chrome or Chromium binary found (set CHROME_BIN)"}' live_doctor || true
check "--live fails when no Chrome is found" grep -q '^FAIL browser: .*no Chrome or Chromium binary' "$scratch/doctor.log"
live_doctor || true
check "--live passes a Gateway that is up and a Chrome that is found" grep -q '^ok   browser: ' "$scratch/doctor.log"
check "and says the Gateway is running" grep -q '^ok   gateway: ' "$scratch/doctor.log"
rm -f "$root/state/openclaw/openclaw.json"
NO_GATEWAY_CONFIG=1 live_doctor || true
check "--live fails when the core has not written the Gateway's config" \
  grep -q '^FAIL gateway lockdown: no ' "$scratch/doctor.log"

# 2026-11-19: a studio preview's every first writer and editor turn on
# claude-fable-5.2 and claude-opus-5.5 was refused "messages: at least one
# message is required", the run fell back to the core's default model, and
# only the spine's log said so. --live asks each studio model one turn.
live_doctor || true
check "--live skips the studio models when none is set" \
  grep -q '^ok   studio models answer: no studio model set' "$scratch/doctor.log"
mkdir -p "$root/secrets"; printf 'STUDIO=1\nSTUDIO_WRITER_MODEL=w\nSTUDIO_EDITOR_MODEL=e\nSAFETY_JUDGE_MODEL=j\n' >"$root/secrets/teeny.env"
: >"$scratch/turns.log"
FAKE_TURNS="$scratch/turns.log" live_doctor || true
check "--live passes when every studio model answers its turn" \
  grep -q '^ok   studio models answer: 3 model turn(s) answered: w (agent), e (agent), j (simple)' "$scratch/doctor.log"
check "and asks the writer's and editor's models as an agent turn, the judges' as the simple chat" \
  test "$(sed -E 's/^([a-z_]+) (.*)$/\1 \2/' "$scratch/turns.log" | while read -r m p; do jq -r --arg m "$m" '"\($m) \(.agent_id // "-") \(.model_override)"' <<<"$p"; done | paste -sd, -)" \
    = "inference_agent_chat editor w,inference_agent_chat editor e,inference_agent_chat_simple - j"
FAKE_REFUSED=e live_doctor || true
check "--live fails when a studio model refuses its turn, and names it and why" \
  grep -q '^FAIL studio models answer: refused a turn: e (agent): .*at least one message is required' "$scratch/doctor.log"
FAKE_REFUSED=j live_doctor || true
check "--live fails when the judges' model refuses the simple chat" \
  grep -q '^FAIL studio models answer: refused a turn: j (simple)' "$scratch/doctor.log"
printf 'STUDIO=0\nSTUDIO_EDITOR_MODEL=e\n' >"$root/secrets/teeny.env"
FAKE_REFUSED=e live_doctor || true
check "--live still asks a model set while the studio is off" grep -q '^FAIL studio models answer: refused a turn: e' "$scratch/doctor.log"
DOCTOR_MODEL_TURNS=0 FAKE_REFUSED=e live_doctor || true
check "and DOCTOR_MODEL_TURNS=0 skips the turns" bash -c "! grep -q 'studio models answer' '$scratch/doctor.log'"
rm -f "$root/secrets/teeny.env"

# A stale exec-approvals policy (or a plugin that never loaded) makes a shell agent's exec dead while everything
# else reads healthy: --live has a worker run a harmless command and checks the answer is the real time.
live_doctor || true
check "--live passes when a shell agent's exec runs a command through the Gateway" \
  grep -q '^ok   shell agent exec runs: ' "$scratch/doctor.log"
FAKE_EXEC=denied live_doctor || true
check "--live fails when the shell agent's exec is refused, and says where to look" \
  grep -q '^FAIL shell agent exec runs: .*exec-approvals' "$scratch/doctor.log"
FAKE_EXEC=old live_doctor || true
check "--live fails an answer that is not the current time (a model that never ran it)" \
  grep -q '^FAIL shell agent exec runs: ' "$scratch/doctor.log"
DOCTOR_MODEL_TURNS=0 FAKE_EXEC=denied live_doctor || true
check "and DOCTOR_MODEL_TURNS=0 skips it" bash -c "! grep -q 'shell agent exec runs' '$scratch/doctor.log'"

# The meme service: named only when STUDIO_FORMATS has meme, never a failure;
# --live asks it for its templates (a stand-in memegen on a loopback port).
fresh; doctor || true
check "the meme service is off while no format needs it" grep -q '^ok   meme service: off' "$scratch/doctor.log"
fresh; mkdir -p "$root/secrets"; printf 'STUDIO_FORMATS=post,meme\n' >"$root/secrets/teeny.env"
check "a boot with memes on passes without asking the service" doctor
check "and says where it is" grep -q '^ok   meme service: on, at http://memegen:5000' "$scratch/doctor.log"
printf 'STUDIO_FORMATS=post,meme\nMEMEGEN_URL=ftp://memegen\n' >"$root/secrets/teeny.env"
check "an address that is not http warns and still passes" doctor
check "and says so" grep -q '^warn meme service: MEMEGEN_URL is not an http address' "$scratch/doctor.log"
mkdir -p "$scratch/memegen/templates"
printf '[{"id": "drake", "lines": 2}, {"id": "cmm", "lines": 1}]' >"$scratch/memegen/templates/index.html"
python3 -c '
import functools, http.server, sys
handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=sys.argv[1])
handler.log_message = lambda *a: None
server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
open(sys.argv[2], "w").write(str(server.server_address[1]))
server.serve_forever()
' "$scratch/memegen" "$scratch/memegen.port" &
memegen_pid=$!
for _ in $(seq 50); do [ -s "$scratch/memegen.port" ] && break; sleep 0.1; done
printf 'STUDIO_FORMATS=post,meme\nMEMEGEN_URL=http://127.0.0.1:%s\n' "$(cat "$scratch/memegen.port")" >"$root/secrets/teeny.env"
live_doctor || true
check "--live counts the meme service's templates" grep -q '^ok   meme service: 2 templates at http://127.0.0.1:' "$scratch/doctor.log"
kill "$memegen_pid" 2>/dev/null || true
wait "$memegen_pid" 2>/dev/null || true
live_doctor || true
check "--live warns, never fails, when the meme service is down" \
  test "$(jq -r '.checks[] | select(.check == "meme service") | .status' "$root/state/doctor.json")" = warn
check "and says how to bring it up" grep -q '^warn meme service: no templates from .*--profile memegen up -d memegen' "$scratch/doctor.log"

# Skits: named only when STUDIO_FORMATS has skit, never a failure; says when
# hands/video would refuse every one, or when no shot fits the day's budget.
fresh; doctor || true
check "skits are off while no format names them" grep -q '^ok   skits: off' "$scratch/doctor.log"
fresh; mkdir -p "$root/secrets"; printf 'STUDIO_FORMATS=post,skit\n' >"$root/secrets/teeny.env"
check "a boot with the skit format but no skit budget passes" doctor
check "and warns that hands/video refuses every skit" grep -q '^warn skits: .*MEDIA_SKIT_MAX_PER_DAY is 0' "$scratch/doctor.log"
printf 'STUDIO_FORMATS=post,skit\nMEDIA_SKIT_MAX_PER_DAY=2\nMEDIA_SKIT_MAX_USD_PER_SHOT=1.50\n' >"$root/secrets/teeny.env"
doctor || true
check "a shot cap over the daily budget warns" grep -q '^warn skits: MEDIA_SKIT_MAX_USD_PER_SHOT' "$scratch/doctor.log"
printf 'STUDIO_FORMATS=post,skit\nMEDIA_SKIT_MAX_PER_DAY=2\nMEDIA_MAX_USD_PER_DAY=4.50\n' >"$root/secrets/teeny.env"
doctor || true
check "skits on say their caps, one take and its model, and where they are cut" \
  grep -q '^ok   skits: on: 2 a day, up to 1.60 USD a shot, 2 shot(s) under 4.50 USD a day; one take on venice-wan-3-0-text-to-video (then seedance-2-0-text-to-video, wan-2-7-text-to-video), up to 20 s at 720p, one job a shot if it cannot be had; cut at http://reel:8080' "$scratch/doctor.log"
printf 'STUDIO_FORMATS=post,skit\nMEDIA_SKIT_MAX_PER_DAY=2\nMEDIA_MAX_USD_PER_DAY=4.50\nMEDIA_SKIT_ONE_TAKE=0\n' >"$root/secrets/teeny.env"
doctor || true
check "and say when one take is off" grep -q '^ok   skits: on: .*; one take off: one job a shot; cut at' "$scratch/doctor.log"
printf 'STUDIO_FORMATS=post,skit\nMEDIA_SKIT_MAX_PER_DAY=2\nMEDIA_MAX_USD_PER_DAY=4.50\nREEL_URL=http://127.0.0.1:9\n' \
  >"$root/secrets/teeny.env"
live_doctor || true
check "--live warns, never fails, when the reel service is down" \
  test "$(jq -r '.checks[] | select(.check == "skits") | .status' "$root/state/doctor.json")" = warn
rm -f "$root/secrets/teeny.env"

# Remakes: named only when STUDIO_FORMATS has remake, never a failure; says
# when hands/video would refuse every one, when no chart can be read, when six
# shots would not fit the day's budget, and (--live) when ingest is down.
fresh; doctor || true
check "remakes are off while no format names them" grep -q '^ok   remakes: off' "$scratch/doctor.log"
fresh; mkdir -p "$root/secrets"; printf 'STUDIO_FORMATS=post,remake\n' >"$root/secrets/teeny.env"
check "a boot with the remake format but no remake budget passes" doctor
check "and warns that hands/video refuses every remake" grep -q '^warn remakes: .*MEDIA_REMAKE_MAX_PER_DAY is 0' "$scratch/doctor.log"
printf 'STUDIO_FORMATS=post,remake\nMEDIA_REMAKE_MAX_PER_DAY=1\n' >"$root/secrets/teeny.env"
doctor || true
check "no chart provider warns" grep -q '^warn remakes: .*REMAKE_TIKTOK_ACCOUNTS.*YOUTUBE_API_KEY' "$scratch/doctor.log"
printf 'STUDIO_FORMATS=post,remake\nMEDIA_REMAKE_MAX_PER_DAY=1\nYOUTUBE_API_KEY=k\nMEDIA_REMAKE_MAX_USD_PER_TAKE=6.00\nMEDIA_MAX_USD_PER_DAY=5.00\n' >"$root/secrets/teeny.env"
doctor || true
check "a take cap over the daily budget warns" \
  grep -q '^warn remakes: MEDIA_REMAKE_MAX_USD_PER_TAKE (6.00 USD) is over MEDIA_MAX_USD_PER_DAY (5.00 USD)' "$scratch/doctor.log"
# The per-shot path is the fallback (MEDIA_REMAKE_PER_SHOT=1): only then does
# a shot's cap count. Unset, it is the hand's own default (media/remakes.py,
# PER_SHOT), so six of them are over a day a cent short of six.
shot_default="$(python3 -I -c 'import sys; sys.path.insert(0, sys.argv[1]); from teeny_hands.media.remakes import PER_SHOT as m; print(f"{float(m):.2f}")' "$repo/hands/lib")"
short_day="$(python3 -c "print(f'{6 * $shot_default - 0.01:.2f}')")"
printf 'STUDIO_FORMATS=post,remake\nMEDIA_REMAKE_MAX_PER_DAY=1\nYOUTUBE_API_KEY=k\nMEDIA_REMAKE_PER_SHOT=1\nMEDIA_REMAKE_MAX_USD_PER_TAKE=1.00\nMEDIA_REMAKE_MAX_USD_PER_SHOT=1.00\nMEDIA_MAX_USD_PER_DAY=5.00\n' >"$root/secrets/teeny.env"
doctor || true
check "with the per-shot fallback on, six shots over the daily budget warn" grep -q '^warn remakes: 6 shots x MEDIA_REMAKE_MAX_USD_PER_SHOT (1.00 USD)' "$scratch/doctor.log"
printf 'STUDIO_FORMATS=post,remake\nMEDIA_REMAKE_MAX_PER_DAY=1\nYOUTUBE_API_KEY=k\nMEDIA_REMAKE_PER_SHOT=1\nMEDIA_REMAKE_MAX_USD_PER_TAKE=1.00\nMEDIA_MAX_USD_PER_DAY=%s\n' "$short_day" >"$root/secrets/teeny.env"
doctor || true
check "an unset shot cap is the hand's own default" grep -q "^warn remakes: 6 shots x MEDIA_REMAKE_MAX_USD_PER_SHOT ($shot_default USD)" "$scratch/doctor.log"
printf 'STUDIO_FORMATS=post,remake\nMEDIA_REMAKE_MAX_PER_DAY=1\nYOUTUBE_API_KEY=k\nMEDIA_REMAKE_MAX_USD_PER_TAKE=1.00\nMEDIA_REMAKE_MAX_USD_PER_SHOT=1.00\nMEDIA_MAX_USD_PER_DAY=5.00\n' >"$root/secrets/teeny.env"
doctor || true
check "with the per-shot fallback off, a shot's cap is not warned about" \
  bash -c "! grep -q 'MEDIA_REMAKE_MAX_USD_PER_SHOT' '$scratch/doctor.log'"
printf 'STUDIO_FORMATS=post,remake\nMEDIA_REMAKE_MAX_PER_DAY=1\nYOUTUBE_API_KEY=k\nMEDIA_MAX_USD_PER_DAY=3.00\n' >"$root/secrets/teeny.env"
doctor || true
check "with no take cap set, the hand's own 4.00 USD counts" \
  grep -q '^warn remakes: MEDIA_REMAKE_MAX_USD_PER_TAKE (4.00 USD) is over MEDIA_MAX_USD_PER_DAY (3.00 USD)' "$scratch/doctor.log"
printf 'STUDIO_FORMATS=post,remake\nMEDIA_REMAKE_MAX_PER_DAY=1\nREMAKE_TIKTOK_ACCOUNTS=a,b\nMEDIA_MAX_USD_PER_DAY=6.00\nINGEST_URL=http://ingest:8080\n' >"$root/secrets/teeny.env"
doctor || true
check "remakes on say their caps, one take and its models, charts and ingest" \
  grep -q '^ok   remakes: on: 1 a day, up to 4.00 USD a take under 6.00 USD a day; one take on venice-wan-3-0-image-to-video (then wan-2-7-image-to-video); charts from tiktok; ingest at http://ingest:8080' "$scratch/doctor.log"
printf 'STUDIO_FORMATS=post,remake\nMEDIA_REMAKE_MAX_PER_DAY=1\nYOUTUBE_API_KEY=k\nMEDIA_MAX_USD_PER_DAY=9.00\nMEDIA_REMAKE_ONE_TAKE_MODELS=wan-2-7-image-to-video\n' >"$root/secrets/teeny.env"
doctor || true
check "with no INGEST_URL, the hand's own address is named, and its own one-take models" \
  grep -q '^ok   remakes: on: 1 a day, up to 4.00 USD a take under 9.00 USD a day; one take on wan-2-7-image-to-video; charts from youtube; ingest at http://ingest:8090' "$scratch/doctor.log"
printf 'STUDIO_FORMATS=post,remake\nMEDIA_REMAKE_MAX_PER_DAY=1\nYOUTUBE_API_KEY=k\nMEDIA_MAX_USD_PER_DAY=20\nINGEST_URL=http://127.0.0.1:9\n' \
  >"$root/secrets/teeny.env"
live_doctor || true
check "--live warns, never fails, when ingest does not answer" \
  grep -q '^warn remakes: the ingest service at http://127.0.0.1:9 does not answer' "$scratch/doctor.log"
rm -f "$root/secrets/teeny.env"

# --- the core and its OpenClaw Gateway: the lockdown, the audit, the runtime ------------
# The core writes the Gateway's config to state/openclaw/openclaw.json; the
# doctor reads the flags back and runs `openclaw security audit` on it.
cat >"$scratch/audit-core" <<'FAKE'
#!/usr/bin/env bash
# A stand-in core: `openclaw security audit --json` answers $FAKE_AUDIT.
[ "$1 $2 $3" = "openclaw security audit" ] || exit 2
printf '%s\n' "$FAKE_AUDIT"
FAKE
chmod +x "$scratch/audit-core"
audit_doctor() { OPENHUMAN_CORE_BIN="$scratch/audit-core" FAKE_AUDIT="$1" doctor; }
CLEAN_AUDIT='{"findings": []}'
fresh
check "before the core has written a config the lockdown only warns" doctor
check "and says it waits for the core" grep -q '^warn gateway lockdown: no .*openclaw.json yet' "$scratch/doctor.log"
put_gateway_config
check "a locked-down config passes" audit_doctor "$CLEAN_AUDIT"
put_gateway_config '.gateway.auth.token = {source: "file", provider: "teeny_gateway_token", id: "value"} | .models = {providers: {m: {apiKey: {source: "file", provider: "k", id: "value"}}}}'
check "and so does one whose secrets are SecretRefs" audit_doctor "$CLEAN_AUDIT"
check "and says how many flags hold" grep -q '^ok   gateway lockdown: 9 lockdown flags hold' "$scratch/doctor.log"
for broken in \
  '.hooks.enabled = true|hooks.enabled is true' \
  '.gateway.http.endpoints.chatCompletions.enabled = true|chatCompletions.enabled is true' \
  '.gateway.http.endpoints.responses.enabled = true|responses.enabled is true' \
  '.update.checkOnStart = true|update.checkOnStart is true' \
  '.gateway.bind = "lan"|gateway.bind is "lan"' \
  '.gateway.mode = "remote"|gateway.mode is "remote"' \
  'del(.hooks)|hooks.enabled is not set' \
  '.channels = {telegram: {enabled: true}}|channels is configured' \
  '.bindings = [{agentId: "x"}]|bindings is configured' \
  '.gateway.auth.token = "abc"|gateway.auth.token is in the config file' \
  'del(.plugins.slots)|plugins.slots.memory is not set' \
  '.plugins.slots.memory = "memory-core"|plugins.slots.memory is "memory-core"' \
  '.plugins.entries["memory-core"].enabled = true|plugins.entries.memory-core.enabled is not false' \
  '.plugins.allow += ["teeny-cortexdb"]|teeny-cortexdb is in the plugins config but does not own plugins.slots.memory'; do
  filter="${broken%%|*}" reason="${broken#*|}"
  fresh
  put_gateway_config "$filter"
  fails "a config with $reason fails the doctor" "gateway lockdown"
  check "and names it ($reason)" grep -qF "$reason" "$scratch/doctor.log"
done
# Teeny's memory plugin on the slot: the config the core writes when CortexDB is configured.
fresh
put_gateway_config "$with_cortex"
check "a config with teeny-cortexdb on the memory slot passes" audit_doctor "$CLEAN_AUDIT"
check "and says whose slot it is" grep -q '^ok   gateway lockdown: 9 lockdown flags hold.*memory slot teeny-cortexdb' "$scratch/doctor.log"
for broken in \
  'del(.plugins.allow[] | select(. == "teeny-cortexdb"))@@plugins.allow does not name teeny-cortexdb' \
  '.plugins.entries["teeny-cortexdb"].enabled = false@@plugins.entries.teeny-cortexdb.enabled is not true' \
  'del(.plugins.load)@@plugins.load.paths does not load core/plugins/teeny-cortexdb' \
  '.plugins.entries["teeny-cortexdb"].config.apiKey = "cortex-key-fake"@@config.apiKey holds a secret value' \
  '.plugins.entries["teeny-cortexdb"].config.auth.bearer = "abc"@@config.auth.bearer holds a secret value' \
  '.plugins.entries["teeny-cortexdb"].config.url = "http://cortex.example.com:3141"@@config.url is not https or loopback' \
  '.plugins.entries["teeny-cortexdb"].config.url = "ftp://x"@@config.url is not https or loopback' \
  '.plugins.entries["teeny-cortexdb"].config.url = "http://127.0.0.1:pw@cortex.example.com:3141"@@config.url is not https or loopback' \
  'del(.plugins.entries["teeny-cortexdb"].config)@@plugins.entries.teeny-cortexdb.config is not set'; do
  filter="${broken%%@@*}" reason="${broken#*@@}"
  fresh
  put_gateway_config "$with_cortex | $filter"
  fails "a memory plugin config with $reason fails the doctor" "gateway lockdown"
  check "and names it ($reason)" grep -qF "$reason" "$scratch/doctor.log"
done
fresh
put_gateway_config "$with_cortex | .plugins.entries[\"teeny-cortexdb\"].config.recall.agents.mind.budgetTokens = 3000"
check "budget counts named tokens are not mistaken for secrets" audit_doctor "$CLEAN_AUDIT"
for loop_url in "http://[::1]:3141" "http://127.0.0.2:3141" "http://localhost:3141"; do
  fresh
  put_gateway_config "$with_cortex | .plugins.entries[\"teeny-cortexdb\"].config.url = \"$loop_url\""
  check "a loopback memory url ($loop_url) passes" audit_doctor "$CLEAN_AUDIT"
done
# Turns traced to the private Langfuse: the exporter as the core writes it (core/src/gateway/otel.ts).
with_otel='.plugins.allow += ["diagnostics-otel"] | .plugins.entries["diagnostics-otel"] = {enabled: true}
  | .diagnostics = {enabled: true, otel: {enabled: true, endpoint: "http://otel-collector:4318", protocol: "http/protobuf",
      traces: true, metrics: false, logs: false, sampleRate: 1, captureContent: true}}'
fresh
put_gateway_config "$with_otel"
check "a config tracing turns to the private collector passes" audit_doctor "$CLEAN_AUDIT"
for broken in \
  '.diagnostics.otel.endpoint = "http://traces.example.com:4318"@@diagnostics.otel.endpoint is not https, loopback or the otel-collector service' \
  '.diagnostics.otel.endpoint = "http://collector:4318"@@diagnostics.otel.endpoint is not https' \
  '.diagnostics.otel.tracesEndpoint = "http://10.1.2.3:4318/v1/traces"@@diagnostics.otel.tracesEndpoint is not https' \
  '.diagnostics.otel.endpoint = "http://pk:sk@otel-collector:4318"@@diagnostics.otel.endpoint is not https' \
  '.diagnostics.otel.headers = {Authorization: "Basic cGs6c2s="}@@diagnostics.otel.headers is set'; do
  filter="${broken%%@@*}" reason="${broken#*@@}"
  fresh
  put_gateway_config "$with_otel | $filter"
  fails "a tracing config with $reason fails the doctor" "gateway lockdown"
  check "and names it ($reason)" grep -qF "$reason" "$scratch/doctor.log"
done
fresh
put_gateway_config
printf 'not json' >"$root/state/openclaw/openclaw.json"
fails "an unreadable config fails the doctor" "gateway lockdown"

fresh
put_gateway_config
audit_doctor '{"summary": {"critical": 0, "warn": 0, "info": 1}, "findings": [{"checkId": "x.info", "severity": "info"}]}' || true
check "a clean security audit passes" grep -q '^ok   openclaw security audit: no critical or warning findings (1 informational)' "$scratch/doctor.log"
audit_doctor '{"findings": [{"checkId": "tools.exec.fs_tools_disabled_but_exec_enabled", "severity": "warn"}]}' || true
check "an audit warning only warns" grep -q '^warn openclaw security audit: warning(s): tools.exec.fs_tools_disabled_but_exec_enabled' "$scratch/doctor.log"
if audit_doctor '{"findings": [{"checkId": "gateway.bind_no_auth", "severity": "critical"}]}'; then
  fail "a critical audit finding fails the doctor (it passed)"
else
  check "a critical audit finding fails the doctor" grep -q '^FAIL openclaw security audit: critical finding(s): gateway.bind_no_auth' "$scratch/doctor.log"
fi
if audit_doctor 'the audit crashed'; then
  fail "an audit that prints no JSON fails the doctor (it passed)"
else
  check "an audit that prints no JSON fails the doctor" grep -q '^FAIL openclaw security audit: the audit printed no JSON' "$scratch/doctor.log"
fi
fresh
OPENHUMAN_CORE_BIN="$scratch/no-such-core" fails "a missing core binary fails the doctor" "core runtime"
check "and says where it looked" grep -q "no executable core at $scratch/no-such-core" "$scratch/doctor.log"
check "the lockdown script itself refuses a config it cannot read" bash -c "! python3 '$repo/bin/lib/gateway_check.py' lockdown '$scratch/none.json' >/dev/null"

# --- TEENY_INBOX=core: this core has no inbound queue -----------------------------------
fresh
check "the default inbox passes" doctor
check "and is the stimuli inbox" grep -q '^ok   inbox: mind' "$scratch/doctor.log"
mkdir -p "$root/secrets"
printf 'TEENY_INBOX=core\n' >"$root/secrets/teeny.env"
fails "TEENY_INBOX=core in secrets/teeny.env fails the doctor" "inbox"
printf 'TEENY_INBOX="Core"\n' >"$root/secrets/teeny.env"
fails "however it is spelled" "inbox"
printf 'TEENY_INBOX=mind\n' >"$root/secrets/teeny.env"
check "TEENY_INBOX=mind passes" doctor
rm -f "$root/secrets/teeny.env"

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

# --- bin/spine hands the core the wallet policy and the cluster ----------------------------
cat >"$scratch/env-core" <<'FAKE'
#!/usr/bin/env bash
printf 'cluster=%s rpc=%s override=%s\n' "${OPENHUMAN_SOLANA_CLUSTER:-}" "${OPENHUMAN_WALLET_RPC_SOLANA:-}" \
  "${OPENHUMAN_WALLET_POLICY_ASSETS:-unset}" >>"$FAKE_CORE_LOG"
exit 0
FAKE
chmod +x "$scratch/env-core"
fresh
wallet_env WALLET_CLUSTER=mainnet "${caps_ok[@]}" SOLANA_RPC_URL=https://rpc.example/key
: >"$scratch/core.log"
FAKE_CORE_LOG="$scratch/core.log" OPENHUMAN_CORE_BIN="$scratch/env-core" OPENHUMAN_CORE_PORT=1 SPINE_BOOT_SECS=2 \
  OPENHUMAN_WALLET_POLICY_ASSETS='[{"chain":"solana","symbol":"SOL"}]' "$root/bin/spine" >"$scratch/spine.log" 2>&1 || true
check "bin/spine gives the core the cluster and the RPC, and drops policy overrides" \
  grep -qx 'cluster=mainnet rpc=https://rpc.example/key override=unset' "$scratch/core.log"
check "and writes a capped policy that applies to RPC executes" \
  grep -q '^apply_to_interactive = true$' "$root/.openhuman/config.toml"
check "with USDC's mainnet mint pinned" \
  grep -q '^token_address = "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"$' "$root/.openhuman/config.toml"
fresh
wallet_env WALLET_CLUSTER=devnet
: >"$scratch/core.log"
if FAKE_CORE_LOG="$scratch/core.log" OPENHUMAN_CORE_BIN="$scratch/env-core" OPENHUMAN_CORE_PORT=1 \
  "$root/bin/spine" >"$scratch/spine.log" 2>&1; then
  fail "bin/spine refuses wallet keys without caps"
else
  check "bin/spine refuses wallet keys without caps" test ! -s "$scratch/core.log"
fi

if [ "$failures" -gt 0 ]; then
  printf '%d failure(s)\n' "$failures" >&2
  exit 1
fi
echo "all passed"
