---
name: Reflect
description: The daily look at what happened, what it taught, and whether the definition should change — the loop the brain learns through.
category: Brain
---

# Reflect

The `reflect` workflow runs this once a day. This skill is what each stage
means, so a teammate running its node knows what "done" is.

## Look (scout)

Read the runs and events since the last pass, and look outward at Teeny's
world. Write one `signals` row per observation: what, where, when, link.
Observations only. Ten signals a day is plenty; a hundred means you stopped
choosing.

Done when every new thing worth a row has one and none of them draws a
conclusion.

## Learn (scribe)

Read every `new` signal and every run that settled since the last pass. Fold
them into `learnings`: one row per thing actually taught, written so someone
could act on it from the row alone. Move the signals to `folded` (naming the
learning) or `noise` (saying why). Then read the `goals` ledger and make
`Goals.md` match it, in prose.

Set `change_needed` to true only when a learning names something in the
definition — a prompt, a goal, a task, a skill, the roster — that is now
wrong. "Could be better" is not "wrong".

Done when the unreviewed section of `signals` is empty and `Goals.md` is
current.

## Revise (curator)

Only when the gate said so. Follow `self-edit` end to end for the single
change the record most clearly justifies. If two changes are justified,
make the one that would be hardest to argue with and leave a task for the
other.

Done when there is a `changes` row and a reload request, or when you have
recorded in `learnings` why the change the scribe named should not be made.

## Report

Three short lists — seen, learned, changed — and nothing else. A day where
nothing changed says so.
