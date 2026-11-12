"""Shared by the hands/reddit tests: a scratch TEENY_ROOT and a fake Reddit
on 127.0.0.1 (hands/x's fake server, which routes by method and path). Both
the token endpoint and the API point at it through `REDDIT_AUTH_BASE` and
`REDDIT_API_BASE` in the scratch secrets/teeny.env. Nothing here talks to
the real Reddit. The guard is the real one, with the model judge off."""

from __future__ import annotations

import logging
import os
import tempfile
import unittest
import urllib.parse
from pathlib import Path

from x.support import FakeX, Seen  # noqa: F401 - re-exported for the tests
from teeny_hands import hand
from teeny_hands.reddit import client

logging.getLogger("teeny_hands.guard").addHandler(logging.NullHandler())
logging.getLogger("teeny_hands.guard").propagate = False

#: Distinctive, so a leak into any output or file is found.
SECRET = "reddit_secret_FAKEFAKEFAKE0000"
PASSWORD = "hunter2_FAKEpasswordFAKE"
TOKEN = "reddit_token_FAKEFAKEtoken0000"
T0 = 1_789_369_200.0  # 2026-09-14T07:00:00Z
MINUTE = 60.0
HOUR = 3600.0
DAY = 86400.0
ME = "teenydroid"


def form(seen: Seen) -> dict[str, str]:
    return dict(urllib.parse.parse_qsl(seen.body.decode()))


def listing(*things: dict) -> dict:
    return {"kind": "Listing", "data": {"children": list(things), "after": None}}


def t3(pid: str, author: str = "someone", subreddit: str = "learnpython", **more) -> dict:
    data = {"id": pid, "name": f"t3_{pid}", "author": author, "subreddit": subreddit, "title": f"post {pid}",
            "is_self": True, "selftext": "body", "created_utc": T0 - HOUR, "permalink": f"/r/{subreddit}/comments/{pid}/x/"}
    data.update(more)
    return {"kind": "t3", "data": data}


def t1(cid: str, author: str, post: str, parent: str | None = None, body: str = "a comment", **more) -> dict:
    data = {"id": cid, "name": f"t1_{cid}", "author": author, "body": body, "link_id": f"t3_{post}",
            "parent_id": parent or f"t3_{post}", "subreddit": "learnpython", "created_utc": T0 - HOUR}
    data.update(more)
    return {"kind": "t1", "data": data}


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
        self.rd = FakeX()
        self.addCleanup(self.rd.close)
        self.rd.route("POST", "/api/v1/access_token", {"access_token": TOKEN, "token_type": "bearer", "expires_in": 3600})
        self.configure()

    def tearDown(self) -> None:
        os.environ.clear()
        os.environ.update(self._env)
        hand._ENV_CACHE = None
        client.reset()
        self.tmp.cleanup()

    def configure(self, **overrides: str | None) -> None:
        values: dict[str, str | None] = {
            "REDDIT_CLIENT_ID": "client-id-FAKE",
            "REDDIT_CLIENT_SECRET": SECRET,
            "REDDIT_USERNAME": ME,
            "REDDIT_PASSWORD": PASSWORD,
            "REDDIT_AUTH_BASE": self.rd.base,
            "REDDIT_API_BASE": self.rd.base,
            "REDDIT_SUBREDDITS": "learnpython, r/SideProject",
            "DRY_RUN": "0",
            "SAFETY_JUDGE": "0",
        }
        values.update(self.settings)
        values.update(overrides)
        text = "".join(f"{k}={v}\n" for k, v in values.items() if v is not None)
        (self.root / "secrets" / "teeny.env").write_text(text)
        hand._ENV_CACHE = None
        client.reset()

    def at(self, seconds: float) -> None:
        os.environ["TEENY_NOW"] = str(seconds)

    # --- fake Reddit answers ---------------------------------------------------------------

    def community(self, name: str = "learnpython", **about) -> None:
        data = {"display_name": name, "subreddit_type": "public", "submission_type": "any", "over18": False}
        data.update(about)
        self.rd.route("GET", f"/r/{name}/about", {"kind": "t5", "data": data})
        self.rd.route("GET", f"/r/{name}/about/rules", {"rules": [{"short_name": "Be nice", "kind": "all", "description": "be nice"}]})

    def things(self, *items: dict) -> None:
        """/api/info answers each fullname with its thing."""
        by_name = {i["data"]["name"]: i for i in items}
        for name, item in by_name.items():
            self.rd.route("GET", "/api/info", listing(item), when=lambda s, name=name: s.query.get("id") == name)

    def submitted(self, pid: str = "new1", errors: list | None = None, ratelimit: float | None = None) -> None:
        body: dict = {"json": {"errors": errors or []}}
        if errors:
            if ratelimit is not None:
                body["json"]["ratelimit"] = ratelimit
        else:
            body["json"]["data"] = {"id": pid, "name": f"t3_{pid}", "url": f"https://www.reddit.com/r/x/comments/{pid}/"}
        self.rd.route("POST", "/api/submit", body)

    def commented(self, cid: str = "c100", errors: list | None = None, ratelimit: float | None = None) -> None:
        body: dict = {"json": {"errors": errors or []}}
        if errors:
            if ratelimit is not None:
                body["json"]["ratelimit"] = ratelimit
        else:
            body["json"]["data"] = {"things": [{"kind": "t1", "data": {"id": cid, "name": f"t1_{cid}"}}]}
        self.rd.route("POST", "/api/comment", body)

    def leaked(self) -> list[Path]:
        """Every file under the scratch root, but secrets/, holding a secret."""
        needles = [s.encode() for s in (SECRET, PASSWORD, TOKEN)]
        return [
            p for p in self.root.rglob("*")
            if p.is_file() and "secrets" not in p.parts and any(n in p.read_bytes() for n in needles)
        ]
