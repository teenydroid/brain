"""Whether a stranger's text carries a slur: the one screen the studio's
outside material faces (2026-11-22: the news reached the writer pre-filtered
thin by the whole lexicon). Read from the guard's own lexicon (its `hate`
category) and matched as its screen matches, imported, never copied; the rest
of the lexicon is the guard's, on Teeny's own words in hands/x."""

from __future__ import annotations

import functools
import re

from .guard.lexicon import LEXICON
from .guard.screen import normalise_for_screen, term_pattern

CATEGORY = "hate"


@functools.lru_cache(maxsize=1)
def _patterns() -> tuple[re.Pattern[str], ...]:
    return tuple(term_pattern(term, False) for entry in LEXICON if entry.category == CATEGORY
                 for term in entry.terms)


def hateful(*texts: str) -> bool:
    """True when any text carries a term of the lexicon's `hate` category,
    whatever else it carries."""
    for text in texts:
        if not isinstance(text, str) or not text.strip():
            continue
        plain, leet = normalise_for_screen(text, True)
        if any(p.search(plain) or p.search(leet) for p in _patterns()):
            return True
    return False
