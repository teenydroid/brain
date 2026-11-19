"""Teeny's own clone (teeny_hands.build.selfclone): synced from origin/main on
every bin/build-ship run, never clobbering unshipped work, and the `self`
job kind that edits it and ships it as a pull request. The fake origin is a
local bare repository under the scratch root (GITHUB_GIT_BASE), the fake
GitHub is hands/x's fake server, as in the other build tests."""

from __future__ import annotations

import json
import os
import subprocess
import unittest
from pathlib import Path
from unittest import mock

from gh.support import T0, Scratch, git
from teeny_hands import hand
from teeny_hands.build import feature, jobs, selfclone, verify

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


INSTRUCTIONS = "# the builder, on a self job\nchange only what is yours.\n"


class SelfJobScratch(SelfScratch):
    """Teeny's instructions for a self job in the live brain, and a way to
    queue one."""

    def setUp(self) -> None:
        super().setUp()
        (self.root / "brain" / "agents").mkdir(parents=True)
        (self.root / "brain" / "agents" / "builder-self.md").write_text(INSTRUCTIONS)

    def queue(self, text="tighten the morning post playbook") -> int:
        from teeny_hands import requests as build_requests

        rid = jobs.propose_self({"text": text})["id"]
        build_requests.pick({"id": rid, "reason": "it reads flat"})
        return rid


class SelfJobTest(SelfJobScratch):
    """A self change comes in as a build request from Teeny itself, is picked
    like any other, and becomes a job whose workdir is the clone, on a fresh
    teeny/<slug> branch."""

    def test_a_proposal_is_a_pending_request_from_teeny_itself(self):
        from teeny_hands import requests as build_requests

        out = jobs.propose_self({"text": "say gm less often"})
        row = build_requests.get(out["id"])["request"]
        self.assertEqual((row["source"], row["author"], row["status"], row["text"]),
                         ("self", "teenydroid", "pending", "say gm less often"))
        for bad in (None, {}, {"text": ""}, {"text": "x", "extra": 1}):
            with self.subTest(body=bad), self.assertRaises(hand.HandError):
                jobs.propose_self(bad)

    def refused_second(self):
        """The refusal a second proposal gets now: its detail."""
        with self.assertRaises(hand.Refused) as caught:
            jobs.propose_self({"text": "something else"})
        self.assertEqual(caught.exception.detail["rule"], "one_at_a_time")
        self.assertEqual(caught.exception.reason,
                         f"one change to yourself is already in flight: {caught.exception.detail['active']}")
        return caught.exception.detail

    def test_a_second_self_proposal_is_refused_while_one_is_in_flight(self):
        from teeny_hands import requests as build_requests

        first = jobs.propose_self({"text": "say gm less often"})["id"]
        self.assertIn(f"self request #{first} (pending)", self.refused_second()["active"])
        build_requests.pick({"id": first, "reason": "it reads flat"})
        self.assertIn(f"self request #{first} (picked)", self.refused_second()["active"])
        # Built and reported ok: the job is still in flight until it ships or fails.
        job = jobs.next_job()["job"]
        (self.tree / "brain" / "SOUL.md").write_text("# soul\nbriefer\n")
        jobs.done({"job_id": job["job_id"], "status": "ok", "summary": "briefer soul", "tests": "none"})
        self.refused_second()
        self.assertEqual(len(build_requests.listing("pending")["requests"]), 0)
        # A failed job settles its request: the next may go.
        conn = jobs.db()
        try:
            conn.execute("UPDATE build_jobs SET status = 'failed'")
            conn.execute("UPDATE build_requests SET status = 'failed'")
            conn.commit()
        finally:
            conn.close()
        self.assertEqual(jobs.propose_self({"text": "something else"})["status"], "pending")

    def test_a_request_from_somebody_else_is_never_a_self_change_in_flight(self):
        from teeny_hands import requests as build_requests

        build_requests.record("x", "1", "somebody", "build me a thing")
        conn = jobs.db()
        try:
            self.assertIsNone(jobs.self_in_flight(conn))
        finally:
            conn.close()
        self.assertEqual(jobs.propose_self({"text": "something else"})["status"], "pending")

    def test_a_skipped_or_shipped_self_request_is_not_in_flight(self):
        from teeny_hands import requests as build_requests

        rid = jobs.propose_self({"text": "say gm less often"})["id"]
        build_requests.skip({"id": rid, "reason": "not now"})
        conn = jobs.db()
        try:
            self.assertIsNone(jobs.self_in_flight(conn))
            conn.execute("UPDATE build_requests SET status = 'shipped' WHERE id = ?", (rid,))
            self.assertIsNone(jobs.self_in_flight(conn))
            conn.execute("UPDATE build_requests SET status = 'picked' WHERE id = ?", (rid,))
            self.assertIn(f"self request #{rid} (picked)", jobs.self_in_flight(conn))
        finally:
            conn.close()

    def test_a_want_proposal_is_tagged_in_its_source_id(self):
        from teeny_hands import requests as build_requests

        out = jobs.propose_self({"text": "build the chart renderer", "want": "w:m15"})
        self.assertEqual(out, {"id": out["id"], "status": "pending", "want": "w:m15"})
        row = build_requests.get(out["id"])["request"]
        self.assertRegex(row["source_id"], r"^want:w:m15:\d+\.\d{6}$")
        conn = jobs.db()
        try:
            self.assertEqual(jobs.want_of(conn, out["id"]), "w:m15")
            self.assertIsNone(jobs.want_of(conn, 9999))
        finally:
            conn.close()

    def test_a_plain_proposal_keeps_its_old_source_id_and_has_no_want(self):
        from teeny_hands import requests as build_requests

        out = jobs.propose_self({"text": "say gm less often"})
        self.assertEqual(out, {"id": out["id"], "status": "pending"})
        self.assertRegex(build_requests.get(out["id"])["request"]["source_id"], r"^\d+\.\d{6}$")
        conn = jobs.db()
        try:
            self.assertIsNone(jobs.want_of(conn, out["id"]))
        finally:
            conn.close()

    def test_a_bad_want_id_is_refused(self):
        from teeny_hands import requests as build_requests

        for bad in ("m15", "w:", "w:UP", "w:has space", "w:" + "a" * 61, "", 15, None, ["w:m15"]):
            with self.subTest(want=bad), self.assertRaises(hand.HandError):
                jobs.propose_self({"text": "build the chart renderer", "want": bad})
        self.assertEqual(build_requests.listing("pending")["requests"], [])
        # The edges that are fine.
        for good in ("w:m15", "w:" + "a" * 60, "w:g9:c1_x-y"):
            with self.subTest(want=good):
                self.assertEqual(jobs.propose_self({"text": "build it", "want": good})["want"], good)
                conn = jobs.db()
                try:
                    conn.execute("UPDATE build_requests SET status = 'skipped'")
                    conn.commit()
                finally:
                    conn.close()

    def test_the_cli_refuses_a_second_proposal_with_exit_2_and_the_one_in_flight(self):
        build = HANDS / "build"
        env = {**os.environ, "TEENY_ROOT": str(self.root)}

        def propose(body):
            return subprocess.run([str(build), "self"], input=json.dumps(body), capture_output=True, text=True, env=env)

        first = propose({"text": "build the chart renderer", "want": "w:m15"})
        self.assertEqual(first.returncode, 0, first.stderr)
        self.assertEqual(json.loads(first.stdout)["want"], "w:m15")
        second = propose({"text": "build another", "want": "w:m16"})
        self.assertEqual(second.returncode, 2, second.stderr)
        out = json.loads(second.stdout)
        self.assertEqual((out["ok"], out["rule"]), (False, "one_at_a_time"))
        self.assertIn("self request #", out["active"])
        self.assertTrue(out["refused"].startswith("one change to yourself is already in flight: "))

    def test_a_picked_self_request_starts_a_branch_from_a_freshly_pulled_main(self):
        selfclone.sync()
        fresh = self.upstream({"brain/NEW.md": "merged since the last sync\n"}, "since")
        rid = self.queue()
        out = jobs.next_job()["job"]
        slug = f"self-{rid}-tighten-the-morning-post-playbook"
        self.assertEqual((out["kind"], out["workdir"], out["repo"]), ("self", "state/workbench/brain", "brain"))
        self.assertEqual((out["branch"], out["base"]), (f"teeny/{slug}", fresh))
        self.assertEqual(out["instructions"], INSTRUCTIONS)
        self.assertEqual(self.clone_git("symbolic-ref", "--short", "HEAD").strip(), f"teeny/{slug}")
        self.assertEqual(self.clone_git("rev-parse", "HEAD").strip(), fresh)
        self.assertTrue((self.tree / "brain" / "NEW.md").exists())

    def test_the_desk_hands_the_builder_the_rules_for_its_own_repository(self):
        self.queue()
        jobs.desk()
        offer = json.loads((self.root / "state" / "workbench" / "desk" / "build.json").read_text())
        self.assertEqual(offer["job"]["kind"], "self")
        self.assertIn("frozen", offer["rules"])
        self.assertIn("Never commit", offer["rules"])

    def test_a_self_request_waits_while_the_clone_is_not_free(self):
        selfclone.sync()
        (self.tree / "brain" / "SOUL.md").write_text("an edit nobody shipped\n")
        rid = self.queue()
        out = jobs.next_job()
        self.assertEqual(out["job"]["kind"], "daily")
        self.assertIn("dirty", out["waiting"])
        self.assertEqual((self.tree / "brain" / "SOUL.md").read_text(), "an edit nobody shipped\n")
        from teeny_hands import requests as build_requests

        self.assertEqual(build_requests.get(rid)["request"]["status"], "picked")

    def test_ok_needs_a_change_in_the_clone(self):
        self.queue()
        job = jobs.next_job()["job"]
        with self.assertRaises(hand.Refused):
            jobs.done({"job_id": job["job_id"], "status": "ok", "summary": "nothing", "tests": "none"})
        (self.tree / "brain" / "SOUL.md").write_text("# soul\nsmall, kind, brief\n")
        jobs.done({"job_id": job["job_id"], "status": "ok", "summary": "briefer soul", "tests": "1 passed",
                   "repo": "not-this"})
        self.assertEqual(jobs.listing("ok")["jobs"][0]["repo"], "brain")  # a self job ships to the brain only

    def test_the_builders_work_in_progress_is_left_alone(self):
        self.queue()
        job = jobs.next_job()["job"]
        (self.tree / "brain" / "SOUL.md").write_text("half done\n")
        self.upstream({"brain/NEW.md": "new\n"}, "new")
        out = selfclone.sync()
        self.assertEqual((out["state"], out["branch"]), ("busy", job["branch"]))
        self.assertEqual((self.tree / "brain" / "SOUL.md").read_text(), "half done\n")

    def test_a_failed_self_job_is_parked_on_its_branch_and_main_checked_out(self):
        self.queue()
        job = jobs.next_job()["job"]
        (self.tree / "brain" / "SOUL.md").write_text("a draft that went nowhere\n")
        jobs.done({"job_id": job["job_id"], "status": "failed", "summary": "could not make it work", "tests": "none"})
        new = self.upstream({"brain/NEW.md": "new\n"}, "new")
        out = selfclone.sync()
        self.assertEqual((out["state"], out["branch"], out["head"]), ("synced", "main", new))
        self.assertEqual(out["parked"], job["branch"])
        # Nothing was thrown away: the draft is a commit on its branch, as Teeny.
        kept = self.clone_git("show", f"{job['branch']}:brain/SOUL.md")
        self.assertEqual(kept, "a draft that went nowhere\n")
        self.assertEqual(self.clone_git("log", "-1", "--format=%an", job["branch"]).strip(), "Teeny")
        self.assertEqual(out["branches"], [{"branch": job["branch"], "ahead": 1, "behind": 1, "pushed": False}])


class ChangedPathsTest(SelfJobScratch):
    """What a self job changed in the clone against origin/main: committed or
    not, each path once, as A, M, D or R."""

    def test_changed_paths_are_the_committed_and_the_uncommitted_ones(self):
        self.queue()
        jobs.next_job()
        self.assertEqual(selfclone.changed_paths(), [])
        (self.tree / "hands").mkdir()
        (self.tree / "hands" / "committed").write_text("one\n")
        (self.tree / "brain" / "SOUL.md").write_text("# soul\nchanged\n")
        self.clone_git("add", "-A")
        self.clone_git("-c", "user.name=t", "-c", "user.email=t@example.com", "commit", "-q", "-m", "work")
        (self.tree / "hands" / "committed").write_text("two\n")           # committed as added, edited since
        (self.tree / "hands" / "fresh file").write_text("untracked\n")    # a path with a space
        (self.tree / "bin" / "tool").unlink()                              # deleted, not committed
        (self.tree / "brain" / "SOUL.md").unlink()                         # committed as modified, deleted since
        self.assertEqual(sorted(selfclone.changed_paths()), [("A", "hands/committed"), ("A", "hands/fresh file"),
                                                              ("D", "bin/tool"), ("D", "brain/SOUL.md")])

    def test_a_rename_is_one_path_with_its_new_name(self):
        self.queue()
        jobs.next_job()
        self.clone_git("mv", "bin/tool", "bin/tool2")
        self.assertEqual(selfclone.changed_paths(), [("R", "bin/tool2")])


def feature_files(name="gizmo", want_tests=True):
    """A whole feature, as the files the builder writes in the clone."""
    files = {
        f"hands/{name}": "#!/usr/bin/env python3\n",
        f"hands/lib/teeny_hands/{name}/__init__.py": "",
        f"hands/lib/teeny_hands/{name}/cli.py": f"from .. import hand\n\n\ndef main(argv):\n    return hand.run('{name}', None, argv)\n",
        "brain/playbooks/gizmo-look.md": f'+++\ndescription = "look"\noutput = "post"\n\n[[inputs]]\nname = "g"\n'
                                         f'run = "hands/{name} look"\n+++\nSay it.\n',
        "brain/cron.toml": '[jobs.gizmo-look]\nschedule = "0 9 * * *"\ncommand = "bin/act gizmo-look"\n',
        "docs/hands.md": f"# Hands\n\n## `{name}`: looks\n\nIt looks.\n",
    }
    if want_tests:
        files[f"hands/tests/test_{name}.py"] = ("import unittest\n\n\nclass T(unittest.TestCase):\n"
                                                "    def test_it(self):\n        self.assertTrue(True)\n")
    return files


class FeatureJobTest(SelfJobScratch):
    """A self job whose request carries a want is a feature: the desk hands
    the builder the rubric, and takes "ok" only when the clone holds all of
    it (build/feature.py), keeping the rubric on the job."""

    def lay(self, files):
        for rel, text in files.items():
            target = self.tree / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(text)
            if rel == "hands/gizmo":
                target.chmod(0o755)

    def queue_want(self, want="w:m15", text="build the gizmo hand"):
        from teeny_hands import requests as build_requests

        rid = jobs.propose_self({"text": text, "want": want})["id"]
        build_requests.pick({"id": rid, "reason": "the top want"})
        return rid

    def report(self, job, status="ok"):
        return jobs.done({"job_id": job["job_id"], "status": status, "summary": "gizmo: looks at things",
                          "tests": "python3 -m unittest: 1 passed"})

    def passing(self, ran=1):
        return mock.patch.object(verify, "run", return_value={"ok": True, "jailed": True, "ran": ran, "output": "OK"})

    def test_a_feature_job_carries_the_feature_and_its_rules_on_the_desk(self):
        self.queue_want()
        jobs.desk()
        offer = json.loads((self.root / "state" / "workbench" / "desk" / "build.json").read_text())
        self.assertEqual(offer["job"]["kind"], "self")
        self.assertEqual(offer["job"]["feature"], {"want": "w:m15", "requires": jobs.FEATURE_REQUIRES})
        self.assertEqual(offer["rules"], jobs.SELF_FEATURE_RULES)
        self.assertTrue(offer["rules"].startswith(jobs.SELF_RULES))
        self.assertIn("docs/hands.md", offer["rules"])
        self.assertIn("scheduled playbook", offer["rules"])
        # What a feature requires, in the builder's words, names every part.
        needed = " ".join(jobs.FEATURE_REQUIRES)
        for part in ("hands/<name>", "hand.run", "hands/tests/test_<name>.py", "brain/playbooks", "brain/cron.toml",
                     "bin/act", "docs/hands.md"):
            self.assertIn(part, needed)
        # A run that ended mid-job gets the same offer back.
        again = jobs.next_job()
        self.assertTrue(again["job"]["resumed"])
        self.assertEqual((again["job"]["feature"]["want"], again["rules"]), ("w:m15", jobs.SELF_FEATURE_RULES))

    def test_a_plain_self_job_is_unchanged(self):
        self.queue()
        offer = jobs.next_job()
        self.assertNotIn("feature", offer["job"])
        self.assertEqual(offer["rules"], jobs.SELF_RULES)
        (self.tree / "brain" / "SOUL.md").write_text("# soul\nsmall, kind, brief\n")
        with mock.patch.object(feature, "rubric", side_effect=AssertionError("a plain job is not checked")):
            self.report(offer["job"])
        row = jobs.listing("ok")["jobs"][0]
        self.assertNotIn("rubric", row)
        self.assertEqual(row["kind"], "self")

    def test_ok_on_a_feature_without_tests_is_sent_back_naming_what_is_missing(self):
        self.queue_want()
        job = jobs.next_job()["job"]
        self.lay(feature_files(want_tests=False))
        with self.passing(), self.assertRaises(hand.Refused) as caught:
            self.report(job)
        exc = caught.exception
        self.assertEqual(exc.detail["rule"], "feature")
        self.assertEqual(exc.detail["missing"], ["no hands/tests/test_gizmo.py that parses"])
        self.assertEqual(exc.reason, "the feature is not complete: no hands/tests/test_gizmo.py that parses")
        # The builder keeps the job and finishes it.
        self.assertEqual(jobs.listing("claimed")["jobs"][0]["id"], job["job_id"])
        self.assertTrue(jobs.next_job()["job"]["resumed"])
        self.lay({"hands/tests/test_gizmo.py": feature_files()["hands/tests/test_gizmo.py"]})
        with self.passing():
            self.report(job)
        self.assertEqual(jobs.listing("ok")["jobs"][0]["id"], job["job_id"])

    def test_ok_on_a_feature_no_scheduled_playbook_calls_is_sent_back(self):
        self.queue_want()
        job = jobs.next_job()["job"]
        files = feature_files()
        files["brain/cron.toml"] = '[jobs.other]\nschedule = "0 9 * * *"\ncommand = "bin/act something-else"\n'
        self.lay(files)
        with self.passing(), self.assertRaises(hand.Refused) as caught:
            self.report(job)
        self.assertEqual(caught.exception.detail["missing"],
                         ["no playbook that calls hands/gizmo is scheduled in brain/cron.toml"])
        self.assertEqual(jobs.listing("claimed")["jobs"][0]["id"], job["job_id"])

    def test_a_feature_whose_tests_fail_in_the_jail_is_sent_back_with_the_failure(self):
        self.queue_want()
        job = jobs.next_job()["job"]
        self.lay(feature_files())
        failing = {"ok": False, "jailed": True, "ran": 1, "output": "FAIL: test_it\nAssertionError: False is not true"}
        with mock.patch.object(verify, "run", return_value=failing), self.assertRaises(hand.Refused) as caught:
            self.report(job)
        (missing,) = caught.exception.detail["missing"]
        self.assertIn("did not pass when the desk ran them", missing)
        self.assertIn("AssertionError: False is not true", missing)

    def test_a_complete_feature_is_settled_with_its_rubric(self):
        self.queue_want()
        job = jobs.next_job()["job"]
        self.lay(feature_files())
        with self.passing(ran=1) as run:
            out = self.report(job)
        self.assertEqual(out["status"], "ok")
        self.assertEqual(run.call_args.kwargs, {"start": "hands/tests", "top": "hands/tests", "pattern": "test_gizmo.py"})
        row = jobs.listing("ok")["jobs"][0]
        self.assertTrue(row["rubric"]["ok"])
        self.assertEqual(row["rubric"]["missing"], [])
        (gizmo,) = row["rubric"]["hands"]
        self.assertEqual((gizmo["name"], gizmo["entry"], gizmo["contract"], gizmo["tests"], gizmo["docs"]),
                         ("gizmo", True, True, True, True))
        self.assertEqual((gizmo["callers"], gizmo["scheduled"]), (["gizmo-look"], ["gizmo-look"]))
        self.assertEqual(gizmo["ran"], {"ok": True, "ran": 1, "jailed": True, "output": "OK"})

    @unittest.skipUnless(verify.landlock_abi() > 0, "this kernel has no Landlock")
    def test_a_complete_feature_passes_with_the_tests_really_run_in_the_jail(self):
        self.queue_want()
        job = jobs.next_job()["job"]
        self.lay(feature_files())
        self.report(job)
        (gizmo,) = jobs.listing("ok")["jobs"][0]["rubric"]["hands"]
        self.assertEqual((gizmo["ran"]["ok"], gizmo["ran"]["ran"], gizmo["ran"]["jailed"]), (True, 1, True), gizmo["ran"])

    def test_a_failed_feature_report_is_not_checked(self):
        rid = self.queue_want()
        job = jobs.next_job()["job"]
        with mock.patch.object(feature, "rubric", side_effect=AssertionError("unchecked")):
            self.report(job, "failed")
        row = jobs.listing("failed")["jobs"][0]
        self.assertNotIn("rubric", row)
        from teeny_hands import requests as build_requests

        self.assertEqual(build_requests.get(rid)["request"]["status"], "failed")

    def test_a_gave_up_feature_report_is_not_checked(self):
        rid = self.queue_want()
        job = jobs.next_job()["job"]
        with mock.patch.object(feature, "rubric", side_effect=AssertionError("unchecked")):
            self.report(job, "gave_up")
        self.assertEqual(jobs.listing("gave_up")["jobs"][0]["id"], job["job_id"])

    def test_the_pull_request_body_shows_the_rubric(self):
        self.queue_want()
        job = jobs.next_job()["job"]
        self.lay(feature_files())
        with self.passing(ran=3):
            self.report(job)
        done = jobs.listing("ok")["jobs"][0]
        title, body = selfclone.pr_text(done)
        self.assertEqual(title, "gizmo: looks at things")
        block = body[body.index("**Feature rubric**"):]
        self.assertIn("hands/gizmo", block)
        self.assertIn("3 tests ran in the jail", block)
        self.assertIn("called by gizmo-look", block)
        self.assertIn("scheduled", block)
        self.assertIn("docs/hands.md section", block)
        self.assertIn("used: counted by bin/wants 7 days after merge (scheduled playbook runs that call it)", block)
        # A job without a rubric reads as before.
        plain = {k: v for k, v in done.items() if k != "rubric"}
        self.assertNotIn("Feature rubric", selfclone.pr_text(plain)[1])


PR_URL = "https://github.com/teenydroid/brain/pull/7"


class SelfShipTest(SelfJobScratch):
    """bin/build-ship's `hands/build ship` on a self job: the frozen-path
    check against origin/main, then the branch pushed and a pull request
    opened as teenydroid, through hands/gh. Never a merge."""

    def setUp(self) -> None:
        super().setUp()
        self.me()
        self.gh.route("POST", "/repos/teenydroid/brain/pulls", {"number": 7, "html_url": PR_URL}, status=201)

    def built(self, files, summary="a briefer soul\nthe old one rambled", tests=None):
        rid = self.queue()
        job = jobs.next_job()["job"]
        for rel, text in files.items():
            target = self.tree / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(text)
        jobs.done({"job_id": job["job_id"], "status": "ok", "summary": summary,
                   "tests": tests or ["python3 -m unittest discover -s hands/tests -t hands/tests: 900 passed"]})
        return rid, job

    def request_row(self, rid):
        from teeny_hands import requests as build_requests

        return build_requests.get(rid)["request"]

    def told(self):
        from teeny_hands import stimuli

        return [s for s in stimuli.take(max_items=20, max_chars=50_000)["stimuli"] if s["surface"] == "builder"]

    def assert_nothing_merged(self, main_before):
        self.assertEqual(self.origin_ref("main"), main_before)
        self.assertEqual([s.path for s in self.gh.seen if "merge" in s.path or s.method == "PUT"], [])

    def test_an_allowed_brain_change_is_pushed_and_a_pull_request_opened(self):
        main_before = self.origin_ref("main")
        rid, job = self.built({"brain/SOUL.md": "# soul\nsmall, kind, brief\n"})
        out = jobs.ship()
        self.assertEqual(len(out["shipped"]), 1, out)
        shipped = out["shipped"][0]
        self.assertEqual((shipped["branch"], shipped["pr_url"]), (job["branch"], PR_URL))
        # The branch is on origin, committed as Teeny with the summary's first line.
        head = self.origin_ref(job["branch"])
        self.assertEqual(head, shipped["commit"])
        log = git("--git-dir", str(self.origin), "log", "-1", "--format=%an <%ae>|%s", job["branch"]).strip()
        self.assertEqual(log, "Teeny <teeny@agentmail.to>|a briefer soul")
        # One pull request, from the branch into main, saying what it is and what ran.
        sent = self.gh.requests("POST", "/repos/teenydroid/brain/pulls")
        self.assertEqual(len(sent), 1)
        pr = sent[0].json()
        self.assertEqual((pr["head"], pr["base"], pr["title"]), (job["branch"], "main", "a briefer soul"))
        self.assertIn("the old one rambled", pr["body"])
        self.assertIn("900 passed", pr["body"])
        self.assertIn("frozen", pr["body"])
        # Recorded on the job and the request, and told to the mind.
        row = jobs.listing("shipped")["jobs"][0]
        self.assertEqual((row["id"], row["pr_url"]), (job["job_id"], PR_URL))
        self.assertEqual(row["commit_url"], f"https://github.com/teenydroid/brain/commit/{head}")
        self.assertEqual((self.request_row(rid)["status"], self.request_row(rid)["note"]), ("shipped", PR_URL))
        told = self.told()
        self.assertEqual([s["kind"] for s in told][-1], "pull_request")
        self.assertIn(PR_URL, told[-1]["text"])
        self.assert_nothing_merged(main_before)
        self.assertEqual(jobs.ship()["results"], [])

    def test_after_shipping_the_next_sync_returns_to_main_and_says_how_far_behind_the_branch_is(self):
        rid, job = self.built({"brain/SOUL.md": "# soul\nsmall, kind, brief\n"})
        jobs.ship()
        new = self.upstream({"brain/NEW.md": "merged while the PR is open\n"}, "meanwhile")
        out = selfclone.sync()
        self.assertEqual((out["state"], out["branch"], out["head"], out["parked"]), ("synced", "main", new, job["branch"]))
        self.assertEqual(out["branches"], [{"branch": job["branch"], "ahead": 1, "behind": 1, "pushed": True}])

    def test_a_change_touching_bin_is_refused_naming_the_path(self):
        main_before = self.origin_ref("main")
        rid, job = self.built({"brain/SOUL.md": "# soul\nbrief\n", "bin/tool": "#!/bin/sh\necho loosened\n"})
        out = jobs.ship()
        self.assertEqual(out["shipped"], [])
        result = out["results"][0]
        self.assertIn("bin/tool", result["error"])
        self.assertEqual(result["detail"]["paths"], ["bin/tool"])
        self.assertTrue(result["gave_up"])  # a frozen path is not retried
        self.assertIsNone(self.origin_ref(job["branch"]))
        self.assertEqual(self.gh.seen, [])
        row = jobs.listing("failed")["jobs"][0]
        self.assertIn("bin/tool", row["ship_error"])
        self.assertEqual(self.request_row(rid)["status"], "failed")
        told = self.told()
        self.assertEqual(told[-1]["kind"], "ship_refused")
        self.assertIn("bin/tool", told[-1]["text"])
        self.assert_nothing_merged(main_before)

    def test_dry_run_pushes_nothing_and_opens_nothing(self):
        self.configure(DRY_RUN="1")
        rid, job = self.built({"brain/SOUL.md": "# soul\nsmall, kind, brief\n"})
        out = jobs.ship()
        result = out["results"][0]
        self.assertTrue(out["dry_run"])
        self.assertEqual((result["would_push"], result["would_open_pr"], result["branch"]),
                         ("teenydroid/brain", "teenydroid/brain", job["branch"]))
        self.assertEqual(out["shipped"], [])
        self.assertIsNone(self.origin_ref(job["branch"]))
        self.assertEqual(self.gh.seen, [])
        self.assertEqual(jobs.listing("ok")["jobs"][0]["id"], job["job_id"])
        self.assertEqual(self.request_row(rid)["status"], "picked")

    def test_a_held_pull_request_pushes_nothing(self):
        rid, job = self.built({"brain/SOUL.md": "# soul\nbrief\n"}, summary="this is fucking great")
        out = jobs.ship()
        self.assertEqual(out["results"][0]["error"], "held")
        self.assertIsNone(self.origin_ref(job["branch"]))
        self.assertEqual(self.gh.requests("POST", "/repos/teenydroid/brain/pulls"), [])


class ScheduledTest(SelfJobScratch):
    """The pinned build-ship job (every 30 minutes) syncs the clone first, so
    Teeny's copy of itself is never more than a run behind main."""

    def build_ship(self) -> dict:
        env = {**os.environ, "TEENY_ROOT": str(self.root), "TEENY_HANDS": str(HANDS)}
        done = subprocess.run([str(BUILD_SHIP)], capture_output=True, text=True, env=env, stdin=subprocess.DEVNULL)
        self.assertEqual(done.returncode, 0, done.stderr)
        return json.loads(done.stdout)

    def test_a_scheduled_run_picks_up_a_new_commit_on_main(self):
        self.configure(DRY_RUN="1")
        self.build_ship()
        record = json.loads((self.root / "state" / "self" / "sync.json").read_text())
        self.assertEqual((record["state"], record["commit"]), ("cloned", self.origin_ref("main")))
        new = self.upstream({"brain/NEW.md": "merged between two runs\n"}, "between runs")
        self.later(1800)
        self.build_ship()
        record = json.loads((self.root / "state" / "self" / "sync.json").read_text())
        self.assertEqual((record["state"], record["commit"], record["head"], record["at"]),
                         ("synced", new, new, T0 + 1800))
        self.assertTrue((self.tree / "brain" / "NEW.md").exists())

    def test_a_sync_that_fails_does_not_stop_the_desk_or_shipping(self):
        self.configure(DRY_RUN="1", GITHUB_GIT_BASE="file://" + str(self.root / "nowhere"))
        out = self.build_ship()
        self.assertTrue(out["dry_run"])
        self.assertEqual(json.loads((self.root / "state" / "workbench" / "desk" / "build.json").read_text())
                         ["job"]["kind"], "daily")

    def test_the_cli_proposes_and_syncs(self):
        build = HANDS / "build"
        env = {**os.environ, "TEENY_ROOT": str(self.root)}
        proposed = subprocess.run([str(build), "self"], input='{"text": "say gm less often"}', capture_output=True,
                                  text=True, env=env)
        self.assertEqual(proposed.returncode, 0, proposed.stderr)
        self.assertEqual(json.loads(proposed.stdout)["status"], "pending")
        synced = subprocess.run([str(build), "self-sync"], capture_output=True, text=True, env=env,
                                stdin=subprocess.DEVNULL)
        self.assertEqual(synced.returncode, 0, synced.stderr)
        self.assertEqual(json.loads(synced.stdout)["state"], "cloned")


class BabysitTest(SelfJobScratch):
    """docs/roadmap.md, A7: Teeny's own open pull request with a red check
    goes back on the builder's desk as a `fix` job on its own branch, at most
    MAX_FIX_ATTEMPTS times, and the fix is pushed to that branch with no new
    pull request. Off unless BUILD_BABYSIT=1."""

    def setUp(self) -> None:
        super().setUp()
        self.me()
        self.gh.route("POST", "/repos/teenydroid/brain/pulls", {"number": 7, "html_url": PR_URL}, status=201)

    built = SelfShipTest.built
    told = SelfShipTest.told

    def shipped_pr(self):
        rid, job = self.built({"brain/SOUL.md": "# soul\nsmall, kind, brief\n"})
        jobs.ship()
        return job

    def ci(self, branch, state="open", red=("scripts",), conclusion="failure"):
        sha = self.origin_ref(branch)
        self.gh.route("GET", "/repos/teenydroid/brain/pulls/7",
                      {"number": 7, "state": state, "merged": False, "user": {"login": "teenydroid"},
                       "head": {"ref": branch, "sha": sha, "label": f"teenydroid:{branch}"}, "base": {"ref": "main"}})
        runs = [{"name": n, "status": "completed", "conclusion": conclusion} for n in red]
        runs.append({"name": "web", "status": "completed", "conclusion": "success"})
        self.gh.route("GET", f"/repos/teenydroid/brain/commits/{sha}/check-runs", {"check_runs": runs})

    def test_off_unless_switched_on(self):
        job = self.shipped_pr()
        self.ci(job["branch"])
        self.assertIn("BUILD_BABYSIT", jobs.babysit()["skipped"])

    def test_a_red_check_becomes_a_fix_job_on_its_own_branch_and_ships_there(self):
        job = self.shipped_pr()
        self.configure(BUILD_BABYSIT="1")
        self.ci(job["branch"])
        out = jobs.babysit()
        (fix,) = out["fixes"]
        self.assertEqual((fix["branch"], fix["attempt"], fix["red"]), (job["branch"], 1, ["scripts"]))
        self.assertEqual(self.clone_git("symbolic-ref", "--short", "HEAD").strip(), job["branch"])
        offered = jobs.next_job()["job"]
        self.assertEqual((offered["job_id"], offered["kind"], offered["branch"]), (fix["job_id"], "fix", job["branch"]))
        self.assertIn("scripts", offered["request"])
        self.assertTrue(offered["resumed"])
        # The builder fixes it; the fix ships to the same branch, no new pull request.
        (self.tree / "brain" / "SOUL.md").write_text("# soul\nsmall, kind, brief, fixed\n")
        jobs.done({"job_id": fix["job_id"], "status": "ok", "summary": "fixed the test", "tests": "all pass"})
        before = self.origin_ref(job["branch"])
        prs_before = len(self.gh.requests("POST", "/repos/teenydroid/brain/pulls"))
        out = jobs.ship()
        self.assertEqual(len(out["shipped"]), 1, out)
        after = self.origin_ref(job["branch"])
        self.assertNotEqual(after, before)
        self.assertEqual(git("--git-dir", str(self.origin), "rev-parse", f"{after}~1").strip(), before)  # not forced
        self.assertEqual(len(self.gh.requests("POST", "/repos/teenydroid/brain/pulls")), prs_before)
        self.assertEqual(self.told()[-1]["kind"], "fix_pushed")

    def test_at_most_three_fixes_a_pull_request(self):
        job = self.shipped_pr()
        self.configure(BUILD_BABYSIT="1")
        for attempt in range(1, jobs.MAX_FIX_ATTEMPTS + 1):
            self.ci(job["branch"])
            (fix,) = jobs.babysit()["fixes"]
            self.assertEqual(fix["attempt"], attempt)
            (self.tree / "brain" / "SOUL.md").write_text(f"# soul\nattempt {attempt}\n")
            jobs.done({"job_id": fix["job_id"], "status": "ok", "summary": f"try {attempt}", "tests": "t"})
            jobs.ship()
            selfclone.sync()
        self.ci(job["branch"])
        out = jobs.babysit()
        self.assertEqual(out["fixes"], [])
        self.assertTrue(out["checked"][0]["left_to_a_person"])

    def test_a_closed_or_green_pull_request_is_left_alone(self):
        job = self.shipped_pr()
        self.configure(BUILD_BABYSIT="1")
        self.ci(job["branch"], state="closed")
        self.assertEqual(jobs.babysit()["fixes"], [])
        self.ci(job["branch"], red=("scripts",), conclusion="success")
        self.assertEqual(jobs.babysit()["fixes"], [])

    def test_no_second_fix_while_one_is_in_progress(self):
        job = self.shipped_pr()
        self.configure(BUILD_BABYSIT="1")
        self.ci(job["branch"])
        self.assertEqual(len(jobs.babysit()["fixes"]), 1)
        out = jobs.babysit()
        self.assertEqual(out["fixes"], [])


class FrozenListingTest(unittest.TestCase):
    """`hands/build frozen`: what the self-improve playbook reads so a change
    to frozen code is skipped up front. On 2026-11-13 a self change asked to
    reword the builder's daily brief, frozen code, and the builder spent three
    hours finding that out."""

    def test_it_lists_the_frozen_paths_and_where_the_often_wanted_ones_live(self):
        got = selfclone.frozen_listing()
        self.assertIn("bin/**", got["patterns"])
        self.assertIn("hands/lib/teeny_hands/build/", got["patterns"])
        brief = next(w for w in got["often_wanted"] if "daily brief" in w["what"])
        self.assertIn("hands/lib/teeny_hands/build/jobs.py", brief["frozen_in"])
        self.assertIn("brain/playbooks/build-idea.md", brief["yours_instead"])

    def test_the_cli_prints_it(self):
        done = subprocess.run([str(HANDS / "build"), "frozen"], capture_output=True, text=True,
                              env={**os.environ, "DRY_RUN": "1"})
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertIn("often_wanted", json.loads(done.stdout))
