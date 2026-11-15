"""The lexical screen: does this text contain a word the account does not
touch? Deterministic, synchronous, no model. It runs on everything coming
in and everything going out, and it cannot be argued with, because it is
not listening. A port of `crates/teeny-safety/src/screen.rs` (itself
`legacy/src/safety/screen.ts`).

The Rust emulated the legacy one-character lookarounds on a regex engine
without them (`bounded.rs`); Python's `re` is the backtracker the legacy
patterns were written for, so they are used as written.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

from .lexicon import IDIOMS, JUDGED_TERMS, LEXICON

# Which reading of the lexicon applies, the hold log's direction.
#
# `outbound` is the account's own words, strictest. `narrative` (the serial)
# and `article` (long-form pieces of it) are also the account's words: the
# hard categories are read exactly as strictly, and only the outbound-only
# entries (profanity) are let through, because the serial swears and the
# reply voice does not.
INBOUND = "inbound"
OUTBOUND = "outbound"
NARRATIVE = "narrative"
ARTICLE = "article"
DIRECTIONS = (INBOUND, OUTBOUND, NARRATIVE, ARTICLE)


@dataclass(frozen=True)
class ScreenMatch:
    """What the screen held a text for."""

    category: str
    # The lexicon term that matched, for the log. Never the text around it.
    term: str
    # A term the gate passes to the judge rather than holding on sight
    # (lexicon.JUDGED_TERMS). Still a match: a caller with no judge holds it.
    judged: bool = False


# Leetspeak, folded. Applied as a second reading of the text rather than the
# only one, because it turns "1488" and "ak47" into letters the digit-bearing
# terms then cannot see; each term is tried against both readings.
_UNLEET = str.maketrans({"0": "o", "1": "i", "!": "i", "3": "e", "4": "a", "@": "a", "5": "s", "$": "s", "7": "t"})

_ZERO_WIDTH = dict.fromkeys(map(ord, "​‌‍⁠﻿"))

# A term stands on its own: no ASCII letter or digit right before or after it.
_NOT_AFTER_ALNUM = r"(?<![a-zA-Z0-9])"
_NOT_BEFORE_ALNUM = r"(?![a-zA-Z0-9])"

_GAP = r"[\s\-_.*]+"
_LOOSE_GAP = r"[\s\-_.*]+(?:[a-z]+[\s\-_.*]+)?"


def _is_ascii_lower(c: str) -> bool:
    return "a" <= c <= "z"


def term_pattern(term: str, loose_gaps: bool) -> re.Pattern[str]:
    """One term as a pattern: each letter may repeat ("fuuuck"), a space or
    hyphen matches any run of spacing or punctuation, and the whole must stand
    on its own rather than sit inside a longer word."""
    letters = [c for c in term.lower() if c not in " -"]
    # A term that is one letter repeated ("xxx", "kkk") keeps its count. Read as
    # `x+` it would hold a lone "x", and on X that is "x.com" and "on X".
    if len(letters) > 1 and len(set(letters)) == 1 and _is_ascii_lower(letters[0]):
        body = f"{letters[0]}{{{len(letters)},}}"
        return re.compile(f"{_NOT_AFTER_ALNUM}(?:{body}){_NOT_BEFORE_ALNUM}", re.IGNORECASE)
    body = ""
    previous: str | None = None
    for c in term.lower():
        if c in " -":
            # An idiom survives one stray word ("got absolutely murdered"); a
            # term does not, because a term with a gap in it is another term.
            if previous != " ":
                body += _LOOSE_GAP if loose_gaps else _GAP
            previous = " "
            continue
        # Consecutive identical letters collapse to one repeating class, so
        # "kill" reads k+i+l+ and still matches "kil" and "killll". A doubled
        # vowel is the exception: shot/shoot and bob/boob are different words,
        # so the term keeps asking for two. "sh00t" still is, by the leet reading.
        if c == previous and _is_ascii_lower(c):
            single = f"{c}+"
            if c in "aeiou" and body.endswith(single):
                body = body[:-1] + "{2,}"
            continue
        body += f"{c}+" if _is_ascii_lower(c) else re.escape(c)
        previous = c
    return re.compile(f"{_NOT_AFTER_ALNUM}(?:{body}){_NOT_BEFORE_ALNUM}", re.IGNORECASE)


_IDIOM_PATTERNS = [term_pattern(idiom, True) for idiom in IDIOMS]

_COMPILED = [
    (entry.category, entry.outbound_only, [(term, term_pattern(term, False)) for term in entry.terms])
    for entry in LEXICON
]


def normalise_for_screen(text: str, idioms: bool) -> tuple[str, str]:
    """The text as the screen reads it: folded, unspaced, idioms removed.
    Returns the plain reading and the leetspeak one."""
    plain = unicodedata.normalize("NFKC", text).lower().translate(_ZERO_WIDTH)
    if idioms:
        for idiom in _IDIOM_PATTERNS:
            plain = idiom.sub(" ", plain)
    return plain, plain.translate(_UNLEET)


def screen_text(text: str, direction: str) -> ScreenMatch | None:
    """The prohibited term in the text, or nothing.

    The longest matching term wins, so "kill yourself" is reported as
    self-harm rather than as the violence of "kill": the log names the most
    specific reason it has. Ties go to lexicon order. A term held on sight
    always wins over a judged one (JUDGED_TERMS), whatever their lengths, so
    a judged match means nothing else in the text matched."""
    return screen_text_with(text, direction, idioms=True)


def screen_text_with(text: str, direction: str, idioms: bool = True) -> ScreenMatch | None:
    """`screen_text`, optionally without the idiom exceptions (to keep the
    idiom list honest)."""
    if direction not in DIRECTIONS:
        raise ValueError(f"unknown direction {direction!r}")
    if not text.strip():
        return None
    plain, leet = normalise_for_screen(text, idioms)
    best: ScreenMatch | None = None
    judged: ScreenMatch | None = None
    for category, outbound_only, terms in _COMPILED:
        if outbound_only and direction != OUTBOUND:
            continue
        for term, pattern in terms:
            soft = term in JUDGED_TERMS
            kept = judged if soft else best
            if kept is not None and len(term) <= len(kept.term):
                continue
            if pattern.search(plain) or pattern.search(leet):
                if soft:
                    judged = ScreenMatch(category, term, judged=True)
                else:
                    best = ScreenMatch(category, term)
    return best or judged
