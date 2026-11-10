"""Shared by the hands/mail tests: a scratch TEENY_ROOT, a fake AgentMail on
127.0.0.1 (hands/x's fake server) through `AGENTMAIL_API_BASE`, and a fake
core for the inbound offers. Nothing here talks to the real AgentMail. The
guard is the real one, with the model judge off."""

from __future__ import annotations

import logging
import os
import tempfile
import unittest
import urllib.parse
from pathlib import Path

from x.support import FakeX, Seen  # noqa: F401 - re-exported for the tests
from teeny_hands import hand

logging.getLogger("teeny_hands.guard").addHandler(logging.NullHandler())
logging.getLogger("teeny_hands.guard").propagate = False

KEY = "am_FAKEkeyFAKEkeyFAKEkey0000"
INBOX = "teeny@agentmail.to"
T0 = 1_789_369_200.0  # 2026-09-14T07:00:00Z
HOUR = 3600.0
MSGS = "/inboxes/teeny%40agentmail.to/messages"


def path_of(mid: str) -> str:
    """The path the hand asks for a message id at (quoted as one segment),
    as the fake server sees it (the server unquotes nothing)."""
    return MSGS + "/" + urllib.parse.quote(mid, safe="")


def message(mid: str, sender: str = "Ana <ana@example.com>", subject: str = "hello", text: str = "hi teeny",
            thread: str | None = None, labels: list[str] | None = None, stamp: str = "2026-09-14T06:00:00Z", **more) -> dict:
    return {
        "inbox_id": INBOX, "thread_id": thread or f"t-{mid}", "message_id": mid, "labels": labels or ["received", "unread"],
        "timestamp": stamp, "from": sender, "to": [INBOX], "subject": subject, "preview": text[:40], "text": text, **more,
    }  # fmt: skip


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
        os.environ["TEENY_NOW"] = str(T0)
        self.am = FakeX()
        self.addCleanup(self.am.close)
        self.configure()

    def tearDown(self) -> None:
        os.environ.clear()
        os.environ.update(self._env)
        hand._ENV_CACHE = None
        self.tmp.cleanup()

    def configure(self, **overrides: str | None) -> None:
        values: dict[str, str | None] = {
            "AGENTMAIL_API_KEY": KEY,
            "AGENTMAIL_API_BASE": self.am.base,
            "AGENTMAIL_INBOX": INBOX,
            "DRY_RUN": "0",
            "SAFETY_JUDGE": "0",
        }
        values.update(self.settings)
        values.update(overrides)
        text = "".join(f"{k}={v}\n" for k, v in values.items() if v is not None)
        (self.root / "secrets" / "teeny.env").write_text(text)
        hand._ENV_CACHE = None

    def inbox(self, *messages: dict) -> None:
        """The list answers these; each is also routed by id."""
        self.am.route("GET", MSGS, {"count": len(messages), "messages": [
            {k: v for k, v in m.items() if k != "text"} for m in messages]})
        for m in messages:
            self.am.route("GET", path_of(m["message_id"]), m)
