"""Shared by the hands/trend tests: a scratch TEENY_ROOT and a fake
`digg-pp-cli` (TREND_DIGG_BIN in the scratch secrets/teeny.env) that answers
from fixtures beside it and logs how it was called. Nothing here talks to
Digg."""

from __future__ import annotations

import json
import os
import stat
import tempfile
import unittest
from pathlib import Path
from typing import Any

from teeny_hands import hand

T0 = 1_795_000_000.0  # 2026-11-18

#: The fake tool. `behave.json` beside it maps "top:<topic>" or
#: "posts:<cluster>" to how it answers: ok (the fixture, a {meta, results}
#: envelope), bare (the fixture's results as a bare list), rate (Digg's
#: "temporarily unavailable"), fail, garbage, sleep.
FAKE = r'''#!/usr/bin/env python3
import json, os, sys, time
from pathlib import Path
here = Path(__file__).resolve().parent
with open(here / "calls.log", "a") as log:
    log.write(json.dumps({"argv": sys.argv[1:], "home": os.environ.get("HOME"), "cwd": os.getcwd(),
                          "xdg": os.environ.get("XDG_DATA_HOME"), "leak": os.environ.get("TREND_TEST_LEAK")}) + "\n")
verb = sys.argv[1]
key = f"{verb}:{sys.argv[sys.argv.index('--topic') + 1]}" if verb == "top" else f"{verb}:{sys.argv[2]}"
behave = json.loads((here / "behave.json").read_text()) if (here / "behave.json").exists() else {}
how = behave.get(key, "ok")
if how == "rate":
    print("Error: digg search is temporarily unavailable, retry after 60s", file=sys.stderr); sys.exit(1)
if how == "fail":
    print("Error: digg_get_post: Post not found.", file=sys.stderr); sys.exit(5)
if how == "garbage":
    print("<html>not json</html>"); sys.exit(0)
if how == "sleep":
    time.sleep(30); sys.exit(0)
fixture = here / "fixtures" / (key.replace(":", "_") + ".json")
if not fixture.exists():
    print(f"Error: no fixture {key}", file=sys.stderr); sys.exit(2)
got = json.loads(fixture.read_text())
print(json.dumps(got["results"] if how == "bare" else got))
'''


def cluster(n: int) -> str:
    return f"{n:08x}-0000-4000-8000-{n:012x}"


def row(n: int, topic: str, score: int, title: str | None = None, **more: Any) -> dict:
    return {"rank": n, "clusterId": cluster(n), "clusterUrlId": f"s{n}", "url": f"https://digg.com/{topic}/s{n}",
            "topic": topic, "title": title or f"story {n}", "description": f"what story {n} says",
            "diggScore": score, "postCount": 10, "uniqueAuthors": 8, "publishedAt": "2026-11-18T05:00:00+00:00",
            **more}


def post(name: str, body: str, kind: str = "x", n: int = 1) -> dict:
    return {"author": {"display_name": name, "username": name.lower().replace(" ", "")}, "body": body, "kind": kind,
            "post_x_id": str(n), "posted_at": "2026-11-18T05:30:00.000Z", "xUrl": f"https://x.com/u/status/{n}"}


class Scratch(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name).resolve()
        (self.root / "secrets").mkdir()
        self.tool = self.root / "tool"
        (self.tool / "fixtures").mkdir(parents=True)
        self.exe = self.tool / "digg-pp-cli"
        self.exe.write_text(FAKE)
        self.exe.chmod(self.exe.stat().st_mode | stat.S_IEXEC)
        self._env = dict(os.environ)
        os.environ["TEENY_ROOT"] = str(self.root)
        os.environ["TEENY_NOW"] = str(T0)
        for key in ("DRY_RUN", "TEENY_HANDS_DEBUG"):
            os.environ.pop(key, None)
        self.configure()

    def tearDown(self) -> None:
        os.environ.clear()
        os.environ.update(self._env)
        hand._ENV_CACHE = None
        self.tmp.cleanup()

    def configure(self, **overrides: str | None) -> None:
        values: dict[str, str | None] = {"TREND_DIGG_BIN": str(self.exe), "TREND_TIMEOUT_SECS": "5"}
        values.update(overrides)
        text = "".join(f"{k}={v}\n" for k, v in values.items() if v is not None)
        (self.root / "secrets" / "teeny.env").write_text(text)
        hand._ENV_CACHE = None

    def board(self, topic: str, *rows: dict) -> None:
        (self.tool / "fixtures" / f"top_{topic}.json").write_text(
            json.dumps({"meta": {"count": len(rows), "topics": [topic], "source": "live"}, "results": list(rows)}))

    def sources(self, n: int, *posts: dict) -> None:
        (self.tool / "fixtures" / f"posts_{cluster(n)}.json").write_text(json.dumps(
            {"meta": {"clusterId": cluster(n), "clusterUrlId": f"s{n}", "title": f"story {n}", "topic": "ai",
                      "url": f"https://digg.com/ai/s{n}", "count": len(posts)}, "results": list(posts)}))

    def behave(self, **how: str) -> None:
        (self.tool / "behave.json").write_text(json.dumps({k.replace("__", ":"): v for k, v in how.items()}))

    def behave_keys(self, mapping: dict[str, str]) -> None:
        (self.tool / "behave.json").write_text(json.dumps(mapping))

    def calls(self) -> list[dict]:
        path = self.tool / "calls.log"
        return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []
