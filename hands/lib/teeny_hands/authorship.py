"""Whose words a guard hold held: Teeny's model's, or text code wrote.

Every outbound hold is filed with `direction = 'outbound'`, whoever composed
the text. Most of it is Teeny's model drafting, and a hold on it is something
Teeny can change by writing differently. Some of it is fixed text a hand
composes in code, posted under Teeny's account: the babysitter's audit
comment on a pull request (hands/lib/teeny_hands/babysit/actions.py,
`report`). No edit Teeny makes to its brain changes that text, so a count of
"your held drafts" that includes it tells Teeny to fix what it never wrote.
On 2026-11-18, 17 of the 21 `github`/`links` holds evolve showed Teeny were
the babysitter's comment on PR #368 (a guard link-screen bug since fixed by
#375), and two generations proposed widening a SOUL.md rule to work around
them.

`code_wrote(context, text)` names the recogniser that matched, or None when
the text is (as far as these recognisers know) Teeny's model's own. Each
recogniser is strict: it matches the generator's fixed opening and closing
lines together, not a word that a draft could quote. Add one to
NOT_TEENYS when another hand starts posting composed text; the test that
pins each marker renders the generator itself, so a recogniser and its
generator cannot drift apart silently.

Evolve's own pull request (`post_kind: "pr"`, head `teeny/gen-*`) is not
here: the proposer model wrote its title and body, so a hold on it is
Teeny's to change.

Read only by counters (bin/lib/evolve_outcomes.py, bin/lib/evolve_evidence.py,
teeny_hands.moltbook.material). The held text is matched, never returned.
Frozen (evals/frozen.txt): a candidate cannot decide which of its holds count.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Callable, Mapping

Json = dict[str, Any]

#: The babysitter's audit comment, as babysit.actions.report() writes it
#: (hands/lib/teeny_hands/babysit/actions.py, `report`, around line 87 for the
#: head and line 106 for the closing line). hands/tests/test_authorship.py
#: renders report() and fails if either stops matching.
BABYSIT_HEAD = "**babysitter** · "
BABYSIT_FOOT = "_automated audit by hands/babysit. a person approves; the babysitter only presses the button._"


@dataclass(frozen=True)
class Recogniser:
    """One generator of text that is not Teeny's words: its NAME, the code
    that WRITES it, and MATCH(context, fields) over the hold's parsed
    context and held fields."""

    name: str
    writes: str
    match: Callable[[Mapping[str, Any], Mapping[str, Any]], bool]


def _babysit_audit(context: Mapping[str, Any], fields: Mapping[str, Any]) -> bool:
    body = fields.get("body")
    return (context.get("surface") == "github" and context.get("post_kind") == "comment"
            and isinstance(body, str) and body.startswith(BABYSIT_HEAD) and body.rstrip().endswith(BABYSIT_FOOT))


#: Every generator of text posted under Teeny's name that Teeny's model did
#: not write.
NOT_TEENYS: tuple[Recogniser, ...] = (
    Recogniser("babysit_audit", "hands/lib/teeny_hands/babysit/actions.py report()", _babysit_audit),
)


def _object(raw: Any) -> Any:
    if isinstance(raw, (str, bytes)):
        try:
            return json.loads(raw)
        except ValueError:
            return raw
    return raw


def context_of(raw: Any) -> Json:
    """A hold's `context` column as a dict ({} when it is not one)."""
    got = _object(raw)
    return dict(got) if isinstance(got, Mapping) else {}


def fields_of(raw: Any) -> Json:
    """A hold's `text` column as its fields: the guard files a multi-field
    write as a JSON object ({"title": ..., "body": ...}) and a single text as
    a plain string, which reads here as {"body": text}."""
    got = _object(raw)
    if isinstance(got, Mapping):
        return dict(got)
    return {"body": got if isinstance(got, str) else str(raw or "")}


def code_wrote(context: Any, text: Any) -> str | None:
    """The NOT_TEENYS recogniser that matches a hold's CONTEXT and TEXT (raw
    columns or parsed), or None: Teeny's model wrote it."""
    ctx, fields = context_of(context), fields_of(text)
    for r in NOT_TEENYS:
        if r.match(ctx, fields):
            return r.name
    return None


def teenys_own(direction: Any, context: Any, text: Any) -> bool:
    """Whether a hold is on Teeny's own words: outbound, and no recogniser
    says code wrote it. Inbound holds are strangers' words, never Teeny's."""
    return direction == "outbound" and code_wrote(context, text) is None
