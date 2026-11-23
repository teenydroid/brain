"""Shared by the hands/call tests: a scratch TEENY_ROOT, a fake service on
127.0.0.1 (hands/x's fake server, which routes by method and path and keeps
every request it saw), and a services list naming it.

Nothing here talks to a real service. The fake is reached through the one
seam the loader has for it, `services.load(..., loopback=True)`, which only
Python can pass: `services.configured` is replaced for the test's length."""

from __future__ import annotations

import contextlib
import io
import json
import logging
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from x.support import FakeX, Seen  # noqa: F401 - re-exported for the tests
from teeny_hands import hand
from teeny_hands.call import cli, services

logging.getLogger("teeny_hands.guard").addHandler(logging.NullHandler())
logging.getLogger("teeny_hands.guard").propagate = False

#: Distinctive, so a leak into any output or file is found.
KEY = "svc_sk_FAKEkeyFAKEkeyFAKEkey0000"
T0 = 1_789_369_200.0  # 2026-09-14T07:00:00Z
DAY = 86400.0

SERVICES = '''
[services.fake-svc]
base = "{base}/api/v1"
key = "FAKE_SVC_API_KEY"
surface = "moltbook"
agent = "teeny-call-fake"
allow = [
  {{ method = "GET", path = "/[A-Za-z0-9_/-]{{1,200}}" }},
  {{ method = "POST", path = "/posts/[A-Za-z0-9_-]{{1,100}}/(upvote|downvote)" }},
  {{ method = "POST", path = "/notes" }},
  {{ method = "PATCH", path = "/notes/[0-9]{{1,10}}" }},
  {{ method = "DELETE", path = "/notes/[0-9]{{1,10}}" }},
]
'''


class Scratch(unittest.TestCase):
    #: Settings for every test of a class, over the defaults.
    settings: dict[str, str] = {}

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name).resolve()
        (self.root / "secrets").mkdir()
        self._env = dict(os.environ)
        os.environ["TEENY_ROOT"] = str(self.root)
        for name in ("DRY_RUN", "TEENY_NOW", "TEENY_HANDS_DEBUG"):
            os.environ.pop(name, None)
        self.at(T0)
        self.svc = FakeX()
        self.addCleanup(self.svc.close)
        self.services_file = self.root / "services.toml"
        self.services_file.write_text(SERVICES.format(base=self.svc.base))
        patcher = mock.patch.object(services, "configured",
                                    lambda: services.load(self.services_file, loopback=True))
        patcher.start()
        self.addCleanup(patcher.stop)
        self.configure()
        self.stderr = ""

    def tearDown(self) -> None:
        os.environ.clear()
        os.environ.update(self._env)
        hand._ENV_CACHE = None
        self.tmp.cleanup()

    def configure(self, **overrides: str | None) -> None:
        values: dict[str, str | None] = {
            "FAKE_SVC_API_KEY": KEY,
            "DRY_RUN": "0",
            "SAFETY_JUDGE": "0",
            "CALL_FAKE_SVC_READS_PER_DAY": "100",
            "CALL_FAKE_SVC_WRITES_PER_DAY": "100",
        }
        values.update(self.settings)
        values.update(overrides)
        text = "".join(f"{k}={v}\n" for k, v in values.items() if v is not None)
        (self.root / "secrets" / "teeny.env").write_text(text)
        hand._ENV_CACHE = None

    def at(self, seconds: float) -> None:
        os.environ["TEENY_NOW"] = str(seconds)

    def call(self, body, raw: str | None = None) -> tuple[int, dict]:
        """hands/call send, in this process, with BODY as its stdin."""
        stdin = io.StringIO(raw if raw is not None else json.dumps(body))
        out, err = io.StringIO(), io.StringIO()
        with mock.patch("sys.stdin", stdin), contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = cli.main(["send"])
        self.stderr += err.getvalue()
        lines = out.getvalue().splitlines()
        self.assertEqual(len(lines), 1, f"one JSON line, got {out.getvalue()!r}")
        self.assertNotIn(KEY, out.getvalue() + err.getvalue())
        return code, json.loads(lines[0])

    def get(self, path: str = "/posts", **more) -> tuple[int, dict]:
        return self.call({"service": "fake-svc", "method": "GET", "path": path, **more})

    def write(self, method: str = "POST", path: str = "/notes", **more) -> tuple[int, dict]:
        return self.call({"service": "fake-svc", "method": method, "path": path, **more})

    def log(self) -> list[dict]:
        conn = hand.db()
        try:
            return [dict(r) for r in conn.execute("SELECT * FROM call_log ORDER BY id")]
        except Exception:  # noqa: BLE001 - no table yet: no calls
            return []
        finally:
            conn.close()

    def leaked(self) -> list[Path]:
        """Every file under the scratch root, but secrets/, holding the key."""
        return [p for p in self.root.rglob("*")
                if p.is_file() and "secrets" not in p.parts and KEY.encode() in p.read_bytes()]
