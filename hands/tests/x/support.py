"""Shared by the hands/x tests: a scratch TEENY_ROOT and a fake X on 127.0.0.1.

Nothing here talks to the real X. The hand is pointed at the fake through
`X_API_BASE` in the scratch `secrets/teeny.env`, as production points it at
https://api.x.com.
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import threading
import time
import unittest
import urllib.parse
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable

HERE = Path(__file__).resolve().parent
LIB = HERE.parent.parent / "lib"
if str(LIB) not in sys.path:
    sys.path.insert(0, str(LIB))

from teeny_hands import hand  # noqa: E402

# Fake credentials, distinctive enough that a leak into any output is found.
SECRETS = {
    "X_API_KEY": "ck-FAKE-CONSUMER-KEY",
    "X_API_SECRET": "cs-FAKE-CONSUMER-SECRET",
    "X_ACCESS_TOKEN": "at-FAKE-ACCESS-TOKEN",
    "X_ACCESS_TOKEN_SECRET": "ats-FAKE-ACCESS-SECRET",
    "X_BEARER_TOKEN": "app-bearer-FAKE",
}


@dataclass
class Seen:
    """One request the fake received."""

    method: str
    path: str
    query: dict[str, str]
    headers: dict[str, str]
    body: bytes

    def json(self) -> Any:
        return json.loads(self.body or b"null")

    @property
    def authorization(self) -> str:
        return self.headers.get("authorization", "")

    def is_oauth(self) -> bool:
        return self.authorization.startswith("OAuth ") and "oauth_signature=" in self.authorization


@dataclass
class Route:
    method: str
    path: str
    status: int
    body: Any
    when: Callable[[Seen], bool] | None = None
    raw: bytes | None = None
    content_type: str = "application/json"
    delay: float = 0.0
    times: list[int] = field(default_factory=lambda: [0])
    headers: dict[str, str] = field(default_factory=dict)


class FakeX:
    """Routes by method and exact path; anything unrouted is a 404 that a
    test can assert never happened."""

    def __init__(self) -> None:
        self.routes: list[Route] = []
        self.seen: list[Seen] = []
        self._lock = threading.Lock()
        fake = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args: Any) -> None:
                pass

            def _serve(self) -> None:
                parsed = urllib.parse.urlsplit(self.path)
                length = int(self.headers.get("content-length") or 0)
                seen = Seen(
                    method=self.command,
                    path=parsed.path,
                    query=dict(urllib.parse.parse_qsl(parsed.query, keep_blank_values=True)),
                    headers={k.lower(): v for k, v in self.headers.items()},
                    body=self.rfile.read(length) if length else b"",
                )
                with fake._lock:
                    fake.seen.append(seen)
                    route = fake._match(seen)
                if route is None:
                    status, data, ctype = 404, json.dumps({"title": "Not Routed"}).encode(), "application/json"
                else:
                    route.times[0] += 1
                if route is not None and route.delay:
                    time.sleep(route.delay)
                if route is not None:
                    status = route.status
                    ctype = route.content_type
                    data = route.raw if route.raw is not None else json.dumps(route.body).encode()
                self.send_response(status)
                self.send_header("content-type", ctype)
                self.send_header("content-length", str(len(data)))
                for name, value in (route.headers.items() if route is not None else ()):
                    self.send_header(name, value)
                self.end_headers()
                self.wfile.write(data)

            do_GET = do_POST = do_DELETE = do_PUT = do_PATCH = _serve

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, kwargs={"poll_interval": 0.02}, daemon=True)
        self.thread.start()

    @property
    def base(self) -> str:
        return f"http://127.0.0.1:{self.server.server_port}"

    def route(
        self,
        method: str,
        path: str,
        body: Any = None,
        status: int = 200,
        when: Callable[[Seen], bool] | None = None,
        raw: bytes | None = None,
        content_type: str = "application/json",
        delay: float = 0.0,
        headers: dict[str, str] | None = None,
    ) -> Route:
        route = Route(method, path, status, body, when, raw, content_type, delay, headers=dict(headers or {}))
        with self._lock:
            # Newest first, so a test can override a default route.
            self.routes.insert(0, route)
        return route

    def _match(self, seen: Seen) -> Route | None:
        for route in self.routes:
            if route.method == seen.method and route.path == seen.path and (route.when is None or route.when(seen)):
                return route
        return None

    def requests(self, method: str | None = None, path: str | None = None) -> list[Seen]:
        with self._lock:
            return [s for s in self.seen if (method is None or s.method == method) and (path is None or s.path == path)]

    def writes(self) -> list[Seen]:
        """Every request that is not a read."""
        return [s for s in self.requests() if s.method != "GET"]

    def close(self) -> None:
        self.server.shutdown()
        self.server.server_close()


class Scratch(unittest.TestCase):
    """A scratch TEENY_ROOT per test, the env cache cleared, a fake X running
    and `secrets/teeny.env` pointing the hand at it."""

    #: Extra settings for every test of a class.
    settings: dict[str, str] = {}

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name).resolve()
        (self.root / "secrets").mkdir()
        self._env = dict(os.environ)
        os.environ["TEENY_ROOT"] = str(self.root)
        for key in ("DRY_RUN", "TEENY_NOW", "TEENY_HANDS_DEBUG"):
            os.environ.pop(key, None)
        self.x = FakeX()
        self.addCleanup(self.x.close)
        self.configure()

    def tearDown(self) -> None:
        os.environ.clear()
        os.environ.update(self._env)
        hand._ENV_CACHE = None
        self.tmp.cleanup()

    def configure(self, **overrides: str | None) -> None:
        """Rewrites secrets/teeny.env: the fake keys, X_API_BASE, DRY_RUN=0
        unless a test says otherwise, the class's settings, then overrides
        (None removes a key)."""
        values: dict[str, str | None] = {**SECRETS, "X_API_BASE": self.x.base, "DRY_RUN": "0"}
        values.update(self.settings)
        values.update(overrides)
        text = "".join(f"{k}={v}\n" for k, v in values.items() if v is not None)
        (self.root / "secrets" / "teeny.env").write_text(text)
        hand._ENV_CACHE = None

    def at(self, seconds: float) -> None:
        """Pins the hand's clock."""
        os.environ["TEENY_NOW"] = str(seconds)


def me(fake: FakeX, user_id: str = "1000", username: str = "teenydroid") -> Route:
    """/2/users/me, as X answers it for the account."""
    return fake.route("GET", "/2/users/me", {"data": {"id": user_id, "username": username}})


T0 = 1_789_369_200.0  # 2026-09-14T07:00:00Z, the morning the legacy tests start from
HOUR = 3600.0
MINUTE = 60.0


class Guard:
    """Stands in for teeny_hands.guard.gate.check_outbound, patched into
    teeny_hands.x.publish. Answers one verdict and records every call."""

    def __init__(self, kind="allow", transform=None, reason="", category=None, hold_id=None, judge_failed=False):
        self.kind = kind
        self.judge_failed = judge_failed
        self.transform = transform or (lambda text: text)
        self.reason = reason
        self.category = category
        self.hold_id = hold_id
        self.calls: list[tuple[str, dict]] = []

    def __call__(self, text, **kwargs):
        from types import SimpleNamespace

        self.calls.append((text, kwargs))
        return SimpleNamespace(
            kind=self.kind,
            text=self.transform(text),
            reason=self.reason,
            category=self.category,
            hold_id=self.hold_id,
            judge_failed=self.judge_failed,
        )


def shout(text: str) -> str:
    """Rewrites every draft, as house style would."""
    return text.upper()
