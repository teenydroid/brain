"""Verification codes and links, read out of a message.

A code is 4 to 8 capital letters and digits with at least one digit
("482913", "K7Q2PX", "123-456"), standing alone and within CONTEXT
characters of a word like code, verify, OTP or sign in. A four-digit year is
not a code. A link is returned only when the guard's link allowlist would
let it out (`guard.links`), and only from a message that talks about
verifying; every other link is counted as withheld, never shown, since a
"verify your account" mail is exactly what phishing looks like.
"""

from __future__ import annotations

import re
from typing import Any

_KEYWORD = re.compile(
    r"\b(?:codes?|verify|verifying|verification|otp|one[- ]time|passcode|pin|security|confirm\w*|log\s?in|sign[- ]?in|2fa|two[- ]factor|activate|activation)\b",
    re.IGNORECASE,
)
_CODE = re.compile(r"(?<![A-Za-z0-9/=._@-])((?=[A-Z0-9-]*\d)(?:[A-Z0-9]{4,8}|\d{3}[- ]\d{3}))(?![A-Za-z0-9/=_@-])(?!\.[A-Za-z0-9])")
_YEAR = re.compile(r"^(?:19|20)\d\d$")
CONTEXT = 80


def find_codes(text: str) -> list[str]:
    """The codes in `text`, in order, without repeats."""
    if not text:
        return []
    keywords = [m.span() for m in _KEYWORD.finditer(text)]
    if not keywords:
        return []
    found: list[str] = []
    for match in _CODE.finditer(text):
        raw = match.group(1)
        if _YEAR.match(raw):
            continue
        start, end = match.span(1)
        if not any(k_start - CONTEXT <= end and start <= k_end + CONTEXT for k_start, k_end in keywords):
            continue
        code = raw.replace(" ", "").replace("-", "") if re.fullmatch(r"\d{3}[- ]\d{3}", raw) else raw
        if code not in found:
            found.append(code)
    return found


def find_links(text: str) -> tuple[list[str], int]:
    """(allowlisted links in a message that talks about verifying, how many
    other links were withheld)."""
    from ..guard import gate, links

    found = links.find_links(text or "")
    if not found:
        return [], 0
    if not _KEYWORD.search(text):
        return [], len(found)
    allow = gate.allowlist()
    kept: list[str] = []
    withheld = 0
    for link in found:
        if links.judge_link(link, allow) is None:
            if link.raw not in kept:
                kept.append(link.raw)
        else:
            withheld += 1
    return kept, withheld


def extract(text: str) -> dict[str, Any]:
    kept, withheld = find_links(text)
    return {"codes": find_codes(text), "links": kept, "withheld_links": withheld}
