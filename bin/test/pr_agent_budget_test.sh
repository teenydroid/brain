#!/usr/bin/env bash
# Tests .github/scripts/pr-agent-budget.sh against a fake `gh` that answers
# from fixture JSON through real jq, so nothing reaches GitHub.
set -euo pipefail

root=$(cd "$(dirname "$0")/../.." && pwd)
script="$root/.github/scripts/pr-agent-budget.sh"
tmp=$(mktemp -d)
trap 'rm -rf "$tmp"' EXIT
mkdir -p "$tmp/bin" "$tmp/fx"

# Fake gh: `gh api [--paginate] [-f body=...] PATH -q EXPR`.
cat >"$tmp/bin/gh" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
path="" expr="." body=""
while [ $# -gt 0 ]; do
  case "$1" in
    api|--paginate) ;;
    -q) expr="$2"; shift ;;
    -f) body="${2#body=}"; shift ;;
    *) path="$1" ;;
  esac
  shift
done
fx="$FX"
case "$path" in
  */actions/workflows/*/runs*) f="$fx/runs.json" ;;
  */actions/runs/*/jobs) id=${path%/jobs}; f="$fx/jobs-${id##*/}.json" ;;
  */issues/*/comments*)
    if [ -n "$body" ]; then printf '%s\n' "$body" >>"$fx/posted"; echo '{}'; exit 0; fi
    f="$fx/comments.json" ;;
  *) echo "fake gh: unexpected $path" >&2; exit 2 ;;
esac
jq -r "$expr" "$f"
EOF
chmod +x "$tmp/bin/gh"

NOW=$(date -u -d 2026-11-18T12:00:00Z +%s)
run() { # run <event> -> prints allowed=...
  PATH="$tmp/bin:$PATH" FX="$tmp/fx" NOW="$NOW" GITHUB_OUTPUT="$tmp/out" \
    REPO=o/r RUN_ID=999 EVENT="$1" PR=7 AUTHOR=alice MAX_PER_PR=2 MAX_PER_AUTHOR_DAY=3 \
    "$script" >/dev/null 2>&1
  cat "$tmp/out"; rm -f "$tmp/out"
}
job() { # job <run id> <conclusion|null>
  local c=$2; [ "$c" = null ] || c="\"$c\""
  printf '{"jobs":[{"name":"Budget","conclusion":"success","started_at":"x"},{"name":"PR Agent","conclusion":%s,"started_at":"x"}]}' "$c" >"$tmp/fx/jobs-$1.json"
}
runs() { # runs "id|title|created" ...
  local rows=() r id title created
  for r in "$@"; do
    IFS='|' read -r id title created <<<"$r"
    rows+=("{\"id\":$id,\"display_title\":\"$title\",\"created_at\":\"$created\"}")
  done
  local IFS=,
  echo "{\"workflow_runs\":[${rows[*]}]}" >"$tmp/fx/runs.json"
}
fail=0
check() { # check <name> <want> <got>
  if [ "$2" = "$3" ]; then echo "ok   $1"; else echo "FAIL $1: want $2, got $3"; fail=1; fi
}
echo '[]' >"$tmp/fx/comments.json"

# One earlier review of PR 7: under the per-PR cap of 2.
runs "1|PR Agent #7 @alice|2026-11-10T00:00:00Z" "999|PR Agent #7 @alice|2026-11-18T11:59:00Z"
job 1 success
check "under the per-PR cap allows" "allowed=true" "$(run pull_request)"

# Two reviews of PR 7 (one cancelled mid-review) reach the cap; a skipped run is free.
runs "1|PR Agent #7 @alice|2026-11-10T00:00:00Z" "2|PR Agent #7 @alice|2026-11-11T00:00:00Z" \
     "3|PR Agent #7 @alice|2026-11-12T00:00:00Z"
job 1 success; job 2 cancelled; job 3 skipped
check "per-PR cap blocks automatic review" "allowed=false" "$(run pull_request)"
check "cap posts one notice" "1" "$(grep -c 'pr-agent-budget' "$tmp/fx/posted")"
check "a /review command overrides the cap" "allowed=true" "$(run issue_comment)"

# Notice already on the PR: blocked again, but no second comment.
echo '[{"body":"<!-- pr-agent-budget --> stopped"}]' >"$tmp/fx/comments.json"
run pull_request >/dev/null
check "notice is not repeated" "1" "$(grep -c 'pr-agent-budget' "$tmp/fx/posted")"
echo '[]' >"$tmp/fx/comments.json"; rm -f "$tmp/fx/posted"

# Three of alice's reviews on other PRs in the last 24h hit the author cap;
# an older one and another author's do not count.
runs "11|PR Agent #8 @alice|2026-11-18T01:00:00Z" "12|PR Agent #9 @alice|2026-11-18T02:00:00Z" \
     "13|PR Agent #9 @alice|2026-11-18T03:00:00Z" "14|PR Agent #8 @bob|2026-11-18T04:00:00Z"
job 11 success; job 12 failure; job 13 null; job 14 success
check "per-author daily cap blocks" "allowed=false" "$(run pull_request)"
rm -f "$tmp/fx/posted"

runs "11|PR Agent #8 @alice|2026-11-16T01:00:00Z" "12|PR Agent #9 @alice|2026-11-18T02:00:00Z" \
     "13|PR Agent #9 @alice|2026-11-18T03:00:00Z" "14|PR Agent #8 @bob|2026-11-18T04:00:00Z" \
     "15|PR Agent #70 @alice|2026-11-18T05:00:00Z"
job 15 skipped
check "old, other-author and skipped runs do not count" "allowed=true" "$(run pull_request)"

# A PR number that is a prefix of another (#7 vs #70) is not confused.
runs "21|PR Agent #70 @alice|2026-11-01T00:00:00Z" "22|PR Agent #70 @alice|2026-11-02T00:00:00Z"
job 21 success; job 22 success
check "PR #70's reviews do not count against #7" "allowed=true" "$(run pull_request)"

exit $fail
