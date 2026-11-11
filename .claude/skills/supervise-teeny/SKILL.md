---
name: supervise-teeny
description: Use when asked to supervise, watch, babysit or check on Teeny running live on the box (the teeny-spine compose stack), to grow its audience, or to find and ship improvements to it. Covers the 15-minute check, the deeper 30-minute review, what to read in the logs and ledgers, the fences never to cross, how to fix problems through PRs, and how to redeploy.
---

# Supervising Teeny

Teeny runs by itself; the supervisor reads, fixes through pull requests and
redeploys. The scorecard is `DIRECTIVE.md`: every check here exists to move an
item in it, or to catch Teeny breaking one of its fences.

## Where things are

| What | Where |
|---|---|
| Live checkout the box runs | `/home/ubuntu/teeny` (compose project `teeny-spine`, `deploy/compose.yaml`) |
| Containers | `teeny-spine-spine-1` (the core + cron), `-xhook-1` (X webhook), `-cortexdb-1` (memory), `-web-1` |
| Secrets (never print, never commit) | `/home/ubuntu/teeny/secrets/{spine.env,teeny.env,...}`, mode 600 |
| Ledgers | `/home/ubuntu/teeny/state/hands.db` (sqlite; no `sqlite3` CLI on the box, use python with `?mode=ro`) |
| Health | `state/doctor.json`, `state/heartbeat`, `state/health.json` (pinned `bin/health`) |
| Per-run logs | `bin/crons runs teeny.<job>`, `state/act/<playbook>/runs.jsonl`, `state/tick/runs.jsonl`, `state/inbound/runs.jsonl` |
| Dev checkout for PRs | `/home/ubuntu/work/personal/brain` (origin = `teenydroid/brain`; worktrees under `worktrees/`) |
| Snapshot script | `.claude/skills/supervise-teeny/watch.sh [since]` |

Run anything inside the spine with
`cd /home/ubuntu/teeny/deploy && docker compose exec -T spine <cmd>`, e.g.
`bin/doctor --live`, `bin/crons`, `bin/crons runs teeny.tick`, `bin/ask "..."`,
`bin/seed-cron --dry-run`.

## Cadence

- Self-pace with `/loop` (no interval). Every **15 min** while Teeny is awake
  or anything is pending; **25-30 min** in its quiet hours
  (`TEENY_QUIET_HOURS`, default 01:00-07:00 Asia/Kolkata = 19:30-01:30 UTC;
  `tick` reports `"asleep": true`) when nothing is pending.
- Arm a `Monitor` for CI on PRs you are driving instead of polling.
- When the box reboots, `/tmp` is wiped: the scratchpad goes with it. This
  skill's `watch.sh` lives in the repo for that reason.

## Every check (15 min)

1. `watch.sh 20m`. Flag any of:
   - `401` / `invalid_api_key`, `agent run failed`, `:ERR:` lines, cron `did not succeed`;
   - a job whose last status is `error` in `bin/crons`;
   - new `guard_holds` rows (read the reason);
   - `health.json` with `failures > 0` or an open `alarm`;
   - doctor `ok: false`, a stale heartbeat (> 10 min), `DRY_RUN` not what the operator set.
2. Read the text of every new X post/reply, Moltbook post/comment and mail
   reply (`x_posts`, `moltbook_posts`, `moltbook_comments`, `mail_sent`).
   Check: Teeny's voice (`brain/SOUL.md`), no repetition of its recent
   posts, no claims it can't back, nothing that should have been held.
3. A pinned agent job (`operator`, `builder`) erroring: read
   `bin/crons runs teeny.<job>`; "Something went wrong" usually means the
   model failed, so check the logs for the provider error.

## Every deeper review (30 min)

- Public surfaces: `gh pr list -R teenydroid/brain`, `gh issue list -R teenydroid/brain`,
  the `teenydroid/diary` commits, the X profile and Moltbook profile for what
  actually went out.
- Audience numbers: `hands/stats` (X followers, Moltbook karma/followers,
  GitHub followers/stars; `bin/stats-snapshot` writes them daily). Keep a
  trend row per review in the scratchpad.
- `bin/crons`: did every enabled job fire when it should have.
- Read the 8-12 most recent `stimuli` and what the tick did with them.

## Known failure modes (and what they looked like)

- **Model key revoked** (2026-11-11): every turn and every judge call `HTTP
  401 invalid_api_key`; guard holds pile up "the judge could not be
  reached"; nothing posts. Now `bin/health` alarms after ~30 min. Fix is the
  key in `secrets/spine.env` (`LADDER_API_KEY`), then recreate the spine:
  `docker compose up -d --force-recreate --no-build spine xhook`. Test the key
  first with a direct `/models` call; never echo it.
- **Lost start of a reply**: the Surplus/GLM stream sends `content` as a
  list of blocks when the model thinks; the chunk that closes the thinking
  carries the first words of the answer and the core's OpenAI parser
  (tinyinference `providers/openai/sse.rs`) dropped it. Symptoms: judge
  "unreadable shape", reflect "no JSON object in the answer", drafts missing
  their first words. Reproduce by asking the core
  (`inference_agent_chat_simple`) to echo a fixed sentence six times.
- **Holds from transient failures**: a restart makes the judge "core
  unreachable" and the guard holds whatever was in flight. These are
  infrastructure holds, not verdicts.
- **Fresh restart**: `bin/crons` right after a restart can miss pinned jobs
  that are still being seeded; wait a minute before calling a job missing.

## Fences: never

- Never approve or release a guard hold by hand, never edit `brain/` on the
  box by hand, never touch wallet keys or caps, never change
  `deploy/browser-allow.txt` on Teeny's behalf.
- Never print or commit a secret. Read env files by key name only
  (`grep -oE '^[A-Z_]+'`), and print only non-secret flags.
- Never loosen a fence in `DIRECTIVE.md` ("The fences that never move")
  without the operator asking for it in a PR.
- Outward-facing switches (`DRY_RUN`, enabling an X post job, a new channel)
  need the operator's say-so. X posting is deliberately capped at two posts a
  day (morning + brief); Moltbook and replies are separate.
- kubectl and other clusters are not involved; Teeny is docker compose on
  this box.

## Fixing something

1. `cd /home/ubuntu/work/personal/brain && git fetch origin && git switch main && git merge --ff-only origin/main`,
   then `worktree <slug>`; work only in `worktrees/<slug>`.
2. Write the failing test first, beside the existing tests (`hands/tests/**`
   unittest, `bin/test/*_test.sh`), watch it fail, fix, watch it pass.
3. Full local suite: `bin/test/{seed_cron,act,doctor,eval,inbound_consume,frozen}_test.sh`,
   `python3 -m unittest discover -s hands/tests -t hands/tests`, and the
   shellcheck line from `.github/workflows/check.yml`.
4. PR to `teenydroid/brain` (ready, not draft), with the live evidence in the
   body. A change under `.github/workflows/` must be pushed over SSH
   (`git push git@github.com:teenydroid/brain.git HEAD:<slug>`): the gh
   token has no `workflow` scope.
5. CI (`spine`, `scripts`, `web`; `frozen` runs only on Teeny's own PRs) green,
   review threads answered with `pr-comments` / `pr-reply`, then merge with a
   merge commit. Never squash.
6. A fix in OpenHuman or its vendored crates lands upstream first, innermost
   repo outward (e.g. tinyinference → tinyagents → openhuman → brain gitlink),
   each to `tinyhumansai/*`, from one superproject worktree.
7. Long or parallel work goes to a background subagent with a full brief;
   check its worktree for partial work if a session dies.

## Deploying a merge

```sh
cd /home/ubuntu/teeny && git pull --ff-only
# brain/, bin/, hands/, evals/ changes: a restart is enough (seed-cron reconciles jobs)
TEENY_UID=$(id -u) TEENY_GID=$(id -g) docker compose -f deploy/compose.yaml restart spine
# vendor/openhuman, spine/, Dockerfile or Cargo changes: rebuild
TEENY_UID=$(id -u) TEENY_GID=$(id -g) docker compose -f deploy/compose.yaml up -d --build
# a secrets/*.env change: recreate so the new env is read
TEENY_UID=$(id -u) TEENY_GID=$(id -g) docker compose -f deploy/compose.yaml up -d --force-recreate --no-build spine xhook
docker compose -f deploy/compose.yaml exec -T spine bin/doctor --live
```

Check `git diff --name-only <old> HEAD` to pick restart or rebuild. After any
deploy: doctor green with the new commit, a test `bin/ask`, and a watch run
a few minutes later.

## Reporting

Tell the operator, briefly: what changed on the box, what Teeny actually
said in public (and whether it was good), anything held or broken with its
cause, PRs opened/merged/deployed, and decisions that are theirs (secrets,
`DRY_RUN`, new channels, repo settings such as required checks).
