"""Noticing a bad deploy (rollback.py), under BABYSIT_SELF_MERGE_ALL: at the
start of each sweep the newest deploy is judged in code, from the doctor's
report and bin/spine's last-good marker written after it. A bad one is
recorded (`deploy_bad`), gets one revert issue, and holds self-merge of any
pull request touching its paths for ROLLBACK_BLOCK_HOURS; a deploy healthy
when its window closes is `deploy_ok`, once. It never restarts or rolls back
the box: bin/spine already boots last-good when the doctor fails."""

from __future__ import annotations

import datetime as dt
import json
import os

from .support import DIARY, REPO, T0, patch
from .test_self_merge_all import AllScratch
from teeny_hands import hand
from teeny_hands.babysit import actions, audit, ledger, rollback

MERGE = "m" * 40
WINDOW = rollback.ROLLBACK_WINDOW_MINUTES * 60


def iso(at: float) -> str:
    return dt.datetime.fromtimestamp(at, dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class RollbackScratch(AllScratch):
    def setUp(self) -> None:
        super().setUp()
        self.on()
        self.gh.route("POST", f"/repos/{REPO}/issues", {"number": 99, "html_url": f"https://github.com/{REPO}/issues/99"})
        # #7, what was merged: its files are what a bad deploy blocks.
        self.self_pr(files=[
            {"filename": "brain/playbooks/morning.md", "status": "modified", "additions": 1, "deletions": 1,
             "patch": patch("say gm later")},
            {"filename": "hands/lib/teeny_hands/x.py", "status": "added", "additions": 1, "deletions": 0,
             "patch": patch("x = 1")},
        ])

    def at(self, seconds: float) -> None:
        os.environ["TEENY_NOW"] = str(T0 + seconds)

    def deployed(self, number: int = 7, sha: str = MERGE) -> None:
        """The deploy_ff row deploy.run writes, now."""
        ledger.record_action("deploy_ff", REPO, number, sha, **{"from": "f" * 40, "files": 2, "numbers": [number]})

    def doctor(self, at: float, *failing: str) -> None:
        checks = [{"check": "storage", "status": "ok"}] + [{"check": name, "status": "fail"} for name in failing]
        (hand.state_dir() / "doctor.json").write_text(json.dumps(
            {"ok": not failing, "at": iso(T0 + at), "commit": MERGE, "live": False, "failed": len(failing),
             "checks": checks}))

    def fell_back(self, at: float) -> None:
        (hand.state_dir() / "spine-fallback.json").write_text(json.dumps(
            {"from": MERGE, "to": "f" * 40, "gen": "pr-7", "reason": "bin/doctor failed", "at": iso(T0 + at)}))

    def rows(self, *verbs: str) -> list[dict]:
        conn = ledger.connect()
        try:
            got = conn.execute("SELECT verb, number, head_sha, detail FROM babysit_actions ORDER BY id").fetchall()
        finally:
            conn.close()
        return [{**dict(r), "detail": json.loads(r["detail"])} for r in got if not verbs or r["verb"] in verbs]

    def issues(self) -> list:
        return self.gh.requests("POST", f"/repos/{REPO}/issues")


class JudgeTest(RollbackScratch):
    def test_switch_off_judges_nothing(self):
        self.configure(BABYSIT_SELF_MERGE="1")
        self.deployed()
        self.doctor(60, "cron.toml")
        self.at(120)
        self.assertIsNone(rollback.check())
        self.assertEqual(self.rows("deploy_ok", "deploy_bad"), [])
        self.assertEqual(self.issues(), [])

    def test_nothing_deployed_is_nothing_to_judge(self):
        self.assertIsNone(rollback.check())

    def test_a_healthy_deploy_is_watched_then_ok_once(self):
        self.deployed()
        self.doctor(30)
        self.at(20 * 60)
        self.assertEqual(rollback.check()["watching"], 7)
        self.assertEqual(self.rows("deploy_ok", "deploy_bad"), [])
        self.at(WINDOW + 60)
        self.assertEqual(rollback.check()["judged"], "deploy_ok")
        self.at(WINDOW + 20 * 60)
        rollback.check()
        (ok,) = self.rows("deploy_ok")
        self.assertEqual((ok["number"], ok["head_sha"]), (7, MERGE))
        self.assertEqual(self.rows("deploy_bad"), [])

    def test_doctor_failures_after_the_deploy_are_a_bad_deploy(self):
        self.deployed()
        self.doctor(90, "cron.toml", "playbooks parse")
        self.at(5 * 60)
        out = rollback.check()
        self.assertEqual(out["judged"], "deploy_bad")
        (bad,) = self.rows("deploy_bad")
        self.assertEqual((bad["number"], bad["head_sha"]), (7, MERGE))
        self.assertEqual(bad["detail"]["paths"], ["brain/playbooks/morning.md", "hands/lib/teeny_hands/x.py"])
        self.assertEqual(bad["detail"]["doctor"], ["cron.toml", "playbooks parse"])
        self.assertFalse(bad["detail"]["fallback"])

    def test_a_doctor_report_from_before_the_deploy_does_not_count(self):
        self.doctor(-600, "cron.toml")
        self.deployed()
        self.at(WINDOW + 60)
        self.assertEqual(rollback.check()["judged"], "deploy_ok")

    def test_a_fallback_after_the_deploy_is_a_bad_deploy_even_with_a_green_doctor(self):
        self.deployed()
        self.fell_back(40)
        self.doctor(50)  # last-good booted, and its doctor is green
        self.at(10 * 60)
        self.assertEqual(rollback.check()["judged"], "deploy_bad")
        (bad,) = self.rows("deploy_bad")
        self.assertTrue(bad["detail"]["fallback"])

    def test_a_fallback_from_before_the_deploy_does_not_count(self):
        self.fell_back(-3600)
        self.deployed()
        self.at(WINDOW + 60)
        self.assertEqual(rollback.check()["judged"], "deploy_ok")

    def test_only_the_newest_deploy_is_judged(self):
        self.deployed(number=6, sha="6" * 40)
        self.at(10 * 60)
        self.deployed()
        self.doctor(11 * 60, "cron.toml")
        self.at(12 * 60)
        rollback.check()
        self.assertEqual([r["number"] for r in self.rows("deploy_bad")], [7])


class RevertTest(RollbackScratch):
    def bad(self) -> None:
        self.deployed()
        self.doctor(60, "cron.toml")
        self.at(5 * 60)

    def test_a_bad_deploy_opens_one_revert_issue_not_one_per_run(self):
        self.bad()
        out = rollback.check()
        self.assertEqual(out["reverts"][0]["number"], 99)
        for minutes in (25, 45, 65, 85):
            self.at(minutes * 60)
            rollback.check()
        (issue,) = self.issues()
        body = json.loads(issue.body)
        self.assertEqual(body["title"], "revert #7: the deploy left the box unhealthy")
        self.assertIn("cron.toml", body["body"])
        self.assertIn(MERGE[:12], body["body"])
        self.assertIn("brain/playbooks/morning.md", body["body"])
        self.assertEqual(len(self.rows("deploy_bad")), 1)
        self.assertEqual(len(self.rows("deploy_revert")), 1)
        # it went through the guard, like every issue hands/gh opens
        self.assertEqual(len(self.guard.calls), 1)

    def test_an_issue_that_did_not_open_is_tried_again_next_run(self):
        self.gh.route("POST", f"/repos/{REPO}/issues", {"message": "boom"}, status=502)
        self.bad()
        out = rollback.check()
        self.assertIn("error", out["reverts"][0])
        self.assertEqual(len(self.rows("deploy_bad")), 1)
        self.gh.route("POST", f"/repos/{REPO}/issues", {"number": 99, "html_url": "https://github.com/i/99"})
        self.at(25 * 60)
        self.assertEqual(rollback.check()["reverts"][0]["number"], 99)
        self.at(45 * 60)
        rollback.check()
        self.assertEqual(len(self.rows("deploy_revert")), 1)
        self.assertEqual(len(self.rows("deploy_bad")), 1)


class BlockTest(RollbackScratch):
    def test_paths_of_a_bad_deploy_hold_self_merge_for_a_day(self):
        self.deployed()
        self.doctor(60, "cron.toml")
        self.at(5 * 60)
        rollback.check()
        # a later candidate touching one of its paths
        self.self_pr(number=8, files=[{"filename": "brain/playbooks/morning.md", "status": "modified",
                                       "additions": 1, "deletions": 0, "patch": patch("say gm at noon")}])
        result = audit.audit(REPO, 8)
        self.assertIn({"rule": "recently_rolled_back", "severity": "operator", "where": "brain/playbooks/morning.md"},
                      result["findings"])
        self.assertEqual(result["verdict"], "needs_operator")
        self.assertFalse(result["self_merge"])
        self.at(rollback.ROLLBACK_BLOCK_HOURS * 3600 + 600)
        result = audit.audit(REPO, 8)
        self.assertNotIn("recently_rolled_back", self.rules(result))
        self.assertEqual(result["verdict"], "ready", result["findings"])

    def test_a_candidate_touching_other_paths_is_not_held(self):
        self.deployed()
        self.doctor(60, "cron.toml")
        self.at(5 * 60)
        rollback.check()
        self.self_pr(number=8, files=[{"filename": "docs/notes.md", "status": "modified", "additions": 1,
                                       "deletions": 0, "patch": patch("x")}])
        self.assertEqual(audit.audit(REPO, 8)["verdict"], "ready")


class DryRunTest(RollbackScratch):
    def test_dry_run_only_records_what_it_would_do(self):
        self.on(DRY_RUN="1")
        self.deployed()
        self.doctor(60, "cron.toml")
        self.at(5 * 60)
        out = rollback.check()
        self.assertTrue(out["dry_run"])
        self.assertEqual(out["would"], "deploy_bad")
        self.at(25 * 60)
        rollback.check()
        self.assertEqual(self.issues(), [])
        self.assertEqual(self.rows("deploy_bad", "deploy_ok", "deploy_revert"), [])
        (row,) = self.rows("rollback_dry_run")
        self.assertEqual(row["detail"]["would"], "deploy_bad")
        self.self_pr(number=8)
        self.assertNotIn("recently_rolled_back", self.rules(audit.audit(REPO, 8)))


class SweepTest(RollbackScratch):
    def test_the_sweep_judges_the_last_deploy_first(self):
        self.gh.route("GET", f"/repos/{DIARY}/pulls", [])
        self.deployed()
        self.doctor(60, "cron.toml")
        self.at(5 * 60)
        out = actions.sweep()
        self.assertEqual(out["rollback"]["judged"], "deploy_bad")
        # the sweep audited #7 after the judgement, so it is held already
        (pull,) = [p for p in audit.queue(REPO, 20)["pulls"] if p["number"] == 7]
        self.assertIn("recently_rolled_back", self.rules(pull))
