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
  mkdir -p "$root/hands"
  # Every hand a playbook may name, and their library; not the tests.
  cp -R "$repo/hands/lib" "$root/hands/"
  find "$repo/hands" -maxdepth 1 -type f -perm -u+x -exec cp {} "$root/hands/" \;
  cp -R "$repo/brain/playbooks" "$repo/brain/agents" "$repo/brain/narrative" "$root/brain/"
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
fails "fails a playbook that hands its draft to the builder's queue" "playbook someday"
fresh; printf 'no front matter\n' >"$root/brain/playbooks/someday.md"
fails "fails a playbook nothing schedules yet" "playbook someday"
fresh; sed -i.bak 's/^named = \[\]/named = ["shell"]/' "$root/brain/agents/act.toml"
fails "fails an act agent that was given a tool" "agent act.toml"
fresh; rm "$root/brain/agents/act.toml"
fails "fails without the act agent" "agent act.toml"
fresh; rm "$root/brain/agents/act.md"
fails "fails an act agent whose prompt file has no instructions beside it" "agent act.toml"
# An agent job has no command: the playbook scan must step over it, not stop at it.
fresh; printf '[jobs.aaa]\nschedule = "0 */2 * * *"\nagent = "builder"\nprompt = "build"\n[jobs.morning]\nschedule = "0 7 * * *"\ncommand = "bin/act morning"\n' >"$root/brain/cron.toml"
doctor || true
check "checks a scheduled playbook listed after an agent job" grep -q '^ok   playbook morning' "$scratch/doctor.log"
check "and an agent job does not upset the scan" bash -c "! grep -q 'startswith' '$scratch/doctor.log'"
# The scheduled agents: they must opt in, name their tools, and keep to their role.
fresh; sed -i.bak 's/^scheduled_actions = true/scheduled_actions = false/' "$root/brain/agents/builder.toml"
fails "fails a builder that does not set scheduled_actions" "cron.toml"
fresh; perl -pi -e 's/^    "shell",/    "shell",\n    "browser",/' "$root/brain/agents/builder.toml"
fails "fails a builder that was given the browser" "agent builder.toml"
fresh; perl -pi -e 's/^    "browser",/    "browser",\n    "shell",/' "$root/brain/agents/operator.toml"
fails "fails an operator that was given the shell" "agent operator.toml"
fresh; perl -pi -e 's/^    "browser",/    "browser",\n    "wallet_send",/' "$root/brain/agents/operator.toml"
fails "fails an operator that was given a wallet tool" "agent operator.toml"
fresh; perl -pi -e 's/^    "browser",/    "browser",\n    "mcp_github_create_issue",/' "$root/brain/agents/operator.toml"
fails "fails an operator that was given an MCP tool" "agent operator.toml"
fresh; perl -ni -e 'print unless /^    "file_write",/' "$root/brain/agents/builder.toml"
fails "fails a builder that cannot write its report" "agent builder.toml"
# An idle cron run that ends on an empty message is a failed run, every hour.
fresh; perl -pi -e 's/`nothing to do: no task waiting`/nothing/' "$root/brain/agents/operator.toml"
fails "fails an operator whose idle run can end on an empty message" "agent operator.toml"
# The builder's shell is rooted at the workbench (scheduled_workspace), never
# the checkout that holds brain/ and secrets/; no other agent picks a root.
fresh; perl -ni -e 'print unless /^scheduled_workspace/' "$root/brain/agents/builder.toml"
fails "fails a builder whose shell is not rooted at the workbench" "agent builder.toml"
fresh; sed -i.bak 's|^scheduled_workspace = .*|scheduled_workspace = "."|' "$root/brain/agents/builder.toml"
fails "fails a builder rooted at the whole checkout" "agent builder.toml"
fresh; perl -pi -e 's/^id = "operator"/id = "operator"\nscheduled_workspace = "state\/workbench"/' "$root/brain/agents/operator.toml"
fails "fails an operator that picks a workspace root" "agent operator.toml"
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
fresh; sed -i.bak 's/recorded in a ledger/recorded somewhere/' "$root/brain/DRIVE.md"
fails "fails a drive that no longer needs a ledger" "invariants"
check "and names the drive" grep -q '^FAIL invariants: .*drive' "$scratch/doctor.log"
fresh; rm "$root/brain/narrative/LORE.md"
fails "fails a brain that lost the lore's satire rule" "invariants"
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
check "teeny-mentions must stay tool-less" bash -c "grep -q 'teeny-mentions' '$root/bin/lib/agent_check.py'"
fresh; sed -i.bak 's/^named = \[\]/named = ["shell"]/' "$root/brain/agents/teeny-mail.toml"
fails "fails a mail agent that was given a tool" "agent teeny-mail.toml"
# A drafting agent's turn is one model call, but the harness pauses a session
# turn at max_iterations even after a final answer: at 1 every turn is
# recorded as failed. 2 absorbs that pause; more is a loop it cannot use.
fresh; sed -i.bak 's/^max_iterations = 2$/max_iterations = 1/' "$root/brain/agents/teeny.toml"
fails "fails a drafting agent at max_iterations = 1 (the cap pause fails every turn)" "agent teeny.toml"
fresh; sed -i.bak 's/^max_iterations = 2$/max_iterations = 8/' "$root/brain/agents/judge.toml"
fails "fails a drafting agent given more iterations than it can use" "agent judge.toml"
fresh; perl -ni -e 'print unless /^max_iterations/' "$root/brain/agents/act.toml"
fails "fails a drafting agent that leaves max_iterations to the default" "agent act.toml"
for a in teeny act judge teeny-mentions teeny-mail; do
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
  config_get_agent_settings) printf '{"agent_timeout_secs": %s}\n' "${FAKE_AGENT_TIMEOUT:-900}" ;;
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
live_doctor() {
  FAKE_AGENT_TIMEOUT="${FAKE_AGENT_TIMEOUT:-900}" FAKE_JOBS="$scratch/jobs.json" TEENY_OH="$scratch/fake-oh" OPENHUMAN_CORE_URL="file://$scratch/core" \
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

# 2026-11-12: the builder's cron turn ran out of the core's default 120 s
# (`[agent] agent_timeout_secs`, which bounds a whole cron agent turn) after
# six model calls, twice, and never reached its tests. bin/spine raises it.
FAKE_AGENT_TIMEOUT=120 live_doctor || true
check "--live fails when a cron agent turn gets only the core's default 120 s" \
  grep -q '^FAIL cron agent turn budget: ' "$scratch/doctor.log"
live_doctor || true
check "--live passes the budget bin/spine sets" grep -q '^ok   cron agent turn budget' "$scratch/doctor.log"

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
