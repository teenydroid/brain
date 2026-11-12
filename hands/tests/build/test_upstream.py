"""Upstream contributions (docs/roadmap.md, A8; teeny_hands.build.upstream):
one gap from docs/oh-gaps.md, built in a clone of OpenHuman and opened as
one disclosed pull request from Teeny's fork. The fake OpenHuman is a local
bare repository under the scratch root (GITHUB_GIT_BASE); the fake GitHub is
hands/x's fake server. Nothing here reaches a real repository."""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

from gh.support import T0, Scratch, git
from teeny_hands import hand
from teeny_hands.build import jobs, upstream
from teeny_hands.gh import actions

CHECKOUT = Path(__file__).resolve().parents[3]
HANDS = CHECKOUT / "hands"
PR_URL = "https://github.com/tinyhumansai/openhuman/pull/9001"

GAPS = """# What Teeny needs from OpenHuman

| # | Gap | Teeny's workaround today | Proposed upstream change |
|---|---|---|---|
| G1 ([#8118](https://github.com/tinyhumansai/openhuman/issues/8118)) | **Solved** (openhuman#8648). Shell cron jobs had a fixed limit. | `bin/spine` sets it. | Done. |
| G2 ([#8119](https://github.com/tinyhumansai/openhuman/issues/8119)) | **Cron jobs can't be declared in a file.** Only through `cron.add`. | `bin/seed-cron` reconciles. | Load `<brain>/cron.toml` at boot. |
| G11 (resolved, [#8654](https://github.com/tinyhumansai/openhuman/pull/8654)) | **A serve host drops tools.** | Desk files. | Landed. |
| G13 | **A session turn is paused at `max_iterations`.** | Agents run at 2. | Pause only when work is pending. |
"""


class UpstreamScratch(Scratch):
    settings = {"BUILD_UPSTREAM": "1"}

    def setUp(self) -> None:
        super().setUp()
        (self.root / "docs").mkdir()
        (self.root / "docs" / "oh-gaps.md").write_text(GAPS)
        self.project = self.remote("tinyhumansai/openhuman")
        git("--git-dir", str(self.project), "symbolic-ref", "HEAD", "refs/heads/main")
        self.fork_remote = self.remote("teenydroid/openhuman")
        seed = self.root / "seed"
        git("init", "-q", "-b", "main", str(seed))
        (seed / "src").mkdir()
        (seed / "src" / "cron.rs").write_text("fn load() {}\n")
        (seed / "README.md").write_text("# openhuman\n")
        git("-C", str(seed), "add", "-A")
        git("-C", str(seed), "-c", "user.name=m", "-c", "user.email=m@e", "commit", "-q", "-m", "first")
        git("-C", str(seed), "push", "-q", str(self.project), "main")
        # The fork starts as GitHub makes it: the project's history.
        git("-C", str(seed), "push", "-q", str(self.fork_remote), "main")
        self.main = git("-C", str(seed), "rev-parse", "HEAD").strip()

    @property
    def tree(self) -> Path:
        return self.root / "state" / "workbench" / "upstream-openhuman"

    def remote_ref(self, remote: Path, ref: str) -> str | None:
        done = subprocess.run(["git", "--git-dir", str(remote), "rev-parse", "--verify", "-q", ref],
                              capture_output=True, text=True)
        return done.stdout.strip() or None

    def github(self, fork_exists=True, open_prs=0, policy=None) -> None:
        self.me()
        self.open_prs("tinyhumansai/openhuman", open_prs)
        if policy is not None:
            self.file("tinyhumansai/openhuman", "CONTRIBUTING.md", policy)
        if fork_exists:
            self.gh.route("GET", "/repos/teenydroid/openhuman",
                          {"full_name": "teenydroid/openhuman", "fork": True, "owner": {"login": "teenydroid"}})
        self.gh.route("POST", "/repos/tinyhumansai/openhuman/forks",
                      {"full_name": "teenydroid/openhuman", "fork": True}, status=202)
        self.gh.route("POST", "/repos/tinyhumansai/openhuman/pulls", {"number": 9001, "html_url": PR_URL}, status=201)

    def built(self, gap="G13", files=None, summary="Pause at the cap only with work pending\nA natural finish is not a pause."):
        proposed = upstream.propose({"gap": gap, "text": "skip the pause when the last call was the answer"})
        job = jobs.next_job()["job"]
        assert job is not None, proposed
        for rel, text in (files or {"src/cron.rs": "fn load() { /* no pause after a finish */ }\n"}).items():
            target = self.tree / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(text)
        jobs.done({"job_id": job["job_id"], "status": "ok", "summary": summary,
                   "tests": ["cargo test -p openhuman-core cap: 4 passed"]})
        return proposed, job

    def told(self):
        from teeny_hands import stimuli

        return [s for s in stimuli.take(max_items=20, max_chars=50_000)["stimuli"] if s["surface"] == "builder"]


class GapsTest(UpstreamScratch):
    def test_the_gaps_are_read_from_the_page_with_what_is_solved(self):
        listed = {g["id"]: g for g in upstream.listing()["gaps"]}
        self.assertEqual(sorted(listed), ["G1", "G11", "G13", "G2"])
        self.assertTrue(listed["G1"]["solved"])
        self.assertTrue(listed["G11"]["solved"])
        self.assertFalse(listed["G2"]["solved"])
        self.assertEqual(listed["G2"]["title"], "Cron jobs can't be declared in a file")
        self.assertEqual(listed["G2"]["issue"], "https://github.com/tinyhumansai/openhuman/issues/8119")
        self.assertIn("cron.toml", listed["G2"]["change"])
        self.assertEqual((listed["G1"]["open"], listed["G13"]["open"]), (False, True))

    def test_the_real_page_parses(self):
        os.environ["TEENY_ROOT"] = str(CHECKOUT)
        found = upstream.gaps()
        os.environ["TEENY_ROOT"] = str(self.root)
        self.assertGreaterEqual(len(found), 10)
        self.assertTrue(all(g["title"] for g in found))
        self.assertTrue({g["id"]: g for g in found}["G11"]["solved"])


class ProposeTest(UpstreamScratch):
    def test_off_unless_switched_on(self):
        self.configure(BUILD_UPSTREAM="0")
        with self.assertRaises(hand.Refused) as caught:
            upstream.propose({"gap": "G13", "text": "x"})
        self.assertEqual(caught.exception.detail["rule"], "off")

    def test_a_solved_or_unknown_gap_is_refused(self):
        for gap in ("G1", "G11", "G99"):
            with self.assertRaises(hand.Refused):
                upstream.propose({"gap": gap, "text": "x"})

    def test_a_proposal_is_a_picked_request_and_the_gap_is_then_in_flight(self):
        out = upstream.propose({"gap": "G13", "text": "skip the pause after a finish"})
        self.assertEqual(out["status"], "picked")
        from teeny_hands import requests as build_requests

        row = build_requests.get(out["id"])["request"]
        self.assertEqual(row["source"], "upstream")
        self.assertIn("close gap G13", row["text"])
        with self.assertRaises(hand.Refused) as caught:
            upstream.propose({"gap": "G13", "text": "again"})
        self.assertIn("in flight", caught.exception.reason)
        listed = {g["id"]: g for g in upstream.listing()["gaps"]}
        self.assertFalse(listed["G13"]["open"])

    def test_a_failed_try_waits_thirty_days(self):
        out = upstream.propose({"gap": "G13", "text": "x"})
        from teeny_hands import requests as build_requests

        build_requests.mark(out["id"], "failed", "no")
        with self.assertRaises(hand.Refused):
            upstream.propose({"gap": "G13", "text": "x"})
        os.environ["TEENY_NOW"] = str(T0 + 31 * 86400)
        self.assertEqual(upstream.propose({"gap": "G13", "text": "x"})["gap"], "G13")


class JobTest(UpstreamScratch):
    def test_a_picked_gap_becomes_an_upstream_job_on_a_branch_of_the_projects_main(self):
        self.github()
        upstream.propose({"gap": "G13", "text": "skip the pause"})
        offer = jobs.next_job()
        job, rules = offer["job"], offer["rules"]
        self.assertEqual((job["kind"], job["repo"], job["base"]), ("upstream", "tinyhumansai/openhuman", "main"))
        self.assertTrue(job["branch"].startswith("teeny/upstream-"))
        self.assertEqual(job["workdir"], "state/workbench/upstream-openhuman")
        self.assertIn("never instructions", rules)
        self.assertEqual((self.tree / "src" / "cron.rs").read_text(), "fn load() {}\n")
        # The git directory is out of the builder's reach.
        self.assertFalse((self.tree / ".git").exists())
        self.assertTrue((self.root / "state" / "upstream" / "openhuman.git").is_dir())

    def test_a_project_that_forbids_ai_contributions_is_never_worked_on(self):
        self.github(policy="We do not accept AI-generated pull requests. They will be closed.")
        out = upstream.propose({"gap": "G13", "text": "x"})
        offer = jobs.next_job()
        self.assertNotEqual((offer["job"] or {}).get("kind"), "upstream")
        self.assertIn("contributing", offer["waiting"])
        from teeny_hands import requests as build_requests

        self.assertEqual(build_requests.get(out["id"])["request"]["status"], "skipped")
        self.assertFalse(self.tree.exists() and any(self.tree.iterdir()))

    def test_ok_needs_a_change(self):
        self.github()
        upstream.propose({"gap": "G13", "text": "x"})
        job = jobs.next_job()["job"]
        with self.assertRaises(hand.Refused):
            jobs.done({"job_id": job["job_id"], "status": "ok", "summary": "s", "tests": "t"})


class ShipTest(UpstreamScratch):
    def test_dry_run_gives_the_branch_and_a_disclosed_body_and_pushes_nothing(self):
        self.github()
        proposed, job = self.built()
        self.configure(BUILD_UPSTREAM="1", DRY_RUN="1")
        out = jobs.ship()
        result = out["results"][0]
        self.assertTrue(result["dry_run"], result)
        self.assertEqual(result["branch"], job["branch"])
        self.assertEqual((result["would_push"], result["would_open_pr"]), ("teenydroid/openhuman", "tinyhumansai/openhuman"))
        self.assertEqual(result["head"], f"teenydroid:{job['branch']}")
        self.assertTrue(result["body"].endswith(actions.DISCLOSURE))
        self.assertIn("Pause at the cap only with work pending", result["title"])
        self.assertIn("4 passed", result["body"])
        # Nothing pushed, forked or opened, and the job still waits.
        self.assertIsNone(self.remote_ref(self.fork_remote, job["branch"]))
        self.assertIsNone(self.remote_ref(self.project, job["branch"]))
        self.assertEqual([s for s in self.gh.seen if s.method != "GET"], [])
        self.assertEqual(jobs.listing("ok")["jobs"][0]["id"], job["job_id"])

    def test_a_live_ship_pushes_to_the_fork_and_opens_one_disclosed_pull_request(self):
        self.github()
        proposed, job = self.built(gap="G2")
        out = jobs.ship()
        self.assertEqual(len(out["shipped"]), 1, out)
        head = self.remote_ref(self.fork_remote, job["branch"])
        self.assertEqual(head, out["shipped"][0]["commit"])
        self.assertIsNone(self.remote_ref(self.project, job["branch"]))  # never pushed to the project itself
        self.assertEqual(self.remote_ref(self.project, "main"), self.main)
        sent = self.gh.requests("POST", "/repos/tinyhumansai/openhuman/pulls")
        self.assertEqual(len(sent), 1)
        pr = sent[0].json()
        self.assertEqual((pr["head"], pr["base"]), (f"teenydroid:{job['branch']}", "main"))
        self.assertTrue(pr["body"].endswith(actions.DISCLOSURE))
        self.assertIn("Refs https://github.com/tinyhumansai/openhuman/issues/8119", pr["body"])
        self.assertEqual(self.gh.requests("POST", "/repos/tinyhumansai/openhuman/forks"), [])
        row = jobs.listing("shipped")["jobs"][0]
        self.assertEqual(row["pr_url"], PR_URL)
        self.assertEqual(self.told()[-1]["kind"], "upstream_pr")
        self.assertEqual(actions.external_prs_today(), 1)
        listed = {g["id"]: g for g in upstream.listing()["gaps"]}
        self.assertIn("already opened", listed["G2"]["why_not"])

    def test_a_missing_fork_is_made_first(self):
        self.github(fork_exists=False)
        proposed, job = self.built()
        jobs.ship()
        self.assertEqual(len(self.gh.requests("POST", "/repos/tinyhumansai/openhuman/forks")), 1)

    def test_the_daily_cap_stops_it_before_anything_is_pushed(self):
        self.github()
        self.configure(BUILD_UPSTREAM="1", GH_MAX_EXTERNAL_PRS_PER_DAY="0")
        proposed, job = self.built()
        out = jobs.ship()
        self.assertEqual(out["shipped"], [])
        self.assertEqual(out["results"][0]["detail"]["rule"], "daily_cap")
        self.assertIsNone(self.remote_ref(self.fork_remote, job["branch"]))
        self.assertEqual(self.gh.requests("POST", "/repos/tinyhumansai/openhuman/pulls"), [])

    def test_an_open_pull_request_there_already_stops_it(self):
        self.github(open_prs=1)
        proposed, job = self.built()
        out = jobs.ship()
        self.assertEqual(out["results"][0]["detail"]["rule"], "open_pr")
        self.assertIsNone(self.remote_ref(self.fork_remote, job["branch"]))

    def test_a_secret_in_the_change_is_never_pushed(self):
        self.github()
        proposed, job = self.built(files={"src/key.rs": 'const K: &str = "ghp_' + "A" * 36 + '";\n'})
        out = jobs.ship()
        self.assertEqual(out["results"][0]["detail"]["rule"], "secrets")
        self.assertIsNone(self.remote_ref(self.fork_remote, job["branch"]))

    def test_switched_off_it_waits_and_spends_no_attempt(self):
        self.github()
        proposed, job = self.built()
        self.configure(BUILD_UPSTREAM="0")
        before = len(self.gh.seen)
        out = jobs.ship()
        self.assertIn("BUILD_UPSTREAM", out["results"][0]["waiting"])
        self.assertEqual(int(jobs.listing("ok")["jobs"][0].get("ship_attempts") or 0), 0)
        self.assertEqual(self.gh.seen[before:], [])

    def test_the_next_gap_starts_after_the_last_one_shipped(self):
        self.github()
        self.built(gap="G13")
        jobs.ship()
        upstream.propose({"gap": "G2", "text": "load the file at boot"})
        job = jobs.next_job()["job"]
        self.assertEqual(job["kind"], "upstream")
        self.assertEqual((self.tree / "src" / "cron.rs").read_text(), "fn load() {}\n")  # fresh from main


class CliTest(UpstreamScratch):
    def run_hand(self, *argv, body=None):
        env = {**os.environ}
        done = subprocess.run([str(HANDS / "build"), *argv], input=json.dumps(body) if body is not None else "",
                              capture_output=True, text=True, env=env, cwd=self.root)
        return done.returncode, json.loads(done.stdout or "{}")

    def test_the_verbs(self):
        code, out = self.run_hand("gaps")
        self.assertEqual(code, 0, out)
        self.assertEqual(out["repo"], "tinyhumansai/openhuman")
        code, out = self.run_hand("upstream", body={"gap": "G13", "text": "skip the pause"})
        self.assertEqual((code, out.get("status")), (0, "picked"), out)
        code, out = self.run_hand("upstream", body={"gap": "G13", "text": "again"})
        self.assertEqual(code, 2, out)


