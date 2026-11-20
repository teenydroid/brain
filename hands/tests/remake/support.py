"""Shared by the hands/remake tests: a scratch TEENY_ROOT and the fakes of
what the hand talks to (the ingest service, the YouTube Data API, a vision
model). Nothing here uses the network, yt-dlp or ffmpeg."""

from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any

LIB = Path(__file__).resolve().parents[2] / "lib"
if str(LIB) not in sys.path:
    sys.path.insert(0, str(LIB))

from teeny_hands import hand  # noqa: E402
from teeny_hands.remake.service import IngestError  # noqa: E402

T0 = 1_795_000_000.0  # 2026-11-18T11:06:40Z

MP4 = b"\x00\x00\x00\x18ftypisom" + b"\x00" * 40
M4A = b"\x00\x00\x00\x18ftypM4A " + b"\x00" * 40
JPEG = b"\xff\xd8\xff\xe0" + b"jpeg" * 10


class Scratch(unittest.TestCase):
    """A scratch TEENY_ROOT per test with an empty secrets/teeny.env and the
    clock pinned to T0."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        (self.root / "secrets").mkdir()
        self._env = dict(os.environ)
        os.environ["TEENY_ROOT"] = str(self.root)
        for key in ("DRY_RUN", "TEENY_NOW"):
            os.environ.pop(key, None)
        os.environ["TEENY_NOW"] = str(T0)
        self.secrets("")

    def tearDown(self) -> None:
        os.environ.clear()
        os.environ.update(self._env)
        hand._ENV_CACHE = None
        self.tmp.cleanup()

    def secrets(self, text: str) -> None:
        (self.root / "secrets" / "teeny.env").write_text(text)
        hand._ENV_CACHE = None


def entry(video_id: str, title: str = "a dance", views: int = 1000, seconds: float | None = 20.0,
          hours_old: float | None = 5.0, uploader: str = "someone") -> dict[str, Any]:
    """A listing entry as the ingest service answers it."""
    import datetime as dt

    published = None
    if hours_old is not None:
        published = dt.datetime.fromtimestamp(T0 - hours_old * 3600, dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    return {"url": f"https://www.tiktok.com/@{uploader}/video/{video_id}", "video_id": video_id, "title": title,
            "uploader": uploader, "seconds": seconds, "views": views, "published_at": published}


def item(video_id: str, title: str = "a song", views: int = 5000, duration: str = "PT45S", category: str = "10",
         live: str = "none", hours_old: float = 6.0, rating: dict | None = None, channel: str = "A Channel") -> dict[str, Any]:
    """One `videos.list` item as the YouTube Data API answers it."""
    import datetime as dt

    return {"id": video_id,
            "snippet": {"title": title, "channelTitle": channel, "categoryId": category, "liveBroadcastContent": live,
                        "publishedAt": dt.datetime.fromtimestamp(T0 - hours_old * 3600, dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")},
            "contentDetails": {"duration": duration, "contentRating": rating or {}},
            "statistics": {"viewCount": str(views)}}


class FakeIngest:
    """The ingest service as the hand's client has it: `lists` maps an
    account page URL to its entries (or an exception), `fetches` what a
    fetch answers; `files` the bytes it serves. Everything asked is kept."""

    def __init__(self) -> None:
        self.lists: dict[str, Any] = {}
        self.calls: list[tuple] = []
        self.probe_answer: dict[str, Any] | None = None
        self.fetch_answer: dict[str, Any] | Exception | None = None
        self.files: dict[str, bytes | Exception] = {}
        self.deleted: list[str] = []

    def listing(self, url: str, max: int = 10) -> list[dict]:
        self.calls.append(("listing", url, max))
        got = self.lists.get(url, [])
        if isinstance(got, Exception):
            raise got
        return got

    def probe(self, url: str) -> dict:
        self.calls.append(("probe", url))
        if isinstance(self.probe_answer, Exception):
            raise self.probe_answer
        return self.probe_answer or {"source": "tiktok", "video_id": "7300", "url": url}

    def fetch(self, body: dict, timeout: float = 600.0) -> dict:
        self.calls.append(("fetch", dict(body)))
        if isinstance(self.fetch_answer, Exception):
            raise self.fetch_answer
        return self.fetch_answer or fetched()

    def file(self, fetch_id: str, name: str, limit: int) -> bytes:
        self.calls.append(("file", fetch_id, name, limit))
        got = self.files.get(name)
        if got is None:
            got = MP4 if name == "source.mp4" else M4A if name == "audio.m4a" else JPEG
        if isinstance(got, Exception):
            raise got
        return got

    def delete(self, fetch_id: str) -> None:
        self.calls.append(("delete", fetch_id))
        self.deleted.append(fetch_id)

    def asked(self, kind: str) -> list[tuple]:
        return [c for c in self.calls if c[0] == kind]


FETCH_ID = "0123456789abcdef0123456789abcdef"


def fetched(n: int = 2, audio: bool = True, video_id: str = "7300") -> dict[str, Any]:
    shots = [{"n": i, "start": float(3 * (i - 1)), "seconds": 3.0, "frame": f"shot-{i}.jpg"} for i in range(1, n + 1)]
    return {"fetch_id": FETCH_ID,
            "source": {"source": "tiktok", "video_id": video_id, "url": f"https://www.tiktok.com/@someone/video/{video_id}",
                       "title": "a dance", "uploader": "someone", "seconds": 40.0, "views": 1000, "age_limit": 0,
                       "live": False, "category": None, "published_at": "2026-11-18T05:00:00Z", "width": 720,
                       "height": 1280},
            "excerpt": {"start": 0.0, "seconds": 3.0 * n}, "aspect": "9:16", "fps": 30.0, "shots": shots,
            "files": ["source.mp4", *(["audio.m4a"] if audio else []), *[s["frame"] for s in shots]]}
