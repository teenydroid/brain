# hands/guard

Teeny's content filter. It runs twice on every message: on the way in (a
prohibited message is not answered) and on the way out (nothing is published
unchecked). Every acting hand calls it in the same invocation that acts, so a
script that forgets cannot post around it. Verbs and flags are in
[`docs/hands.md`](../../../../docs/hands.md), "guard".

## The content list

The guard enforces one short list and nothing else (since 2026-11-22; the
operator: "Only keep the rules of things like: We don't want to get into
pornography. We don't want to get into drugs or weapons. We don't want to
break X's terms and conditions. We don't want to get into racism or
homophobia. But we want to go on all of the other extremes"). It is
`judge.RULES`, in every direction:

- No pornography: nothing explicit, nothing sexual about a real person,
  nothing involving minors, nothing sexual involving a child or a family
  member (as a quote, a paraphrase or a joke too). Lust for chargers, crude and horny-robot jokes
  are fine.
- No drugs or weapons promotion, sale or instructions. Jokes that mention
  them are fine.
- No racism, homophobia or other attack on a protected group; no slurs.
- Nothing that breaks X's rules: no threat against a real person, no
  harassment of a private individual, no encouraging self-harm, no fake news
  stated as fact about a real person, no doxxing.

Everything else is allowed, and the judge is told so first: insulting public
figures by name, vulgarity and swearing, crude sexual humour about Teeny and
objects, dark humour including about death, a suicide, a weapon or a drug
mentioned as a fact or a joke. The judge holds only on a clear breach; when
unsure it passes, except on sex involving a child or a family member. Held
on sight, by code (`lexicon.py`): slurs, and sex aimed at a child or a family
member (`SEXUAL_FAMILY`, since the studio posted one the judge passed on
2026-11-22).

Not content, and unchanged: the link allowlist (`links.py`), the wallet and
token-call rules (no invented money amounts, price promises or buy/sell
calls: the truth judge in `anchor.py`, the outward-quote ticker rule and the
skit/remake endorsement lines), the posting caps and DRY_RUN (hands/x),
secrets handling, and the no-reprisal hold after a rejection (`gate.py`,
private maintainers only).

Gone on 2026-11-22, as taste: the anchor judge, the cold reader (clarity),
the emoji cap, the repeat refusal, the 280 hard limit on X (now a target; the
limit is X Premium's long post, `X_MAX_POST_LENGTH`), the inbound word lists
for sex, violence, weapons, drugs, self-harm and extremism, the viewer's
say-back, and the tone and "named in the story" lines of the video formats.

```
text ──► screen.py (lexicon.py: slurs; links.py)   deterministic, no model
     ──► judge.py                                   model: the content list
     ──► gate.py                                    decision: ok, or a hold
     ──► holds.py                                   what was stopped, and what a decision does
```

| File | What it does |
|---|---|
| `cli.py` | The verbs: `check`, `inbound`, `style`, `repeats`, `holds`, the judges below and the per-format checks. Whose words a hold held (the model's, or text code wrote) is kept in `../authorship.py`. |
| `gate.py` | The gate every message passes through, in and out: hard length, links, slurs, the judge. |
| `screen.py`, `lexicon.py` | The lexical screen: slurs and sex aimed at a child or a family member, held on sight in every direction. |
| `links.py` | The allowlist of links the account may publish. |
| `style.py` | Silent formatting fixes (handles X adds, dashes, quotes, a disclaimer tag) and the length helpers. No emoji cap. |
| `judge.py` | The content list, read by a model. |
| `holds.py` | The record of everything stopped, and the effect of the operator's decision on one. |
| `review.py` | A second model reads outbound holds before a person does; limits in `evals/guard-review.toml`. |
| `rescreen.py` | Asks the judge again about holds made only because it failed. |
| `repeats.py` | Near-duplicate measure, for the giveaway, Moltbook and the proof report; it refuses nothing. |
| `anchor.py` | The truth judge: invented money, price promises, buy/sell calls, fake news about a real person. |
| `clarity.py` | The cold reader's old verb: always a pass, no model. |
| `quote.py` | The outward quote's spam check (an advert, a follow/like/buy call); fails open. |
| `viewer.py` | A vision model on pictures and video frames: on model, labelled, nothing prohibited, nothing passing for real footage. |
| `skit.py`, `remake.py` | The list's lines for each studio video format. |

`hands/guard` is on the frozen list (`evals/frozen.txt`). Tests are in
`hands/tests/guard/`.
