"""Shared by the hands/khaby tests: the remake tests' scratch TEENY_ROOT and
ingest fake (hands/tests/remake/support.py), a fetch shaped like a Khaby
video, and a fake video model that answers either provider's call. Nothing
here uses the network, yt-dlp or ffmpeg."""

from __future__ import annotations

import json
from typing import Any

from remake.support import FETCH_ID, JPEG, M4A, MP4, T0, FakeIngest, Scratch, entry, fetched  # noqa: F401

TT_URL = "https://www.tiktok.com/@khaby.lame/video/7300"
FLAGS = {"minors": False, "news": False, "tragedy": False, "sexual": False, "violence": False, "identifiable_private": False}


def khaby_fetch(lengths: tuple[float, ...] = (4.0, 3.5, 4.5), fps: float = 30.0, video_id: str = "7300",
                audio: bool = True) -> dict[str, Any]:
    """A fetch answer whose shots last `lengths` seconds: with the defaults the
    cuts are at 0, 4.0, 7.5 and 12.0."""
    got = fetched(len(lengths), audio=audio, video_id=video_id)
    at = 0.0
    shots = []
    for n, seconds in enumerate(lengths, 1):
        shots.append({"n": n, "start": round(at, 3), "seconds": seconds, "frame": f"shot-{n}.jpg"})
        at += seconds
    got["shots"] = shots
    got["excerpt"] = {"start": 0.0, "seconds": round(at, 3)}
    got["fps"] = fps
    got["source"] = {**got["source"], "url": TT_URL, "title": "when the hack is too much", "uploader": "khaby.lame"}
    return got


def segment(start: float, end: float, role: str, what: str = "something happens") -> dict[str, Any]:
    return {"start": start, "end": end, "role": role, "what": what}


def reading(segments: list[dict] | None = None, beats: list[dict] | None = None, joke: str = "the hack was never needed",
            **flags: bool) -> dict[str, Any]:
    """A video model's answer: by default a setup to the cut at 4.0 and the
    reaction after it, with three beats."""
    return {"segments": segments if segments is not None else
            [segment(0, 4.1, "setup", "a woman peels a banana with a drill"),
             segment(4.1, 12.0, "reaction", "a man looks at the camera and peels it by hand")],
            "beats": beats if beats is not None else
            [{"at": 4.5, "what": "looks at the camera"}, {"at": 7.0, "what": "peels the banana by hand"},
             {"at": 10.2, "what": "opens his hands"}],
            "joke": joke, "flags": {**FLAGS, **flags}}


def surplus_reply(content: Any, cost: float | None = 0.0412) -> dict[str, Any]:
    text = content if isinstance(content, str) else json.dumps(content)
    usage: dict[str, Any] = {"prompt_tokens": 12000, "completion_tokens": 600}
    if cost is not None:
        usage["cost"] = cost
    return {"choices": [{"message": {"content": text}}], "usage": usage}


def gemini_reply(content: Any) -> dict[str, Any]:
    text = content if isinstance(content, str) else json.dumps(content)
    return {"candidates": [{"content": {"role": "model", "parts": [{"text": text}]}}],
            "usageMetadata": {"promptTokenCount": 9000, "candidatesTokenCount": 500, "totalTokenCount": 9500}}


class FakeModel:
    """A video model as the hand's clients have it (`call`, as Surplus's):
    the replies in turn (the last one again once they run out), an exception
    raised; every call kept."""

    def __init__(self, *replies: Any) -> None:
        self.replies = list(replies) or [surplus_reply(reading())]
        self.calls: list[tuple[str, str, Any]] = []

    def call(self, method: str, path: str, body: Any = None, timeout: float = 60.0, headers: dict | None = None) -> Any:
        self.calls.append((method, path, body))
        got = self.replies.pop(0) if len(self.replies) > 1 else self.replies[0]
        if isinstance(got, Exception):
            raise got
        return got

    @property
    def bodies(self) -> list[Any]:
        return [c[2] for c in self.calls]
