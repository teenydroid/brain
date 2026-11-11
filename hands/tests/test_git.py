"""hands/git: how every git command Teeny runs is set up. One identity (from
secrets/teeny.env, never the box), no system or user config, no hooks, no
credential store, a HOME under state/; and `hands/git check`, which proves
git works by making a commit in a scratch repository, the way the container
needs it to (a uid with no passwd entry and no identity of its own)."""

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
sys.path.insert(0, str(HERE.parent / "lib"))

from teeny_hands import git, hand  # noqa: E402


class GitTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.scratch = Path(self.tmp.name).resolve()
        self.root = self.scratch / "root"
        (self.root / "secrets").mkdir(parents=True)
        self._env = dict(os.environ)
        # A box like the container: no identity anywhere, and a hostile
        # global config that would break a commit if it were read.
        hooks = self.scratch / "hooks"
        hooks.mkdir()
        self.marker = self.scratch / "hook-ran"
        (hooks / "pre-commit").write_text(f"#!/bin/sh\ntouch {self.marker}\n")
        (hooks / "pre-commit").chmod(0o755)
        box = self.scratch / "box.gitconfig"
        box.write_text(f"[core]\n\thooksPath = {hooks}\n[commit]\n\tgpgsign = true\n[gpg]\n\tprogram = /nonexistent\n"
                       "[user]\n\tname = Not Teeny\n\temail = box@example.com\n")
        os.environ.update(TEENY_ROOT=str(self.root), GIT_CONFIG_GLOBAL=str(box), HOME=str(self.scratch))
        for key in ("GIT_AUTHOR_NAME", "GIT_AUTHOR_EMAIL", "GIT_COMMITTER_NAME", "GIT_COMMITTER_EMAIL", "EMAIL"):
            os.environ.pop(key, None)
        self.secrets("")

    def tearDown(self):
        os.environ.clear()
        os.environ.update(self._env)
        hand._ENV_CACHE = None
        self.tmp.cleanup()

    def secrets(self, text):
        (self.root / "secrets" / "teeny.env").write_text(text)
        hand._ENV_CACHE = None

    def test_the_identity_defaults_to_teeny_and_its_mailbox(self):
        self.assertEqual(git.identity(), ("Teeny", "teeny@agentmail.to"))

    def test_the_identity_from_secrets_wins_and_the_environment_does_not(self):
        os.environ.update(GIT_NAME="Env", GIT_EMAIL="env@example.com")
        self.secrets("GIT_NAME=Teeny Droid\nGIT_EMAIL=teeny@example.com\n")
        self.assertEqual(git.identity(), ("Teeny Droid", "teeny@example.com"))

    def test_a_commit_ignores_the_box_config_and_its_hooks(self):
        work = self.scratch / "work"
        work.mkdir()
        git.run(work, "init", "-q", "-b", "main")
        (work / "a.txt").write_text("hi\n")
        git.run(work, "add", "-A")
        git.run(work, "commit", "-q", "-m", "first")
        who = git.run(work, "log", "-1", "--format=%an <%ae>|%cn <%ce>").stdout.decode().strip()
        self.assertEqual(who, "Teeny <teeny@agentmail.to>|Teeny <teeny@agentmail.to>")
        self.assertFalse(self.marker.exists())

    def test_home_is_under_state_so_nothing_is_read_from_or_left_in_the_users_home(self):
        env = git.env()
        self.assertEqual(env["HOME"], str(self.root / "state"))
        self.assertEqual(env["GIT_CONFIG_GLOBAL"], os.devnull)
        self.assertNotIn("GITHUB_TOKEN", env)

    def test_a_token_is_redacted_from_a_failure(self):
        token = "ghp_FAKEtokenFAKEtokenFAKEtoken0000"
        with self.assertRaises(hand.HandError) as caught:
            git.run(self.scratch, "push", f"https://x:{token}@127.0.0.1:1/nope.git", "HEAD", token=token)
        self.assertNotIn(token, str(caught.exception))

    def test_check_commits_in_a_scratch_repository_and_reports_who(self):
        proc = subprocess.run([str(REPO / "hands" / "git"), "check"], capture_output=True, text=True, env=os.environ)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        out = json.loads(proc.stdout)
        self.assertTrue(out["ok"], out)
        self.assertEqual((out["name"], out["email"]), ("Teeny", "teeny@agentmail.to"))
        self.assertRegex(out["version"], r"^\d+\.\d+")
        self.assertFalse(self.marker.exists())
        self.assertEqual(list((self.root / "state").glob("git-check*")), [])

    def test_check_fails_when_git_is_missing(self):
        env = {**os.environ, "PATH": str(self.scratch / "empty")}
        proc = subprocess.run([sys.executable, str(REPO / "hands" / "git"), "check"], capture_output=True, text=True, env=env)
        self.assertEqual(proc.returncode, 1)
        self.assertFalse(json.loads(proc.stdout)["ok"])


if __name__ == "__main__":
    unittest.main()
