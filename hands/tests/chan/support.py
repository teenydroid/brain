"""Shared by the hands/chan tests: a scratch TEENY_ROOT and one fake server
on 127.0.0.1 (hands/x's fake) standing in for 4chan's API (`CHAN_API_BASE`,
set in the scratch secrets/teeny.env). Nothing here talks to the real
4chan, and the pace between requests is off."""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from typing import Any

from x.support import FakeX
from teeny_hands import hand

T0 = 1_789_369_200.0  # 2026-09-14T07:00:00Z
HOUR = 3600.0
MINUTE = 60.0


def op(no: int, sub: str = "", com: str = "the moon is a hard drive and it is almost full", replies: int = 10,
       at: float = T0 - HOUR, **more: Any) -> dict:
    out = {"no": no, "resto": 0, "time": int(at), "replies": replies, "images": 1, "name": "Anonymous",
           "trip": "!!secret", "id": "AbCd1234", "country": "US", "com": com, **more}
    if sub:
        out["sub"] = sub
    return out


class Scratch(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name).resolve()
        (self.root / "secrets").mkdir()
        self._env = dict(os.environ)
        os.environ["TEENY_ROOT"] = str(self.root)
        for key in ("DRY_RUN", "TEENY_NOW", "TEENY_HANDS_DEBUG", "CHAN_BOARDS"):
            os.environ.pop(key, None)
        os.environ["TEENY_NOW"] = str(T0)
        self.chan = FakeX()
        self.addCleanup(self.chan.close)
        self.configure()

    def tearDown(self) -> None:
        os.environ.clear()
        os.environ.update(self._env)
        hand._ENV_CACHE = None
        self.tmp.cleanup()

    def configure(self, **overrides: str | None) -> None:
        values: dict[str, str | None] = {"CHAN_API_BASE": self.chan.base, "CHAN_MIN_INTERVAL_MS": "0",
                                         "SAFETY_JUDGE": "0"}
        values.update(overrides)
        (self.root / "secrets" / "teeny.env").write_text(
            "".join(f"{k}={v}\n" for k, v in values.items() if v is not None))
        hand._ENV_CACHE = None

    def at(self, seconds: float) -> None:
        os.environ["TEENY_NOW"] = str(seconds)

    def catalog(self, board: str, *threads: dict, pages: int = 1) -> None:
        per = max(1, -(-len(threads) // pages))
        body = [{"page": i + 1, "threads": list(threads[i * per:(i + 1) * per])} for i in range(pages)]
        self.chan.route("GET", f"/{board}/catalog.json", body)

    def thread(self, board: str, no: int, *posts: dict) -> None:
        self.chan.route("GET", f"/{board}/thread/{no}.json", {"posts": list(posts)})

    def reads(self, path: str) -> int:
        return len(self.chan.requests("GET", path))
