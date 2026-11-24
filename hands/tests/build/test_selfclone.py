"""Teeny's own clone (teeny_hands.build.selfclone): synced from origin/main
at the start of every bin/evolve run and by `hands/build self-sync`, never
clobbering unshipped work. The fake origin is a local bare repository under
the scratch root (GITHUB_GIT_BASE)."""

from __future__ import annotations

import json
import os
import subprocess
import unittest
from pathlib import Path

from gh.support import T0, Scratch, git
from teeny_hands import hand
from teeny_hands.build import ledger, selfclone

CHECKOUT = Path(__file__).resolve().parents[3]
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
        # Nothing in the working tree points at the git directory.
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
        self.clone_git("checkout", "-q", "-b", "by-hand")
        (self.tree / "brain" / "SOUL.md").write_text("by hand\n")
        self.clone_git("add", "-A")
        self.clone_git("-c", "user.name=t", "-c", "user.email=t@e", "commit", "-q", "-m", "by hand")
        mine = self.clone_git("rev-parse", "HEAD").strip()
        new = self.upstream({"brain/NEW.md": "new\n"}, "new")
        out = selfclone.sync()
        self.assertEqual(out["state"], "busy")
        self.assertEqual((out["branch"], out["head"], out["commit"]), ("by-hand", mine, new))
        self.assertEqual((self.tree / "brain" / "SOUL.md").read_text(), "by hand\n")
        self.assertEqual(self.clone_git("rev-parse", "main").strip(), new)  # main moved on beside it
        self.assertNotEqual(first, new)

    def test_a_teeny_branch_the_retired_builder_left_is_parked_and_main_checked_out(self):
        """Nothing works on the clone's teeny/* branches any more: one left
        checked out, with a draft on it, is committed there as Teeny (nothing
        thrown away) and main comes back, so evolve's base keeps moving."""
        selfclone.sync()
        self.clone_git("checkout", "-q", "-b", "teeny/self-3-old")
        (self.tree / "brain" / "SOUL.md").write_text("a draft that went nowhere\n")
        new = self.upstream({"brain/NEW.md": "new\n"}, "new")
        out = selfclone.sync()
        self.assertEqual((out["state"], out["branch"], out["head"]), ("synced", "main", new))
        self.assertEqual(out["parked"], "teeny/self-3-old")
        self.assertEqual(self.clone_git("show", "teeny/self-3-old:brain/SOUL.md"), "a draft that went nowhere\n")
        self.assertEqual(self.clone_git("log", "-1", "--format=%an", "teeny/self-3-old").strip(), "Teeny")
        self.assertEqual(out["branches"], [{"branch": "teeny/self-3-old", "ahead": 1, "behind": 1, "pushed": False}])

    def test_a_folder_that_is_not_the_clone_is_left_alone(self):
        self.tree.mkdir(parents=True)
        (self.tree / "notes.txt").write_text("somebody else's\n")
        with self.assertRaises(hand.Refused):
            selfclone.sync()
        self.assertEqual((self.tree / "notes.txt").read_text(), "somebody else's\n")


class CliTest(SelfScratch):
    """What is left of hands/build: the builder's history, the frozen list and
    the clone's sync. The builder's queue verbs are gone."""

    def run_build(self, *argv: str, body: str = "") -> subprocess.CompletedProcess:
        env = {**os.environ, "TEENY_ROOT": str(self.root)}
        return subprocess.run([str(HANDS / "build"), *argv], input=body, capture_output=True, text=True, env=env)

    def test_self_sync_through_the_command_line(self):
        synced = self.run_build("self-sync")
        self.assertEqual(synced.returncode, 0, synced.stderr)
        self.assertEqual(json.loads(synced.stdout)["state"], "cloned")

    def test_jobs_reads_the_builders_history(self):
        conn = ledger.db()
        conn.execute("INSERT INTO build_jobs (kind, slug, repo, text, workdir, status, claimed_at, shipped_at, repo_url)"
                     " VALUES ('daily', 'daily-2026-11-20', 'dice-cli', 'x', 'w', 'shipped', 1, 2,"
                     " 'https://github.com/teenydroid/dice-cli')")
        conn.close()
        got = self.run_build("jobs", "--status", "shipped", "--limit", "5")
        self.assertEqual(got.returncode, 0, got.stderr)
        self.assertEqual([j["repo"] for j in json.loads(got.stdout)["jobs"]], ["dice-cli"])

    def test_jobs_on_a_box_that_never_built_anything_is_empty(self):
        got = self.run_build("jobs")
        self.assertEqual((got.returncode, json.loads(got.stdout)), (0, {"jobs": []}))

    def test_the_builders_queue_verbs_are_gone(self):
        for verb in ("next", "done", "desk", "ship", "babysit", "self", "upstream", "gaps", "idea", "ideas"):
            with self.subTest(verb=verb):
                got = self.run_build(verb, body="{}")
                self.assertNotEqual(got.returncode, 0)


class FrozenListingTest(unittest.TestCase):
    """`hands/build frozen`: so a change to frozen code is skipped up front.
    On 2026-11-13 a self change asked to reword the builder's daily brief,
    frozen code, and the builder spent three hours finding that out."""

    def test_it_lists_the_frozen_paths_and_where_the_often_wanted_ones_live(self):
        got = selfclone.frozen_listing()
        self.assertIn("bin/**", got["patterns"])
        self.assertIn("hands/lib/teeny_hands/build/", got["patterns"])
        cron = next(w for w in got["often_wanted"] if "evals/cron.pinned.toml" in w["frozen_in"])
        self.assertIn("brain/cron.toml", cron["yours_instead"])
        for wanted in got["often_wanted"]:
            self.assertNotIn("builder", wanted["what"])

    def test_the_cli_prints_it(self):
        done = subprocess.run([str(HANDS / "build"), "frozen"], capture_output=True, text=True,
                              env={**os.environ, "DRY_RUN": "1"})
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertIn("often_wanted", json.loads(done.stdout))


if __name__ == "__main__":
    unittest.main()
