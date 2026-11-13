"""How much Teeny's thread holds before it folds, and how much memory each
turn is handed (docs/architecture.md, "One mind").

  [context]        compaction_trigger_tokens = 100000: Teeny's mind is one
                   long thread (bin/tick, `teeny:mind`). At 100k tokens the
                   core seals the generation and opens the next with a
                   summary of it plus the most recent 40k tokens verbatim.
                   The core's own trigger is min(80% of the window, 350k),
                   which on a 384k model lets a turn grow slow and costly
                   long before it folds.
                   compaction_strategy = "summary": the core's default,
                   task_state, is a coding checkpoint (files touched, the
                   test command, the next tool call); a day of Teeny is
                   people, threads of thought and promises, which a
                   free-form summary keeps and a typed one drops.
  [memory.recall]  budget_tokens = 3000: the pack recalled before each turn
                   (core default 1200), since recall is what carries Teeny
                   across a compaction.
                   build_beliefs_every = 0: a beat logs its whole batch as
                   "the user", so beliefs built from the log read "the user
                   energy level 56" and lead the memory block. Teeny's
                   facts come from `remember`, the diary and MEMORY.md.
  [memory] recall_each_turn_ms = 3000 (one key; the table is the core's):
                   each turn waits up to 3 s for the pack recalled for its
                   own message. Unset, a turn only takes a pack an earlier
                   turn left in a cache that lives 60 s, so a beat every 5
                   minutes got none, or the previous batch's.

TEENY_COMPACTION_TRIGGER_TOKENS moves the trigger (a smaller model, a test).
Only `[memory.recall]` is owned here: the `[memory]` table itself (the
engine, CortexDB) is the core's, set over RPC at boot (bin/spine), and only
its `recall_each_turn_ms` key is set here.
"""

from __future__ import annotations

import os
from pathlib import Path

#: Context tokens at which a thread folds into its next generation.
COMPACTION_TRIGGER_TOKENS = 100_000
#: Recent tokens kept verbatim across a fold (the rest is summarised).
KEEP_RECENT_TOKENS = 40_000
#: The memory pack recalled before each turn.
RECALL_BUDGET_TOKENS = 3000
#: How long a turn waits for the pack recalled for its own message.
RECALL_EACH_TURN_MS = 3000


def trigger_tokens() -> int:
    given = os.environ.get("TEENY_COMPACTION_TRIGGER_TOKENS", "").strip()
    if not given:
        return COMPACTION_TRIGGER_TOKENS
    try:
        value = int(given)
    except ValueError:
        raise ValueError(f"TEENY_COMPACTION_TRIGGER_TOKENS is not a number: {given!r}") from None
    if value <= KEEP_RECENT_TOKENS:
        raise ValueError(f"TEENY_COMPACTION_TRIGGER_TOKENS must exceed the kept tail ({KEEP_RECENT_TOKENS})")
    return value


def tables(root: Path) -> dict[str, str]:
    del root
    return {
        "[context]": (
            "autocompact_enabled = true\n"
            f"compaction_trigger_tokens = {trigger_tokens()}\n"
            'compaction_strategy = "summary"\n'
            f"keep_recent_tokens = {KEEP_RECENT_TOKENS}\n"
        ),
        "[memory.recall]": f"enabled = true\nbudget_tokens = {RECALL_BUDGET_TOKENS}\nbuild_beliefs_every = 0\n",
    }


def keys(root: Path) -> dict[str, dict[str, str]]:
    del root
    return {"memory": {"recall_each_turn_ms": str(RECALL_EACH_TURN_MS)}}
