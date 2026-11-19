"""Which of Teeny's own pictures the memegen service may read, as plain code
with no memegen import, so hands/tests/meme tests it (deploy/memegen/front.py
uses it; docs/hands.md, "meme").

memegen takes an overlay (`style`) or a background only as a URL, and
downloads it only from a public address: it cannot read a local file
(`file://` answers 415, a bare path 422; tried on the pinned commit,
2026-11-18). Teeny's art is mounted read-only into the container instead
(state/media/ at /teeny/media, brain/assets/ at /teeny/assets), and the front
replaces upstream's download with a copy from that mount for one made-up
address, ART_PREFIX, that no network resolves. Every other URL is refused, so
the service fetches nothing from the internet.

    http://teeny-art/assets/tiny/tiny-love.jpg  ->  /teeny/assets/tiny/tiny-love.jpg
"""

from __future__ import annotations

from pathlib import Path, PurePosixPath
from urllib.parse import unquote, urlsplit

ART_PREFIX = "http://teeny-art/"
#: The mounted folders, by the first part of the URL's path.
FOLDERS = ("assets", "media")
EXTENSIONS = (".jpg", ".jpeg", ".png", ".webp")


def url_for(folder: str, relative: str) -> str:
    """The address the service reads `relative` (under `folder`) at."""
    if folder not in FOLDERS:
        raise ValueError(f"not a mounted folder: {folder!r}")
    return ART_PREFIX + folder + "/" + "/".join(PurePosixPath(relative).parts)


def local_path(url: str, root: Path, limit: int) -> Path | None:
    """The file under `root` that `url` names, or None: when it is not an
    ART_PREFIX address, names no mounted folder, climbs out with `..` or a
    symlink, is not a regular picture file, or is over `limit` bytes."""
    if not isinstance(url, str) or not url.startswith(ART_PREFIX):
        return None
    parts = urlsplit(url)
    if parts.query or parts.fragment:
        return None
    segments = [unquote(s) for s in parts.path.split("/") if s]
    if len(segments) < 2 or segments[0] not in FOLDERS:
        return None
    if any(s in (".", "..") or "\x00" in s or "\\" in s for s in segments):
        return None
    if not segments[-1].lower().endswith(EXTENSIONS):
        return None
    base = (root / segments[0]).resolve()
    try:
        path = base.joinpath(*segments[1:]).resolve(strict=True)
    except (OSError, RuntimeError):
        return None
    if not path.is_relative_to(base) or not path.is_file():
        return None
    if path.stat().st_size > limit:
        return None
    return path
