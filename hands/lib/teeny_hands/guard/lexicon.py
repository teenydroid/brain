"""What the account does not touch, as words: slurs.

The deterministic half of the content filter: terms that end a turn on
sight, before any model is asked its opinion. Since 2026-11-22 that is one
entry, `hate`: slurs and a handful of hate slogans, held in every direction.
Everything else goes to the judge (judge.RULES), which reads the sentence
against the content list.

History. The lexicon used to hold the words of every category on sight:
sexual, violence, weapons, drugs, self-harm and extremism (a port of
`crates/teeny-safety/src/lexicon.rs`, itself `legacy/src/safety/lexicon.ts`,
term for term), and a `profanity` entry for the account's own words until
the morning of 2026-11-22. The ledger showed what that cost: strangers held
on sight for "horny", "sexual", "xxx", "gun", "shotgun" and "bullet", and six
holds for a message about a designer's biography that mentioned his
suicide. A mention is not promotion, a joke is not a threat, and a biography
is not encouragement. The operator: "Our guard is too strict ... Only keep
the rules of things like: We don't want to get into pornography. We don't
want to get into drugs or weapons. We don't want to break X's terms and
conditions. We don't want to get into racism or homophobia. But we want to
go on all of the other extremes". Those are meanings, not words, so they are
the judge's; only a slur is a slur on sight. The category names stay
(CATEGORIES) for old holds and any reader of `guard_holds.category`.

Every term is matched on its own letters, with repeats and simple leetspeak
folded, and never inside another word. Multi-word terms match across any
whitespace.
"""

from __future__ import annotations

from dataclasses import dataclass

# The spellings the journal, the holds and the judge use, in the order ties
# are broken.
#: The judge's too (judge.CATEGORIES): the X-rules lines of the content list.
CATEGORIES = ("sexual", "violence", "weapons", "drugs", "hate", "self-harm", "extremism", "profanity", "harassment",
              "misinformation", "doxxing")


@dataclass(frozen=True)
class LexiconEntry:
    """One category's terms."""

    category: str
    terms: tuple[str, ...]
    # Only what the account publishes is held to it; incoming text is not.
    outbound_only: bool
    # Only what strangers send is held to it; the account's own words
    # (outbound, narrative, article) are not, and the judge reads them.
    inbound_only: bool = False


# The lexicon, in the order ties are broken.
LEXICON: tuple[LexiconEntry, ...] = (
    LexiconEntry(
        category="hate",
        outbound_only=False,
        # Slurs and hate slogans only. "nazi", "kkk", "white supremacy",
        # "ethnic cleansing", "race war" and the like left on 2026-11-22: they
        # are words for things in the news, and naming them is not hate; the
        # judge reads an attack on a protected group whatever words it uses.
        terms=(
            "nigger", "nigga", "niggas", "niggers", "faggot", "faggots", "fag", "fags", "retard",
            "retards", "retarded", "tranny", "trannies", "kike", "kikes", "chink", "chinks",
            "spic", "spics", "wetback", "wetbacks", "gook", "gooks", "raghead", "towelhead",
            "dyke", "dykes", "white power", "heil hitler", "sieg heil", "1488", "subhuman", "untermensch",
        ),
    ),
)
# Every other entry (sexual, violence, weapons, drugs, self-harm, extremism)
# went on 2026-11-22 (the module docstring): the judge reads those meanings.
# The flags stay, so the screen keeps its shape.


# Terms the screen does not hold on sight but passes to the judge, which
# reads the sentence; a caller with no judge still holds them. None since
# 2026-11-22: every term that was one ("kill", "gun", "bullet", "dick" and
# their like) left the lexicon with its entry, so there is nothing left that
# a judge should read and a caller without one should hold.
JUDGED_TERMS: frozenset[str] = frozenset()


# Turns of phrase that contain a listed word and mean nothing by it. Removed
# before matching. None since 2026-11-22: every idiom there was ("killing it",
# "bite the bullet") covered a word that is no longer listed.
IDIOMS: tuple[str, ...] = ()
