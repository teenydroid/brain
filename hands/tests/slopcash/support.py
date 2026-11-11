"""Shared by the hands/slopcash tests: a scratch TEENY_ROOT and three fakes on
127.0.0.1 (hands/x's fake server, which routes by method and path): slop.cash
(`SLOPCASH_API_BASE`), its identity service (`SLOPCASH_IDENTITY_BASE`) and
GitHub's REST API (`GITHUB_API_BASE`). Nothing here talks to the real ones.

`fixtures/teeny-s-brain.json` is the public `GET /api/v1/projects/teeny-s-brain`
answer of 2026-11-11, with the escrow deployment and the vetting assessment
trimmed to a few fields."""

from __future__ import annotations

import copy
import json
import os
import tempfile
import unittest
from pathlib import Path

from x.support import FakeX, Seen  # noqa: F401 - re-exported for the tests
from teeny_hands import hand

HERE = Path(__file__).resolve().parent
PROJECT = "teeny-s-brain"
REPO = "teenydroid/brain"
SLOPBOT = "slopdotcash[bot]"

#: Distinctive, so a leak into any output, error or file is found.
FLOW_ID = "flow_FAKEflowFAKEflowFAKEflow0000"
POLL_CAPABILITY = "pollcap_FAKEcapFAKEcapFAKEcap0000"
ASSERTION = "slop_assert_v1_" + "FAKEassertionFAKEassertionFAKEassertion0000"
SESSION_TOKEN = "sess_FAKEsessionFAKEsessionFAKE0000"
GITHUB_TOKEN = "ghp_FAKEgithubFAKEgithubFAKEgithub00"
AUTHORIZATION_URL = "https://identity.slop.cash/v1/oauth/authorize?request=opaque-abc"
SECRETS = (FLOW_ID, POLL_CAPABILITY, ASSERTION, SESSION_TOKEN)


def fixture() -> dict:
    """The real project answer (a fresh copy each time)."""
    return json.loads((HERE / "fixtures" / f"{PROJECT}.json").read_text())


def manifest() -> dict:
    return copy.deepcopy(fixture()["project"])


def listed(pid: str, login: str, status: str = "active", headline: str = "h") -> dict:
    """A project as `GET /api/v1/projects` lists it."""
    return {
        "id": pid, "name": pid, "headline": headline, "status": status,
        "steward": {"github": {"login": login}}, "repositories": [{"id": f"{login}/{pid}"}],
    }  # fmt: skip


def pull(number: int, author: str = "ana", title: str = "a fix", body: str = "", merged_at: str | None = None,
         draft: bool = False, sha: str | None = None, state: str = "open", **more) -> dict:
    """A pull request as GitHub's REST API lists it."""
    return {
        "number": number, "title": title, "user": {"login": author, "type": "User"}, "state": state, "draft": draft,
        "created_at": "2026-11-11T10:00:00Z", "updated_at": f"2026-11-11T1{number % 10}:00:00Z", "merged_at": merged_at,
        "html_url": f"https://github.com/{REPO}/pull/{number}", "body": body,
        "head": {"sha": sha or f"{number:040d}", "ref": f"fix-{number}"}, "base": {"ref": "main"}, **more,
    }  # fmt: skip


def comment(cid: int, login: str, body: str, at: str = "2026-11-11T12:00:00Z") -> dict:
    return {
        "id": cid, "user": {"login": login, "type": "Bot" if login.endswith("[bot]") else "User"}, "body": body,
        "created_at": at, "updated_at": at, "html_url": f"https://github.com/{REPO}/pull/1#issuecomment-{cid}",
    }  # fmt: skip


SLOPBOT_REVIEW = (
    "<!-- slopbot:review -->\n**Slopbot review complete — no finding**\n\n"
    "This pull request adds the gate. " + "It is careful. " * 200
)


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
        self.slop = FakeX()
        self.idp = FakeX()
        self.gh = FakeX()
        for fake in (self.slop, self.idp, self.gh):
            self.addCleanup(fake.close)
        self.configure()

    def tearDown(self) -> None:
        os.environ.clear()
        os.environ.update(self._env)
        hand._ENV_CACHE = None
        self.tmp.cleanup()

    def configure(self, **overrides: str | None) -> None:
        values: dict[str, str | None] = {
            "SLOPCASH_API_BASE": self.slop.base,
            "SLOPCASH_IDENTITY_BASE": self.idp.base,
            "GITHUB_API_BASE": self.gh.base,
            "DRY_RUN": "0",
        }
        values.update(self.settings)
        values.update(overrides)
        text = "".join(f"{k}={v}\n" for k, v in values.items() if v is not None)
        (self.root / "secrets" / "teeny.env").write_text(text)
        hand._ENV_CACHE = None

    # --- fake slop.cash ----------------------------------------------------------------------

    def a_project(self, project: dict | None = None, revision: int = 3) -> dict:
        """`GET /api/v1/projects/teeny-s-brain` answers the fixture (or
        `project`) at `revision`; returns the manifest it serves."""
        answer = fixture()
        if project is not None:
            answer["project"] = project
        answer["revision"] = revision
        self.slop.route("GET", f"/api/v1/projects/{PROJECT}", answer)
        return answer["project"]

    # --- fake identity and sign-in -------------------------------------------------------------

    def signin(self, pending: int = 1, poll_after: float = 0, cookie: str | None = None, login: str = "teenydroid") -> None:
        """The whole sign-in answers: start, `pending` 202s, one 200 with the
        assertion, 410 after; then join sets the session cookie."""
        self.idp.route("POST", "/v1/oauth/start", {
            "flowId": FLOW_ID, "authorizationUrl": AUTHORIZATION_URL, "pollCapability": POLL_CAPABILITY,
            "expiresAt": "2026-11-11T20:05:00.000Z", "pollAfterSeconds": poll_after,
        }, status=201)  # fmt: skip
        polls = lambda: len(self.idp.requests("POST", "/v1/oauth/poll"))  # noqa: E731
        self.idp.route("POST", "/v1/oauth/poll", {"error": "gone"}, status=410)
        self.idp.route("POST", "/v1/oauth/poll", {"status": "complete", "assertion": ASSERTION,
                       "assertionType": "SlopIdentity"}, when=lambda s: polls() == pending + 1)  # fmt: skip
        self.idp.route("POST", "/v1/oauth/poll", {"status": "pending", "retryAfterSeconds": poll_after},
                       status=202, when=lambda s: polls() <= pending)  # fmt: skip
        set_cookie = cookie if cookie is not None else (
            f"__Host-slop_points={SESSION_TOKEN}; Path=/; Max-Age=3600; Secure; HttpOnly; SameSite=Strict")
        self.slop.route("POST", "/api/v1/points/join", {"actor": {"id": "a1", "login": login}, "public": True},
                        headers={"set-cookie": set_cookie} if set_cookie else None)  # fmt: skip

    def assert_no_secret(self, *texts: str) -> None:
        for text in texts:
            for secret in SECRETS:
                self.assertNotIn(secret, text)

    def leaked(self) -> list[Path]:
        """Every file under the scratch root holding a sign-in secret."""
        return [
            p for p in self.root.rglob("*")
            if p.is_file() and any(s.encode() in p.read_bytes() for s in SECRETS)
        ]
