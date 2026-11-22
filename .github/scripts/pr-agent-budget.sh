#!/usr/bin/env bash
# pr-agent-budget.sh: decide whether PR-Agent may review a pull request
# automatically, so pushes cannot run up the Surplus bill without bound.
#
# It counts earlier reviews from this workflow's own run history: every run is
# named "PR Agent #<pr> @<author>" (the workflow's run-name), and a run counts
# when its "PR Agent" job actually started (succeeded, failed or was cancelled
# mid-review; skipped runs cost nothing). Two caps:
#
#   MAX_PER_PR          reviews of one PR, over the last 30 days
#   MAX_PER_AUTHOR_DAY  reviews of one author's PRs, over the last 24 hours
#
# A 0 cap means unlimited. Only automatic runs (EVENT=pull_request) are
# blocked: a /review typed by a collaborator is the override, and still counts.
# When a cap first stops a PR it says so once on the PR.
#
# Env: GH_TOKEN REPO RUN_ID EVENT PR AUTHOR MAX_PER_PR MAX_PER_AUTHOR_DAY
#      [WORKFLOW=pr-agent.yml] [NOW=epoch seconds, for tests]
# Writes allowed=true|false to $GITHUB_OUTPUT (stdout when unset).
set -euo pipefail

: "${REPO:?}" "${RUN_ID:?}" "${EVENT:?}" "${PR:?}" "${AUTHOR:?}"
MAX_PER_PR="${MAX_PER_PR:-10}"
MAX_PER_AUTHOR_DAY="${MAX_PER_AUTHOR_DAY:-25}"
WORKFLOW="${WORKFLOW:-pr-agent.yml}"
NOW="${NOW:-$(date -u +%s)}"
OUT="${GITHUB_OUTPUT:-/dev/stdout}"
MARKER="<!-- pr-agent-budget -->"

since=$(date -u -d "@$((NOW - 30 * 86400))" +%Y-%m-%dT%H:%M:%SZ)
day_ago=$((NOW - 86400))

# id, title, created (epoch) of every other run of this workflow in the window.
runs=$(gh api --paginate \
  "repos/$REPO/actions/workflows/$WORKFLOW/runs?per_page=100&created=%3E%3D$since" \
  -q ".workflow_runs[] | select(.id != $RUN_ID)
      | [.id, .display_title, (.created_at | fromdateiso8601)] | @tsv")

reviewed() { # did run $1's review job start?
  gh api "repos/$REPO/actions/runs/$1/jobs" \
    -q '[.jobs[] | select(.name == "PR Agent" and .conclusion != "skipped" and .started_at != null)] | length > 0' \
    | grep -qx true
}

per_pr=0 per_author=0
while IFS=$'\t' read -r id title created; do
  [ -n "$id" ] || continue
  mine_pr=0 mine_author=0
  [ "$title" = "PR Agent #$PR @$AUTHOR" ] && mine_pr=1
  [[ "$title" == *" @$AUTHOR" ]] && [ "$created" -ge "$day_ago" ] && mine_author=1
  [ $mine_pr = 1 ] || [ $mine_author = 1 ] || continue
  reviewed "$id" || continue
  per_pr=$((per_pr + mine_pr))
  per_author=$((per_author + mine_author))
done <<<"$runs"

echo "PR #$PR: $per_pr review(s) of max $MAX_PER_PR; @$AUTHOR: $per_author in 24h of max $MAX_PER_AUTHOR_DAY" >&2

reason=""
if [ "$MAX_PER_PR" -gt 0 ] && [ "$per_pr" -ge "$MAX_PER_PR" ]; then
  reason="this PR has had $per_pr reviews (cap $MAX_PER_PR)"
elif [ "$MAX_PER_AUTHOR_DAY" -gt 0 ] && [ "$per_author" -ge "$MAX_PER_AUTHOR_DAY" ]; then
  reason="@$AUTHOR's PRs have had $per_author reviews in the last 24 hours (cap $MAX_PER_AUTHOR_DAY)"
fi

if [ -z "$reason" ] || [ "$EVENT" != "pull_request" ]; then
  echo "allowed=true" >>"$OUT"
  exit 0
fi

echo "allowed=false" >>"$OUT"
echo "::notice::PR-Agent skipped: $reason"
if ! gh api --paginate "repos/$REPO/issues/$PR/comments?per_page=100" -q '.[].body' | grep -qF "$MARKER"; then
  gh api "repos/$REPO/issues/$PR/comments" -f body="$MARKER
PR-Agent stopped reviewing pushes automatically: $reason. Comment \`/review\` to run one anyway." >/dev/null
fi
