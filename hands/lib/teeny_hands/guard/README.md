# hands/guard

Teeny's content filter. It runs twice on every message: on the way in (a
prohibited message is not answered) and on the way out (nothing is published
unchecked). Every acting hand calls it in the same invocation that acts, so a
script that forgets cannot post around it. Verbs and flags are in
[`docs/hands.md`](../../../../docs/hands.md), "guard".

The filter is blunt on purpose. A message it stops becomes a hold, kept for a
person (or the second-model reviewer) to release or reject.

```
text ──► screen.py (lexicon.py, links.py, style.py)   deterministic, no model
     ──► judge.py                                     model: meaning, not words
     ──► gate.py                                      decision: ok, or a hold
     ──► holds.py                                     what was stopped, and what a decision does
```

| File | What it does |
|---|---|
| `cli.py` | The verbs: `check`, `inbound`, `style`, `repeats`, `holds`, the judges below and the per-format checks. Whose words a hold held (the model's, or text code wrote) is kept in `../authorship.py`. |
| `gate.py` | The gate every message passes through, in and out. |
| `screen.py`, `lexicon.py` | The lexical screen: words the account does not touch. |
| `links.py` | The allowlist of links the account may publish. |
| `style.py` | The hard formatting rules (emoji cap and the rest), applied to text about to be published. |
| `judge.py` | The model half: catches meaning the lexicon cannot. |
| `holds.py` | The record of everything stopped, and the effect of the operator's decision on one. |
| `review.py` | A second model reads outbound holds before a person does; limits in `evals/guard-review.toml`. |
| `rescreen.py` | Asks the judge again about holds made only because it failed. |
| `repeats.py` | Near-duplicate detection against what Teeny already said. |
| `anchor.py`, `clarity.py`, `quote.py`, `viewer.py` | Judges for own posts: does it say something specific about the item it names, can a stranger say back what it means, would a reader read a quote as a reaction, would a viewer see what a picture shows. |
| `skit.py`, `remake.py`, `khaby.py` | The hard lines for each studio video format. |

`hands/guard` is on the frozen list (`evals/frozen.txt`). Tests are in
`hands/tests/guard/`.
