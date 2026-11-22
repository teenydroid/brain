"""The agentic mind's own clone (teeny_hands.build.mindclone): a working tree
in state/home/repo/ that the jailed mind edits, a git directory outside its
jail, and `ship`, which turns the tree into a pull request from
teeny/mind-<slug> after the guard, the frozen-path check, the secret scan
and the size limits.

The mind is untrusted: anything it can write (the whole tree, including a
`.git`, links and hook-looking files) is treated as hostile. The fake origin
is a local bare repository (GITHUB_GIT_BASE), the fake GitHub is hands/x's
fake server, and the guard is the real one with its judge off, as in the
other build tests."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

from gh.support import Scratch, git
from teeny_hands import hand
from teeny_hands.build import mindclone

CHECKOUT = Path(__file__).resolve().parents[3]
PR_URL = "https://github.com/teenydroid/brain/pull/9"
#: Token-shaped, as the secret scan's GitHub rule reads one.
LEAKED = "ghp_" + "A1b2C3d4E5f6G7h8I9j0K1l2M3n4"


def commit_as_somebody(path: Path, message: str) -> str:
    git("-C", str(path), "add", "-A")
    git("-C", str(path), "-c", "user.name=op", "-c", "user.email=op@example.com", "commit", "-q", "-m", message)
    return git("-C", str(path), "rev-parse", "HEAD").strip()


class MindScratch(Scratch):
    """A fake teenydroid/brain: main with the real frozen list, a soul and a
    frozen bin/doctor."""

    def setUp(self) -> None:
        super().setUp()
        self.origin = self.remote("teenydroid/brain")
        git("--git-dir", str(self.origin), "symbolic-ref", "HEAD", "refs/heads/main")
        self.upstream_dir = self.root / "upstream"
        git("init", "-q", "-b", "main", str(self.upstream_dir))
        self.upstream({
            "evals/frozen.txt": (CHECKOUT / "evals" / "frozen.txt").read_text(),
            "brain/SOUL.md": "# soul\nsmall and kind\n",
            "bin/doctor": "#!/bin/sh\necho doctor\n",
            ".gitignore": "__pycache__/\nnode_modules/\n",
        }, "the first brain")
        self.me()
        self.gh.route("POST", "/repos/teenydroid/brain/pulls", {"number": 9, "html_url": PR_URL}, status=201)

    def upstream(self, files: dict[str, str], message: str) -> str:
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
    def home(self) -> Path:
        return self.root / "state" / "home"

    @property
    def tree(self) -> Path:
        return self.home / "repo"

    def edit(self, files: dict[str, str]) -> None:
        for rel, text in files.items():
            target = self.tree / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(text)

    def pulls(self) -> list:
        return self.gh.requests("POST", "/repos/teenydroid/brain/pulls")


class PrepareTest(MindScratch):
    def test_prepare_checks_out_main_with_its_git_directory_outside_the_home(self):
        out = mindclone.prepare()
        self.assertEqual(out["commit"], self.origin_ref("main"))
        self.assertEqual(Path(out["path"]), self.tree)
        self.assertEqual((self.tree / "brain" / "SOUL.md").read_text(), "# soul\nsmall and kind\n")
        self.assertFalse((self.tree / ".git").exists(), "nothing in the tree points at the git directory")
        gitdir = mindclone.gitdir()
        self.assertTrue(gitdir.is_dir())
        self.assertNotIn(self.home.resolve(), gitdir.resolve().parents)
        self.assertEqual(git("--git-dir", str(gitdir), "rev-parse", "HEAD").strip(), self.origin_ref("main"))

    def test_a_clean_tree_moves_to_the_new_upstream_head(self):
        mindclone.prepare()
        new = self.upstream({"brain/NEW.md": "new\n"}, "new")
        out = mindclone.prepare()
        self.assertEqual((out["commit"], out["head"]), (new, new))
        self.assertEqual((self.tree / "brain" / "NEW.md").read_text(), "new\n")

    def test_edits_in_the_tree_are_left_as_they_are(self):
        mindclone.prepare()
        self.edit({"brain/SOUL.md": "mine\n"})
        new = self.upstream({"brain/NEW.md": "new\n"}, "new")
        out = mindclone.prepare()
        self.assertEqual((out["state"], out["commit"]), ("dirty", new))
        self.assertEqual((self.tree / "brain" / "SOUL.md").read_text(), "mine\n")
        self.assertFalse((self.tree / "brain" / "NEW.md").exists())

    def test_a_deleted_tree_is_checked_out_afresh(self):
        mindclone.prepare()
        subprocess.run(["rm", "-rf", str(self.tree)], check=True)
        out = mindclone.prepare()
        self.assertEqual(out["state"], "cloned")
        self.assertTrue((self.tree / "brain" / "SOUL.md").is_file())

    def test_a_tree_replaced_by_a_link_is_never_written_through(self):
        mindclone.prepare()
        outside = self.root / "secrets"
        before = sorted(p.name for p in outside.iterdir())
        subprocess.run(["rm", "-rf", str(self.tree)], check=True)
        self.tree.symlink_to(outside)
        mindclone.prepare()
        self.assertEqual(sorted(p.name for p in outside.iterdir()), before, "nothing written into the link's target")
        self.assertFalse(self.tree.is_symlink())
        self.assertTrue((self.tree / "brain" / "SOUL.md").is_file())
