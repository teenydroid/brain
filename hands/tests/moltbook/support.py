"""Shared by the hands/moltbook tests: a scratch TEENY_ROOT and a fake
Moltbook on 127.0.0.1 (hands/x's fake server, which routes by method and
path), reached through `MOLTBOOK_API_BASE` in the scratch secrets/teeny.env.
Nothing here talks to the real Moltbook. The guard is the real one, with the
model judge off."""

from __future__ import annotations

import logging
import os
import tempfile
import unittest
from pathlib import Path

from x.support import FakeX, Seen  # noqa: F401 - re-exported for the tests
from teeny_hands import hand

logging.getLogger("teeny_hands.guard").addHandler(logging.NullHandler())
logging.getLogger("teeny_hands.guard").propagate = False

#: Distinctive, so a leak into any output or file is found.
KEY = "moltbook_sk_FAKEkeyFAKEkeyFAKEkey0000"
T0 = 1_789_369_200.0  # 2026-09-14T07:00:00Z
MINUTE = 60.0
HOUR = 3600.0
DAY = 86400.0
#: Claimed long ago: the established limits apply unless a test says otherwise.
OLD_CLAIM = str(T0 - 30 * DAY)

CHALLENGE = "A] lO^bSt-Er S[wImS aT/ tW]eNn-Tyy mE^tE[rS aNd] SlO/wS bY^ fI[vE, wH-aTs] ThE/ nEw^ SpE[eD?"
UNREADABLE = "A] lO^bSt-Er S[wImS fIvE tWeNtY mE^tE[rS, wH-aTs] ThE/ nEw^ SpE[eD?"


def verification(text: str = CHALLENGE, code: str = "moltbook_verify_abc") -> dict:
    return {"verification_code": code, "challenge_text": text, "expires_at": "2026-09-14T07:05:00Z"}


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
        self.mb = FakeX()
        self.addCleanup(self.mb.close)
        self.configure()

    def tearDown(self) -> None:
        os.environ.clear()
        os.environ.update(self._env)
        hand._ENV_CACHE = None
        self.tmp.cleanup()

    def configure(self, **overrides: str | None) -> None:
        values: dict[str, str | None] = {
            "MOLTBOOK_API_KEY": KEY,
            "MOLTBOOK_API_BASE": self.mb.base,
            "MOLTBOOK_AGENT_NAME": "teenydroid",
            "MOLTBOOK_CLAIMED_AT": OLD_CLAIM,
            "DRY_RUN": "0",
            "SAFETY_JUDGE": "0",
        }
        values.update(self.settings)
        values.update(overrides)
        text = "".join(f"{k}={v}\n" for k, v in values.items() if v is not None)
        (self.root / "secrets" / "teeny.env").write_text(text)
        hand._ENV_CACHE = None

    def at(self, seconds: float) -> None:
        os.environ["TEENY_NOW"] = str(seconds)

    # --- fake Moltbook answers ---------------------------------------------------------------

    def created(self, kind: str = "post", content_id: str = "p1", ver: dict | None = None, path: str = "/posts") -> None:
        inner = {"id": content_id}
        if ver is not None:
            inner["verification_status"] = "pending"
            inner["verification"] = ver
        body = {"success": True, kind: inner}
        if ver is not None:
            body["verification_required"] = True
        self.mb.route("POST", path, body, status=201)

    def verified(self, ok: bool = True, status: int = 200) -> None:
        body = {"success": True, "message": "Verification successful!"} if ok else {"success": False, "error": "Incorrect answer"}
        self.mb.route("POST", "/verify", body, status=status)

    def a_post(self, post_id: str, author: str = "someone", submolt: str = "general", comments: list | None = None) -> None:
        self.mb.route("GET", f"/posts/{post_id}", {"success": True, "post": {"id": post_id, "author": {"name": author}, "submolt": {"name": submolt}}})
        self.mb.route("GET", f"/posts/{post_id}/comments", {"success": True, "comments": comments or []})

    def submolt(self, name: str, allow_crypto: bool | None) -> None:
        info = {"name": name}
        if allow_crypto is not None:
            info["allow_crypto"] = allow_crypto
        self.mb.route("GET", f"/submolts/{name}", {"success": True, "submolt": info})

    def leaked(self) -> list[Path]:
        """Every file under the scratch root, but secrets/, holding the key."""
        return [
            p for p in self.root.rglob("*")
            if p.is_file() and "secrets" not in p.parts and KEY.encode() in p.read_bytes()
        ]
