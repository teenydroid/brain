#!/bin/bash
# Rebuilds giveaway_tx_rust.json from the UNMODIFIED spine/src/giveaway_tx.rs.
#
#   core/test/wallet/fixtures/rust-parity/run.sh
#
# Builds a one-file crate in a throwaway rust:1.96.1 container (its own copy of
# the file, its own target dir under a temp folder, nothing in the checkout),
# runs the deterministic requests in ../giveaway_tx_gen_cases.py through it and
# writes the answers to ../giveaway_tx_rust.json's `cases`. The one random
# thing, a fresh seed keypair, is not in the fixtures: the TypeScript test
# checks its signature instead (a different seed is a different distribution
# address, so there is nothing to compare).
set -euo pipefail
here="$(cd "$(dirname "$0")" && pwd)"
root="$(cd "$here/../../../../.." && pwd)"
work="$(mktemp -d)"
trap 'rm -rf "$work"' EXIT
mkdir -p "$work/spine/src" "$work/hands/tests/giveaway/fixtures"
cp "$root/spine/src/giveaway_tx.rs" "$work/spine/src/"
cp "$here/main.rs" "$work/spine/src/main.rs"
cp "$here/Cargo.toml" "$work/spine/Cargo.toml"
cp "$root/hands/tests/giveaway/fixtures/rewards_tx.json" "$work/hands/tests/giveaway/fixtures/"
chmod -R a+rwX "$work"
docker run --rm -u "$(id -u):$(id -g)" -e HOME=/tmp -e CARGO_HOME=/tmp/cargo \
  -e PATH=/usr/local/cargo/bin:/usr/local/bin:/usr/bin:/bin \
  -v "$work:/x" -w /x/spine rust:1.96.1-bookworm bash -c 'cargo test --release && cargo build --release'
PARITY_DIR="$work" python3 "$here/../giveaway_tx_gen_cases.py" >/dev/null
python3 - "$work/cases/results.json" "$here/../giveaway_tx_rust.json" <<'PY'
import json, sys
cases = [c for c in json.load(open(sys.argv[1])) if c["name"] != "err_seed_null"]
old = json.load(open(sys.argv[2]))
json.dump({"note": old["note"], "cases": cases}, open(sys.argv[2], "w"), indent=1)
PY
echo "wrote $(realpath "$here/../giveaway_tx_rust.json")"
