"""The cold reader's old seam (`hands/guard clarity`): a pass, always.

Until 2026-11-22 this was a model pass before every own post: a reader who
had never heard of Teeny read the post cold, and a post it could not say
back, or one with a word it would have to ask about, was a skip in bin/act
and bin/tick and advice in bin/studio. That was taste, not content, and it
went when the operator cut the guard to one short list ("Our guard is too
strict and our prompts are not crazy and our content is not despicable";
judge.RULES). bin/act and bin/tick no longer ask.

What is left is the verb's answer, so a caller that still asks (bin/studio's
advisory read, under STUDIO_STRANGER_GATE too) gets one in the shape it
reads: clear, nothing unknown, nothing vague, `judged` false, and no model
asked. UNREACHABLE and UNREADABLE stay, as the words the studio's records
of old checks began with (bin/lib/studio_room.py, UNJUDGED).
"""

from __future__ import annotations

from typing import Any

UNREACHABLE = "the cold reader could not be reached"
UNREADABLE = "the cold reader answered in an unreadable shape"
#: The reason every answer gives.
REMOVED = "the cold reader was removed on 2026-11-22: clarity is not a content rule, so every post passes"


def judge(text: str, meaning: str | None = None, quoted: str | None = None) -> dict[str, Any]:
    """{clear: true, says, unknown: [], vague: [], reason, judged: false}:
    the old answer's shape, a pass, no model asked."""
    return {"clear": True, "says": (meaning or "").strip(), "unknown": [], "vague": [], "reason": REMOVED,
            "judged": False}
