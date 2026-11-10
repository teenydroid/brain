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
  mkdir -p "$root/hands"
  cp -R "$repo/hands/lib" "$repo/hands/x" "$repo/hands/guard" "$repo/hands/mail" "$repo/hands/moltbook" "$repo/hands/wallet" "$repo/hands/bounty" "$root/hands/"
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

# --- pre-spine leftovers warn, never fail --------------------------------------------
fresh; mkdir -p "$root/secrets" "$root/workspace"; : >"$root/secrets/kernel.env"; : >"$root/workspace/storage.db"
check "passes with pre-spine leftovers" doctor
check "but warns about the old kernel env" grep -q '^warn secrets/kernel.env: pre-spine leftover' "$scratch/doctor.log"
check "and the old workspace storage" grep -q '^warn workspace/storage.db: pre-spine leftover' "$scratch/doctor.log"
check "teeny-mentions must stay tool-less" bash -c "grep -q 'teeny-mentions' '$root/bin/doctor'"
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
