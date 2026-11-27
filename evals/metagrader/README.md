# the metagrader

`selected.json` here, when it exists, is the grader `bin/eval compare` uses
instead of `../judge.md`, as long as its `promoted` is `true`. It is written by

```sh
python3 bin/lib/metagrader.py select evals brain PAIRS CANDIDATES OUT --install -- bin/ask --agent judge
```

which keeps the candidate program whose rewards agree most often with which of
two of Teeny's own outputs drew more engagement, and promotes it only when it
beats `judge.md` on held-out pairs it was never chosen on. The file holds the
program (two prompts, a criteria count and a tie margin), the accuracies it was
chosen on and the pair counts. Like the rest of `evals/` it is frozen to Teeny:
the operator lands it.

`EVAL_GRADER=judge.md` puts `judge.md` back for one run. See
[`docs/evolve.md`](../../docs/evolve.md), "The metagrader".
