"""Deploy on merge (hands/lib/teeny_hands/babysit/deploy.py): after the
babysitter self-merges a teeny/gen-* pull request, the live checkout
(TEENY_ROOT) is brought to the merge: fast-forward only, submodules, a
reseed when the cron files changed, and a spine restart only when the merge
touches something read at boot. Never while a generation or an eval runs.

Every test here runs on a real git checkout in the scratch root, with a
bare `origin` beside it and a sleeping process standing in for bin/spine."""

from __future__ import annotations

import fcntl
import json
import os
import subprocess
import sys
from pathlib import Path

from .support import DIARY, REPO, BabysitScratch
from teeny_hands import hand
from teeny_hands.babysit import actions, deploy, ledger

FAKE_SEED_CRON = "#!/bin/sh\necho seeded >> \"$TEENY_ROOT/state/seeded\"\n"


def git(where: Path, *args: str) -> str:
    done = subprocess.run(["git", "-C", str(where), "-c", "user.name=t", "-c", "user.email=t@t",
                           "-c", "commit.gpgsign=false", "-c", "core.hooksPath=/dev/null", *args],
                          capture_output=True, text=True, check=True,
                          env={**os.environ, "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1"})
    return done.stdout.strip()


class DeployScratch(BabysitScratch):
    def setUp(self) -> None:
        super().setUp()
        self.configure(BABYSIT_SELF_MERGE="1")
        (self.root / ".gitignore").write_text("secrets/\nstate/\n")
        files = {
            "brain/SOUL.md": "teeny\n",
            "brain/playbooks/morning.md": "say gm\n",
            "brain/playbooks/mentions.md": "answer\n",
            "brain/cron.toml": "[jobs.heartbeat]\n",
            "docs/notes.md": "notes\n",
            "bin/seed-cron": FAKE_SEED_CRON,
        }
        for path, text in files.items():
            (self.root / path).parent.mkdir(parents=True, exist_ok=True)
            (self.root / path).write_text(text)
        (self.root / "bin" / "seed-cron").chmod(0o755)
        git(self.root, "init", "-q", "-b", "main")
        git(self.root, "add", "-A")
        git(self.root, "commit", "-q", "-m", "gen-0")
        self.origin = self.root.parent / (self.root.name + "-origin.git")
        git(self.root.parent, "init", "-q", "--bare", "-b", "main", str(self.origin))
        self.addCleanup(subprocess.run, ["rm", "-rf", str(self.origin), str(self.origin) + "-work"])
        git(self.root, "remote", "add", "origin", str(self.origin))
        git(self.root, "push", "-q", "origin", "main")
        self.work = Path(str(self.origin) + "-work")
        git(self.root.parent, "clone", "-q", str(self.origin), str(self.work))
        self.start = git(self.root, "rev-parse", "HEAD")
        self.spine = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
        self.addCleanup(self._stop_spine)
        hand.state_dir().joinpath("spine.pid").write_text(f"{self.spine.pid}\n")

    def _stop_spine(self) -> None:
        if self.spine.poll() is None:
            self.spine.kill()
        self.spine.wait()

    def merged(self, changes: dict[str, str]) -> str:
        """A merge on origin's main (what GitHub's merge leaves), not yet in the live checkout."""
        for path, text in changes.items():
            (self.work / path).parent.mkdir(parents=True, exist_ok=True)
            (self.work / path).write_text(text)
        git(self.work, "add", "-A")
        git(self.work, "commit", "-q", "-m", "a learning")
        git(self.work, "push", "-q", "origin", "HEAD:main")
        sha = git(self.work, "rev-parse", "HEAD")
        deploy.request(REPO, 7, sha)
        return sha

    def head(self) -> str:
        return git(self.root, "rev-parse", "HEAD")

    def spine_termed(self) -> bool:
        try:
            self.spine.wait(timeout=5)
        except subprocess.TimeoutExpired:
            return False
        return True

    def verbs(self) -> list[str]:
        conn = ledger.connect()
        try:
            return [r["verb"] for r in conn.execute("SELECT verb FROM babysit_actions ORDER BY id")]
        finally:
            conn.close()

    def pending(self) -> dict | None:
        path = hand.state_dir() / deploy.PENDING
        return json.loads(path.read_text()) if path.exists() else None


class DeployTest(DeployScratch):
    def test_a_playbook_only_merge_reseeds_nothing_and_doesnt_restart(self):
        sha = self.merged({"brain/playbooks/morning.md": "say gm later\n"})
        out = deploy.run()
        self.assertEqual((out["deployed"], out["restart"], out["reseed"]), (sha, False, False))
        self.assertEqual(self.head(), sha)
        self.assertFalse((hand.state_dir() / "seeded").exists())
        self.assertFalse(self.spine_termed())
        self.assertFalse((hand.state_dir() / "last-good").exists())
        self.assertIsNone(self.pending())
        self.assertEqual(self.verbs(), ["deploy_ff"])

    def test_a_cron_change_reseeds_without_a_restart(self):
        sha = self.merged({"brain/cron.toml": "[jobs.heartbeat]\n# later\n"})
        out = deploy.run()
        self.assertEqual((out["restart"], out["reseed"]), (False, True))
        self.assertEqual(self.head(), sha)
        self.assertTrue((hand.state_dir() / "seeded").exists())
        self.assertFalse(self.spine_termed())
        self.assertEqual(self.verbs(), ["deploy_ff", "deploy_seed_cron"])

    def test_a_soul_change_records_last_good_and_restarts_the_spine(self):
        sha = self.merged({"brain/SOUL.md": "teeny, a little braver\n"})
        out = deploy.run()
        self.assertTrue(out["restart"])
        self.assertEqual(out["because"], ["brain/SOUL.md"])
        self.assertEqual(self.head(), sha)
        self.assertTrue(self.spine_termed())
        good = json.loads((hand.state_dir() / "last-good").read_text())
        self.assertEqual(good["commit"], self.start)
        # A restart reseeds cron at boot (bin/spine), so the deploy does not.
        self.assertFalse((hand.state_dir() / "seeded").exists())
        self.assertIsNone(self.pending())
        self.assertEqual(self.verbs(), ["deploy_ff", "deploy_restart"])

    def test_deploy_waits_while_evolve_runs(self):
        sha = self.merged({"brain/SOUL.md": "teeny, a little braver\n"})
        locks = hand.state_dir() / "locks"
        locks.mkdir(parents=True, exist_ok=True)
        with open(locks / "evolve.lock", "a+") as fh:
            fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
            out = deploy.run()
            self.assertEqual(out["waiting"], ["evolve"])
            deploy.run()  # a second sweep while it still runs: no second row
            fcntl.flock(fh, fcntl.LOCK_UN)
        self.assertEqual(self.head(), self.start)
        self.assertFalse(self.spine_termed())
        self.assertEqual(self.pending()["sha"], sha)
        self.assertEqual(self.verbs(), ["deploy_wait"])
        # The next sweep, once the generation is done, deploys.
        self.assertEqual(deploy.run()["deployed"], sha)
        self.assertTrue(self.spine_termed())

    def test_deploy_waits_while_an_eval_runs(self):
        self.merged({"brain/playbooks/morning.md": "x\n"})
        locks = hand.state_dir() / "locks"
        locks.mkdir(parents=True, exist_ok=True)
        with open(locks / "eval.lock", "a+") as fh:
            fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
            self.assertEqual(deploy.run()["waiting"], ["eval"])
        self.assertEqual(self.head(), self.start)

    def test_deploy_refuses_a_non_fast_forward_checkout(self):
        self.merged({"brain/SOUL.md": "theirs\n"})
        (self.root / "docs" / "notes.md").write_text("a local commit\n")
        git(self.root, "commit", "-q", "-am", "local")
        local = self.head()
        out = deploy.run()
        self.assertIn("cannot fast-forward", out["refused"])
        self.assertEqual(self.head(), local)
        self.assertFalse(self.spine_termed())
        self.assertFalse((hand.state_dir() / "last-good").exists())
        self.assertEqual(self.pending()["refused"], out["refused"])
        deploy.run()  # the same refusal again is not a second row
        self.assertEqual(self.verbs(), ["deploy_refused"])

    def test_deploy_refuses_a_checkout_with_tracked_edits(self):
        self.merged({"brain/SOUL.md": "theirs\n"})
        (self.root / "brain" / "SOUL.md").write_text("an edit by hand\n")
        out = deploy.run()
        self.assertIn("tracked edits", out["refused"])
        self.assertEqual(self.head(), self.start)
        self.assertEqual((self.root / "brain" / "SOUL.md").read_text(), "an edit by hand\n")
        self.assertFalse(self.spine_termed())

    def test_a_checkout_already_at_the_merge_is_done(self):
        sha = self.merged({"docs/notes.md": "more\n"})
        git(self.root, "pull", "-q", "--ff-only", "origin", "main")
        out = deploy.run()
        self.assertEqual(out["deployed"], sha)
        self.assertTrue(out["already"])
        self.assertIsNone(self.pending())

    def test_dry_run_only_records_what_it_would_do(self):
        self.configure(BABYSIT_SELF_MERGE="1", DRY_RUN="1")
        self.merged({"brain/SOUL.md": "theirs\n"})
        out = deploy.run()
        self.assertTrue(out["dry_run"])
        self.assertEqual(self.head(), self.start)
        self.assertFalse(self.spine_termed())
        self.assertIsNotNone(self.pending())
        self.assertEqual(self.verbs(), ["deploy_dry_run"])

    def test_self_merge_off_deploys_nothing(self):
        self.configure(BABYSIT_SELF_MERGE="0")
        self.merged({"brain/SOUL.md": "theirs\n"})
        out = deploy.run()
        self.assertIn("skipped", out)
        self.assertEqual(self.head(), self.start)
        self.assertFalse(self.spine_termed())

    def test_nothing_pending_is_nothing_to_do(self):
        self.assertIsNone(deploy.run())

    def test_the_sweep_runs_a_pending_deploy_last(self):
        self.gh.route("GET", f"/repos/{REPO}/pulls", [])
        self.gh.route("GET", f"/repos/{DIARY}/pulls", [])
        sha = self.merged({"brain/playbooks/morning.md": "x\n"})
        out = actions.sweep()
        self.assertEqual(out["deploy"]["deployed"], sha)


class PlanTest(BabysitScratch):
    def test_per_run_paths_restart_nothing(self):
        plan = deploy.plan(["brain/playbooks/morning.md", "brain/narrative/chronicle/gen-3.md", "docs/x.md"])
        self.assertEqual((plan["restart"], plan["reseed"]), (False, False))

    def test_the_playbooks_the_core_reads_at_boot_restart(self):
        for path in ("brain/playbooks/mentions.md", "brain/playbooks/mail.md"):
            self.assertTrue(deploy.plan([path])["restart"], path)

    def test_what_the_prompts_are_built_from_restarts(self):
        for path in ("brain/SOUL.md", "brain/CONTEXT.md", "brain/DRIVE.md", "brain/CHARTER.md",
                     "brain/narrative/LORE.md", "brain/narrative/ARCS.md", "brain/agents/act.md",
                     "brain/agents/act.toml", "brain/skills/x/SKILL.md"):
            self.assertTrue(deploy.plan([path])["restart"], path)

    def test_a_path_nobody_listed_restarts(self):
        self.assertEqual(deploy.plan(["brain/something-new.md"])["because"], ["unlisted"])

    def test_cron_files_reseed(self):
        self.assertTrue(deploy.plan(["brain/cron.toml"])["reseed"])
        self.assertTrue(deploy.plan(["evals/cron.pinned.toml"])["reseed"])
