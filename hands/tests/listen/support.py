"""Shared by the hands/listen tests: a scratch TEENY_ROOT and one fake server
on 127.0.0.1 (hands/x's fake) standing in for every source: Hacker News's two
APIs under /v0 and /api/v1 (as in the hands/hn tests), Lobsters at /lobsters,
GitHub at /github and arXiv at /arxiv. The recorded answers are in
fixtures/ (taken from the real services on 2026-11-13, trimmed). Nothing here
talks to the network."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from hn.support import DAY, HOUR, MINUTE, T0, FakeX, Scratch as HnScratch, story  # noqa: F401

FIXTURES = Path(__file__).resolve().parent / "fixtures"


def fixture(name: str) -> Any:
    raw = (FIXTURES / name).read_text()
    return json.loads(raw) if name.endswith(".json") else raw


def hit(object_id: int, title: str, points: int = 10, comments: int = 2, kind: str = "story", **more: Any) -> dict:
    out = {"objectID": str(object_id), "_tags": [kind], "author": "ana", "points": points,
           "num_comments": comments, "created_at_i": int(T0 - HOUR), **more}
    if kind == "story":
        out.update(title=title, url=f"https://example.com/{object_id}")
    else:
        out.update(comment_text=title)
    return out


class Scratch(HnScratch):
    def configure(self, **overrides: str | None) -> None:
        values = {
            "LISTEN_LOBSTERS_BASE": self.hn.base + "/lobsters",
            "LISTEN_GITHUB_BASE": self.hn.base + "/github",
            "LISTEN_ARXIV_BASE": self.hn.base + "/arxiv",
        }
        values.update(overrides)
        super().configure(**values)

    def sources(self, lobsters: Any = None, github: Any = None, arxiv: str | None = None) -> None:
        """Every non-HN source answering its recorded page."""
        self.hn.route("GET", "/lobsters/hottest.json", lobsters if lobsters is not None else fixture("lobsters_hottest.json"))
        self.hn.route("GET", "/github/search/repositories",
                      github if github is not None else fixture("github_search_repositories.json"))
        self.hn.route("GET", "/arxiv/api/query", raw=(arxiv or fixture("arxiv_query.xml")).encode(),
                      content_type="application/atom+xml")

    def algolia(self, by_query: dict[str, list[dict]]) -> None:
        """Algolia's answer per query; any other query finds nothing."""
        self.hn.route("GET", "/api/v1/search", {"hits": []})
        for query, hits in by_query.items():
            self.hn.route("GET", "/api/v1/search", {"hits": hits}, when=lambda s, q=query: s.query.get("query") == q)
