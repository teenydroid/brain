"""The revert pull request for a bad deploy (rollback.py, `_revert_pr`): built
in Teeny's own clone's git directory with no working tree, pushed as
teeny/revert-<n>-<sha> and opened through hands/gh. Only when main is still
the deploy; otherwise the revert issue stands alone."""

from __future__ import annotations

import json
import subprocess

from gh.support import git
from teeny_hands.babysit import rollback
from teeny_hands.build import selfclone

from .support import REPO
from .test_rollback import RollbackScratch

PR_URL = f"https://github.com/{REPO}/pull/41"


class RevertPrTest(RollbackScratch):
    def setUp(self) -> None:
        super().setUp()
        self.me()
        self.gh.route("POST", f"/repos/{REPO}/pulls", {"number": 41, "html_url": PR_URL}, status=201)
        self.origin = self.remote(REPO)
        git("--git-dir", str(self.origin), "symbolic-ref", "HEAD", "refs/heads/main")
        self.work = self.root / "upstream"
        git("init", "-q", "-b", "main", str(self.work))
        self.good = self.commit({"brain/playbooks/morning.md": "say gm\n", "brain/SOUL.md": "kind\n"}, "good")
        selfclone.sync()
        self.merge = self.commit({"brain/playbooks/morning.md": "say gm later\n", "brain/new.md": "x\n"}, "the merge")

    def commit(self, files: dict[str, str], message: str) -> str:
        for rel, text in files.items():
            target = self.work / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(text)
        git("-C", str(self.work), "add", "-A")
        git("-C", str(self.work), "-c", "user.name=op", "-c", "user.email=op@example.com", "commit", "-q", "-m", message)
        git("-C", str(self.work), "push", "-q", str(self.origin), "main")
        return git("-C", str(self.work), "rev-parse", "HEAD").strip()

    def origin_git(self, *args: str) -> str:
        return subprocess.run(["git", "--git-dir", str(self.origin), *args], capture_output=True, text=True).stdout.strip()

    def bad_deploy(self) -> None:
        from teeny_hands.babysit import ledger
        ledger.record_action("deploy_ff", REPO, 7, self.merge, **{"from": self.good, "files": 2, "numbers": [7]})
        self.doctor(60, "cron.toml")
        self.at(120)

    def pulls(self) -> list[dict]:
        return [json.loads(r.body) for r in self.gh.requests("POST", f"/repos/{REPO}/pulls")]

    def test_a_bad_deploy_gets_a_revert_pull_request_back_to_the_tree_before_it(self):
        self.bad_deploy()
        out = rollback.check()
        self.assertEqual(out["revert_prs"][0]["number"], 41)
        (pull,) = self.pulls()
        branch = f"teeny/revert-7-{self.merge[:7]}"
        self.assertEqual((pull["head"], pull["base"]), (branch, "main"))
        self.assertEqual(pull["title"], "revert #7: the deploy left the box unhealthy")
        # The branch is one commit on main whose tree is the tree before the deploy.
        self.assertEqual(self.origin_git("rev-parse", f"{branch}^"), self.merge)
        self.assertEqual(self.origin_git("rev-parse", f"{branch}^{{tree}}"),
                         self.origin_git("rev-parse", f"{self.good}^{{tree}}"))
        row = self.rows("deploy_revert_pr")[-1]
        self.assertEqual((row["detail"]["pr"], row["detail"]["branch"]), (41, branch))
        # The issue is still opened: it is where the reasons are.
        self.assertEqual(len(self.issues()), 1)

    def test_it_is_opened_once(self):
        self.bad_deploy()
        rollback.check()
        for minutes in (25, 45):
            self.at(minutes * 60)
            self.assertNotIn("revert_prs", rollback.check() or {})
        self.assertEqual(len(self.pulls()), 1)

    def test_main_that_moved_past_the_deploy_gets_the_issue_only(self):
        self.bad_deploy()
        self.commit({"brain/other.md": "y\n"}, "something later")
        out = rollback.check()
        self.assertIn("main has moved", out["revert_prs"][0]["skipped"])
        self.assertEqual(self.pulls(), [])
        self.assertEqual(len(self.issues()), 1)
        self.assertEqual(len(self.rows("deploy_revert_pr_skipped")), 1)
        self.at(25 * 60)
        self.assertNotIn("revert_prs", rollback.check() or {})

    def test_without_teenys_clone_there_is_the_issue_only(self):
        import shutil
        shutil.rmtree(selfclone.gitdir())
        self.bad_deploy()
        out = rollback.check()
        self.assertIn("no clone", out["revert_prs"][0]["skipped"])
        self.assertEqual(self.pulls(), [])

    def test_a_deploy_row_without_where_it_came_from_gets_the_issue_only(self):
        from teeny_hands.babysit import ledger
        ledger.record_action("deploy_ff", REPO, 7, self.merge, files=2, numbers=[7])
        self.doctor(60, "cron.toml")
        self.at(120)
        out = rollback.check()
        self.assertIn("skipped", out["revert_prs"][0])
        self.assertEqual(self.pulls(), [])

    def test_a_push_that_fails_is_tried_again_next_run(self):
        self.bad_deploy()
        hook = self.origin / "hooks" / "pre-receive"
        hook.write_text("#!/bin/sh\nexit 1\n")
        hook.chmod(0o755)
        out = rollback.check()
        self.assertIn("error", out["revert_prs"][0])
        self.assertEqual(self.pulls(), [])
        hook.unlink()
        self.at(25 * 60)
        self.assertEqual(rollback.check()["revert_prs"][0]["number"], 41)

    def test_the_revert_is_held_for_the_operator_like_any_change_to_those_paths(self):
        self.bad_deploy()
        rollback.check()
        self.assertIn("brain/playbooks/morning.md", " ".join(sorted(rollback.blocked_places())))
