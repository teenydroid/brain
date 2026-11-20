"""Where 4chan's read-only API is, which boards Teeny may read, and the pace.

The API (https://a.4cdn.org) serves boards, catalogs and threads as JSON. It
takes no key, so every call is made with `auth=False`; the transport
(`teeny_hands.rest`) never follows a redirect or names a URL in an error.
4chan's API rules ask for at most one request a second: `pace` keeps a
process to that (`CHAN_MIN_INTERVAL_MS`), and the read cache keeps most calls
from being made at all.

Only the boards in SAFE_BOARDS are ever read. `CHAN_BOARDS` narrows the
default set; it can never add a board outside SAFE_BOARDS. /pol/, /b/,
/r9k/ and the adult boards are not in it and cannot be put in it.
"""

from __future__ import annotations

import re
import time
import urllib.parse

from .. import hand
from ..rest import RestApi

#: `CHAN_API_BASE` moves it (the tests' fake).
API_BASE = "https://a.4cdn.org"
#: Where a person reads a thread.
THREAD_PAGE = "https://boards.4chan.org/{board}/thread/{no}"
_LOCAL = ("127.0.0.1", "localhost", "::1")

#: Every board Teeny may read, with what it is for. Worksafe, and the strange
#: ideas live here: /x/ is paranormal and conspiracies, the rest are the
#: hobby boards where people theorise about their own corner.
SAFE_BOARDS: dict[str, str] = {
    "x": "paranormal, conspiracies, the unexplained",
    "lit": "books and writing",
    "sci": "science and maths",
    "his": "history and humanities",
    "tv": "film and television",
    "g": "technology",
    "biz": "business and finance",
    "ck": "food and cooking",
    "diy": "do it yourself",
    "out": "the outdoors",
    "an": "animals and nature",
    "mu": "music",
    "tg": "tabletop games",
    "co": "comics and cartoons",
    "v": "video games",
}
#: Read when CHAN_BOARDS is unset: the boards most likely to hold a premise.
DEFAULT_BOARDS = ("x", "lit", "sci", "his", "tv", "g")

_BOARD = re.compile(r"^[a-z0-9]{1,6}$")
_MAX_NO = 10**12
_last_request = [0.0]


def _base() -> str:
    value = hand.setting("CHAN_API_BASE", API_BASE) or API_BASE
    parts = urllib.parse.urlsplit(value)
    if parts.scheme != "https" and (parts.hostname or "").lower() not in _LOCAL:
        raise hand.HandError("CHAN_API_BASE must be https")
    return value


def api() -> RestApi:
    return RestApi("4chan", _base(), None, "teeny-hands-chan")


def pace() -> None:
    """Waits until CHAN_MIN_INTERVAL_MS (1100 ms) have passed since this
    process's last request to 4chan."""
    gap = float(hand.number("CHAN_MIN_INTERVAL_MS", 1100, 0, 10_000)) / 1000
    wait = _last_request[0] + gap - time.monotonic()
    if wait > 0:
        time.sleep(wait)
    _last_request[0] = time.monotonic()


def board(value: object, what: str = "BOARD") -> str:
    """A board Teeny may read, without slashes: "/x/" is "x"."""
    name = str(value or "").strip().strip("/").lower()
    if not _BOARD.match(name):
        raise hand.HandError(f'"{what}" must be a 4chan board, like x or lit')
    if name not in SAFE_BOARDS:
        raise hand.HandError(f"/{name}/ is not a board Teeny reads (one of {', '.join(sorted(SAFE_BOARDS))})")
    return name


def boards(value: str | None = None) -> list[str]:
    """The boards to read: VALUE (comma-separated), else CHAN_BOARDS, else
    DEFAULT_BOARDS. A board outside SAFE_BOARDS in CHAN_BOARDS is skipped;
    one named in VALUE is an error."""
    if value:
        return list(dict.fromkeys(board(v) for v in value.split(",") if v.strip()))
    configured = hand.setting("CHAN_BOARDS")
    if configured:
        names = [n.strip().strip("/").lower() for n in configured.split(",")]
        kept = [n for n in dict.fromkeys(names) if n in SAFE_BOARDS]
        if kept:
            return kept
    return list(DEFAULT_BOARDS)


def thread_no(value: object, what: str = "NO") -> int:
    if isinstance(value, bool):
        raise hand.HandError(f'"{what}" must be a 4chan thread number')
    try:
        number = int(str(value).strip())
    except ValueError:
        raise hand.HandError(f'"{what}" must be a 4chan thread number') from None
    if not 0 < number < _MAX_NO:
        raise hand.HandError(f'"{what}" must be a 4chan thread number')
    return number
