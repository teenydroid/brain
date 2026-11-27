# cron

`cron_list`, `cron_add`, `cron_update`, `cron_remove` and `cron_run`, as the contract in
`test/contract/cron_*.json` records them, implemented over **OpenClaw automations**. `index.ts`
exports `methods`, plus `setGatewayEnv(fn)`, `startCron(ctx)` and `stopCron()`.

## Wiring (for main.ts / registry.ts)

```ts
import { methods as cronMethods, setGatewayEnv, startCron, stopCron } from "./cron/index.js";
setGatewayEnv(gatewayEnv);        // main.ts's gatewayEnv(): OPENCLAW_GATEWAY_TOKEN, OPENCLAW_STATE_DIR,
                                  // OPENCLAW_CONFIG_PATH, the Gateway URL/port. Read afresh on every call.
// register cronMethods in MODULES (cron_list, cron_runs, cron_add, cron_update, cron_remove, cron_run)
await startCron(ctx);             // after the Gateway is up: only VERIFIES cron.skipMissedJobs
```

- The Gateway env goes to the `openclaw` CLI only (merged over the core's own env, so PATH/HOME still resolve
  it). A shell job's environment is the `env -i` wrapper below and never contains it. Tested.
- Without `setGatewayEnv`, or while `cron.skipMissedJobs` is not `true`, every `cron_*` RPC fails closed with an
  error (the key is checked lazily on each call until it passes, so a Gateway restart that fixes the config
  reopens cron without a core restart). `startCron` only logs. The generated Gateway config must carry
  `cron: { skipMissedJobs: true }`; this module never sets it, because the core rewrites the config at every
  start and the Gateway reads the key at startup.

There is no scheduler in the core. The Gateway fires the jobs; this module translates Teeny's job
shape to `openclaw automations ... --json` (`automations.ts`, `map.ts`) and back. The binary is
`$OPENCLAW_BIN` (default `openclaw`); every automations command needs the Gateway running and
`operator.admin` for add, edit, remove and run (docs `cli/cron.md`).

## What each call becomes

Sources are under `node_modules/openclaw/docs/`: `cli/cron.md` (CLI), `automation/cron-jobs/payloads.md`
(payloads, sessions), `automation/cron-jobs/managing-jobs.md` (config keys), and the `CronJob` schema in
`dist/index-*.d.ts` for the JSON fields.

| Contract | OpenClaw |
| --- | --- |
| `cron_list` | `automations list --all --json` (`{jobs: [...]}`), each job mapped back (`map.ts view`) |
| `cron_add` | `automations add --name N <schedule> --session isolated --no-deliver <payload> --json` |
| `cron_update` | `automations edit <id> <flags>`, and `enable` / `disable` for `enabled`; answers `get <id>` |
| `cron_remove` | `automations remove <id>` |
| `cron_run` | `automations run <id>` (force; returns after the Gateway queues it: `{ok, enqueued, runId}`) |
| `cron_runs` | `automations runs --all --limit/--offset --json` (or `runs <id>`), paged 200 at a time; params `{job_id?, limit?}` (default 3000); rows `{job_id, status, started_ms, finished_ms, seq, duration_ms, output}` |

- Schedule `{kind: "cron", expr, tz}`: `--cron EXPR --tz TZ --exact`. `--exact` turns the Gateway's stagger
  (up to 5 minutes on top-of-hour expressions) off, so a job fires on its minute. `{kind: "at", at}`: `--at ISO`,
  plus `--keep-after-run` unless `delete_after_run` is true (one-offs delete after a successful run by default).
- Agent job: `--agent ID --message PROMPT --session isolated` (an agent-turn payload, a fresh session and
  `cron:<jobId>` run session per run). `--no-deliver`: nothing goes to a chat; the agent reports through its hands.
  `delivery` other than `{mode: "none"}` is refused.
- Shell job: a `command` payload, see below.
- Response fields: `id`, `name`, `job_type` (`agentTurn` payload is `agent`, anything else `shell`), `schedule`,
  `enabled`, `command`, `agent_id`, `prompt`, `next_run` (`state.nextRunAtMs`, left out when disabled or none),
  `last_run`, `last_status`, `running` (`state.runningAtMs` is set). Absent values are omitted, never null
  (the contract checker rejects null for a string field).

## Shell jobs: environment, cwd, timeout

OpenClaw's `--command` stores `sh -lc <cmd>`: a login shell that reads profile files, with the Gateway's
environment. That is not what OpenHuman gave and not what Teeny's jobs were written for. So the core uses
`--command-argv` (exact argv, no shell parsing) and wraps the command itself:

```
env -i HOME=<HOME> PATH=<PATH> LANG=<LANG> timeout -k 10 1800 sh -c '<command>'
```

- `env -i` clears the environment down to exactly HOME, PATH and LANG (taken from the core's own environment,
  with defaults), as OpenHuman did. `--command-env` can only add variables, never remove the Gateway's, which is why
  the wrapper is needed.
- cwd: `--command-cwd $OPENHUMAN_ACTION_DIR` (the repo root when that is unset).
- timeout: `timeout 1800` inside the wrapper is `[cron] shell_timeout_secs`, and kills the whole command (exit
  124, children too; tested). The scheduler's own `--timeout-seconds` is 1830, so the wrapper ends the run first
  and the run history says why. There is no 300 s cap (gap G12 was TinyBox's, not OpenClaw's).
- `command` in responses is the wrapper's last argument (`commandOf`), so callers see the command they added.
- Output: the Gateway captures stdout and stderr into run history (`--output-max-bytes` left at its default).

## Missed slots (catch-up)

OpenClaw replays a recurring slot missed while the Gateway was down unless `cron.skipMissedJobs: true`
(`managing-jobs.md`, "Configuration"). That key is global, not per job. Teeny's rule (`cron_policy.py`: no
catch-up for anything that posts or pays, a bounded window for the rest) wants no replay by the scheduler at
all: `bin/lib/catch_up.py` makes up the jobs that allow it, through `cron_run`. The key lives in the generated
Gateway config and this module verifies it (see Wiring). One-shot `at` jobs keep OpenClaw's catch-up either way
(documented); wakes are not posts.

## Run history and "running"

`cron_runs` serves the history. The Python readers go through `bin/lib/cron_store.py`, which calls the core's
`cron_list` and `cron_runs` (through `bin/oh` / `hand.core`) and never the `openclaw` CLI, since it has no Gateway
token. It also documents what replaces `tsd.leases`: the job's `state.runningAtMs` marker (set while a run is in
flight, cleared when it ends; `running_ms` in `cron_list`) is the claim on a slot. `cron_list` rows also carry
numeric `created_ms`, `next_run_ms`, `last_run_ms` and `running_ms` for the readers.

## Not here

- `runAgent` injection (the earlier in-core design): agent jobs are OpenClaw agent turns now, so P1 supplies
  nothing for cron.
- A real Gateway was not available to the tests. `test/cron/fake.ts` stands in for the CLI and parses the
  flags the core sends; the JSON shapes it returns are those of the docs and type definitions, but the CLI's exact
  `--json` envelope for `list`, `add`, `get` and `runs` is tolerated both as a bare array/object and as
  `{jobs}` / `{entries}`.
