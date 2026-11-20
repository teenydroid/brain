"""One video fetched through the ingest service and its files kept: the half
of a study that is not the model, shared by hands/remake study and
hands/khaby dissect. Each keeps its videos under its own folder root,
state/media/<root>/ (`remake`, `khaby`), one folder per video.

  checked_fetch  the service's fetch answer held to the shape asked for, or
                 a `fetch` problem
  source_fields  the source's facts from it, every string capped and cleaned
  screen_source  the source's own words the guard's screen stops, blanked
  pull           each file into a staging folder, within its cap and with the
                 magic bytes its name says
  keep           what is left on disk: a usable video's media moved in whole
                 beside its answer, an unusable one's answer alone, an
                 unreadable one nothing
  sweep          folders not touched for a while removed
"""

from __future__ import annotations

import json
import os
import re
import shutil
import time
from pathlib import Path
from typing import Any

from .. import hand
from ..guard.screen import INBOUND, screen_text
from ..guard.skit import HARM, NOT_A_FIGURE
from .service import IngestError, clean_text, source_of

Json = dict[str, Any]

MAX_SOURCE_BYTES = 60 * 1024 * 1024
MAX_AUDIO_BYTES = 15 * 1024 * 1024
MAX_FRAME_BYTES = 2 * 1024 * 1024
MAX_SHOTS = 9
#: What a reading must say false, every one, for the video to be usable.
FLAGS = ("minors", "news", "tragedy", "sexual", "violence", "identifiable_private")
CODES = {"youtube": "yt", "tiktok": "tt", "instagram": "ig"}
ASPECTS = ("16:9", "9:16", "1:1")
SOURCE_CAPS = {"title": 300, "uploader": 100, "category": 60, "published_at": 40}
VIDEO_ID = re.compile(r"[A-Za-z0-9_-][A-Za-z0-9_.-]{0,63}")

_NAME = re.compile(r"source\.mp4|audio\.m4a|shot-[1-9]\.jpg")
_FETCH_ID = re.compile(r"[0-9a-f]{32}")


class Problem(Exception):
    """The work stopped under a rule: `rule` and the message."""

    def __init__(self, rule: str, message: str) -> None:
        super().__init__(message)
        self.rule = rule


def rule_of(exc: IngestError) -> str:
    """The rule an ingest failure is: down, busy or broken is `ingest`; a
    request it will not take is `source`; the rest (it tried and could not,
    or would not take that video) is `fetch`."""
    if exc.status is None or exc.status == 429 or exc.status >= 500:
        return "ingest"
    return "source" if exc.status == 400 else "fetch"


def number(value: Any, low: float, high: float) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and low <= value <= high


# --- the fetch --------------------------------------------------------------------------------


def checked_fetch(got: Any, video_id: str) -> Json:
    """The ingest service's fetch answer, or a `fetch` problem when it is not
    the shape (and the video) asked for."""
    def bad(why: str) -> Problem:
        return Problem("fetch", f"the ingest service's fetch is not usable: {why}")

    if not isinstance(got, dict) or not isinstance(got.get("fetch_id"), str) or not _FETCH_ID.fullmatch(got["fetch_id"]):
        raise bad("no fetch id")
    files, shots, source, excerpt = got.get("files"), got.get("shots"), got.get("source"), got.get("excerpt")
    if not isinstance(files, list) or not all(isinstance(f, str) and _NAME.fullmatch(f) for f in files) or "source.mp4" not in files:
        raise bad("its files")
    if not isinstance(shots, list) or not 1 <= len(shots) <= MAX_SHOTS:
        raise bad("its shots")
    for n, shot in enumerate(shots, 1):
        if not isinstance(shot, dict) or shot.get("n") != n or isinstance(shot.get("n"), bool) \
                or not number(shot.get("seconds"), 0.01, 60) or shot.get("frame") != f"shot-{n}.jpg" \
                or shot["frame"] not in files:
            raise bad(f"shot {n}")
    if not isinstance(source, dict) or source.get("video_id") != video_id:
        raise bad("it is not the video asked for")
    if not isinstance(excerpt, dict) or not number(excerpt.get("start"), 0, 3600) or not number(excerpt.get("seconds"), 0.01, 120):
        raise bad("its excerpt")
    if got.get("aspect") not in ASPECTS or not number(got.get("fps"), 1, 240):
        raise bad("its aspect or frame rate")
    return got


def source_fields(got: Json, url: str) -> Json:
    source = got["source"]
    out: Json = {"source": source.get("source") if source.get("source") in CODES else None,
                 "video_id": source["video_id"], "url": source["url"] if source_of(source.get("url")) else url}
    for key, cap in SOURCE_CAPS.items():
        out[key] = clean_text(source.get(key), cap) or None
    for key in ("seconds", "views", "age_limit", "width", "height"):
        value = source.get(key)
        out[key] = value if isinstance(value, (int, float)) and not isinstance(value, bool) else None
    out["live"] = source.get("live") is True
    return out


def magic(name: str, data: bytes) -> bool:
    if name.endswith(".jpg"):
        return data[:3] == b"\xff\xd8\xff"
    return data[4:8] == b"ftyp"


def pull(ingest: Any, got: Json, folder: Path) -> dict[str, bytes]:
    """Every file of the fetch into `folder` (a staging folder), each within
    its cap and with the bytes its name says; the frames' bytes, by name."""
    frames: dict[str, bytes] = {}
    for name in got["files"]:
        limit = MAX_SOURCE_BYTES if name == "source.mp4" else MAX_AUDIO_BYTES if name == "audio.m4a" else MAX_FRAME_BYTES
        data = ingest.file(got["fetch_id"], name, limit)
        if not data or len(data) > limit or not magic(name, data):
            raise Problem("fetch", f"the ingest service's {name} is not the file it says")
        (folder / name).write_bytes(data)
        if name.endswith(".jpg"):
            frames[name] = data
    return frames


def forget(ingest: Any, fetch_id: str) -> None:
    try:
        ingest.delete(fetch_id)
    except IngestError:
        pass  # the service's own sweep removes it within the hour


# --- the screen -------------------------------------------------------------------------------


def tripped(text: Any, title: bool = False) -> bool:
    """Whether the guard's inbound screen stops the text (a title: or a word
    of the HARM or NOT_A_FIGURE lists)."""
    if not isinstance(text, str) or not text:
        return False
    if title and (HARM.search(text) or NOT_A_FIGURE.search(text)):
        return True
    return screen_text(text, INBOUND) is not None


def screen_source(source: Json) -> list[str]:
    """The source's own words the screen stops, blanked, and which."""
    hit = []
    for key in ("title", "uploader"):
        if tripped(source.get(key), title=key == "title"):
            source[key] = None
            hit.append(key)
    return hit


# --- what is kept -----------------------------------------------------------------------------


def media_dir(root: str) -> Path:
    """state/media/<root>/, made when missing."""
    path = hand.state_dir() / "media" / root
    path.mkdir(parents=True, exist_ok=True)
    return path


def rel(root: str, folder: str, name: str) -> str:
    """A kept file's path as an answer gives it: relative to the checkout."""
    return f"state/media/{root}/{folder}/{name}"


def sweep(root: str, keep_hours: int) -> None:
    """Removes the folders under state/media/<root>/ (and staging leftovers)
    not touched for `keep_hours`. The files' own times are real, so the real
    clock is the one to read."""
    cutoff = time.time() - keep_hours * 3600
    for entry in media_dir(root).iterdir():
        try:
            if entry.is_dir() and entry.stat().st_mtime < cutoff:
                shutil.rmtree(entry, ignore_errors=True)
        except OSError:
            pass


def keep(final: Path, answer_name: str, answer: Json, staging: Path | None, readable: bool) -> None:
    """What is left on disk in `final`: a usable answer, its media moved whole
    out of staging beside `answer_name`; an unusable one, only its answer (no
    media); one that could not be read, nothing."""
    shutil.rmtree(final, ignore_errors=True)
    if answer["usable"] and staging is not None:
        os.replace(staging, final)
    elif readable:
        final.mkdir(parents=True)
    else:
        return
    (final / answer_name).write_text(json.dumps(answer, ensure_ascii=False), encoding="utf-8")
