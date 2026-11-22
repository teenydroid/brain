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


class HostileTreeTest(ShipTest):
    """Everything in the tree is the mind's: none of it may steer git, run,
    or carry a file from outside the tree into a commit."""

    def objects(self) -> str:
        """Every object in the clone's git directory and in origin, as text."""
        out = ""
        for where in (mindclone.gitdir(), self.origin):
            out += subprocess.run(["git", "--git-dir", str(where), "cat-file", "--batch-all-objects", "--batch"],
                                  capture_output=True).stdout.decode("utf-8", "replace")
        return out

    def plant_hooks(self, folder: Path) -> Path:
        marker = self.root / "hooked"
        for name in ("pre-commit", "commit-msg", "post-commit", "pre-push", "post-checkout", "post-index-change"):
            hook = folder / name
            hook.parent.mkdir(parents=True, exist_ok=True)
            hook.write_text(f"#!/bin/sh\ntouch {marker}\n")
            hook.chmod(0o755)
        return marker

    def test_a_planted_git_directory_or_file_is_refused_and_never_used(self):
        for where in (".git", "brain/.git", "notes/sub/.git", "brain/.GIT"):
            with self.subTest(where=where):
                mindclone.prepare()
                self.edit({"brain/SOUL.md": "# soul\nbrief\n"})
                planted = self.tree / where
                planted.mkdir(parents=True)
                (planted / "config").write_text(f"[core]\n\thooksPath = {planted / 'hooks'}\n")
                marker = self.plant_hooks(planted / "hooks")
                refused = self.refused()
                self.assertIn(where, refused.reason)
                self.assertEqual(refused.detail["rule"], "git_in_tree")
                self.assertFalse(marker.exists())
                self.assert_nothing_sent()
                subprocess.run(["rm", "-rf", str(self.tree)], check=True)
        mindclone.prepare()
        (self.tree / ".git").write_text(f"gitdir: {self.origin}\n")
        self.edit({"brain/SOUL.md": "# soul\nbrief\n"})
        self.assertIn(".git", self.refused().reason)
        self.assert_nothing_sent()

    def test_a_planted_hook_is_never_run(self):
        mindclone.prepare()
        self.edit({"brain/SOUL.md": "# soul\nbrief\n", ".gitattributes": "* filter=evil diff=evil\n"})
        marker = self.plant_hooks(self.tree / ".githooks")
        self.plant_hooks(self.tree / "hooks")
        self.assertEqual(self.ship()["pr_url"], PR_URL)
        self.assertFalse(marker.exists())

    def test_a_link_out_of_the_tree_is_refused_and_its_target_never_committed(self):
        secret = self.root / "secrets" / "teeny.env"
        self.assertIn("ghp_", secret.read_text())
        for link in ("brain/leak.md", "brain/inside.md"):
            with self.subTest(link=link):
                mindclone.prepare()
                self.edit({"brain/SOUL.md": "# soul\nbrief\n"})
                target = secret if link == "brain/leak.md" else Path("SOUL.md")
                (self.tree / link).symlink_to(target)
                refused = self.refused()
                self.assertEqual(refused.detail["rule"], "link")
                self.assertIn(link, refused.reason)
                self.assert_nothing_sent()
                (self.tree / link).unlink()
        self.assertNotIn("ghp_", self.objects())

    def test_a_hard_link_to_a_file_outside_the_tree_is_refused(self):
        # A hard link reads the same with or without following links, so
        # only its link count tells it from a file of the tree's own.
        mindclone.prepare()
        outside = self.root / "secrets" / "plain.txt"
        outside.write_text("the operator's private notes\n")
        os.link(outside, self.tree / "brain" / "notes.md")
        refused = self.refused()
        self.assertEqual((refused.detail["rule"], refused.detail["path"]), ("changed", "brain/notes.md"))
        self.assert_nothing_sent()

    def test_a_folder_swapped_for_a_link_is_refused(self):
        mindclone.prepare()
        subprocess.run(["rm", "-rf", str(self.tree / "brain")], check=True)
        (self.tree / "brain").symlink_to(self.root / "secrets")
        refused = self.refused()
        self.assertEqual((refused.detail["rule"], refused.detail["path"]), ("link", "brain"))
        self.assert_nothing_sent()
        self.assertNotIn("ghp_", self.objects())

    def test_a_file_git_would_read_differently_from_the_disk_is_refused(self):
        # What git stores must be the bytes the broker reads itself, without
        # following a link: here an attribute converts the line ends, which
        # stands in for a file swapped for a link while git read it.
        mindclone.prepare()
        self.edit({".gitattributes": "*.md text eol=lf\n"})
        (self.tree / "brain" / "SOUL.md").write_bytes(b"# soul\r\nbrief\r\n")
        refused = self.refused()
        self.assertEqual((refused.detail["rule"], refused.detail["path"]), ("changed", "brain/SOUL.md"))
        self.assert_nothing_sent()

    def test_a_file_over_2_mb_is_refused(self):
        mindclone.prepare()
        self.edit({"brain/big.md": "x" * (2 * 1024 * 1024 + 1)})
        self.assertIn("brain/big.md", self.refused().reason)
        self.assert_nothing_sent()


class RulesTest(ShipTest):
    def test_a_frozen_path_is_refused_naming_the_path_and_the_pattern(self):
        mindclone.prepare()
        self.edit({"brain/SOUL.md": "# soul\nbrief\n", "bin/doctor": "#!/bin/sh\necho loosened\n"})
        refused = self.refused()
        self.assertEqual(refused.detail["rule"], "frozen")
        self.assertIn("bin/doctor", refused.reason)
        self.assertIn("bin/**", refused.reason)
        self.assert_nothing_sent()

    def test_editing_the_frozen_list_in_the_same_change_unfreezes_nothing(self):
        mindclone.prepare()
        loosened = "\n".join(line for line in (self.tree / "evals" / "frozen.txt").read_text().splitlines()
                             if line.strip() not in ("bin/**", "evals/**")) + "\n"
        self.edit({"evals/frozen.txt": loosened, "bin/doctor": "#!/bin/sh\necho loosened\n"})
        refused = self.refused()
        self.assertIn("bin/doctor (bin/**)", refused.reason)
        self.assertIn("evals/frozen.txt (evals/**)", refused.reason)
        self.assert_nothing_sent()

    def test_a_token_looking_secret_is_refused_by_the_secret_scan(self):
        mindclone.prepare()
        self.edit({"brain/notes.md": f"remember {LEAKED}\n"})
        refused = self.refused()
        self.assertEqual(refused.detail["rule"], "secrets")
        self.assertIn("brain/notes.md", refused.reason)
        self.assertNotIn(LEAKED, refused.reason)
        self.assert_nothing_sent()

    def test_a_secret_looking_path_is_refused(self):
        mindclone.prepare()
        self.edit({"brain/.env": "A=1\n"})
        self.assertEqual(self.refused().detail["rule"], "secrets")
        self.assert_nothing_sent()

    def test_a_held_title_is_refused_with_the_guards_reason(self):
        mindclone.prepare()
        self.edit({"brain/SOUL.md": "# soul\nbrief\n"})
        refused = self.refused(title="what a retard")
        self.assertIn("guard", refused.reason)
        self.assertIn("title", refused.reason)
        self.assertIn(refused.detail["category"], refused.reason)
        self.assert_nothing_sent()

    def test_more_than_100_files_is_refused(self):
        mindclone.prepare()
        self.edit({f"brain/notes/{n}.md": "n\n" for n in range(101)})
        refused = self.refused()
        self.assertIn("101 files", refused.reason)
        self.assert_nothing_sent()
        subprocess.run(["rm", "-rf", str(self.tree / "brain" / "notes" / "100.md")], check=True)
        self.assertEqual(self.ship()["pr_url"], PR_URL)

    def test_more_than_3000_changed_lines_is_refused(self):
        mindclone.prepare()
        self.edit({"brain/long.md": "line\n" * 3000, "brain/SOUL.md": "# soul\n"})  # 3000 added, 1 deleted
        refused = self.refused()
        self.assertIn("3001 lines", refused.reason)
        self.assert_nothing_sent()

    def test_under_dry_run_every_rule_runs_and_nothing_is_sent(self):
        self.configure(DRY_RUN="1")
        mindclone.prepare()
        self.edit({"brain/SOUL.md": "# soul\nbrief\n"})
        out = self.ship()
        self.assertIs(out["dry_run"], True)
        self.assertEqual((out["branch"], out["files"]), ("teeny/mind-fix-morning-post", ["brain/SOUL.md"]))
        would = out["would_open"]
        self.assertEqual((would["head"], would["base"], would["title"]), (out["branch"], "main", "a briefer soul"))
        self.assertIn("agentic mind", would["body"])
        self.assertEqual(self.gh.seen, [])
        self.assert_nothing_sent()
        self.assertEqual((self.tree / "brain" / "SOUL.md").read_text(), "# soul\nbrief\n", "the edit stays")
        self.assertIn("refused", self.refused_when_frozen())

    def refused_when_frozen(self) -> str:
        self.edit({"bin/doctor": "#!/bin/sh\n"})
        with self.assertRaises(hand.Refused):
            self.ship()
        return "refused"
