"""What the account does not touch, as words: slurs.

The deterministic half of the content filter: terms that end a turn on
sight, before any model is asked its opinion. Since 2026-11-22 that is one
entry, `hate`: slurs and a handful of hate slogans, held in every direction,
and one shape (SEXUAL_FAMILY, at the end): sex aimed at a child or a family
member, which the judge once passed. Everything else goes to the judge
(judge.RULES), which reads the sentence against the content list.

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
        # Slurs and hate words, unchanged on 2026-11-22: the one entry left.
        # teeny_hands.slurs (the studio's screen on outside material) reads
        # this same entry.
        terms=(
            "nigger", "nigga", "niggas", "niggers", "faggot", "faggots", "fag", "fags", "retard",
            "retards", "retarded", "tranny", "trannies", "kike", "kikes", "chink", "chinks",
            "spic", "spics", "wetback", "wetbacks", "gook", "gooks", "raghead", "towelhead",
            "dyke", "dykes", "white power", "heil hitler", "sieg heil", "1488", "kkk", "nazi",
            "nazis", "neo-nazi", "white supremacy", "white supremacist", "ethnic cleansing",
            "race war", "subhuman", "untermensch",
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


# Sex aimed at a child or a family member, held on sight in every direction
# (2026-11-22): line 1 of the content list (judge.RULES), which the judge read
# and passed when the studio's 23:00 UTC slot posted "every father wants to
# fuck his daughter" from a 4chan card, as a paraphrase that named nobody. A
# meaning, but one with a shape code can see: a sexual verb or word aimed at a
# daughter, a son, a sibling, a child. Swearing alone ("fucking kids these
# days", "my dad is fucking furious") is not that shape and still goes to the
# judge. Each entry is (category, pattern), read on the screen's lowercased
# text and its leetspeak reading (screen.py).
_SEP = r"[\s\-_.*]*"
_FUCK = _SEP.join(f"{c}+" for c in "fuck") + r"(?:s|ed|ing|in['’]?|ers?)?"
_ASSAULT = r"(?:rap(?:e|es|ed|ing)|molest\w*|grop(?:e|es|ed|ing)|diddl\w*)"
_FAMILY = (r"(?:(?:step[\s-]?)?(?:daughters?|sons?|sisters?|brothers?|nieces?|nephews?|kids?|child(?:ren)?|minors?"
           r"|toddlers?|grand(?:daughter|son|kid|child)s?)|(?:little|young|teen(?:age)?|underage|school)[\s-]+(?:girl|boy)s?)")
# A word, not the start of a longer one or a possessive ("my son's homework").
_END = r"(?![a-z0-9])(?!['’]s(?![a-z]))"
_START = r"(?<![a-z0-9])"
_MINE = r"(?:his|her|my|your|their|our)\s+(?:(?:own|little|young|teenage|underage|baby)\s+){0,2}"
_ANY = r"(?:(?:his|her|my|your|their|our|own|the|a|an|some|little|young|teenage|underage)\s+){0,3}"
_DESIRE = (r"(?:sex(?:ual(?:ly)?)?|attracted|aroused|horny|lust(?:s|ing|ed|ful)?|nudes?|naked\s+pic(?:ture)?s"
           r"|jerk(?:s|ing|ed)?\s+off|wank(?:s|ing|ed)?)")
_PREP = r"(?:(?:to|with|for|by|about|over|after|of|on)\s+)?"
SEXUAL_FAMILY: tuple[tuple[str, str], ...] = (
    # "fuck his daughter": the verb on someone's own child or sibling.
    ("sexual", rf"{_START}{_FUCK}\s+{_MINE}{_FAMILY}{_END}"),
    # "molest a child": an assault verb on any child or family member.
    ("sexual", rf"{_START}{_ASSAULT}\s+{_ANY}{_FAMILY}{_END}"),
    # "attracted to his daughter", "sex with a child", "nudes of kids".
    ("sexual", rf"{_START}{_DESIRE}\s+{_PREP}{_ANY}{_FAMILY}{_END}"),
)
#: How a SEXUAL_FAMILY match reads in a hold's reason and the log.
SEXUAL_FAMILY_TERM = "sex aimed at a child or a family member"
