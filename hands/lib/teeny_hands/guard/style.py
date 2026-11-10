"""The account's hard formatting rules, applied to text about to be
published rather than only asked for in the prompt. SOUL.md states the
emoji cap as absolute and the humanizer skill bans em dashes; a model
follows both most of the time, which is not the same as always.
A port of `crates/teeny-safety/src/house_style.rs` (itself
`legacy/src/agent/house-style.ts`).

Lengths are counted in UTF-16 code units, as the legacy service's
`String.length` did, so a draft the TypeScript service accepted is accepted
here. Unlike a JavaScript slice, a cut never splits a character.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

# X's limit on a post, as the legacy service counted it.
MAX_POST_LENGTH = 280

# `\p{Extended_Pictographic}` as the Rust regex crate had it (Unicode 16.0,
# regex-syntax 0.8.11). Python's `re` has no Unicode properties, and its
# `unicodedata` lags behind, so the table is carried here rather than
# derived, and an emoji newer than the interpreter still counts.
_EXTENDED_PICTOGRAPHIC = (
    (0x00A9, 0x00A9), (0x00AE, 0x00AE), (0x203C, 0x203C), (0x2049, 0x2049), (0x2122, 0x2122),
    (0x2139, 0x2139), (0x2194, 0x2199), (0x21A9, 0x21AA), (0x231A, 0x231B), (0x2328, 0x2328),
    (0x2388, 0x2388), (0x23CF, 0x23CF), (0x23E9, 0x23F3), (0x23F8, 0x23FA), (0x24C2, 0x24C2),
    (0x25AA, 0x25AB), (0x25B6, 0x25B6), (0x25C0, 0x25C0), (0x25FB, 0x25FE), (0x2600, 0x2605),
    (0x2607, 0x2612), (0x2614, 0x2685), (0x2690, 0x2705), (0x2708, 0x2712), (0x2714, 0x2714),
    (0x2716, 0x2716), (0x271D, 0x271D), (0x2721, 0x2721), (0x2728, 0x2728), (0x2733, 0x2734),
    (0x2744, 0x2744), (0x2747, 0x2747), (0x274C, 0x274C), (0x274E, 0x274E), (0x2753, 0x2755),
    (0x2757, 0x2757), (0x2763, 0x2767), (0x2795, 0x2797), (0x27A1, 0x27A1), (0x27B0, 0x27B0),
    (0x27BF, 0x27BF), (0x2934, 0x2935), (0x2B05, 0x2B07), (0x2B1B, 0x2B1C), (0x2B50, 0x2B50),
    (0x2B55, 0x2B55), (0x3030, 0x3030), (0x303D, 0x303D), (0x3297, 0x3297), (0x3299, 0x3299),
    (0x1F000, 0x1F0FF), (0x1F10D, 0x1F10F), (0x1F12F, 0x1F12F), (0x1F16C, 0x1F171),
    (0x1F17E, 0x1F17F), (0x1F18E, 0x1F18E), (0x1F191, 0x1F19A), (0x1F1AD, 0x1F1E5),
    (0x1F201, 0x1F20F), (0x1F21A, 0x1F21A), (0x1F22F, 0x1F22F), (0x1F232, 0x1F23A),
    (0x1F23C, 0x1F23F), (0x1F249, 0x1F3FA), (0x1F400, 0x1F53D), (0x1F546, 0x1F64F),
    (0x1F680, 0x1F6FF), (0x1F774, 0x1F77F), (0x1F7D5, 0x1F7FF), (0x1F80C, 0x1F80F),
    (0x1F848, 0x1F84F), (0x1F85A, 0x1F85F), (0x1F888, 0x1F88F), (0x1F8AE, 0x1F8FF),
    (0x1F90C, 0x1F93A), (0x1F93C, 0x1F945), (0x1F947, 0x1FAFF), (0x1FC00, 0x1FFFD),
)
_PICTOGRAPH = "[" + "".join(f"{chr(a)}-{chr(b)}" for a, b in _EXTENDED_PICTOGRAPHIC) + "]"
# `\p{Emoji_Modifier}`: the five skin tones.
_MODIFIER = "[\U0001F3FB-\U0001F3FF]"
_DRESSED = f"{_PICTOGRAPH}(?:️|{_MODIFIER})*"

# One emoji sequence: a pictographic base plus its variation selectors,
# skin-tone modifiers, and any zero-width-joined parts, so a joined family
# counts once.
_EMOJI_SEQUENCE = re.compile(f"{_DRESSED}(?:‍{_DRESSED})*")

# "nfa", "NFA", "not financial advice", "dyor", "do your own research", with
# the comma, dash or bracket that attached it to the sentence.
_DISCLAIMER = re.compile(
    r"(?:\s*[,;:(\[-]\s*|\s+)?(?:\(\s*)?\b(?:nfa|dyor|not\s+financial\s+advice|do\s+your\s+own\s+research)\b(?:\s*[)\]])?",
    re.IGNORECASE,
)

_LEADING_HANDLES = re.compile(r"^(?:\s*@[A-Za-z0-9_]{1,15}\b[\s,:]*)+")
_DASH = re.compile(r"\s*[—–]\s*")
_DOUBLE_COMMA = re.compile(r",\s*,")
_RUNS_OF_BLANKS = re.compile(r"[ \t]{2,}")
_BLANKS_BEFORE_PUNCTUATION = re.compile(r"[ \t]+([,.!?])")
_REPEATED_PUNCTUATION = re.compile(r"([,.!?])[ \t]*\1")
# `$` in the Rust is the end of the text; `\Z` is that here (`$` would also
# match before a final newline).
_TRAILING_URL = re.compile(r"\s*\S*://\S*\Z")
_TRAILING_ASK = re.compile(r"\s*\?\s*\Z")


def count_emoji(text: str) -> int:
    """How many emoji sequences the text carries."""
    return sum(1 for _ in _EMOJI_SEQUENCE.finditer(text))


def post_length(text: str) -> int:
    """The length of a post as the legacy service measured it: UTF-16 code units."""
    return len(text.encode("utf-16-le")) // 2


@dataclass
class HouseStyleResult:
    """The draft as it may be published, and what was changed."""

    text: str
    # What was changed, for logging. Empty when the draft was already clean.
    fixes: list[str] = field(default_factory=list)


def _tidy_blanks(text: str) -> str:
    return _BLANKS_BEFORE_PUNCTUATION.sub(r"\1", _RUNS_OF_BLANKS.sub(" ", text))


def _cap_emoji(text: str, max_emoji: int) -> str:
    """Keeps the first `max_emoji` sequences and drops the rest, tidying the gaps."""
    seen = 0

    def keep(match: re.Match[str]) -> str:
        nonlocal seen
        seen += 1
        return match.group(0) if seen <= max_emoji else ""

    return _tidy_blanks(_EMOJI_SEQUENCE.sub(keep, text)).strip()


def prefix_units(text: str, max_units: int) -> str:
    """The longest prefix of at most `max_units` UTF-16 code units: a JavaScript
    `slice(0, n)` that never splits a character."""
    units = 0
    for index, c in enumerate(text):
        units += 2 if ord(c) > 0xFFFF else 1
        if units > max_units:
            return text[:index]
    return text


def truncate_at_boundary(text: str, max_length: int) -> str:
    """Cuts text to a length without cutting through a word or a URL.

    A blunt slice is how "that's OpenHumans" became "that's OpenHum" in a
    published reply, and half a URL is not a shorter receipt, it is a broken
    one. So the cut falls back to the last whitespace, and if the tail it would
    leave is a URL, that whole URL goes rather than being published in pieces."""
    if post_length(text) <= max_length:
        return text
    cut = prefix_units(text, max_length)
    last_space = next((i for i in range(len(cut) - 1, -1, -1) if cut[i].isspace()), None)
    # No whitespace at all: one very long token, and half of it is meaningless.
    trimmed = cut[:last_space] if last_space else cut
    return _TRAILING_URL.sub("", trimmed, count=1).rstrip()


def apply_house_style(
    draft: str,
    *,
    max_emoji: int = 1,
    max_length: int = MAX_POST_LENGTH,
    replying_to: str | None = None,
    strip_handles: bool = True,
) -> HouseStyleResult:
    """Applies the house style to a draft about to be published. `max_emoji`
    is `MAX_EMOJI_PER_POST`; `replying_to` is the handle being replied to,
    whose `@` X adds by itself. `strip_handles` False keeps a leading
    @handle: on GitHub or in mail nothing adds it back, so it is the address."""
    fixes: list[str] = []
    text = draft.strip()

    # X prefixes a reply with every participant's handle, so any handle a
    # draft opens with is published twice.
    if strip_handles and _LEADING_HANDLES.match(text):
        stripped = _LEADING_HANDLES.sub("", text, count=1).strip()
        # A post that is nothing but handles has no reply left once they go.
        if stripped:
            text = stripped
            fixes.append("dropped leading @handles X adds itself")

    # The same handle further in is still a second tag. The name is kept and
    # only the @ goes, so the sentence still addresses them.
    if replying_to:
        handle = replying_to.lstrip("@")
        anywhere = re.compile(rf"@({re.escape(handle)})\b", re.IGNORECASE)
        if anywhere.search(text):
            text = anywhere.sub(r"\1", text)
            fixes.append("unlinked a repeated @handle X already tags")

    # An em or en dash reads as machine-written, and the humanizer bans it.
    if "—" in text or "–" in text:
        text = _DOUBLE_COMMA.sub(",", _DASH.sub(", ", text))
        fixes.append("replaced em/en dashes")

    if any(q in text for q in "“”‘’"):
        text = text.replace("“", '"').replace("”", '"').replace("‘", "'").replace("’", "'")
        fixes.append("straightened curly quotes")

    # The account does not disclaim: the tag undoes the conviction of the
    # line before it. It goes wherever it sits, with its punctuation.
    undisclaimed = _DISCLAIMER.sub("", text)
    if undisclaimed != text:
        tidied = _REPEATED_PUNCTUATION.sub(r"\1", _tidy_blanks(undisclaimed)).strip()
        text = tidied[:-1] if tidied.endswith((",", ";", ":")) else tidied
        fixes.append("dropped a disclaimer")

    emoji = count_emoji(text)
    if emoji > max_emoji:
        text = _cap_emoji(text, max_emoji)
        fixes.append(f"cut {emoji - max_emoji} emoji over the cap of {max_emoji}")

    if post_length(text) > max_length:
        text = truncate_at_boundary(text, max_length)
        fixes.append(f"trimmed to {max_length} characters")

    return HouseStyleResult(text, fixes)


def asks_the_reader(text: str) -> bool:
    """Whether the post ends by asking the reader something: a question mark on
    its last line."""
    lines = [line for line in text.strip().split("\n") if line.strip()]
    return bool(lines) and lines[-1].rstrip().endswith("?")


def without_the_ask(text: str) -> str:
    """The last line cut when it is a question and there is a post left without
    it; a one-line post that is all question keeps its words and loses the
    mark, which reads as the deadpan it should have been."""
    lines = text.strip().split("\n")
    kept = "\n".join(lines[:-1]).strip()
    if kept:
        return kept
    return _TRAILING_ASK.sub(".", "\n".join(lines), count=1)
