"""Shared by the hands/hn tests: a scratch TEENY_ROOT and one fake server on
127.0.0.1 (hands/x's fake, which routes by method and path) standing in for
both Hacker News APIs: the Firebase one under /v0 (`HN_API_BASE`) and
Algolia's search under /api/v1 (`HN_SEARCH_BASE`), both set in the scratch
secrets/teeny.env. Nothing here talks to the real Hacker News. The guard is
the real one, with the model judge off."""

from __future__ import annotations

import logging
import os
import tempfile
import unittest
from pathlib import Path
from typing import Any

from x.support import FakeX, Seen  # noqa: F401 - re-exported for the tests
from teeny_hands import hand

logging.getLogger("teeny_hands.guard").addHandler(logging.NullHandler())
logging.getLogger("teeny_hands.guard").propagate = False

T0 = 1_789_369_200.0  # 2026-09-14T07:00:00Z
MINUTE = 60.0
HOUR = 3600.0
DAY = 86400.0
ME = "teenydroid"


def story(item_id: int, title: str = "a story", by: str = "pg", at: float = T0 - HOUR, kids: list[int] | None = None,
          **more: Any) -> dict:
    return {"id": item_id, "type": "story", "title": title, "by": by, "time": int(at), "score": 10,
            "descendants": len(kids or []), "url": f"https://example.com/{item_id}", "kids": kids or [], **more}


def comment(item_id: int, parent: int, text: str = "a comment", by: str = "dang", at: float = T0 - HOUR,
            kids: list[int] | None = None, **more: Any) -> dict:
    out = {"id": item_id, "type": "comment", "by": by, "parent": parent, "text": text, "time": int(at), **more}
    if kids:
        out["kids"] = kids
    return out


class Scratch(unittest.TestCase):
    settings: dict[str, str] = {}

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name).resolve()
        (self.root / "secrets").mkdir()
        self._env = dict(os.environ)
        os.environ["TEENY_ROOT"] = str(self.root)
        for key in ("DRY_RUN", "TEENY_NOW", "TEENY_HANDS_DEBUG"):
            os.environ.pop(key, None)
        self.at(T0)
        self.hn = FakeX()
        self.addCleanup(self.hn.close)
        self.configure()

    def tearDown(self) -> None:
        os.environ.clear()
        os.environ.update(self._env)
        hand._ENV_CACHE = None
        self.tmp.cleanup()

    def configure(self, **overrides: str | None) -> None:
        values: dict[str, str | None] = {
            "HN_API_BASE": self.hn.base + "/v0",
            "HN_SEARCH_BASE": self.hn.base + "/api/v1",
            "SAFETY_JUDGE": "0",
        }
        values.update(self.settings)
        values.update(overrides)
        text = "".join(f"{k}={v}\n" for k, v in values.items() if v is not None)
        (self.root / "secrets" / "teeny.env").write_text(text)
        hand._ENV_CACHE = None

    def at(self, seconds: float) -> None:
        os.environ["TEENY_NOW"] = str(seconds)

    # --- fake Hacker News answers --------------------------------------------------------

    def items(self, *items: dict) -> None:
        for item in items:
            self.hn.route("GET", f"/v0/item/{item['id']}.json", item)

    def feed(self, name: str, ids: list[int]) -> None:
        self.hn.route("GET", f"/v0/{name}stories.json", ids)

    def user(self, name: str, submitted: list[int], **more: Any) -> None:
        self.hn.route("GET", f"/v0/user/{name}.json", {"id": name, "created": 1_600_000_000, "karma": 12,
                                                        "submitted": submitted, **more})

    def fetched(self, item_id: int | None = None) -> int:
        """How many item reads the fake answered (of one item, or all)."""
        if item_id is not None:
            return len(self.hn.requests("GET", f"/v0/item/{item_id}.json"))
        return sum(1 for s in self.hn.requests("GET") if s.path.startswith("/v0/item/"))
