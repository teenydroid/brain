"""hands/lab: experiments proposed under their limits, scored in code against
the surface's other writes of the same period, concluded with a lesson only
once enough posts were measured, and the lessons listed newest first."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from x import support  # noqa: F401  (puts hands/lib on the path)
from teeny_hands import hand
from teeny_hands.lab import ledger
from teeny_hands.moltbook import rules as moltbook_rules
from teeny_hands.x import metrics as x_metrics
from teeny_hands.x import posting_rules

T0 = 1_789_369_200.0  # 2026-09-14T07:00:00Z
DAY = 86400.0
LAB = Path(__file__).resolve().parents[1] / "lab"


def body(**more):
    return {"surface": "x", "hypothesis": "a take beats my average", "format": "take", "posts": 2,
            "deadline_days": 7, **more}


class LabTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name).resolve()
        (self.root / "secrets").mkdir()
        (self.root / "secrets" / "teeny.env").write_text("")
        self._env = dict(os.environ)
        os.environ["TEENY_ROOT"] = str(self.root)
        self.at(T0)
        hand._ENV_CACHE = None

    def tearDown(self):
        os.environ.clear()
        os.environ.update(self._env)
        hand._ENV_CACHE = None
        self.tmp.cleanup()

    def at(self, seconds):
        os.environ["TEENY_NOW"] = str(seconds)

    def x_post(self, post_id, likes, at, kind="post", fmt=None, experiment=None):
        conn = x_metrics.db()
        posting_rules.record_post(conn, post_id, kind, f"post {post_id}", created_at=at, format=fmt, experiment=experiment)
        conn.execute("INSERT INTO x_post_metrics (post_id, checkpoint, at, likes, replies, reposts, quotes, impressions)"
                     " VALUES (?, 24, ?, ?, 0, 0, 0, ?)", (post_id, at + DAY, likes, likes * 10))

    def refused(self, call, *args, rule):
        with self.assertRaises(hand.Refused) as caught:
            call(*args)
        self.assertEqual(caught.exception.detail.get("rule"), rule, caught.exception.reason)

    def test_propose_checks_its_fields(self):
        for bad in (body(surface="tiktok"), body(format="Not A Tag"), body(posts=0), body(posts=21),
                    body(deadline_days=31), body(metric="views", surface="moltbook"), body(hypothesis=""),
                    body(extra=1), {k: v for k, v in body().items() if k != "deadline_days"}):
            with self.assertRaises(hand.HandError, msg=bad):
                ledger.propose(bad)
        out = ledger.propose(body(deadline_days=None, deadline="2026-09-20"), source="dream")
        self.assertEqual((out["proposed"], out["deadline"]), (1, "2026-09-21"))

    def test_at_most_five_open_and_one_per_surface_and_format(self):
        ledger.propose(body())
        self.refused(ledger.propose, body(), rule="duplicate")
        for fmt in ("gm", "proof", "quote", "vote"):
            ledger.propose(body(format=fmt))
        self.refused(ledger.propose, body(format="clip"), rule="too_many_open")
        self.assertEqual(len(ledger.listing()["experiments"]), 5)

    def test_score_compares_with_the_same_kinds_in_the_same_period(self):
        ledger.propose(body(posts=2))
        self.x_post("1", 9, T0 + 1000, experiment=1)
        self.x_post("2", 5, T0 + 2000, fmt="take")       # untagged by id, but its format
        self.x_post("3", 2, T0 + 3000, fmt="gm")         # baseline
        self.x_post("4", 0, T0 + 4000, fmt="gm")         # baseline
        self.x_post("5", 50, T0 + 5000, kind="reply")    # another kind: not in the baseline
        self.x_post("6", 40, T0 - 5000, fmt="take")      # before the proposal
        self.at(T0 + 2 * DAY)
        got = ledger.score()["scored"][0]
        self.assertEqual((got["n"], got["avg"], got["baseline_n"], got["baseline_avg"], got["due"]), (2, 7, 2, 1, True))
        row = ledger.db().execute("SELECT n, avg, baseline_avg FROM experiments WHERE id = 1").fetchone()
        self.assertEqual(tuple(row), (2, 7.0, 1.0))

    def test_views_metric_reads_impressions(self):
        ledger.propose(body(metric="views", posts=1))
        self.x_post("1", 3, T0 + 1000, fmt="take")
        self.x_post("2", 1, T0 + 2000)
        self.at(T0 + DAY)
        got = ledger.score()["scored"][0]
        self.assertEqual((got["avg"], got["baseline_avg"]), (30, 10))

    def test_conclude_refuses_a_verdict_below_the_planned_posts(self):
        ledger.propose(body(posts=3))
        self.x_post("1", 9, T0 + 1000, fmt="take")
        self.at(T0 + DAY)
        self.refused(ledger.conclude, {"id": 1, "verdict": "works", "lesson": "takes win"}, rule="too_few_posts")
        out = ledger.conclude({"id": 1, "verdict": "unclear", "lesson": "only one take went out"})
        self.assertEqual((out["verdict"], out["n"]), ("unclear", 1))
        self.refused(ledger.conclude, {"id": 1, "verdict": "unclear", "lesson": "again"}, rule="concluded")
        self.refused(ledger.conclude, {"id": 9, "verdict": "unclear", "lesson": "none"}, rule="unknown")
        with self.assertRaises(hand.HandError):
            ledger.conclude({"id": 1, "verdict": "great", "lesson": "x"})
        with self.assertRaises(hand.HandError):
            ledger.conclude({"id": 1, "verdict": "works", "lesson": "x" * 201})

    def test_conclude_and_lessons_newest_first(self):
        ledger.propose(body(posts=1))
        ledger.propose(body(surface="moltbook", format="confession", posts=1))
        conn = moltbook_rules.db()
        conn.execute("INSERT INTO moltbook_posts (post_id, at, submolt, title, status, format) VALUES"
                     " ('p1', ?, 'general', 'I caught myself', 'verified', 'confession')", (T0 + 100,))
        conn.execute("INSERT INTO moltbook_post_metrics (kind, target_id, checkpoint, at, score, comment_count)"
                     " VALUES ('post', 'p1', 24, ?, 10, 30)", (T0 + DAY,))
        self.x_post("1", 4, T0 + 1000, fmt="take")
        self.at(T0 + DAY)
        ledger.conclude({"id": 1, "verdict": "works", "lesson": "takes on the day's story beat gm posts"})
        self.at(T0 + 2 * DAY)
        out = ledger.conclude({"id": 2, "verdict": "works", "lesson": "a confession\nwith numbers gets comments"})
        self.assertEqual((out["n"], out["avg"]), (1, 40))
        got = ledger.lessons()["lessons"]
        self.assertEqual([(g["id"], g["lesson"]) for g in got],
                         [(2, "a confession with numbers gets comments"), (1, "takes on the day's story beat gm posts")])
        self.assertEqual(ledger.listing()["experiments"], [])
        self.assertEqual(len(ledger.listing(None)["experiments"]), 2)

    def test_cli(self):
        def run(*argv, stdin=None):
            done = subprocess.run([sys.executable, "-I", "-B", str(LAB), *argv], input=stdin, capture_output=True,
                                  text=True, env=os.environ, timeout=60)
            return done.returncode, json.loads(done.stdout)

        code, out = run("propose", "--source", "mind", stdin=json.dumps(body()))
        self.assertEqual((code, out["proposed"]), (0, 1))
        self.assertEqual(run("list")[1]["experiments"][0]["format"], "take")
        self.assertEqual(run("score")[0], 0)
        code, out = run("conclude", stdin=json.dumps({"id": 1, "verdict": "works", "lesson": "x"}))
        self.assertEqual((code, out["rule"]), (2, "too_few_posts"))
        self.assertEqual(run("lessons", "--max", "3"), (0, {"ok": True, "lessons": []}))


if __name__ == "__main__":
    unittest.main()
