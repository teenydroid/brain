#!/usr/bin/env bash
# The last commit of the OpenClaw migration: take the old Rust core out of the
# tree. Run it once, from a clean checkout of the migration branch, after every
# other phase has merged and nothing reads vendor/openhuman any more:
#
#   core/scripts/remove-openhuman.sh --list      print what it would remove, change nothing
#   core/scripts/remove-openhuman.sh             remove it, stage it, leave the commit to you
#
# It removes:
#   spine/                      teeny-spine, OpenHuman's host (Cargo package, src/)
#   Cargo.toml, Cargo.lock      the workspace that built it
#   rust-toolchain.toml         its pinned toolchain
#   vendor/openhuman            the submodule: its .gitmodules section, its git
#                               config and its .git/modules/vendor/openhuman
#   docs/oh-gaps.md             what the Rust core needed from OpenHuman, and
#   docs/oh-defects.md          the defects found in it (history, not a spec)
# and edits:
#   .github/workflows/check.yml the block between `openhuman-removal: begin` and
#                               `end` (the submodule fetch and the one hands test
#                               that reads OpenHuman's source)
#   .dockerignore               the `spine` line (`vendor` stays: it keeps the other submodules out)
#   docs/architecture.md        the notes saying the Rust core is still in the tree
#
# It does NOT touch evals/frozen.txt (a frozen list only gains paths: its
# spine/** and vendor/** lines are harmless once the folders are gone), nor
# bin/lib/core_sections/ (it writes the config.toml the TypeScript core still
# reads: the cron shell timeout and the wallet policy).
#
# Afterwards it prints every remaining mention of OpenHuman's source tree, for
# you to read: docs/developers.md ("Bumping OpenHuman", the cargo commands),
# hands/tests/test_prompts.py (the test that reads vendor/openhuman, which skips
# when it is absent), and comments in bin/lib that cite OpenHuman's files.
set -euo pipefail

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$root"

paths=(spine Cargo.toml Cargo.lock rust-toolchain.toml vendor/openhuman docs/oh-gaps.md docs/oh-defects.md)

if [ "${1:-}" = --list ]; then
  for p in "${paths[@]}"; do
    [ -e "$p" ] && printf 'remove %s\n' "$p" || printf 'absent %s\n' "$p"
  done
  printf 'edit   .github/workflows/check.yml (the openhuman-removal block)\n'
  printf 'edit   .dockerignore (spine)\n'
  printf 'edit   docs/architecture.md (the note that the Rust core is still in the tree)\n'
  printf 'edit   .gitmodules (the vendor/openhuman section)\n'
  exit 0
fi
[ $# -eq 0 ] || { echo "usage: remove-openhuman.sh [--list]" >&2; exit 2; }

[ -z "$(git status --porcelain)" ] || { echo "remove-openhuman: the tree is not clean; commit or stash first" >&2; exit 1; }

# The submodule first: deinit, then remove it from the index and .gitmodules.
if git ls-files --stage vendor/openhuman | grep -q '^160000'; then
  git submodule deinit -f vendor/openhuman >/dev/null 2>&1 || true
  git rm -rf --quiet vendor/openhuman
  git config -f .gitmodules --remove-section submodule.vendor/openhuman 2>/dev/null || true
  git add .gitmodules 2>/dev/null || true
  rm -rf .git/modules/vendor/openhuman
  [ -s .gitmodules ] || git rm -f --quiet .gitmodules 2>/dev/null || true
fi
for p in spine Cargo.toml Cargo.lock rust-toolchain.toml docs/oh-gaps.md docs/oh-defects.md; do
  [ -e "$p" ] && git rm -rf --quiet "$p"
done

python3 -I - <<'PY'
import re
from pathlib import Path

ci = Path(".github/workflows/check.yml")
text = ci.read_text()
text = re.sub(r"      # openhuman-removal: begin.*?      # openhuman-removal: end\n", "", text, flags=re.S)
ci.write_text(text)

di = Path(".dockerignore")
di.write_text("".join(l for l in di.read_text().splitlines(True) if l.strip() != "spine"))

arch = Path("docs/architecture.md")
text = arch.read_text()
text = re.sub(r"\(Until the removal commit lands.*?contract fixtures in `core/test/contract/`\.\)\n\n", "", text, flags=re.S)
text = re.sub(r"`docs/oh-gaps.md` and `docs/oh-defects.md` are the history.*?removal commit\.\n\n", "", text, flags=re.S)
arch.write_text(text)
PY
git add -A .github .dockerignore docs

echo
echo "removed. Staged, not committed. What still mentions OpenHuman's tree (read these):"
git grep -n -I -e 'vendor/openhuman' -e 'cargo build' -e 'teeny-spine' -e 'rust-toolchain' -- \
  . ':!evals/frozen.txt' ':!core/scripts/remove-openhuman.sh' ':!deploy/compose.yaml' | cut -c1-160 | head -60 || true
echo
echo "(deploy/compose.yaml names the image teeny-spine:current and the compose project teeny-spine: those are names, not the Rust package.)"
