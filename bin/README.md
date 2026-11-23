# bin

The scripts that wire the spine, the brain and the hands together. Cron runs
them (every scheduled job is a cron shell job that runs one of these), and the
operator runs them by hand. A script gathers inputs, asks a tool-less agent for
a draft through `bin/ask`, and then acts by calling a hand. Models draft; these
scripts and the hands act ([`docs/architecture.md`](../docs/architecture.md),
section 1).

Most scripts are thin bash or Python wrappers. The logic is in `bin/lib/`, so
it can be unit-tested from `hands/tests/`. Every script prints what it needs
to under `state/` and finds the checkout, `secrets/core.token` and
`state/core.url` by itself, because cron starts shell jobs with an allowlisted
environment.

```
cron (in the spine) ──► bin/<script> ──► bin/ask ──► tool-less agent ──► JSON draft
                              │
                              └──► hands/<name> <verb> ──► guard, caps, DRY_RUN ──► outside
```

This folder is frozen to Teeny's own pull requests (`evals/frozen.txt`).

## Talking to the core

| Script | What it does |
|---|---|
| `spine` | The container entrypoint and the laptop way to start Teeny. Mints the core token and master key on first boot, runs `doctor`, writes the core's config, starts `teeny-spine serve`, seeds cron and points the core at the model and memory store. |
| `oh` | Calls one JSON-RPC method on the running core (`bin/oh cron_list`). |
| `ask` | Runs one agent turn and prints the answer (`bin/ask --agent act "..."`). |
| `crons` | Lists the core's cron jobs and last runs straight from `brain/storage.db`. |
| `seed-cron` | Makes the core's cron match `brain/cron.toml` plus `evals/cron.pinned.toml`. `--check` validates without a core. |
| `doctor` | Is this checkout safe to boot? With `--live`, is the running spine healthy? |
| `heartbeat` | The smallest cron shell job: proves cron fires a job. |
| `keychain-fill` | The browser's password source (`[browser] keychain_command`), backed by `hands/keychain`. |
| `sandbox-runner` | The runner inside the hand-test sandbox where a candidate's hand tests run. |
| `test/` | Shell tests for the scripts, and `spine_live_test.sh`, the end-to-end boot. |

## The mind

| Script | What it does |
|---|---|
| `tick` | One beat of the mind: drain the inbox (`hands/stimuli`) into the `teeny:mind` thread and carry out the intents. `--detach` starts it in the background. |
| `act` | Runs one playbook (`brain/playbooks/<name>.md`): gather inputs, draft, act through a hand. |
| `reflect`, `dream` | End of day and the twice-daily pass that bring `MEMORY.md` up to date item by item. |
| `mentions`, `mail`, `gh-inbound` | Read X mentions, mail and GitHub activity into the inbox. |
| `inbound-consume` | Sends the replies the core drafted for queued inbound events. |
| `x-webhook` | The server for X's webhook; queues mentions, replies and quotes into `state/hands.db`. |
| `studio`, `backroom` | The studio (feed, one writer turn, one editor ranking, then hands/x down the ranking) and the two-minds-riffing room. Logic in `lib/studio*.py` and `lib/formats/`. |
| `health` | Asks the model one question and alarms when it stops answering. |

## Money and the world

| Script | What it does |
|---|---|
| `wallet-setup` | One-time, by the operator at a terminal: gives Teeny its wallet. |
| `wallet-watch`, `x402-fund`, `surplus-sweep`, `tiny-buy`, `pay-bills`, `paid-requests`, `bounty-pay`, `giveaway-run` | The pinned jobs that watch the wallet, keep the x402 rail funded, sweep spare SOL, buy $TINY, pay the server bill, take paid build requests, pay bounty prizes and settle Tiny Drops rounds. |
| `paywall` | Teeny's paid x402 endpoint (`hands/lib/teeny_hands/wallet/paywall.py`). |
| `stats-snapshot`, `world-snapshot` | Daily numbers and the world score into `state/hands.db`. |
| `receipts`, `accounts`, `audit`, `proofs` | The public receipts ledger, the weekly accounts, the weekly audit and the progress of each proof in `DIRECTIVE.md`. |

## Building and evolving

| Script | What it does |
|---|---|
| `build-ship`, `tasks-desk` | The desks the scheduled builder and browser operator agents read and write; a shell job settles each. |
| `babysit` | Reviews and merges pull requests on Teeny's own repositories. |
| `evolve`, `evolve-gate`, `frozen-check`, `wants` | Teeny acting on what it learned: one candidate and one pull request per learning, the gate that refuses a bad candidate, the frozen-path check and the build intents drawn from `MEMORY.md`. |
| `eval`, `autonomy` | Scoring a brain against `evals/`, and the autonomy ladder. |
| `guard-review`, `guard-rescreen` | The second-model pass over guard holds, and the re-screen of holds made when the judge failed. |
| `goals-check`, `moltbook-prune` | The goal ledger's checks, and the weekly Moltbook unfollow. |

## Shared code: `lib/`

| Path | What it holds |
|---|---|
| `lib/teeny.sh` | Sourced by every shell script: the root, state and secrets locations, the core URL, and `log`, `die`, `need`, `core_token` and `oh`. |
| `lib/model_windows.sh`, `lib/model_window.py` | Make sure the core knows each model's context window. |
| `lib/core_config.py`, `lib/inbound_config.py`, `lib/wallet_config.py`, `lib/core_sections/` | Write Teeny's tables into the core's `config.toml` before each boot (`automation.py`, `context.py`, `secrets.py`). |
| `lib/cron_policy.py`, `lib/catch_up.py`, `lib/detach.py` | Validate and flatten cron, run a missed slot late, start a long job in the background. |
| `lib/tick.py`, `lib/reflect.py`, `lib/dream.py`, `lib/rhythm.py`, `lib/outcomes.py` | The mind's loop and its day. |
| `lib/playbook.py`, `lib/prompts.py`, `lib/answer.py`, `lib/act_fit.py`, `lib/post_length.py` | Reading playbooks, building agent prompts, parsing draft answers and fitting them to limits. |
| `lib/studio.py`, `lib/studio_feed.py`, `lib/studio_room.py`, `lib/studio_switch.py`, `lib/formats/` | The studio; `studio_feed.py` builds its cards; `formats/` has one module per video format (`skit`, `remake`). |
| `lib/evolve*.py`, `lib/lab.py`, `lib/frozen.py`, `lib/last_good.py`, `lib/live_checkout.py`, `lib/sandbox.py`, `lib/wants*.py` | The evolution loop, its lab spines, the frozen-path check, last-good boots and the want lane. |
| `lib/evals.py`, `lib/probes.py`, `lib/scoreboard.py`, `lib/proofs.py`, `lib/autonomy.py` | Scoring, probes and progress measures. |
| `lib/receipts.py`, `lib/diary_days.py`, `lib/diary_memory.py`, `lib/audit.py` | What is published to the diary repository. |

## Test

```sh
python3 -m unittest discover -s hands/tests -t hands/tests   # unit tests, including bin/lib
bin/test/seed_cron_test.sh                                   # one of the shell tests; see docs/developers.md, "Tests", for all
shellcheck -x bin/spine bin/oh bin/ask                       # CI runs shellcheck over every script
```
