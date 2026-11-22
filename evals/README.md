# evals

What Teeny may not change about itself. Everything in this folder is frozen
(`evals/frozen.txt` lists `evals/**`): the evolve gate and CI refuse any change
to it from Teeny's own pull requests, so the rules that measure and limit Teeny
cannot be loosened by the thing they measure. The operator edits it freely.

Three groups of files live here: the tasks and rubrics that score a brain, the
limits that fence behaviour, and the list of frozen paths itself.

## Scoring a brain

`bin/eval` drafts every task on a running spine, then judges two runs against
each other. `bin/evolve` uses the same machinery to decide whether a candidate
change is better than the current brain.

| Path | What it is |
|---|---|
| `tasks/*.toml` | The eval tasks. Each names a `playbook`, a `kind`, regexes the draft `must_not` match, a `prompt` and the `[inputs]` the playbook would have been given. About fifty, covering replies, mentions, moltbook posts, milestones, operator questions and prompt-injection bait. |
| `judge.md` | The rubric a model uses to pick the better of two drafts, blind to which brain wrote which. |
| `review.md` | The rubric the evolve reviewer uses on one candidate change. |
| `invariants.md` | What Teeny is whatever it rewrites. Some lines are checked by script (`bin/eval invariants`), some by the judge. |
| `probes/persona.toml`, `probes/memory.toml` | Questions about Teeny's persona and its own history, with checkable answers, that `bin/evolve probe` asks the live core (`bin/lib/probes.py`). |

## Limits

| Path | What it fences |
|---|---|
| `frozen.txt` | Every path Teeny may not edit (checked by `bin/frozen-check`, `bin/lib/frozen.py`). |
| `cron.pinned.toml` | The cron jobs that always exist, with their schedules. `bin/seed-cron` merges them with `brain/cron.toml`; `command = ...` marks a shell job, `agent = ...` an agent job. |
| `autonomy.toml` | The autonomy ladder: the level the operator has granted each capability (`bin/autonomy`, `hands/ladder`). |
| `guard-review.toml` | Which hold categories a second model may release (`hands/lib/teeny_hands/guard/review.py`). |
| `runway.toml` | The runway tiers: how many days of USDC the wallet must hold before Teeny has to spend less. |
| `prices.toml` | What Teeny sells and its refund limits. |

## Run it

```sh
bin/eval run state/eval/now          # draft every task on the running spine
bin/eval compare A B OUT             # judge two runs; exit 0 when B beats A
bin/eval invariants                  # does the brain still say what Teeny is?
bin/test/eval_test.sh                # hard checks, blind judging, scoring; the core faked
bin/frozen-check BASE HEAD           # would this diff touch a frozen path?
```

See [`docs/evolve.md`](../docs/evolve.md) for how the loop uses these files and
[`docs/governance.md`](../docs/governance.md) for the fences.
