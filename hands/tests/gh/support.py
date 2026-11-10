"""Shared by the hands/gh tests: a scratch TEENY_ROOT and a fake GitHub on
127.0.0.1 (hands/x's fake server, which routes by method and path), reached
through `GITHUB_API_BASE` in the scratch secrets/teeny.env. Nothing here
talks to the real GitHub; pushes go to bare repositories in the scratch
root through `GITHUB_GIT_BASE`."""

from __future__ import annotations

import base64
import logging
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

from x.support import FakeX, Seen  # noqa: F401 - re-exported for the tests
from teeny_hands import hand

# The guard logs what it holds; the tests that care read the hold log.
logging.getLogger("teeny_hands.guard").addHandler(logging.NullHandler())
logging.getLogger("teeny_hands.guard").propagate = False

#: Distinctive, so a leak into any output or file is found.
TOKEN = "ghp_FAKEtokenFAKEtokenFAKEtoken0000"
T0 = 1_789_369_200.0  # 2026-09-14T07:00:00Z


def git(*args: str) -> str:
    """git as the tests' own setup runs it: no user or system config, so a
    box with commit signing or hooks set globally changes nothing here."""
    env = {**os.environ, "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1"}
    return subprocess.run(["git", *args], env=env, check=True, capture_output=True, text=True, stdin=subprocess.DEVNULL).stdout


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
        self.gh = FakeX()
        self.addCleanup(self.gh.close)
        self.configure()

    def tearDown(self) -> None:
        os.environ.clear()
        os.environ.update(self._env)
        hand._ENV_CACHE = None
        self.tmp.cleanup()

    def configure(self, **overrides: str | None) -> None:
        values: dict[str, str | None] = {
            "GITHUB_TOKEN": TOKEN,
            "GITHUB_API_BASE": self.gh.base,
            "GITHUB_GIT_BASE": "file://" + str(self.root / "remotes"),
            "DRY_RUN": "0",
            "SAFETY_JUDGE": "0",
        }
        values.update(self.settings)
        values.update(overrides)
        text = "".join(f"{k}={v}\n" for k, v in values.items() if v is not None)
        (self.root / "secrets" / "teeny.env").write_text(text)
        hand._ENV_CACHE = None

    # --- fake GitHub answers ---------------------------------------------------------------

    def me(self, login: str = "teenydroid") -> None:
        self.gh.route("GET", "/user", {"login": login, "id": 1, "name": "Teeny", "html_url": f"https://github.com/{login}"})

    def file(self, repo: str, path: str, text: str) -> None:
        content = base64.b64encode(text.encode()).decode()
        self.gh.route("GET", f"/repos/{repo}/contents/{path}", {"type": "file", "encoding": "base64", "content": content})

    def open_prs(self, repo: str, count: int) -> None:
        self.gh.route(
            "GET",
            "/search/issues",
            {"total_count": count, "items": []},
            when=lambda seen: seen.query.get("q", "").startswith(f"repo:{repo} "),
        )

    # --- the workbench ---------------------------------------------------------------------

    def workdir(self, name: str = "hello", files: dict[str, str] | None = None) -> Path:
        path = self.root / "state" / "workbench" / name
        path.mkdir(parents=True, exist_ok=True)
        for rel, text in (files or {"README.md": "# hello\n"}).items():
            target = path / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(text)
        return path

    def remote(self, repo: str) -> Path:
        path = self.root / "remotes" / f"{repo}.git"
        path.parent.mkdir(parents=True, exist_ok=True)
        git("init", "-q", "--bare", str(path))
        return path
