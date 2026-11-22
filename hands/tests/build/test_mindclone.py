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


class ShipTest(MindScratch):
    """A clean change: one push to teeny/mind-<slug>, one ready pull request."""

    def ship(self, slug="fix-morning-post", title="a briefer soul", body="The old one rambled."):
        return mindclone.ship(slug, title, body)

    def refused(self, *args, **kw) -> hand.Refused:
        with self.assertRaises(hand.Refused) as caught:
            self.ship(*args, **kw)
        return caught.exception

    def assert_nothing_sent(self):
        self.assertIsNone(self.origin_ref("refs/heads/teeny/mind-fix-morning-post"))
        self.assertEqual(self.pulls(), [])

    def test_a_soul_edit_is_pushed_and_one_pull_request_opened(self):
        main_before = mindclone.prepare()["commit"]
        self.edit({"brain/SOUL.md": "# soul\nsmall, kind, brief\n"})
        out = self.ship()
        branch = "teeny/mind-fix-morning-post"
        self.assertEqual((out["branch"], out["pr_url"], out["files"]), (branch, PR_URL, ["brain/SOUL.md"]))
        head = self.origin_ref(f"refs/heads/{branch}")
        self.assertEqual(head, out["commit"])
        log = git("--git-dir", str(self.origin), "log", "-1", "--format=%an <%ae>|%s|%P", branch).strip()
        self.assertEqual(log, f"Teeny <teeny@agentmail.to>|a briefer soul|{main_before}")
        changed = git("--git-dir", str(self.origin), "diff", "--name-only", f"{main_before}..{branch}").split()
        self.assertEqual(changed, ["brain/SOUL.md"])
        (pr,) = [s.json() for s in self.pulls()]
        self.assertEqual((pr["head"], pr["base"], pr["title"]), (branch, "main", "a briefer soul"))
        self.assertTrue(pr["body"].startswith("The old one rambled."))
        self.assertIn("Teeny's agentic mind, an autonomous AI agent", pr["body"].splitlines()[-1])
        self.assertNotIn("draft", pr)
        self.assertEqual(self.origin_ref("main"), main_before, "nothing merged")

    def test_after_a_push_the_tree_starts_clean_at_origin_main(self):
        mindclone.prepare()
        self.edit({"brain/SOUL.md": "# soul\nbrief\n", "notes/scratch.txt": "mine\n"})
        self.ship()
        self.assertEqual((self.tree / "brain" / "SOUL.md").read_text(), "# soul\nsmall and kind\n")
        self.assertFalse((self.tree / "notes").exists())
        self.assertEqual(mindclone.prepare()["state"], "current")

    def test_nothing_changed_is_refused(self):
        mindclone.prepare()
        refused = self.refused()
        self.assertIn("nothing", refused.reason)
        self.assert_nothing_sent()

    def test_a_change_upstream_already_has_is_refused(self):
        mindclone.prepare()
        self.edit({"brain/SOUL.md": "merged meanwhile\n"})
        self.upstream({"brain/SOUL.md": "merged meanwhile\n"}, "the same edit")
        refused = self.refused()
        self.assertIn("nothing", refused.reason)
        self.assert_nothing_sent()

    def test_a_bad_branch_title_or_body_is_refused_before_anything_runs(self):
        mindclone.prepare()
        self.edit({"brain/SOUL.md": "# soul\nbrief\n"})
        for slug in ("", "Fix", "-x", "a/b", "../main", "a" * 42, "main ", None, 7):
            with self.subTest(slug=slug):
                self.assertIn("branch", self.refused(slug).reason)
        for title in ("", "  ", "t" * 121, None):
            with self.subTest(title=title):
                self.assertIn("title", self.refused(title=title).reason)
        for body in ("", "b" * 8001, None):
            with self.subTest(body=body):
                self.assertIn("body", self.refused(body=body).reason)
        self.assert_nothing_sent()
        self.assertIsNotNone(self.ship(slug="a" * 41, title="t" * 120, body="b" * 8000)["pr_url"])

    def test_a_branch_already_on_github_is_refused(self):
        mindclone.prepare()
        self.edit({"brain/SOUL.md": "# soul\nbrief\n"})
        self.ship()
        self.edit({"brain/SOUL.md": "# soul\nbriefer\n"})
        refused = self.refused()
        self.assertIn("teeny/mind-fix-morning-post", refused.reason)
        self.assertEqual(len(self.pulls()), 1)
