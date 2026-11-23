"""A famous person's photo, for a picture that draws them (hands/image).

A picture may show a real public figure as a caricature. Two things follow:
the painter draws a face better from a photo than from a name, and a private
person must never be drawn. One lookup answers both. A name is a public
figure here only when English Wikipedia has an article for exactly that
person, Wikidata says the article's subject is a human (P31 = Q5), and the
article carries a photo. Anyone else (a fan, a commenter, a small account, a
company, a name that is ambiguous) has no portrait, and the caller refuses
the picture.

Reads public hosts and nothing else: en.wikipedia.org (the article's summary),
thumb.wikimedia.org and upload.wikimedia.org (its photo) and www.wikidata.org
(what the subject is). Names are sent as the writer gave them; nothing about
Teeny or its operator is. What comes back is data: a title, a photo.

Kept under state/media/figures/ for CACHE_DAYS, so a person in the news all
week is looked up once.
"""

from __future__ import annotations

import hashlib
import json
import re
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

from .. import hand

Json = dict[str, Any]

SUMMARY = "https://en.wikipedia.org/api/rest_v1/page/summary/"
ENTITY = "https://www.wikidata.org/wiki/Special:EntityData/"
#: A generic client name: the hosts ask automated readers to say what they are.
AGENT = "studio-figure-reference/1.0 (image references for parody cartoons)"
HUMAN = "Q5"
CACHE_DAYS = 30
MAX_NAME = 60
#: The most a photo may weigh, and the most a page of JSON may.
PHOTO_MAX_BYTES = 6 * 1024 * 1024
JSON_MAX_BYTES = 4 * 1024 * 1024
PHOTO_HOSTS = ("upload.wikimedia.org", "thumb.wikimedia.org")


def _get(url: str, limit: int, timeout: float = 30.0) -> bytes:
    """One GET, at most LIMIT bytes. Tests replace this."""
    request = urllib.request.Request(url, headers={"User-Agent": AGENT, "Accept": "*/*"})
    with urllib.request.urlopen(request, timeout=timeout) as answer:  # noqa: S310 - fixed https hosts
        data = answer.read(limit + 1)
    if len(data) > limit:
        raise ValueError("the answer is too large")
    return data


def _folder() -> Path:
    folder = hand.state_dir() / "media" / "figures"
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def _key(name: str) -> str:
    return hashlib.sha256(" ".join(name.casefold().split()).encode()).hexdigest()[:20]


def _extension(data: bytes) -> str | None:
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return "png"
    if data[:3] == b"\xff\xd8\xff":
        return "jpg"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "webp"
    return None


def _is_human(item: str) -> bool:
    entity = json.loads(_get(f"{ENTITY}{urllib.parse.quote(item)}.json", JSON_MAX_BYTES))
    claims = ((entity.get("entities") or {}).get(item) or {}).get("claims") or {}
    kinds = {((c.get("mainsnak") or {}).get("datavalue") or {}).get("value", {}).get("id")
             for c in claims.get("P31") or [] if isinstance(c, dict)}
    return HUMAN in kinds


def _look_up(name: str) -> Json:
    """{name, title, photo: bytes} for a public figure, or {name, why} for
    anyone else."""
    try:
        page = json.loads(_get(SUMMARY + urllib.parse.quote(name.replace(" ", "_"), safe="") + "?redirect=true",
                               JSON_MAX_BYTES))
    except urllib.error.HTTPError as exc:
        return {"name": name, "why": "has no Wikipedia article" if exc.code == 404 else f"lookup failed ({exc.code})",
                "retry": exc.code != 404}
    except (urllib.error.URLError, ValueError, OSError) as exc:
        return {"name": name, "why": f"lookup failed ({type(exc).__name__})", "retry": True}
    if not isinstance(page, dict) or page.get("type") != "standard":
        return {"name": name, "why": "is not one person's Wikipedia article"}
    item = page.get("wikibase_item")
    if not (isinstance(item, str) and re.fullmatch(r"Q\d+", item)):
        return {"name": name, "why": "is not one person's Wikipedia article"}
    try:
        if not _is_human(item):
            return {"name": name, "why": "is not a person"}
    except (urllib.error.URLError, ValueError, OSError) as exc:
        return {"name": name, "why": f"lookup failed ({type(exc).__name__})", "retry": True}
    # The small copy first: it is enough to draw a face from, and the photo
    # host turns away automated fetches of full-size originals (429).
    failed = False
    for field in ("thumbnail", "originalimage"):
        source = (page.get(field) or {}).get("source")
        if not (isinstance(source, str) and urllib.parse.urlsplit(source).scheme == "https"
                and urllib.parse.urlsplit(source).hostname in PHOTO_HOSTS):
            continue
        try:
            photo = _get(source, PHOTO_MAX_BYTES, 60.0)
        except (urllib.error.URLError, ValueError, OSError):
            failed = True
            continue
        if _extension(photo):
            return {"name": name, "title": str(page.get("title") or name)[:120], "photo": photo}
    if failed:
        return {"name": name, "why": "has a photo that could not be fetched", "retry": True}
    return {"name": name, "why": "has no photo on Wikipedia"}


def portrait(name: str) -> Json:
    """{name, title, path} when NAME is a public figure with a photo, else
    {name, why}. Cached: a miss that was not a failed lookup is kept too."""
    name = " ".join(str(name).split())[:MAX_NAME]
    if not name:
        return {"name": name, "why": "is no name"}
    note = _folder() / f"{_key(name)}.json"
    now = hand.now()
    if note.is_file():
        try:
            kept = json.loads(note.read_text())
        except ValueError:
            kept = None
        if isinstance(kept, dict) and now - float(kept.get("at") or 0) < CACHE_DAYS * 86400:
            if kept.get("why"):
                return {"name": name, "why": kept["why"]}
            if kept.get("file") and (_folder() / kept["file"]).is_file():
                return {"name": name, "title": kept.get("title") or name, "path": _folder() / kept["file"]}
    found = _look_up(name)
    if found.get("why"):
        if not found.get("retry"):
            note.write_text(json.dumps({"at": now, "name": name, "why": found["why"]}))
        return {"name": name, "why": found["why"]}
    file = f"{_key(name)}.{_extension(found['photo'])}"
    (_folder() / file).write_bytes(found["photo"])
    note.write_text(json.dumps({"at": now, "name": name, "title": found["title"], "file": file}))
    return {"name": name, "title": found["title"], "path": _folder() / file}
