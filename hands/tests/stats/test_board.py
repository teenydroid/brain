"""hands/stats board: audience with its 1-day and 7-day change, the best and
worst own writes per surface with their format, and the average result per
format, all computed from the ledgers; three short lines for a prompt."""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

from x import support  # noqa: F401  (puts hands/lib on the path)
from teeny_hands import hand
from teeny_hands.moltbook import rules as moltbook_rules
from teeny_hands.stats import board, collect
from teeny_hands.x import metrics as x_metrics
from teeny_hands.x import posting_rules

T0 = 1_789_369_200.0  # 2026-09-14T07:00:00Z
DAY = 86400.0


class BoardTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name).resolve()
        (self.root / "secrets").mkdir()
        (self.root / "secrets" / "teeny.env").write_text("")
        self._env = dict(os.environ)
        os.environ["TEENY_ROOT"] = str(self.root)
        os.environ["TEENY_NOW"] = str(T0)
        hand._ENV_CACHE = None

    def tearDown(self):
        os.environ.clear()
        os.environ.update(self._env)
        hand._ENV_CACHE = None
        self.tmp.cleanup()

    def stat(self, date, source, metric, value):
        collect.db().execute("INSERT INTO stats (date, source, metric, value, at) VALUES (?, ?, ?, ?, 0)",
                             (date, source, metric, value))

    def x_post(self, post_id, likes, kind="post", fmt=None, at=T0 - DAY, impressions=None):
        conn = x_metrics.db()
        posting_rules.record_post(conn, post_id, kind, f"post {post_id}", created_at=at, format=fmt)
        conn.execute("INSERT INTO x_post_metrics (post_id, checkpoint, at, likes, replies, reposts, quotes, impressions)"
                     " VALUES (?, 24, ?, ?, 1, 0, 0, ?)", (post_id, at + DAY, likes, impressions))

    def test_an_empty_ledger_says_so(self):
        out = board.board()
        self.assertEqual(out["audience"], [])
        self.assertEqual(out["x"]["measured"], 0)
        self.assertEqual(out["lines"], ["Audience: no snapshot yet.", "X last 7d: nothing measured yet.",
                                        "Moltbook last 7d: nothing measured yet."])

    def test_audience_changes_over_one_and_seven_days(self):
        self.stat("2026-09-06", "x", "followers", 300)
        self.stat("2026-09-13", "x", "followers", 330)
        self.stat("2026-09-14", "x", "followers", 334)
        self.stat("2026-09-14", "moltbook", "karma", 19)
        out = board.board()
        self.assertEqual(out["audience"], [
            {"metric": "x.followers", "value": 334, "date": "2026-09-14", "d1": 4, "d7": 34},
            {"metric": "moltbook.karma", "value": 19, "date": "2026-09-14"},
        ])
        self.assertEqual(out["lines"][0], "Audience: x.followers 334 (d1 +4, d7 +34); moltbook.karma 19")

    def test_best_worst_and_formats_on_x(self):
        for n, (likes, fmt) in enumerate([(9, "take"), (3, "take"), (0, "gm"), (1, None), (5, "gm"), (2, None), (7, "proof")]):
            self.x_post(str(n + 1), likes, kind="reply" if fmt is None else "post", fmt=fmt, impressions=100 * (n + 1))
        self.x_post("99", 50, at=T0 - 9 * DAY)  # outside the window
        out = board.board(7)["x"]
        self.assertEqual(out["measured"], 7)
        # result = likes + replies + reposts + quotes; every row has 1 reply.
        self.assertEqual([w["id"] for w in out["best"]], ["1", "7", "5", "2", "6"])
        self.assertEqual([w["id"] for w in out["worst"]], ["3", "4"])
        self.assertEqual(out["best"][0]["format"], "take")
        self.assertEqual(out["formats"], [
            {"format": "proof", "n": 1, "avg": 8, "avg_views": 700},
            {"format": "take", "n": 2, "avg": 7, "avg_views": 150},
            {"format": "gm", "n": 2, "avg": 3.5, "avg_views": 400},
            {"format": "(reply)", "n": 2, "avg": 2.5, "avg_views": 500},
        ])
        line = board.board(7)["lines"][1]
        self.assertTrue(line.startswith("X last 7d: 7 measured, avg result 4.9; best take 10"), line)
        self.assertLessEqual(len(line), board.LINE_CHARS)

    def test_moltbook_posts_and_comments(self):
        conn = moltbook_rules.db()
        conn.execute("INSERT INTO moltbook_posts (post_id, at, submolt, title, status, format)"
                     " VALUES ('p1', ?, 'general', 'I caught myself', 'verified', 'confession')", (T0 - DAY,))
        conn.execute("INSERT INTO moltbook_comments (comment_id, post_id, at, status, body)"
                     " VALUES ('c1', 'z', ?, 'verified', 'a reply')", (T0 - DAY,))
        for kind, target, score, comments in (("post", "p1", 12, 30), ("comment", "c1", 1, 0)):
            conn.execute("INSERT INTO moltbook_post_metrics (kind, target_id, checkpoint, at, score, comment_count)"
                         " VALUES (?, ?, 24, ?, ?, ?)", (kind, target, T0, score, comments))
        out = board.board()["moltbook"]
        self.assertEqual([(w["id"], w["kind"], w["result"]) for w in out["best"]], [("p1", "post", 42), ("c1", "comment", 1)])
        self.assertEqual([g["format"] for g in out["formats"]], ["confession", "(comment)"])

    def test_an_old_ledger_without_tag_columns_still_reads(self):
        conn = hand.db()
        conn.executescript(
            "CREATE TABLE x_posts (id TEXT PRIMARY KEY, kind TEXT NOT NULL, text TEXT NOT NULL, in_reply_to TEXT,"
            " quote_of TEXT, media TEXT NOT NULL DEFAULT '[]', created_at REAL NOT NULL);"
            f"INSERT INTO x_posts (id, kind, text, created_at) VALUES ('7', 'post', 'old', {T0 - DAY});"
            "CREATE TABLE x_post_metrics (id INTEGER PRIMARY KEY, post_id TEXT, checkpoint INTEGER, at REAL, likes INTEGER,"
            " replies INTEGER, reposts INTEGER, quotes INTEGER, impressions INTEGER, bookmarks INTEGER);"
            f"INSERT INTO x_post_metrics (post_id, checkpoint, at, likes) VALUES ('7', 24, {T0}, 2);"
        )
        conn.close()
        self.assertEqual(board.board()["x"]["formats"], [{"format": "(post)", "n": 1, "avg": 2}])


if __name__ == "__main__":
    unittest.main()
