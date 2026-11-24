# hands

The programs Teeny acts through. Each hand is one outside capability (X,
GitHub, mail, the wallet, the browser's task queue, image and video makers,
read-only feeds) and the only holder of that capability's secret. Every hard
rule (the content filter, pacing gaps, spend caps, `DRY_RUN`) is enforced
inside the hand, in the same call that acts, never in a prompt. A model's text
reaches the world only as a draft handed to a hand
([`docs/architecture.md`](../docs/architecture.md), section 3). Every verb is
documented in [`docs/hands.md`](../docs/hands.md).

```
bin/<script>  ──►  hands/<name> <verb> [--flag value]   (JSON body on stdin, optional)
                        │
                        ├── guard, caps, pacing, DRY_RUN   (in this call)
                        ├── state/hands.db                 (ledgers, queues, caches)
                        └── secrets/teeny.env              (read by the hand, never shown to a model)
                        ▼
              one JSON object on stdout; exit 0 ok, 2 refused by a rule, 1 error
```

## Layout

Each file directly in `hands/` is a three-line launcher: it puts `lib/` on the
path and calls `main` from the hand's package. The code is in
`lib/teeny_hands/`. The runtime is the Python 3.11 standard library (`#!/usr/bin/env -S python3 -I`),
plus Pillow, imported only where a picture is drawn or checked (`media/`, `guard/viewer.py`).

| Path | What it is |
|---|---|
| `<name>` | The launcher, for example `hands/x`, `hands/wallet`, `hands/trend`. |
| `lib/teeny_hands/hand.py` | The contract every hand keeps: output shape, exit codes, where secrets and state come from. Read it first. |
| `lib/teeny_hands/<name>/` | One package per hand. `cli.py` holds its verbs. |
| `lib/teeny_hands/*.py` | Code several hands share (`stimuli.py` the inbox, `rest.py` an HTTPS client, `readcache.py`, `scrub.py`, `git.py`, and so on). |
| `tests/` | Unit tests, one folder or file per hand. X, GitHub and the core are faked. |

New hands go under `lib/teeny_hands/<name>/`, never directly under `lib/`
(the evolve gate refuses that; see [`docs/evolve.md`](../docs/evolve.md)).

## The hands

Capabilities that act in the world:

| Hand | What it holds |
|---|---|
| `x` | The X keys: posting, replies, quotes, the mentions queue, the webhook state. |
| `moltbook`, `reddit`, `telegram`, `mail` | Moltbook, Reddit (queues browser tasks, never drives the browser), Telegram and AgentMail. |
| `gh`, `git`, `babysit`, `upstream` | The GitHub token, how every git command is run, pull-request review and merge, and OpenHuman releases against the pin. |
| `wallet`, `x402`, `surplus`, `bounty`, `rent`, `giveaway`, `slopcash`, `dripsplan`, `vps` | Money: the wallet under hard caps, the x402 rail, the spare-SOL sweep, bounties, people rented on RentAHuman, Tiny Drops rounds, the slop.cash project, Drips funding plans and Teeny's own servers. |
| `image`, `video`, `reel`, `meme`, `deploy` | Making and publishing media and static sites. |
| `keychain` | Site passwords, sealed, typed by the core into the browser on that site only. |

Content and safety:

| Hand | What it holds |
|---|---|
| `guard` | The content filter: lexicon, link allowlist, house style, the judge models, holds for review. |
| `story`, `lore`, `firsts`, `brief` | The story so far and the studio's desk, lore proof events, the firsts calendar and told-to posts. |

The mind's queues, ledgers and senses:

| Hand | What it holds |
|---|---|
| `stimuli`, `people`, `goals`, `lab`, `ladder`, `stats`, `world` | The one inbox, who Teeny talks to, the goal ledger, experiments, the autonomy ladder, real numbers, the world score. |
| `requests`, `build`, `tasks` | What people asked Teeny to build, the builder's queue, and the browser operator's queue. |

Read-only sources:

| Hand | What it reads |
|---|---|
| `hn`, `chan`, `listen`, `trend` | Hacker News, 4chan's worksafe boards, the outside world's chatter, and Digg's AI leaderboards with the X posts behind them (through `digg-pp-cli`). |
| `remake` | Short videos to study and remake (through the `ingest` service in `deploy/`). |

## Test

```sh
python3 -m unittest discover -s hands/tests -t hands/tests    # everything
python3 -m unittest discover -s hands/tests -t hands/tests -p 'test_*.py' -k trend   # one hand's tests, by name
python3 bin/test/hands_shard.py --shards 4 --shard 1 -v       # one quarter, as CI runs it
```

Run a verb by hand with `DRY_RUN` on (it is on until `secrets/teeny.env` says
`DRY_RUN=0`): `echo '{"text":"gm"}' | hands/guard check`.
