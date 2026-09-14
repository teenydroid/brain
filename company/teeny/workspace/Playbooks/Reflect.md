# Playbook: the reflect pass

Runs daily at 06:00 UTC and by hand. Three stages, three agents, in order:

1. **Scout looks.** Runs, events, the outside world → `signals` rows.
2. **Scribe learns.** Signals and settled runs → `learnings`; `Goals.md`
   brought back in line with the `goals` ledger; `change_needed` set if a
   learning says part of the definition is wrong.
3. **Curator revises.** Only if the gate said so, and only one change, by the
   `self-edit` skill.

Then a report: seen, learned, changed. The skill `reflect` says what each
stage's "done" is.
