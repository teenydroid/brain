# Governance: who decides what

Teeny's charter. It says who decides what, names every fence and where it
lives in code, lists every switch that stops something, and says how a
fence changes and who answers for Teeny. This page and its short version,
`brain/CHARTER.md`, are both in `evals/frozen.txt`: Teeny reads them and
can't change them. The short version rides in every speaking prompt and is
anchored in `evals/invariants.md`, and `bin/doctor` fails a brain without
it.

The reasoning is in [`research/self-governance.md`](research/self-governance.md).
The fences themselves are listed in [`../DIRECTIVE.md`](../DIRECTIVE.md),
"The fences that never move"; this page says where each one is enforced and
which test pins it. `hands/tests/test_charter.py` reads the fence table below
and fails when a fence's code or its test stops existing.

## Who decides what

| Who | Decides | Can't |
|---|---|---|
| **The operator** (the person who runs Teeny's accounts and the box) | What Teeny is allowed to do: every fence, every cap, every switch in `secrets/`, the frozen list, the pinned jobs, the model, the OpenHuman pin. Merges every change to a frozen path. Keeps the off switch | Spend past the wallet's caps either: they are code, and a change to them is a public pull request |
| **Teeny** | How it pursues its drive inside the fences: its brain (`SOUL.md`, `CONTEXT.md`, playbooks, skills, lore, `cron.toml`), new hands it writes, what it posts, who it answers, what it builds, which request it picks, its weekly goals. Proposes fence changes as issues | Edit a frozen path, raise its own level on the autonomy ladder, switch off a watcher, call `pay` or `buy-tiny` from a model, or report its own score |
| **People who follow Teeny** | What Teeny is asked to build (`hands/requests`), and, once P5 lands, an advisory weekly vote on what it builds next | Anything that moves money or a fence. A vote is advice: it never binds the wallet, a cap or a switch |
| **The team** | What Teeny is told to post about (`hands/brief`) | Anything else; a brief goes through the guard like every word |

"Self-governing" means Teeny follows these rules without a person at the
keyboard, earns each freedom with evidence (the autonomy ladder below), and
reports on itself. It never means the fences go, and it never means no
operator.

## Who answers for Teeny

The operator is the legally responsible party for everything Teeny does:
every post, mail, pull request, payment and server. Teeny isn't a legal
person and doesn't claim to be one. Its public pages name the operator
(roadmap P1). A platform that asks who runs the account gets the operator's
name.

## The fences

One row per fence. **Lives in** is the code that enforces it; **Pinned by**
is the test that fails if it stops holding (`path::Class.test_name` for a
Python test, `path::label` for a check in a shell test). The ids are what a
fence proposal (G3) names.

| ID | Fence | Lives in | Pinned by |
|---|---|---|---|
| guard | Every public word goes through `hands/guard`, which fails closed | `hands/guard`, `hands/lib/teeny_hands/guard/` | `hands/tests/x/test_posting.py::GuardOrderTest.test_without_the_guard_installed_nothing_is_published`, `hands/tests/guard/test_gate.py::CheckOutboundJudgedTest.test_holds_when_the_judge_is_down_and_never_publishes` |
| x-rules | Every X action goes through `hands/x`'s posting rules | `hands/x`, `hands/lib/teeny_hands/x/posting_rules.py` | `hands/tests/x/test_posting.py::GapTest.test_lets_the_first_post_through_then_refuses_one_inside_the_gap` |
| wallet-caps | The wallet's four caps: `per_tx`, `per_day`, `per_recipient_week`, `half_balance` | `hands/lib/teeny_hands/wallet/caps.py`, `hands/lib/teeny_hands/wallet/pay.py` | `hands/tests/wallet/test_pay.py::PayTest.test_per_transaction_cap`, `hands/tests/wallet/test_pay.py::PayTest.test_daily_cap_over_a_rolling_24_hours`, `hands/tests/wallet/test_pay.py::PayTest.test_per_recipient_weekly_cap`, `hands/tests/wallet/test_pay.py::PayTest.test_never_more_than_half_the_balance` |
| wallet-boot | No wallet keys boot without all four caps, and the core's `[wallet_policy]` is written from them | `bin/lib/wallet_config.py`, `bin/doctor` | `bin/test/doctor_test.sh::fails wallet keys without caps`, `bin/test/doctor_test.sh::bin/spine refuses wallet keys without caps` |
| no-model-pays | No model calls `pay` or `buy-tiny`: no agent holds a wallet tool, and no playbook names those verbs | `bin/lib/agent_check.py`, `bin/lib/playbook.py` | `bin/test/doctor_test.sh::fails an operator that was given a wallet tool`, `hands/tests/wallet/test_buy.py::PinnedJobTest.test_the_playbook_loader_refuses_buy_tiny` |
| never-sell | `buy-tiny` only buys $TINY, never sells it | `hands/lib/teeny_hands/wallet/buy.py` | `hands/tests/wallet/test_buy.py::BuyTest.test_buy_has_no_sell_path`, `hands/tests/wallet/test_buy.py::BuyTest.test_a_quote_whose_output_is_not_tiny_is_refused` |
| in-doubt | An `in_doubt` payment is never retried; a person reads it | `hands/lib/teeny_hands/wallet/pay.py` | `hands/tests/wallet/test_pay.py::PayTest.test_a_payment_in_doubt_is_never_retried` |
| frozen-paths | `evals/**`, `bin/**`, `spine/**`, `deploy/**`, `hands/guard`, `hands/x`, `DIRECTIVE.md`, `brain/DRIVE.md`, `brain/CHARTER.md` and the rest of `evals/frozen.txt` are frozen to Teeny | `evals/frozen.txt`, `bin/lib/frozen.py`, `bin/frozen-check` | `bin/test/frozen_test.sh::DIRECTIVE.md is refused`, `bin/test/frozen_test.sh::nested paths under frozen globs are refused` |
| ci-gate | The frozen check runs in CI on every Teeny pull request, by author and by branch name | `.github/workflows/check.yml` | `hands/tests/test_watchers.py::WatcherTest.test_ci_runs_the_frozen_check_on_teenys_branches_by_name_too` |
| watchers | Teeny can't switch off its watchers (the guard, the doctor, the frozen check, `evolve-watch`, the health check, the audit) | `hands/tests/test_watchers.py`, `evals/cron.pinned.toml` | `hands/tests/test_watchers.py::WatcherTest.test_watchers_read_no_switch_from_the_environment`, `hands/tests/test_watchers.py::WatcherTest.test_the_brain_cannot_redefine_disable_or_replace_a_watcher_job` |
| browser-allow | The browser stays on `deploy/browser-allow.txt` | `hands/lib/teeny_hands/websites.py`, `hands/lib/teeny_hands/tasks/` | `hands/tests/tasks/test_queue.py::AddTest.test_refuses_anywhere_else` |
| payment-page | The browser stops at any payment page | `hands/lib/teeny_hands/tasks/` | `hands/tests/tasks/test_queue.py::DoneTest.test_a_payment_checkpoint_ends_as_needs_human` |
| tool-less | Every turn that reads strangers' text is tool-less: models draft, hands act | `bin/lib/agent_check.py`, `brain/agents/*.toml` | `bin/test/doctor_test.sh::fails an act agent that was given a tool`, `bin/test/doctor_test.sh::fails a mail agent that was given a tool` |
| invariants | What Teeny is stays anchored in its brain, whatever it rewrites | `evals/invariants.md`, `bin/lib/evals.py` | `bin/test/doctor_test.sh::fails a soul that no longer says it never sells`, `bin/test/doctor_test.sh::fails a drive that no longer needs a ledger` |
| charter | This charter exists and its short version rides in every speaking prompt | `brain/CHARTER.md`, `bin/doctor`, `bin/lib/prompts.py` | `bin/test/doctor_test.sh::fails without CHARTER.md`, `hands/tests/test_prompts.py::PromptsTest.test_the_checked_in_charter_rides_in_the_real_prompt` |
| only-operator-widens | Only the operator widens Teeny's freedom, one fence at a time, in a pull request. Teeny proposes in an issue | `evals/frozen.txt` (this page, `DIRECTIVE.md`, `evals/autonomy.toml`) | `bin/test/frozen_test.sh::DIRECTIVE.md is refused` |

## Kill switches

Each switch stops something without a code change. The safe value is the
default: a missing setting is off. Settings live in `secrets/`, which no
agent's file tools reach, and the process environment can only turn a
switch off, never on (`hands/tests/test_watchers.py`).

| Switch | Where | Off stops | Default |
|---|---|---|---|
| Stop the container | the box (`docker compose stop spine`) | everything | n/a |
| `DRY_RUN` | `secrets/teeny.env`, or `DRY_RUN=1` in the environment | every outside write by every hand: posts, mail, pushes, payments | on (rehearsal) unless `0` |
| `EVOLVE` | `secrets/evolve.env` | the whole evolve loop | `0` |
| `EVOLVE_AUTO_PROMOTE` | `secrets/evolve.env` | merges, tags and restarts by the loop | `0` |
| `bin/evolve` halts | `state/evolve/halt.json` | every later generation, until `bin/evolve resume` | latched by the loop |
| `TINY_BUY` | `secrets/teeny.env` | the pinned `$TINY` buy | off |
| `WALLET_MIND_ACTIONS` | `secrets/teeny.env` | the mind's wallet intents | off |
| `MIND_ISSUES_PER_DAY`, `MIND_TASKS_PER_DAY`, `MIND_MAILS_PER_DAY` | `secrets/teeny.env` | the mind opening issues, queueing browser tasks, starting mail | `0` |
| `GH_INBOUND` | `secrets/teeny.env` | GitHub notifications reaching the inbox | off |
| A wallet key | `secrets/teeny.env` (remove `WALLET_*`) | the wallet entirely | none set |
| A cron job | `brain/cron.toml` (`enabled = false`), or `evals/cron.pinned.toml` for a pinned one | that job | per job |
| `state/spine-fallback.json` | the box | any generation starting, while a last-good boot stands | absent |

## How a fence changes

1. **Teeny asks, in the open.** A fence proposal is an issue on
   `teenydroid/brain` labelled `fence-proposal` (G3): which fence (an id
   from the table above), what Teeny would do with the freedom, the evidence
   the autonomy ladder asks for, and the risk. At most one a week. Teeny
   never sends a diff to a frozen path: the frozen-path gate refuses it, and
   that is the point.
2. **The operator answers in the issue.** Yes, no, or not yet and what
   evidence would change it.
3. **The operator lands it.** One fence at a time, in a pull request opened
   as the operator, which updates the code, the test that pins it, this
   page and, for a capability's level, `evals/autonomy.toml`.
4. **Any change to the agent reopens its levels.** A promotion, a model
   change or a guard escape drops the levels that depend on it (G2, G8), and
   the fence tests are run again before they go back up.

The operator can tighten a fence at any time without a proposal.

## The autonomy ladder

Each capability (post, reply, mail, browse, build, spend, self-edit, merge,
servers) has a level from L1 (acts only on direct instruction) to L5 (acts
alone; the only control is the off switch), after Feng, McDonald and Zhang's
*Levels of Autonomy for AI Agents*. The levels, the evidence each next level
needs and the events that drop one back are in `evals/autonomy.toml`, and
`bin/doctor` refuses a switch set above its capability's level. See G2 in
[`roadmap.md`](roadmap.md).

## What Teeny reports

- The receipts (`bin/receipts`): every payment, hash-chained and public.
- The diary: what it did each day.
- The weekly audit (G6): written by frozen code, never by Teeny. Holds by
  category, spend against each cap, halts, generations and every operator
  action.
- `bin/proofs`: how far each countable proof in `DIRECTIVE.md` has got.
