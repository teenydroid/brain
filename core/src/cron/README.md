# cron

`cron_list`, `cron_add`, `cron_update`, `cron_remove` and `cron_run`, as the
contract in `test/contract/cron_*.json` records them. `index.ts` exports
`methods`, plus `startCron(ctx)` / `stopCron()` (the server calls them at boot
and shutdown) and `setAgentRunner(fn)` (P1 provides the function).

## Decision: an in-core scheduler, not OpenClaw automations

I checked OpenClaw's automations (`node_modules/openclaw/docs/automation/cron-jobs*`)
and chose a scheduler in the core, on `croner`, for four reasons.

1. **The store.** Automations keep jobs and history in OpenClaw's own SQLite
   state database. Teeny's Python (`bin/crons`, `health.py`, `pulse.py`,
   `catch_up.py`, `evolve.py`) reads `docs` rows in `brain/storage.db`
   (`cron.jobs`, `cron.runs`, `tsd.leases`) and must keep working unchanged.
   Only code in this core can write those rows in exactly that shape.
2. **Missed slots.** Automations replay a missed slot at the next tick unless
   `cron.skipMissedJobs` is set. Teeny's rule (`cron_policy.py`: no catch-up for
   posting) puts late runs in `bin/lib/catch_up.py`, so the scheduler must never
   replay one itself.
3. **The shell environment.** A shell job gets HOME, PATH and LANG only, with cwd
   `$OPENHUMAN_ACTION_DIR`, as OpenHuman gave it. Automations document a 10 minute
   default for command payloads, not that environment.
4. **Exact semantics.** `croner` gives cron plus an IANA time zone (tested with
   Asia/Kolkata) and Vixie day-of-month/day-of-week rules.

`node:sqlite` (built into Node 24) is the driver: no native build or prebuilt
binary to ship in the image, synchronous calls that suit a one-writer store,
and it reads and writes the WAL database OpenHuman used. `croner` (9.1.0) is
the one new dependency.

## Behaviour

- Jobs: `job_type` `shell` (`command`) or `agent` (`agent_id`, `prompt`,
  `session_target` `isolated`). `delivery` must be `{mode: "none"}`. Created
  enabled. `schedule` is `{kind: "cron", expr, tz}` (tz defaults to UTC) or
  `{kind: "at", at}`; `delete_after_run` removes a one-off after it succeeds.
- `cron_update` patches `schedule`, `enabled`, `command`, `agent_id`, `prompt`
  and `name`. A schedule patch (or enabling) re-arms `next_run_ms` to the next slot.
  A disabled job has `next_run_ms` null and never fires.
- `cron_run` queues a run now and answers `{job_id, status: "queued"}`. It is refused
  while that job is already running. A scheduled slot that finds the job running is skipped.
- Shell jobs: `sh -c`, own process group, killed whole on timeout
  (`shellTimeoutSecs`, default 1800 = `[cron] shell_timeout_secs`; there is no 300 s cap).
  Output is recorded as `status=exit status: N\nstdout:\n...\nstderr:\n...`
  (the OpenHuman layout), clipped at 64 KiB per stream. No retries.
- Agent jobs go through the injected runner:
  `runAgent(agentId, prompt, sessionKey, signal) => Promise<string | {ok, output} | void>`.
  `sessionKey` is `cron:<job name>:<run id>`, a fresh session per run. A throw, a
  `{ok:false}`, an abort after `agentTimeoutSecs` (3600) or a missing runner fails the run.
- On start: runs a dead process left open become `error`; slots passed while the
  core was down are skipped (next_run_ms moves to the next slot); a past-due
  one-off `at` job still fires once.
- Polling every 1 s (`pollMs`).

## Storage (`OPENHUMAN_STORAGE_URL`, `sqlite:brain/storage.db`)

- `cron.jobs` (id = uuid): `{id, name, job_type, expression, enabled, next_run_ms,
  schedule, command|agent_id+prompt+session_target, delivery, delete_after_run, created_ms}`.
  `next_run_ms` moves to the following slot when a run starts.
- `cron.runs`: `{job_id, status: running|ok|error, started_ms, finished_ms, seq, output}`.
  200 runs per job are kept; removing a job removes its runs.
- `tsd.leases`: id `cron/<esc(esc(job_id)/occurrence_ms)>` (`leaseId`), body
  `{holder, epoch, expires_ms, released}`. Claimed (2 h expiry) when a run
  starts, renewed every 30 s, released (`released: true`, `expires_ms: 0`) when it ends.
  `catch_up.py` and `health.py` read these to tell a running job from a stuck one.

The core has no system jobs of its own (OpenHuman's `memory_background` is gone).
