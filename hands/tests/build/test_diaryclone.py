"""The mind's copy of my diary (teeny_hands.build.diaryclone): a working tree
in state/home/diary/ that the jailed mind edits, a git directory outside its
jail, and `commit`, which pushes its edits under mind/ to the diary's main
after the path rule, the size limits, the secret scan and the guard.

The diary's origin is a local bare repository (RECEIPTS_REMOTE, as the
receipts tests use), and the guard is the real one with its judge off."""

from __future__ import annotations

import subprocess
from pathlib import Path

from gh.support import Scratch, git
from teeny_hands import hand
from teeny_hands.build import diaryclone, mindclone

LEAKED = "ghp_" + "A1b2C3d4E5f6G7h8I9j0K1l2M3n4"


def commit_as_somebody(path: Path, message: str) -> str:
    git("-C", str(path), "add", "-A")
    git("-C", str(path), "-c", "user.name=op", "-c", "user.email=op@example.com", "commit", "-q", "-m", message)
    return git("-C", str(path), "rev-parse", "HEAD").strip()


class DiaryScratch(Scratch):
    """A fake teenydroid/diary: receipts and a README written by code."""

    def setUp(self) -> None:
        super().setUp()
        self.origin = self.root / "diary.git"
        git("init", "-q", "--bare", "-b", "main", str(self.origin))
        self.upstream_dir = self.root / "upstream"
        git("init", "-q", "-b", "main", str(self.upstream_dir))
        self.upstream({"README.md": "# diary\n", "receipts/head.json": "{\"seq\": 1}\n"}, "seed")
        self.configure(RECEIPTS_REMOTE=str(self.origin), RECEIPTS_GITHUB_TOKEN="fake-token-for-tests")

    def upstream(self, files: dict[str, str], message: str) -> str:
        git("-C", str(self.upstream_dir), "pull", "-q", "--ff-only", str(self.origin), "main") if self.origin_ref("main") else None
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

    def origin_file(self, path: str) -> str | None:
        done = subprocess.run(["git", "--git-dir", str(self.origin), "show", f"main:{path}"], capture_output=True, text=True)
        return done.stdout if done.returncode == 0 else None

    @property
    def tree(self) -> Path:
        return self.root / "state" / "home" / "diary"

    def edit(self, files: dict[str, str]) -> None:
        for rel, text in files.items():
            target = self.tree / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(text)

    def refused(self, message: str = "mind: a week of building") -> hand.Refused:
        with self.assertRaises(hand.Refused) as caught:
            diaryclone.commit(message)
        return caught.exception


class PrepareTest(DiaryScratch):
    def test_prepare_checks_out_the_diary_with_its_git_directory_outside_the_home(self):
        out = diaryclone.prepare()
        self.assertEqual((out["state"], out["commit"], out["yours"]), ("cloned", self.origin_ref("main"), "mind/"))
        self.assertEqual((self.tree / "README.md").read_text(), "# diary\n")
        self.assertFalse((self.tree / ".git").exists())
        with mindclone.at(diaryclone.DIARY):
            gitdir = mindclone.gitdir()
        self.assertNotIn((self.root / "state" / "home").resolve(), gitdir.resolve().parents)

    def test_it_does_not_touch_the_brain_clone(self):
        diaryclone.prepare()
        self.assertFalse((self.root / "state" / "home" / "repo").exists())
        self.assertFalse((self.root / "state" / "mind").exists())

    def test_edits_are_left_as_they_are(self):
        diaryclone.prepare()
        self.edit({"mind/log.md": "mine\n"})
        self.upstream({"receipts/head.json": "{\"seq\": 2}\n"}, "receipts: 2")
        out = diaryclone.prepare()
        self.assertEqual(out["state"], "dirty")
        self.assertEqual((self.tree / "mind" / "log.md").read_text(), "mine\n")


class CommitTest(DiaryScratch):
    def setUp(self) -> None:
        super().setUp()
        diaryclone.prepare()

    def test_an_edit_under_mind_is_pushed_and_the_copy_is_fresh_again(self):
        self.edit({"mind/log.md": "# what i built\n\na scraper that counts crabs.\n"})
        out = diaryclone.commit("mind: what i built this week")
        self.assertEqual(out["files"], ["mind/log.md"])
        self.assertEqual(self.origin_ref("main"), out["commit"])
        self.assertEqual(self.origin_file("mind/log.md"), "# what i built\n\na scraper that counts crabs.\n")
        self.assertEqual(git("--git-dir", str(self.origin), "log", "-1", "--format=%an %s").strip(),
                         "Teeny mind: what i built this week")
        self.assertEqual(diaryclone.prepare()["state"], "current")

    def test_it_lands_on_top_of_what_receipts_pushed_since_the_copy(self):
        self.edit({"mind/log.md": "hello\n"})
        receipts = self.upstream({"receipts/head.json": "{\"seq\": 2}\n"}, "receipts: 2")
        out = diaryclone.commit("mind: hello")
        parent = git("--git-dir", str(self.origin), "rev-parse", f"{out['commit']}^").strip()
        self.assertEqual(parent, receipts, "a fast-forward of the receipts' push, never over it")
        self.assertEqual(self.origin_file("receipts/head.json"), "{\"seq\": 2}\n")
        self.assertEqual(self.origin_file("mind/log.md"), "hello\n")

    def test_a_path_outside_mind_is_refused(self):
        for path in ("receipts/head.json", "wallet.json", "self/SELF.md", "README.md"):
            with self.subTest(path=path):
                diaryclone.prepare()
                self.edit({"mind/ok.md": "ok\n", path: "forged\n"})
                self.assertEqual(self.refused().detail.get("rule"), "path")
                subprocess.run(["rm", "-rf", str(self.tree)], check=True)
        self.assertIsNone(self.origin_file("mind/ok.md"))

    def test_a_link_is_refused(self):
        secret = self.root / "secrets" / "teeny.env"
        (self.tree / "mind").mkdir()
        (self.tree / "mind" / "leak.md").symlink_to(secret)
        self.assertEqual(self.refused().detail.get("rule"), "link")

    def test_a_git_directory_is_refused(self):
        self.edit({"mind/sub/.git/config": "[core]\n"})
        self.assertEqual(self.refused().detail.get("rule"), "git_in_tree")

    def test_a_secret_is_refused(self):
        self.edit({"mind/notes.md": f"my token is {LEAKED}\n"})
        self.assertEqual(self.refused().detail.get("rule"), "secrets")
        self.assertIsNone(self.origin_file("mind/notes.md"))

    def test_what_the_guard_refuses_is_not_pushed(self):
        self.edit({"mind/notes.md": "what a retard\n"})
        self.assertEqual(self.refused().detail.get("rule"), "guard")

    def test_binary_and_oversized_changes_are_refused(self):
        (self.tree / "mind").mkdir()
        (self.tree / "mind" / "blob.bin").write_bytes(b"\xff\xfe\x00")
        self.assertEqual(self.refused().detail.get("rule"), "not_text")
        (self.tree / "mind" / "blob.bin").unlink()
        self.edit({f"mind/{i}.md": "x\n" for i in range(diaryclone.MAX_FILES + 1)})
        self.assertEqual(self.refused().detail.get("rule"), "too_big")

    def test_nothing_changed_and_no_copy_are_refused(self):
        self.assertEqual(self.refused().detail.get("rule"), "nothing")
        subprocess.run(["rm", "-rf", str(self.tree)], check=True)
        self.assertEqual(self.refused().detail.get("rule"), "no_copy")

    def test_the_message_is_one_short_line(self):
        self.edit({"mind/log.md": "hi\n"})
        for bad in ("", "two\nlines", "x" * 201, None):
            with self.subTest(message=bad):
                self.assertEqual(self.refused(bad).detail.get("rule"), "message")

    def test_dry_run_checks_everything_and_pushes_nothing(self):
        self.configure(RECEIPTS_REMOTE=str(self.origin), RECEIPTS_GITHUB_TOKEN="fake-token-for-tests", DRY_RUN="1")
        before = self.origin_ref("main")
        self.edit({"mind/log.md": "hi\n"})
        out = diaryclone.commit("mind: hi")
        self.assertTrue(out["dry_run"])
        self.assertEqual(self.origin_ref("main"), before)
