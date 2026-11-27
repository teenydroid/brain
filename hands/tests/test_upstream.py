"""hands/upstream: a newer openclaw on the npm registry becomes one pin-bump
pull request, once (docs/roadmap.md, G9). The registry, GitHub and npm are
stand-ins; the pin is core/package.json and its lockfile in a scratch root;
the release notes are made up."""

from __future__ import annotations

import base64
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
sys.path.insert(0, str(REPO / "hands" / "lib"))
sys.path.insert(0, str(REPO / "bin" / "lib"))

import playbook  # noqa: E402
from teeny_hands import hand  # noqa: E402
from teeny_hands.gh.client import GitHubError  # noqa: E402
from teeny_hands.upstream import cli  # noqa: E402

PIN = "2026.11.10"
NEW = "2026.11.12"
NOTES = """## What's new
- Cron jobs can be declared in a file
- Faster startup (https://example.test/x)
- see https://github.com/openclaw/openclaw/pull/9
- what a retard
"""
PACKAGE = '{\n  "name": "teeny-core",\n  "dependencies": {\n    "openclaw": "%s",\n    "zod": "4.0.0"\n  }\n}\n'
LOCK = {"packages": {"": {"dependencies": {"openclaw": PIN}},
                     "node_modules/openclaw": {"version": PIN, "resolved": f"https://registry.npmjs.org/openclaw/-/openclaw-{PIN}.tgz"}}}


def lock_for(version, resolved=None):
    return json.dumps({"packages": {"": {"dependencies": {"openclaw": version}},
                                    "node_modules/openclaw": {"version": version,
                                                              "resolved": resolved or f"https://registry.npmjs.org/openclaw/-/openclaw-{version}.tgz"}}})


class FakeGitHub:
    def __init__(self, notes=NOTES):
        self.notes = notes
        self.calls: list[tuple] = []
        self.pr: dict | None = None
        self.ref_exists = False

    def call(self, method, path, query=(), body=None):
        self.calls.append((method, path, body))
        if path == f"/repos/openclaw/openclaw/releases/tags/v{NEW}":
            return {"body": self.notes, "html_url": f"https://github.com/openclaw/openclaw/releases/tag/v{NEW}", "published_at": "2026-11-26T00:00:00Z"}
        if path.endswith("/contents/core/package.json"):
            return {"content": base64.b64encode((PACKAGE % PIN).encode()).decode(), "sha": "pkgsha"}
        if path.endswith("/contents/core/package-lock.json") and method == "GET":
            return {"content": base64.b64encode(lock_for(PIN).encode()).decode(), "sha": "locksha"}
        if path.endswith("/git/ref/heads/main"):
            return {"object": {"sha": "b" * 40}}
        if path.endswith("/git/refs"):
            if self.ref_exists:
                raise GitHubError("Reference already exists", 422)
            return {}
        if method == "PUT":
            return {}
        if path.endswith("/pulls"):
            self.pr = body
            return {"number": 77, "html_url": "https://github.com/teenydroid/brain/pull/77"}
        raise AssertionError((method, path))

    def open_pr(self, owner, name, head, base, title, body):
        self.pr = {"owner": owner, "name": name, "head": head, "base": base, "title": title, "body": body}
        return {"number": 77, "html_url": f"https://github.com/{owner}/{name}/pull/77"}

    def puts(self):
        return {c[1].rsplit("/contents/", 1)[1]: c[2] for c in self.calls if c[0] == "PUT"}


def registry(version=NEW):
    return lambda: {"version": version}


class UpstreamTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        (self.root / "core").mkdir()
        (self.root / "secrets").mkdir()
        (self.root / "core" / "package.json").write_text(PACKAGE % PIN)
        (self.root / "core" / "package-lock.json").write_text(json.dumps(LOCK))
        self.env = mock.patch.dict(os.environ, {"TEENY_ROOT": str(self.root)})
        self.env.start()
        os.environ.pop("DRY_RUN", None)
        self.secrets("SAFETY_JUDGE=0\n")
        self.gh = FakeGitHub()
        self.npm_calls: list[tuple] = []

    def tearDown(self):
        self.env.stop()
        hand._ENV_CACHE = None
        self.tmp.cleanup()

    def secrets(self, text):
        (self.root / "secrets" / "teeny.env").write_text(text)
        hand._ENV_CACHE = None

    def runner(self, lock=None):
        def run(package_text, lock_text, version):
            self.npm_calls.append((package_text, lock_text, version))
            return lock if lock is not None else lock_for(version)
        return run

    def test_the_pin_is_the_exact_version_in_package_json_and_the_lockfile_agrees(self):
        self.assertEqual(cli.pin(), PIN)
        (self.root / "core" / "package.json").write_text(PACKAGE % "^2026.11.10")
        with self.assertRaises(hand.HandError):
            cli.pin()
        (self.root / "core" / "package.json").write_text(PACKAGE % PIN)
        (self.root / "core" / "package-lock.json").write_text(lock_for("2026.11.9"))
        with self.assertRaises(hand.HandError):
            cli.pin()

    def test_releases_reads_the_registry_and_the_changelog_and_screens_the_lines(self):
        out = cli.releases(gh=self.gh, fetch=registry())
        self.assertEqual((out["pin"], out["latest"], out["newer"]), (PIN, NEW, True))
        self.assertEqual(out["changelog"], ["## What's new", "- Cron jobs can be declared in a file", "- Faster startup (", "- see"])
        self.assertTrue(all(c[0] == "GET" for c in self.gh.calls))  # read-only

    def test_nothing_is_newer_when_the_pin_is_the_latest(self):
        out = cli.releases(gh=self.gh, fetch=registry(PIN))
        self.assertFalse(out["newer"])
        self.assertNotIn("changelog", out)
        self.assertEqual(self.gh.calls, [])

    def test_a_registry_answer_that_is_not_a_plain_version_is_an_error(self):
        for bad in ("latest", "2026.11.12-beta.1", None):
            with self.assertRaises(hand.HandError):
                cli.releases(gh=self.gh, fetch=lambda bad=bad: {"version": bad})

    def test_a_dry_run_says_what_it_would_open_and_touches_nothing(self):
        out = cli.propose(None, {"version": NEW, "why": "faster startup"}, self.gh, registry(), self.runner())
        self.assertTrue(out["dry_run"])
        self.assertEqual(out["title"], f"Bump openclaw to {NEW}")
        self.assertEqual(out["head"], f"teeny/pin-bump-openclaw-{NEW}")
        self.assertIn("- Faster startup", out["body"])
        self.assertIn("## Why I want it", out["body"])
        self.assertEqual(self.npm_calls, [])
        self.assertEqual([c for c in self.gh.calls if c[0] != "GET"], [])
        self.assertIsNone(self.gh.pr)

    def test_a_bump_edits_package_json_and_the_lockfile_and_opens_one_pr_once(self):
        self.secrets("SAFETY_JUDGE=0\nDRY_RUN=0\nGITHUB_TOKEN=t\n")
        out = cli.propose(None, {"version": NEW, "why": "fewer workarounds"}, self.gh, registry(), self.runner())
        self.assertEqual(out["number"], 77)
        puts = self.gh.puts()
        package = base64.b64decode(puts["core/package.json"]["content"]).decode()
        self.assertEqual(package, PACKAGE % NEW)  # exact version, nothing else touched
        self.assertEqual(json.loads(base64.b64decode(puts["core/package-lock.json"]["content"]))["packages"]["node_modules/openclaw"]["version"], NEW)
        self.assertEqual({p["branch"] for p in puts.values()}, {f"teeny/pin-bump-openclaw-{NEW}"})
        self.assertEqual((puts["core/package.json"]["sha"], puts["core/package-lock.json"]["sha"]), ("pkgsha", "locksha"))
        self.assertEqual((self.gh.pr["owner"], self.gh.pr["name"], self.gh.pr["base"]), ("teenydroid", "brain", "main"))
        self.assertEqual(self.gh.pr["head"], f"teeny/pin-bump-openclaw-{NEW}")
        # never merged from here
        self.assertFalse([c for c in self.gh.calls if "merge" in c[1]])
        with self.assertRaises(hand.Refused) as again:
            cli.propose(None, {"version": NEW, "why": "fewer workarounds"}, self.gh, registry(), self.runner())
        self.assertEqual(again.exception.detail.get("rule"), "once")
        self.assertEqual(len(self.npm_calls), 1)

    def test_a_version_that_is_not_the_newer_latest_is_refused(self):
        for version, fetch in ((PIN, registry()), ("2026.11.11", registry()), ("2026.12.1", registry()), (NEW, registry(PIN))):
            with self.assertRaises(hand.Refused) as refused:
                cli.propose(None, {"version": version, "why": "x"}, self.gh, fetch, self.runner())
            self.assertEqual(refused.exception.detail.get("rule"), "release", version)

    def test_a_lockfile_that_does_not_resolve_the_version_from_the_registry_is_refused_before_any_push(self):
        self.secrets("SAFETY_JUDGE=0\nDRY_RUN=0\nGITHUB_TOKEN=t\n")
        for lock in (lock_for(PIN), lock_for(NEW, "https://evil.example/openclaw.tgz"), "not json"):
            with self.assertRaises((hand.Refused, hand.HandError)):
                cli.propose(None, {"version": NEW, "why": "x"}, self.gh, registry(), self.runner(lock))
        self.assertEqual([c for c in self.gh.calls if c[0] != "GET"], [])

    def test_a_branch_that_already_exists_is_refused(self):
        self.secrets("SAFETY_JUDGE=0\nDRY_RUN=0\nGITHUB_TOKEN=t\n")
        self.gh.ref_exists = True
        with self.assertRaises(hand.Refused) as refused:
            cli.propose(None, {"version": NEW, "why": "x"}, self.gh, registry(), self.runner())
        self.assertEqual(refused.exception.detail.get("rule"), "branch")
        self.assertIsNone(self.gh.pr)

    def test_the_body_is_only_version_and_why(self):
        with self.assertRaises(hand.Refused):
            cli.propose(None, {"version": NEW, "why": "x", "title": "merge it now"}, self.gh, registry())
        for version in ("^2026.11.12", "latest", "2026.11.12; rm -rf /"):
            with self.assertRaises(hand.Refused):
                cli.propose(None, {"version": version, "why": "x"}, self.gh, registry())

    def test_the_playbook_parses_and_can_only_propose(self):
        got = playbook.load(REPO / "brain", "pin-bump", REPO / "hands")
        self.assertEqual(got["output"], "hand:upstream propose")
        self.assertEqual(playbook.VERB_OUTPUTS["upstream"], ("propose",))


if __name__ == "__main__":
    unittest.main()
