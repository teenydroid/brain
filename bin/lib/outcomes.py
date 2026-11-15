"""How an intent ended, in words that cannot be read as "it landed" when it
did not. bin/lib/tick.py writes them into the journal (and the core's memory),
bin/lib/reflect.py into the day it reviews.

The mind writes its thought and `journal` lines before any hand runs, so they
can claim a send that never went out (2026-11-13: "receipt delivered" for a
comment Moltbook refused with a 400). These lines are the hands' side of it:
a dry run and a failed or refused send say "not sent", with the hand's reason.
"""

from __future__ import annotations

from typing import Any, Mapping

#: Teeny's own bookkeeping: a refusal of one of these sent nothing to anybody.
INWARD = ("remember", "follow_up", "drop_follow_up", "feel", "journal", "skip", "promise", "kept")

#: How much of a hand's reason is kept.
REASON_CHARS = 120


def target(entry: Mapping[str, Any]) -> str:
    """What the intent was aimed at: a post, a comment, a message, a repo."""
    intent = entry.get("intent") if isinstance(entry.get("intent"), Mapping) else {}
    if entry.get("do") == "comment_gh" and intent.get("repo"):
        return f"{intent['repo']}#{intent.get('number')}"
    found = next((intent.get(k) for k in ("to", "post_id", "comment_id", "message_id", "chat_id", "id", "who") if intent.get(k)), "")
    return str(found)


def status(entry: Mapping[str, Any]) -> str:
    """`done`, `dry run, not sent`, `error, not sent (reason)`, and so on."""
    said = str(entry.get("status"))
    result = entry.get("result") if isinstance(entry.get("result"), Mapping) else {}
    if said == "done":
        return "dry run, not sent" if result.get("dry_run") else "done"
    if said in ("refused", "error"):
        reason = " ".join(str(result.get("refused") or result.get("error") or result.get("stderr") or "").split())
        said = said if entry.get("do") in INWARD else f"{said}, not sent"
        return f"{said} ({reason[:REASON_CHARS]})" if reason else said
    return said


def line(entry: Mapping[str, Any]) -> str:
    """`do on target: status`; never the words sent."""
    aim = target(entry)
    return f"{entry.get('do')}{' on ' + aim if aim else ''}: {status(entry)}"
