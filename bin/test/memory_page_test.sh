#!/usr/bin/env bash
# brain/MEMORY.md is Teeny's live page: bin/reflect rewrites it on the box
# twice a day, so it is not in git. The repository keeps where it started,
# brain/MEMORY.seed.md (bin/lib/prompts.py starts the live page from it).
#
# Checks that the live page is ignored and the seed tracked, then, in a
# throwaway pair of repositories, what pulling the commit that stopped
# tracking it does to a checkout that already has a live page (the box): a
# plain pull refuses and touches nothing, and the steps in deploy/README.md
# bring the checkout up to date with the live page kept.
#
#   bin/test/memory_page_test.sh
set -euo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
repo="$(cd "$here/../.." && pwd)"
scratch="$(mktemp -d "${TMPDIR:-/tmp}/teeny-memory.XXXXXX")"
trap 'rm -rf "$scratch"' EXIT

failures=0
pass() { printf 'ok   %s\n' "$1"; }
fail() { printf 'FAIL %s\n' "$1" >&2; failures=$((failures + 1)); }
check() { local name="$1"; shift; if "$@"; then pass "$name"; else fail "$name"; fi; }
g() { git -C "$1" -c user.name=test -c user.email=test@example.invalid -c commit.gpgsign=false "${@:2}"; }

# --- this repository -----------------------------------------------------------
check "the seed is tracked" sh -c "git -C '$repo' ls-files --error-unmatch brain/MEMORY.seed.md >/dev/null 2>&1"
check "the live page is not tracked" sh -c "! git -C '$repo' ls-files --error-unmatch brain/MEMORY.md >/dev/null 2>&1"
check "the live page is ignored" g "$repo" check-ignore -q brain/MEMORY.md

# --- pulling the untracking commit onto a checkout with a live page --------------
# Upstream before: the page tracked as brain/MEMORY.md, no ignore rule. After:
# this repository's .gitignore and seed.
up="$scratch/up"
box="$scratch/box"
mkdir -p "$up/brain"
g "$up" init -q -b main
grep -vxF /brain/MEMORY.md "$repo/.gitignore" >"$up/.gitignore"
cp "$repo/brain/MEMORY.seed.md" "$up/brain/MEMORY.md"
g "$up" add -A
g "$up" commit -q -m "the page, tracked"
git clone -q "$up" "$box"
g "$up" mv brain/MEMORY.md brain/MEMORY.seed.md
cp "$repo/.gitignore" "$up/.gitignore"
g "$up" add -A
g "$up" commit -q -m "the page, untracked"
after="$(g "$up" rev-parse HEAD)"

# The box: Teeny has rewritten its page since the clone.
printf '\n- the box wrote this tonight.\n' >>"$box/brain/MEMORY.md"
cp "$box/brain/MEMORY.md" "$scratch/live"

code=0
g "$box" pull -q --ff-only >/dev/null 2>&1 || code=$?
check "a plain pull refuses" test "$code" != 0
check "and leaves the live page as it was" cmp -s "$scratch/live" "$box/brain/MEMORY.md"

# The deploy note: move the page aside, pull, put it back.
mkdir -p "$box/state"
mv "$box/brain/MEMORY.md" "$box/state/MEMORY.live.md"
check "with the page moved aside the pull fast-forwards" g "$box" pull -q --ff-only
mv "$box/state/MEMORY.live.md" "$box/brain/MEMORY.md"
check "the checkout is on the untracking commit" test "$(g "$box" rev-parse HEAD)" = "$after"
check "the live page is Teeny's, as it was" cmp -s "$scratch/live" "$box/brain/MEMORY.md"
check "the seed is upstream's" cmp -s "$repo/brain/MEMORY.seed.md" "$box/brain/MEMORY.seed.md"
check "the checkout is clean" test -z "$(g "$box" status --porcelain)"

# A checkout whose page was never rewritten: the pull removes it, and the next
# build of the prompts (bin/spine, at boot) starts it again from the seed.
fresh="$scratch/fresh"
git clone -q "$up" "$fresh"
g "$fresh" checkout -q HEAD~1
g "$fresh" checkout -q main
check "an unchanged page goes with the pull" test ! -e "$fresh/brain/MEMORY.md"
python3 "$repo/bin/lib/prompts.py" "$fresh/brain" >/dev/null
check "and the next build starts it from the seed" cmp -s "$repo/brain/MEMORY.seed.md" "$fresh/brain/MEMORY.md"
check "which leaves that checkout clean too" test -z "$(g "$fresh" status --porcelain)"

if [ "$failures" -gt 0 ]; then
  printf '%d failure(s)\n' "$failures" >&2
  exit 1
fi
echo "all passed"
