"""hands/slopcash's read verbs against the fake slop.cash and GitHub: the
project and why it is paused, the job as posted, the steward's jobs, the
revision history, and the submissions with Slopbot's reviews."""

from __future__ import annotations

from .support import (
    GITHUB_TOKEN, PROJECT, REPO, SLOPBOT, SLOPBOT_REVIEW, Scratch, comment, listed, manifest, pull,
)  # fmt: skip
from teeny_hands import hand
from teeny_hands.slopcash import reads


class ProjectTest(Scratch):
    def test_reads_status_authority_reward_and_revision(self):
        self.a_project()
        out = reads.project(None)
        self.assertEqual(out["project"], PROJECT)
        self.assertEqual((out["status"], out["revision"]), ("paused", 3))
        self.assertEqual(out["authority"], {"state": "unverified", "reason": "missing-repository-proof"})
        self.assertEqual(out["steward"], "teenydroid")
        self.assertEqual(out["reward"]["monthly_cap"], "333,333 TINY")
        self.assertEqual(out["reward"]["funding_state"], "pledged")
        self.assertEqual(out["funding"]["mode"], "direct-noncustodial")
        self.assertEqual(out["vetting"], "queued")
        self.assertEqual(out["repositories"], [{"repo": REPO, "branch": "main", "url": "https://github.com/teenydroid/brain"}])

    def test_missing_repository_proof_says_install_slopbot_then_check(self):
        self.a_project()
        out = reads.project(PROJECT)
        self.assertIn("Slopbot", out["why_paused"])
        self.assertIn("https://github.com/apps/slopdotcash/installations/new", out["next_step"])
        self.assertIn(REPO, out["next_step"])
        self.assertIn("hands/slopcash check", out["next_step"])

    def test_an_active_project_needs_nothing(self):
        live = manifest()
        live["status"] = "active"
        live["authority"] = {"state": "verified", "reason": None}
        self.a_project(live)
        out = reads.project(PROJECT)
        self.assertIsNone(out["why_paused"])
        self.assertIn("nothing", out["next_step"])

    def test_a_restricted_project_is_refused_and_a_missing_one_is_an_error(self):
        self.slop.route("GET", f"/api/v1/projects/{PROJECT}", {"error": "restricted", "message": "quarantined"}, status=410)
        with self.assertRaises(hand.Refused) as caught:
            reads.project(PROJECT)
        self.assertEqual(caught.exception.reason, "restricted")
        with self.assertRaises(hand.HandError) as error:
            reads.project("no-such-project")
        self.assertIn("404", str(error.exception))

    def test_a_project_id_cannot_reach_another_path(self):
        with self.assertRaises(hand.HandError):
            reads.project("../points/me")
        self.assertEqual(self.slop.seen, [])

    def test_the_configured_project_is_the_default(self):
        self.configure(SLOPCASH_PROJECT="other-one")
        self.slop.route("GET", "/api/v1/projects/other-one", {"project": {**manifest(), "id": "other-one"}, "revision": 1})
        self.assertEqual(reads.project(None)["project"], "other-one")


class JobTest(Scratch):
    def test_the_job_as_posted(self):
        self.a_project()
        out = reads.job(None)
        real = manifest()
        self.assertEqual(out["headline"], real["headline"])
        self.assertEqual(out["description"], real["description"])
        self.assertEqual(out["approvedScope"], real["contribution"]["approvedScope"])
        self.assertEqual(out["requiredTests"], real["contribution"]["requiredTests"])
        self.assertEqual((out["contributionGuide"], out["supportedTasks"]), ("", ""))
        self.assertEqual(out["contribution"], real["contribution"])
        self.assertEqual(out["repositories"][0]["branch"], "main")
        self.assertEqual((out["revision"], out["status"]), (3, "paused"))


class JobsTest(Scratch):
    def setUp(self):
        super().setUp()
        self.slop.route("GET", "/api/v1/projects", {"schemaVersion": "1", "restrictions": [], "projects": [
            listed("asi", "SlopDotCash"), listed(PROJECT, "teenydroid", "paused", "rewrite my brain"),
            listed("hello", "TeenyDroid", "active", "hi"), {"id": "odd", "steward": None},
        ]})  # fmt: skip

    def test_the_stewards_projects_default_teenydroid(self):
        out = reads.jobs(None)
        self.assertEqual(out["steward"], "teenydroid")
        self.assertEqual(out["jobs"], [
            {"project": PROJECT, "name": PROJECT, "status": "paused", "headline": "rewrite my brain"},
            {"project": "hello", "name": "hello", "status": "active", "headline": "hi"},
        ])  # fmt: skip

    def test_another_steward(self):
        self.assertEqual([j["project"] for j in reads.jobs("SlopDotCash")["jobs"]], ["asi"])


class RevisionsTest(Scratch):
    def test_only_this_projects_revisions_newest_first_without_manifests(self):
        self.slop.route("GET", "/api/v1/project-revisions", {"schemaVersion": "1", "revisions": [
            {"projectId": "asi", "revision": 1, "manifest": {}, "manifestSha256": "x", "reason": "r", "createdAt": "t"},
            {"projectId": PROJECT, "revision": 1, "manifest": manifest(), "manifestSha256": "a", "reason": "made", "createdAt": "t1"},
            {"projectId": PROJECT, "revision": 3, "manifest": manifest(), "manifestSha256": "c", "reason": "vault", "createdAt": "t3"},
            {"projectId": PROJECT, "revision": 2, "manifest": manifest(), "manifestSha256": "b", "reason": "edit", "createdAt": "t2"},
        ]})  # fmt: skip
        out = reads.revisions(None)
        self.assertEqual(out["project"], PROJECT)
        self.assertEqual([r["revision"] for r in out["revisions"]], [3, 2, 1])
        self.assertEqual(out["revisions"][0], {"revision": 3, "created_at": "t3", "reason": "vault", "manifest_sha256": "c"})


class SubmissionsTest(Scratch):
    def setUp(self):
        super().setUp()
        self.a_project()
        footer = "does the thing\n\n---\nMade via @slopdotcash\nProvider: unavailable"
        self.gh.route("GET", f"/repos/{REPO}/pulls", [
            pull(7, "ana", "gate: refuse symlinks", footer, draft=True),
            pull(5, "bo", "gate: first cut", "plain", merged_at="2026-11-11T15:00:00Z", state="closed"),
        ])  # fmt: skip
        self.gh.route("GET", f"/repos/{REPO}/issues/7/comments", [
            comment(1, SLOPBOT, "<!-- slopbot:review -->\n**Slopbot review started**", "2026-11-11T11:00:00Z"),
            comment(2, "teenydroid", "thanks"),
            comment(3, SLOPBOT, SLOPBOT_REVIEW, "2026-11-11T13:00:00Z"),
        ])  # fmt: skip
        self.gh.route("GET", f"/repos/{REPO}/issues/5/comments", [comment(4, "bo", "ping")])

    def test_each_pull_request_with_slopbots_latest_review(self):
        out = reads.submissions("all", 10)
        self.assertEqual(out["project"], PROJECT)
        first, second = out["submissions"]
        self.assertEqual((first["number"], first["author"], first["draft"], first["merged"]), (7, "ana", True, False))
        self.assertEqual((first["repo"], first["head_sha"]), (REPO, f"{7:040d}"))
        self.assertTrue(first["made_via_slopcash"])
        self.assertEqual(first["slopbot"]["id"], 3)
        self.assertEqual(first["slopbot"]["headline"], "Slopbot review complete — no finding")
        self.assertLessEqual(len(first["slopbot"]["body"]), 1501)
        self.assertEqual(first["slopbot_comments"], 2)
        self.assertEqual((second["number"], second["merged"], second["made_via_slopcash"]), (5, True, False))
        self.assertIsNone(second["slopbot"])
        query = self.gh.requests("GET", f"/repos/{REPO}/pulls")[0].query
        self.assertEqual((query["state"], query["per_page"]), ("all", "10"))

    def test_max_caps_the_list(self):
        self.assertEqual([s["number"] for s in reads.submissions("open", 1)["submissions"]], [7])

    def test_no_token_sends_no_authorization(self):
        reads.submissions("open", 5)
        self.assertEqual({s.authorization for s in self.gh.seen}, {""})

    def test_the_github_token_raises_the_rate_limit(self):
        self.configure(GITHUB_TOKEN=GITHUB_TOKEN)
        reads.submissions("open", 5)
        self.assertEqual({s.authorization for s in self.gh.seen}, {f"Bearer {GITHUB_TOKEN}"})
        self.assertNotIn(GITHUB_TOKEN, "".join(s.headers.get("authorization", "") for s in self.slop.seen))

    def test_the_token_never_goes_to_a_github_base_that_is_not_githubs(self):
        self.configure(GITHUB_TOKEN=GITHUB_TOKEN, GITHUB_API_BASE="https://ghproxy.example")
        self.assertIsNone(reads.client.github_token())
        self.configure(GITHUB_TOKEN=GITHUB_TOKEN, GITHUB_API_BASE="https://api.github.com")
        self.assertEqual(reads.client.github_token(), GITHUB_TOKEN)


class SubmissionTest(Scratch):
    def setUp(self):
        super().setUp()
        self.a_project()
        sha = "a" * 40
        self.gh.route("GET", f"/repos/{REPO}/pulls/7", pull(
            7, "ana", "gate", "body " * 2000, sha=sha, additions=230, deletions=20, changed_files=3, merged=False,
            mergeable_state="clean"))  # fmt: skip
        self.gh.route("GET", f"/repos/{REPO}/pulls/7/files", [
            {"filename": "bin/evolve", "status": "added", "additions": 30, "deletions": 0},
            {"filename": "bin/lib/evolve_gate.py", "status": "added", "additions": 190, "deletions": 0},
            {"filename": "docs/evolve.md", "status": "renamed", "additions": 10, "deletions": 20, "previous_filename": "docs/old.md"},
        ])  # fmt: skip
        self.gh.route("GET", f"/repos/{REPO}/commits/{sha}/status", {"state": "pending", "total_count": 0, "statuses": []})
        self.gh.route("GET", f"/repos/{REPO}/commits/{sha}/check-runs", {"total_count": 2, "check_runs": [
            {"name": "scripts", "status": "completed", "conclusion": "success"},
            {"name": "spine", "status": "completed", "conclusion": "failure"},
        ]})  # fmt: skip
        self.gh.route("GET", f"/repos/{REPO}/issues/7/comments", [
            comment(1, SLOPBOT, "**Slopbot review started**"), comment(2, "ana", "hi"), comment(3, SLOPBOT, SLOPBOT_REVIEW),
        ])  # fmt: skip

    def test_one_pull_request_in_full_with_the_gate_facts(self):
        out = reads.submission(7, None)
        self.assertEqual((out["number"], out["repo"], out["author"]), (7, REPO, "ana"))
        self.assertEqual(out["files"][2], {"path": "docs/evolve.md", "status": "renamed", "additions": 10, "deletions": 20,
                                           "previous_path": "docs/old.md"})  # fmt: skip
        self.assertEqual(out["total_changed_lines"], 250)
        self.assertEqual(out["touched_paths"], ["bin/evolve", "bin/lib/evolve_gate.py", "docs/evolve.md", "docs/old.md"])
        self.assertEqual(out["checks"]["state"], "failure")
        self.assertEqual(out["checks"]["check_runs"][1], {"name": "spine", "status": "completed", "conclusion": "failure"})
        self.assertEqual([c["id"] for c in out["slopbot"]], [1, 3])
        self.assertLessEqual(len(out["body"]), 4001)
        self.assertFalse(out["made_via_slopcash"])

    def test_a_repository_the_project_does_not_list_is_refused(self):
        with self.assertRaises(hand.HandError):
            reads.submission(7, "someone/else")
        self.assertEqual(self.gh.seen, [])

    def test_checks_with_nothing_reported_are_none(self):
        sha = "a" * 40
        self.gh.route("GET", f"/repos/{REPO}/commits/{sha}/check-runs", {"total_count": 0, "check_runs": []})
        self.assertEqual(reads.submission(7, REPO)["checks"]["state"], "none")
