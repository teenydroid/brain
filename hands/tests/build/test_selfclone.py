"""Teeny's own clone (teeny_hands.build.selfclone): synced from origin/main on
every bin/build-ship run, never clobbering unshipped work, and the `self`
job kind that edits it and ships it as a pull request. The fake origin is a
local bare repository under the scratch root (GITHUB_GIT_BASE), the fake
GitHub is hands/x's fake server, as in the other build tests."""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

from gh.support import T0, Scratch, git
from teeny_hands import hand
from teeny_hands.build import selfclone

CHECKOUT = Path(__file__).resolve().parents[3]
BUILD_SHIP = CHECKOUT / "bin" / "build-ship"
HANDS = CHECKOUT / "hands"


def commit_as_somebody(path: Path, message: str) -> str:
    git("-C", str(path), "add", "-A")
    git("-C", str(path), "-c", "user.name=op", "-c", "user.email=op@example.com", "commit", "-q", "-m", message)
    return git("-C", str(path), "rev-parse", "HEAD").strip()


class SelfScratch(Scratch):
    """A fake teenydroid/brain on GitHub: main with the real frozen list, a
    soul, and a frozen bin/ script."""

    def setUp(self) -> None:
        super().setUp()
        self.origin = self.remote("teenydroid/brain")
        git("--git-dir", str(self.origin), "symbolic-ref", "HEAD", "refs/heads/main")
        self.upstream_dir = self.root / "upstream"
        git("init", "-q", "-b", "main", str(self.upstream_dir))
        self.upstream({
            "evals/frozen.txt": (CHECKOUT / "evals" / "frozen.txt").read_text(),
            "brain/SOUL.md": "# soul\nsmall and kind\n",
            "bin/tool": "#!/bin/sh\necho tool\n",
            ".gitignore": "state/\n__pycache__/\n",
        }, "the first brain")

    def upstream(self, files: dict[str, str], message: str) -> str:
        """Somebody merges a commit into origin/main; its sha."""
        for rel, text in files.items():
            target = self.upstream_dir / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(text)
        sha = commit_as_somebody(self.upstream_dir, message)
        git("-C", str(self.upstream_dir), "push", "-q", str(self.origin), "main")
        return sha

    def origin_ref(self, ref: str) -> str | None:
        done = subprocess.run(["git", "--git-dir", str(self.origin), "rev-parse", "--verify", "-q", ref],
                              capture_output=True, text=True)
        return done.stdout.strip() or None

    @property
    def tree(self) -> Path:
        return self.root / "state" / "workbench" / "brain"

    def clone_git(self, *args: str) -> str:
        return git("--git-dir", str(self.root / "state" / "self" / "brain.git"), "--work-tree", str(self.tree), *args)

    def later(self, seconds: float) -> None:
        os.environ["TEENY_NOW"] = str(T0 + seconds)


class SyncTest(SelfScratch):
    def test_the_first_sync_clones_origin_main(self):
        out = selfclone.sync()
        self.assertEqual(out["state"], "cloned")
        self.assertEqual(out["commit"], self.origin_ref("main"))
        self.assertEqual((self.tree / "brain" / "SOUL.md").read_text(), "# soul\nsmall and kind\n")
        # The git directory is out of the builder's reach: nothing in the
        # working tree points at it.
        self.assertFalse((self.tree / ".git").exists())
        self.assertTrue((self.root / "state" / "self" / "brain.git").is_dir())
        self.assertEqual(self.clone_git("symbolic-ref", "--short", "HEAD").strip(), "main")

    def test_every_sync_is_recorded_with_its_time_and_commit(self):
        selfclone.sync()
        record = json.loads((self.root / "state" / "self" / "sync.json").read_text())
        self.assertEqual((record["at"], record["commit"], record["state"]), (T0, self.origin_ref("main"), "cloned"))

    def test_a_later_sync_fast_forwards_to_a_new_commit_on_main(self):
        selfclone.sync()
        new = self.upstream({"brain/SOUL.md": "# soul\nsmall, kind and curious\n"}, "curious")
        self.later(1800)
        out = selfclone.sync()
        self.assertEqual((out["state"], out["commit"], out["head"]), ("synced", new, new))
        self.assertIn("curious", (self.tree / "brain" / "SOUL.md").read_text())
        record = json.loads((self.root / "state" / "self" / "sync.json").read_text())
        self.assertEqual((record["at"], record["commit"]), (T0 + 1800, new))

    def test_edits_on_main_are_never_clobbered(self):
        selfclone.sync()
        (self.tree / "brain" / "SOUL.md").write_text("mine, not shipped\n")
        new = self.upstream({"brain/NEW.md": "new\n"}, "new")
        out = selfclone.sync()
        self.assertEqual(out["state"], "dirty")
        self.assertEqual(out["commit"], new)  # fetched all the same
        self.assertEqual((self.tree / "brain" / "SOUL.md").read_text(), "mine, not shipped\n")
        self.assertFalse((self.tree / "brain" / "NEW.md").exists())

    def test_a_branch_of_unshipped_work_is_kept_and_main_still_pulled(self):
        first = selfclone.sync()["commit"]
        self.clone_git("checkout", "-q", "-b", "teeny/by-hand")
        (self.tree / "brain" / "SOUL.md").write_text("by hand\n")
        self.clone_git("add", "-A")
        self.clone_git("-c", "user.name=t", "-c", "user.email=t@e", "commit", "-q", "-m", "by hand")
        mine = self.clone_git("rev-parse", "HEAD").strip()
        new = self.upstream({"brain/NEW.md": "new\n"}, "new")
        out = selfclone.sync()
        self.assertEqual(out["state"], "busy")
        self.assertEqual((out["branch"], out["head"], out["commit"]), ("teeny/by-hand", mine, new))
        self.assertEqual((self.tree / "brain" / "SOUL.md").read_text(), "by hand\n")
        self.assertEqual(self.clone_git("rev-parse", "main").strip(), new)  # main moved on beside it
        self.assertEqual(out["branches"], [{"branch": "teeny/by-hand", "ahead": 1, "behind": 1, "pushed": False}])
        self.assertNotEqual(first, new)

    def test_a_folder_that_is_not_the_clone_is_left_alone(self):
        self.tree.mkdir(parents=True)
        (self.tree / "notes.txt").write_text("somebody else's\n")
        with self.assertRaises(hand.Refused):
            selfclone.sync()
        self.assertEqual((self.tree / "notes.txt").read_text(), "somebody else's\n")
