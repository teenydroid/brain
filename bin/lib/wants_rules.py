"""The rules the wants lane (bin/wants) and bin/evolve share
(docs/evolve.md, "1c. Wants").

A memory-page item that says something to build ("the first thing i build
myself is a chart renderer ... not built yet") is a feature, not a "don't do
X" rule: it goes to the builder (bin/wants), and evolve no longer picks it.
And a skip that rests on the memory page is no skip: the page says what Teeny
decided, not what it does, so an `already` has to name a file that runs.

Two things live here, so the two sides cannot drift apart:

  is_build_intent   the item says its own author will build a thing
  memory_skip_reason  a skip's why, where and note lean on the memory page
                    (or on no file at all) rather than on something that runs

Standard library only, and nothing from this repository is imported: frozen
with bin/, a candidate cannot change what counts.
"""

from __future__ import annotations

import re

#: The sections of brain/MEMORY.md whose items may be wants.
WANT_SECTIONS = ("what i am in the middle of", "promises and open threads")
#: What a skip's `where` names when it points at the memory page.
MEMORY_PATHS = ("brain/MEMORY.md", "MEMORY.md", "brain/MEMORY.seed.md")

_APOS = "['’]"
#: First-person intent to build a thing, and nothing else: a status note
#: ("build queue") or a promise to publish a number is not one.
BUILD_INTENT = re.compile(
    r"\bi(?:\s+(?:will|want to|mean to|plan to|am going to)|" + _APOS + r"(?:ll|m going to))\s+"
    r"(?:build|make|ship|write)\s+(?:a|an|my|the)\b"
    r"|\bthe first thing i build\b"
    r"|\bi build (?:myself|it myself)\b"
    r"|\bnot built yet\b"
    r"|\b(?:build|make)\s+(?:myself\s+)?(?:a|an)\s+(?:tool|hand|renderer|bot|cli|script)\b",
    re.I)
#: A note that says the memory page carries the thing out. A bare "memory
#: page" is not enough ("which my memory page already tracks" is a count, not
#: a carrier), nor is a passing mention of MEMORY.md: it has to be where the
#: thing lives, or that the page rides into every prompt.
MEMORY_CARRIER = re.compile(
    r"\blive page\b|\blive memory page\b"
    r"|\b(?:on|in|by|from|of) (?:my |the )?(?:brain/)?memory(?:\.md\b| page\b)"
    r"|\brides\s+(?:into\s+)?(?:every|each)\b",
    re.I)

_ON_THE_PAGE = ("it rests on the memory page, which says what you decided, not what you do; a file in this "
                "repository that runs has to carry it")
_NO_FILE = "an `already` names no file that carries it out"
#: What a turn is told when its skip is refused.
REFUSED_SKIP_NOTE = ("Your memory page is not a carrier: it says what you decided, not what you do. An `already` "
                     "must name a file in this repository that runs.")


def is_build_intent(text: str) -> bool:
    """Whether TEXT, an item of the memory page, says its author will build
    a thing."""
    return bool(BUILD_INTENT.search(" ".join(str(text or "").split())))


def _first_path(where: object) -> str:
    """The first word of WHERE, as a path: a skip writes `brain/MEMORY.md
    ([m67] ...)` or `./MEMORY.md,` as readily as the bare path."""
    words = str(where or "").split()
    path = words[0] if words else ""
    while path.startswith("./"):
        path = path[2:]
    return path.rstrip(",;:").lower()


def memory_skip_reason(why: str, where: str, note: str) -> str | None:
    """Why a skip is refused, or None when it stands. `frozen` always
    stands: a file the candidate cannot touch is no memory page."""
    path = _first_path(where)
    on_page = path in {p.lower() for p in MEMORY_PATHS}
    if why == "already":
        if not path:
            return _NO_FILE
        if on_page:
            return _ON_THE_PAGE
    if why == "not-actionable" and on_page:
        return _ON_THE_PAGE
    if why in ("already", "not-worth") and MEMORY_CARRIER.search(str(note or "")):
        return _ON_THE_PAGE
    return None
