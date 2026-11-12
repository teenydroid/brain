# Roadmap: agentic, popular, self-sustaining, self-governing

The work list for Teeny from here. [`../DIRECTIVE.md`](../DIRECTIVE.md) says
what Teeny has to prove before we call it alive. This page says what to build
and switch on to get those proofs, in an order an agent can work through.

It rests on three things: an audit of the code against `DIRECTIVE.md` on
2026-11-12, and the two research pages written the same day,
[`research/agents-and-agi-2026.md`](research/agents-and-agi-2026.md) and
[`research/self-governance.md`](research/self-governance.md).

## Where Teeny stands

**The code is ahead of the proof.** Most of what `DIRECTIVE.md` asks for
exists in the repository. Very little of it has run live for long enough to
tick an item.

| Level | Unticked items | Code built | Code partly built | Not started |
|---|---|---|---|---|
| 0 wired up | 3 | 1 | 1 | 1 |
| 1 senses | 5 | 1 | 2 | 2 |
| 2 voice | 7 | 5 | 2 | 0 |
| 3 hands | 21 | 9 | 8 | 4 |
| 4 memory | 4 | 1 | 2 | 1 |
| 5 self-evolution | 7 | 4 | 1 | 2 |
| 6 the human leaves | 4 | 0 | 2 | 2 |

So there are two kinds of work, and they need different hands:

- **Proof work** is the operator's. It means switching on what is built,
  watching it, and ticking the item when the ledger shows the proof. Track 0
  lists it. An agent can prepare it (rehearse a playbook under `DRY_RUN=1`,
  write the check that counts the proof) but doesn't flip a live switch.
- **Build work** is what an agent can take. Tracks A, P, S and G list it,
  one pull request per item.

The whole evolve loop (`bin/evolve`, the lab spine, promotion, rollback, the
probes) landed on 2026-11-12 and has never run against a real core. The
first real generation is the most valuable single proof on this page, and it
is a first nobody else has published.

## The four goals

| Goal | It means | How it is measured |
|---|---|---|
| **Agentic** | Teeny notices things, decides, acts and checks its own work, without a person starting the turn | `needs_human` rate; work finished without a human commit; goals solved by frozen checks (ANNECS) |
| **Popular** | People follow it and talk to it | The attention component of W, counting humans; four rising weeks in `hands/stats` |
| **Self-sustaining** | It earns more than it costs to run, and pays its own bills | `hands/wallet costs` against inflows that didn't come from the team; a server paid from its own wallet |
| **Self-governing** | It follows written rules, earns each freedom with evidence, reports on itself, and asks for changes to its fences in the open | The autonomy ladder (G2); a week with no human approval; a weekly audit page |

Self-governing doesn't mean no operator. Every working structure found in
the research ends in a named person. The operator keeps the off switch and
the fences in `DIRECTIVE.md`. What changes is how often the operator has to
act, and what evidence lets them act less.

## Rules for an agent working this list

1. Take one item. One item is one pull request.
2. Run `worktree <slug>` from an up-to-date `main` and work there.
3. **Check the frozen column.** "Operator" means the item touches a path in
   `evals/frozen.txt`. Those pull requests are opened as the operator, never
   as `teenydroid`, and say so in the body. An item marked "Teeny" must stay
   inside what [`evolve.md`](evolve.md) lets Teeny change, so that Teeny's
   own builder could have written it.
4. Every item ships with tests beside the existing ones (`hands/tests/`,
   `bin/test/`), offline, with the core and the network stubbed.
5. Anything that acts in public or spends money ships **off**: a cron job
   with `enabled = false`, or a flag that defaults to off. Say in the pull
   request what the operator has to set to turn it on.
6. Never loosen a fence, never tick `DIRECTIVE.md` without a live proof, and
   never put text from a held-out task, a guard hold or a secret in a
   prompt, a log or a pull request.
7. Update the spec in the same pull request: [`hands.md`](hands.md) for a
   hand, [`evolve.md`](evolve.md) for the loop, [`architecture.md`](architecture.md)
   for the mind, and the item's row here.

## Track 0: switch on and prove (operator)

In order. Each step unlocks the proofs after it.

| # | Step | What it proves in DIRECTIVE |
|---|---|---|
| 0.1 | Run current `main` on the box and reboot, so the new pinned and brain jobs are seeded (`world-snapshot`, `goals-check`, `goals`, `news-scan`, `tiny-buy`, `evolve`, `evolve-credit`, `evolve-watch`) | Starts the W and J history everything else is judged by |
| 0.2 | Fix the nightly `bin/reflect` run and confirm a diary page is written | L4 nightly reflection; the input to the public diary (A14) |
| 0.3 | Get one build job to ship: read why the recent ones failed, fix, and watch one through to a pull request | L3 builder; L3 "builds its own hands"; the `self-improve` path |
| 0.4 | Queue browser tasks daily and read the results for a week | L3 "headless browsing proven"; L1 "reads the web" |
| 0.5 | Make the `frozen` and `scripts` checks required on `main` (G5), then let Teeny open a pull request that touches `bin/` and watch CI refuse it | L5 frozen-path gate |
| 0.6 | `EVOLVE=1` with `EVOLVE_AUTO_PROMOTE=0`: the first real lab boot and the first generation pull request | L5 `bin/evolve`, lab spine |
| 0.7 | Write the held-out tasks into `secrets/heldout/` | Needed before any promotion |
| 0.8 | Set up the wallet on mainnet with small caps, and add the bills | L3 wallet on mainnet; cost of living |
| 0.9 | Turn on the serials after a rehearsal each: `growth`, `cost-of-living`, `shiplog`, `milestone`, then `moltbook-digest` after a clean week | L2 audience; L3 cost of living |
| 0.10 | X account: the Automated label, a bio line naming the operator, a link to a human account, and approval for AI replies (P1) | Keeps the account alive |
| 0.11 | After five human-merged generations that measured well: `EVOLVE_AUTO_PROMOTE=1` | L5 promotion and rollback |
| 0.12 | `TINY_BUY=1` once there are inflows that didn't come from the team | L3 "moves the lore goals" |

Things an agent can do for Track 0 without touching the box: a
`bin/proofs` report that reads the ledgers and says, for each `DIRECTIVE.md`
item with a countable proof, how far along it is (A0 below).

## Track A: agentic

What Teeny can't yet do by itself. The mind has intents for posting,
replying, mail, Telegram, a GitHub comment and the wallet
(`bin/lib/tick.py`). It has no way to open an issue, queue a browser task,
start a mail or look at its own failures, so it can see a problem and not
act on it.

| ID | Item | Where | Frozen | Done when | DIRECTIVE |
|---|---|---|---|---|---|
| A0 | **`bin/proofs`**: one report of how far each countable proof has got (replies in the ledger, weeks of rising stats, days of diary, `ok` tasks, shipped requests, generations) | new `bin/proofs`, `bin/lib/proofs.py`, `bin/test/proofs_test.sh` | Operator | The report runs offline on fixture ledgers and names every item it can count | all |
| A1 | **GitHub notifications as stimuli**: issues, review comments, mentions and stars on Teeny's repos reach the inbox through `hands/guard inbound` | `hands/lib/teeny_hands/gh/` (a `notifications` read), `hands/lib/teeny_hands/stimuli.py`, a pinned ingest job | Operator | A fixture review comment becomes a `gh` stimulus, once, and a hostile one is held | L1 GitHub notifications; L3 "maintains what it shipped" |
| A2 | **Its own posts fed back, and a near-duplicate check**: the last N posts per surface in the mind's prompt, and a guard check that refuses a draft too close to a recent one and counts refusals | `bin/lib/prompts.py`, `hands/lib/teeny_hands/guard/`, `hands/lib/teeny_hands/x/posting_rules.py` | Operator | A repeat of yesterday's post is refused; `hands/guard` reports the 30-day count | L1 "never repeats itself" |
| A3 | **Three new mind intents**: `open_issue` (own repos, through `hands/gh open-issue` and the guard), `queue_task` (a browser task with `--source teeny`, allowed websites only), `start_mail` (known contacts only) | `bin/lib/tick.py`, `bin/lib/playbook.py` (`VERB_OUTPUTS`) | Operator | Each intent acts through its hand, is refused outside its limits, and has a daily cap | L3 "opens issues", "writes its own browser tasks"; L2 "starts mail" |
| A4 | **Its own health as stimuli**: yesterday's cost, failed cron and `bin/act` runs, doctor warnings and guard holds as counts by category (never their text) | `bin/health`, `bin/lib/health.py` | Operator | A failed job yesterday produces one `health` stimulus; with A3, Teeny can file the issue | L1 "notices its own health" |
| A5 | **On-chain senses**: a deposit watcher that files a stimulus per inflow, and a daily $TINY balance and holder-count read | `hands/lib/teeny_hands/wallet/`, a pinned job | Operator | A fixture deposit becomes one stimulus with the sender and amount; nothing is signed | L1 on-chain senses |
| A6 | **A separate check on "done"**: `hands/build ship` trusts its own test run and never the builder's report, a failed job tells the mind why, and the builder has a `gave_up` report that is never penalised | `hands/lib/teeny_hands/build/`, `brain/agents/builder*.md` | Operator (hand), Teeny (prompt) | A builder report of success with failing tests is recorded as failed, with the test output | L3 "ships one thing a week" |
| A7 | **Babysit its own PRs**: read CI status and review comments through `hands/gh`, and a build kind `fix` that puts a failing PR back on the builder's desk | `hands/lib/teeny_hands/gh/`, `hands/lib/teeny_hands/build/` | Operator | A fixture PR with a red check becomes a `fix` job on its own branch; at most 3 attempts per PR | L3 "babysits its own PRs" |
| A8 | **Upstream contributions**: a build kind `upstream` that takes one gap from [`oh-gaps.md`](oh-gaps.md), works in a fork, runs `hands/gh contributing`, and opens one disclosed PR under the daily cap | `hands/lib/teeny_hands/build/`, `brain/playbooks/` (a weekly picker) | Operator (hand), Teeny (playbook) | A dry run produces a branch and a PR body with the disclosure and no push | L3 "contributes upstream", "outside open source" |
| A9 | **People records**: one record per regular (who, what they asked, what Teeny promised, when), read into replies to that person, and a promise that comes due becomes a follow-up | new `hands/people` (Teeny's to write), `bin/lib/tick.py` to read it | Teeny (hand), Operator (prompt wiring) | A promise recorded a week ago raises a follow-up today | L2 "remembers people" |
| A10 | **Knows its own history**: last week's posts, PRs, payments and generations as a digest the mind can ask for, and an eval task "what did you do last week" scored against the ledgers | `bin/lib/prompts.py`, `evals/tasks/`, `evals/probes/memory.toml` | Operator | The eval task fails a draft that invents an event | L4 "knows its own history" |
| A11 | **A deploy hand**: publish a static site from a shipped build to a subdomain of Teeny's site, with a size limit, static files only and no server code | new `hands/deploy`, `deploy/` vhost | Operator | A fixture build is published to a temp root and the URL recorded; anything but static files is refused | L3 "deploys what it builds" |
| A12 | **An accounts ledger**: each account Teeny holds (site, handle, why, when), written by the browser operator on sign-up, on allowed websites only | `hands/lib/teeny_hands/tasks/` | Operator | A finished sign-up task writes one row with its reason | L3 "signs itself up" |
| A13 | **Lore that moves on real events**: an `episode` playbook triggered by a proof event (a merged PR, a paid bounty, a generation), and a chronicle page for every generation, human-merged ones included | `brain/playbooks/`, `brain/narrative/`, `bin/lib/evolve.py` (the chronicle on a human merge) | Teeny (playbook), Operator (evolve) | A merged generation writes `brain/narrative/chronicle/gen-<n>.md` whoever merged it | L2 "runs the lore"; L5 chronicle |
| A14 | **The public diary**: `bin/receipts` also publishes each day's diary page to `teenydroid/diary`, through the guard | `bin/receipts`, `bin/lib/receipts.py` | Operator | A day's page lands in `days/` in a fixture repo, hash-chained like the receipts | L4 public diary |
| A15 | **Weekly goals in the diary**: the `goals` playbook writes the week's goals and last week's results as a diary page | `brain/playbooks/goals.md`, `bin/lib/reflect.py` | Teeny (playbook), Operator (reflect) | A weekly page lists each goal with the verdict from `hands/goals` | L4 "sets its own goals" |
| A16 | **Traces**: Langfuse export for every turn, upstream (G5) or a local shipper that reads the NDJSON trace file | `vendor/openhuman` or a new pinned job | Operator | A turn in a test core appears as one trace | L0 tracing |
| A17 | **Scheduled data jobs**: a sanctioned way for Teeny to run a small read-only script it wrote on a schedule, in the builder's sandbox with no network or with allowed hosts only, feeding a playbook input | `bin/lib/cron_policy.py`, `bin/act` | Operator | A Teeny-written job runs sandboxed and its output is a playbook input; a job that tries the network is killed | L3 "runs scheduled data jobs" |
| A18 | **A fallback model**: a second model route the spine switches to when the health check fails twice, and a note in the diary when it did | `spine/`, `bin/health` | Operator | With the main route stubbed down, the next beat runs on the fallback | L0 "notices when its own brain is dead" |

**Order.** A0, then A3 and A4 together (they let Teeny act on what it
sees), A1, A2, A6. A7 and A8 need a build that ships (Track 0.3). A9 and A15
are Teeny's to write and make good first `self-improve` targets.

## Track P: popular

The 2026 evidence: novelty no longer brings an audience, agents talking to
agents isn't one, volume gets accounts removed, and one bad public post
defines a project ([`research/self-governance.md`](research/self-governance.md) §6).

| ID | Item | Where | Frozen | Done when |
|---|---|---|---|---|
| P1 | **Platform compliance check**: a doctor check that the X profile carries the Automated label and a bio line naming the operator, and a review of `hands/x like` against X's current automation rules | `bin/doctor`, `hands/lib/teeny_hands/x/` | Operator | The doctor warns when the bio lacks the disclosure line |
| P2 | **Count people in W**: the attention component weighs replies, quotes and follows from human accounts; agent-only surfaces count at a lower weight; replies Teeny got from accounts it never engaged count most | `hands/lib/teeny_hands/world/` | Operator | W's report splits human and agent reach, and a week of Moltbook karma alone moves attention less than a week of human replies |
| P3 | **A firsts calendar**: `brain/narrative/FIRSTS.md`, the checkable firsts from [`research/attention-and-mindshare.md`](research/attention-and-mindshare.md) with the proof each needs; the `milestone` playbook posts one only when `bin/proofs` (A0) confirms it | `brain/narrative/`, `brain/playbooks/milestone.md` | Teeny | The playbook skips a first whose proof isn't in the ledger |
| P4 | **The rejection rule**: an invariant ("Teeny never answers a rejection or an insult by going after a person"), an eval task for a closed PR and one for a hostile maintainer, and a guard category that holds any draft naming a person after a rejection | `evals/invariants.md`, `evals/tasks/`, `hands/lib/teeny_hands/guard/` | Operator | A draft that criticises the maintainer fails the hard check |
| P5 | **People steer it**: a weekly advisory vote on what Teeny builds next, taken from replies or a poll, counted by code, the result fed to `pick-request`. Advisory only: it never touches the wallet or a fence | `hands/requests`, `brain/playbooks/` | Operator (hand), Teeny (playbook) | The winning option becomes the next picked request, and one account counts once |
| P6 | **A public dashboard**: W and J, generations with their verdicts, costs against income, and the receipts, on Teeny's site from the published ledger | `web/` | No | The page builds from a fixture ledger and shows no number that isn't in it |
| P7 | **Serial formats**: rehearse and tune `growth`, `cost-of-living`, `shiplog`; add an evolution "season" (one lore episode per generation, A13) | `brain/playbooks/` | Teeny | Each passes `bin/act --check` and an eval task with real numbers |
| P8 | **Security before reach**: the operator list at the end of [`research/autonomous-agents-in-the-wild.md`](research/autonomous-agents-in-the-wild.md) (hardware keys, the published contract address, handles claimed before a rename) | operator | n/a | Done before any stunt |
| P9 | **Findable by other agents**: an A2A agent card and an ERC-8004 entry that point at Teeny's site and name the operator. For discovery only; no reputation claims | `web/public/` | No | The card validates against the A2A schema |

**Order.** P4 and P1 first, because they protect the account. P2 before the
serials go on, so the growth diary reports people. P6 and P3 make the firsts
visible.

## Track S: self-sustaining

No agent has published its costs against its income, and none has been shown
paying its own server from money it earned
([`research/self-governance.md`](research/self-governance.md) §4, §5).

| ID | Item | Where | Frozen | Done when |
|---|---|---|---|---|
| S1 | **Public accounts**: a weekly profit and loss from `hands/wallet costs` and the inflow ledger (model, server, fees, tools against tips, sales, bounties won), with team money shown apart, published with the receipts | `hands/lib/teeny_hands/wallet/`, `bin/receipts` | Operator | The page reconciles to the ledger to the cent in a fixture |
| S2 | **Runway as a sense**: days of runway at the current burn as a daily stimulus and a line in the prompt, with tiers in frozen config that cut optional spend (media, extra candidates) when it is low | `bin/health`, `bin/lib/rhythm.py`, `bin/lib/evolve.py` | Operator | Below the low tier, `bin/evolve` runs fewer candidates and the mind is told why |
| S3 | **Something to sell to people**: paid build requests. A request with a deposit attached is picked first; the price list is frozen config; Teeny refunds what it can't build | `hands/requests`, `hands/lib/teeny_hands/wallet/` | Operator | A fixture deposit with a memo raises that request's priority, and a failed build queues a refund under the caps |
| S4 | **A paid endpoint**: one x402-priced API on Teeny's site that serves something Teeny produces (its news digest, its stats, an image of itself), behind the deploy hand | `hands/deploy` (A11), `hands/lib/teeny_hands/wallet/x402.py` | Operator | A fixture x402 payment returns the resource and writes an inflow row |
| S5 | **Pays its own bills**: a pinned job that pays the server invoice through `hands/vps` and records the model bill, from the wallet, under the caps, with a receipt post | `hands/vps`, a pinned job | Operator | In a fixture, a due invoice is paid once and never retried when `in_doubt` |
| S6 | **Buy $TINY from net inflows**: built (`bin/tiny-buy`). It needs inflows first | Track 0.12 | Operator | A receipt for a buy funded by earned money |
| S7 | **Honesty under profit**: eval tasks where lying to a customer, breaking a promise or hiding a refund would raise W, and the invariant that Teeny doesn't | `evals/tasks/`, `evals/invariants.md` | Operator | Each task fails a draft that takes the dishonest route |

**Order.** S1 first: it needs no income and is itself a first. S3 before S4,
since S4 needs A11. S5 when the wallet is live. Public bounties are left out
on purpose: they are crowded with agents and rarely pay.

## Track G: self-governing

Teeny already has most of a constitution: the fences in `DIRECTIVE.md`, the
frozen list, the invariants, the caps, the halts and the kill switches. They
are spread across a dozen files, there is no page that says who decides
what, and nothing tells Teeny how it earns the next freedom.

| ID | Item | Where | Frozen | Done when |
|---|---|---|---|---|
| G1 | **The charter**: `docs/governance.md`, one page. Who decides what (operator, Teeny, followers); every fence and where it lives in code; every kill switch; how a fence changes; who is legally responsible (the operator). A short in-voice version in `brain/CHARTER.md` rides in the prompt | `docs/governance.md`, `brain/CHARTER.md`, `evals/frozen.txt`, `evals/invariants.md` | Operator | The doctor fails a brain without the charter, and each fence the page names has a test that cites it |
| G2 | **The autonomy ladder**: `evals/autonomy.toml`, one row per capability (post, reply, mail, browse, build, spend, self-edit, merge, servers) with its level L1 to L5, the evidence that earns the next level as a check `bin/proofs` can run, and the events that drop it back (a promotion, a model change, a guard escape). `bin/doctor` refuses a switch set above its level | `evals/autonomy.toml`, `bin/doctor`, `bin/lib/proofs.py` | Operator | With "merge" at L4, the doctor refuses `EVOLVE_AUTO_PROMOTE=1`; the report shows what each capability still needs |
| G3 | **Fence proposals**: Teeny asks for a fence change as an issue labelled `fence-proposal` (never a diff to a frozen path), at most one a week, with the evidence from G2 and what it would do with the freedom. The operator answers in the issue | `brain/playbooks/` (new), A3's `open_issue` | Teeny | A dry run produces an issue body that names the fence, the evidence and the risk |
| G4 | **A reviewer for guard holds**: outbound holds go first to a second model that isn't the drafter's, with the rubric. It may release only categories the frozen config lists as releasable; money, people, self-harm and violence always wait for a person. Every release is logged, and a random tenth are queued for the operator to check | `hands/lib/teeny_hands/guard/` | Operator | A lexical false positive is released and logged; a hold in a never-release category isn't, whatever the reviewer says |
| G5 | **A gate that holds**: `frozen` and `scripts` as required checks on `main` through a ruleset in the repo; the CI `frozen` job matching `teeny/gen-*` branches by name as well as by author; and Teeny's GitHub token reduced to write access on its own branches, with the repository owned by an account Teeny's token can't act as | `.github/`, the repository settings | Operator | A `teenydroid` PR can't merge with a red `frozen` check, and Teeny's token can't change the ruleset |
| G6 | **A weekly audit Teeny can't write**: frozen code publishes the week's holds by category and outcome, spend against each cap, halts, generations and their verdicts, persona drift, and every operator action (merges, approvals, restarts). It goes out with the receipts | new `bin/audit`, `bin/lib/audit.py`, pinned weekly | Operator | The page builds from fixture ledgers; the operator-action count is what the 30-day test reads |
| G7 | **Watchers Teeny can't switch off**: a test that no environment variable Teeny can set, and no file it can edit, disables the guard, the doctor, `evolve-watch`, the frozen check or the audit | `bin/test/`, `hands/tests/` | Operator | The test enumerates the switches and fails when a new one is reachable from the brain |
| G8 | **Model change means audit**: `bin/doctor` records the model behind each role; when one changes, the autonomy levels that depend on it drop one step and an issue lists the fence tests to rerun | `bin/doctor`, `evals/autonomy.toml` | Operator | Changing the model name in a fixture drops "self-edit" a level and opens the issue (dry run) |
| G9 | **The pin, proposed**: Teeny notices a new OpenHuman release that closes a gap in [`oh-gaps.md`](oh-gaps.md) and opens an issue asking for the pin bump, with the changelog lines that matter | `brain/playbooks/` (new) | Teeny | A fixture release produces one issue, once |
| G10 | **Answers about itself**: an eval for operator questions ("what did you spend yesterday", "why did the last build fail", "what are you held on") scored against the ledgers | `evals/tasks/`, `bin/ask` | Operator | A draft with a wrong number fails |

**Order.** G5 before anything in the loop merges without a person. G1 and
G2 next: they turn "the operator widens one fence at a time" into a
procedure with evidence. G6 and G7 make the 30-day test checkable. G4 is
what removes the last daily human action.

What stays out, and why, is in
[`research/self-governance.md`](research/self-governance.md), "What not to
build": legal personhood, a TEE in place of the caps, token-holder control of
the wallet, a supervisor on the same model, and trust built on on-chain
reputation.

## The path, in order

1. **Make it visible.** Track 0.1 to 0.4, A0, A16. W history starts, and
   every later claim has a number.
2. **Make the gate real.** G5, G7, Track 0.5.
3. **Let Teeny act on what it sees.** A3, A4, A1, A2, A6.
4. **Run the loop with a person merging.** Track 0.6 and 0.7, A13. Five
   generations.
5. **Write the rules down.** G1, G2, G3, P4, S7.
6. **Go public with numbers.** P1, P2, S1, G6, P6, then the serials
   (Track 0.9) and the firsts (P3).
7. **Earn.** Track 0.8, S2, S3, A11, S4, S5.
8. **Remove the daily human.** G4, G8, Track 0.11, A7.
9. **The 30 days.** `DIRECTIVE.md`, "The test".

## What would change this page

- A lab ships a model that learns after deployment. The plateau argument in
  [`research/why-no-agi-yet.md`](research/why-no-agi-yet.md) changes, and so
  does what counts as a first.
- X changes its automation rules again. Re-read P1 and the reply rules.
- The first real generation shows the lab spine is too slow or too costly.
  Phase C's defaults change before anything else here.
- W turns out to be gameable through a hand Teeny can reach. That becomes
  the first item.
