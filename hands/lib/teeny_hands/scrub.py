"""One line of text, safe to keep and to publish: no URL (and so no query
string), no address, nothing that looks like a key, and not too long.
bin/health scrubs the errors it quotes with it; hands/moltbook material
scrubs Teeny's receipts."""

from __future__ import annotations

import re

MAX_CHARS = 300

_URL = re.compile(r"\b[a-z][a-z0-9+.-]*://\S+", re.IGNORECASE)
_HOST = re.compile(r"\b\d{1,3}(?:\.\d{1,3}){3}(?::\d+)?\b")
_KEYISH = re.compile(r"\b(?:sk|pk|ghp|gho|github_pat|xox[a-z]?|AKIA)[-_A-Za-z0-9]{6,}|[A-Za-z0-9_-]{32,}")


def scrub(text: str, max_chars: int = MAX_CHARS) -> str:
    line = " ".join(str(text).split())
    line = _URL.sub("[url]", line)
    line = _HOST.sub("[host]", line)
    line = _KEYISH.sub("[redacted]", line)
    return line if len(line) <= max_chars else line[:max_chars].rstrip() + "..."
