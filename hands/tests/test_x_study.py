"""teeny_hands/stats/x_study: what worked on X for the dream, counted from
x_posts and their newest x_post_metrics row: groups by kind and producer and
by format, the best and worst originals, and the replies' reach."""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

from x import support  # noqa: F401  (puts hands/lib on the path)
from teeny_hands import hand
from teeny_hands.stats import x_study
from teeny_hands.x import mentions as x_mentions
from teeny_hands.x import metrics as x_metrics
from teeny_hands.x import posting_rules

T0 = 1_789_369_200.0  # 2026-09-14T07:00:00Z
DAY = 86400.0


class XStudyTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name).resolve()
        (self.root / "secrets").mkdir()
        (self.root / "secrets" / "teeny.env").write_text("")
        self._env = dict(os.environ)
        os.environ["TEENY_ROOT"] = str(self.root)
        os.environ["TEENY_NOW"] = str(T0 + 10 * DAY)
        hand._ENV_CACHE = None
        self.conn = x_metrics.db()
        self.conn.executescript(x_mentions.SCHEMA)

    def tearDown(self):
        self.conn.close()
        os.environ.clear()
        os.environ.update(self._env)
        hand._ENV_CACHE = None
        self.tmp.cleanup()

    def post(self, post_id, likes, views, at, kind="post", producer=None, fmt=None, to=None, checkpoints=(24,)):
        mention = None
        if to:
            mention = f"m{post_id}"
            self.conn.execute("INSERT INTO x_mentions (id, author_id, author_username, created_at, text, post, status,"
                              " seen_at, updated_at) VALUES (?, 'a', ?, '2026-09-14', 'hi', '{}', 'answered', ?, ?)",
                              (mention, to, at, at))
        posting_rules.record_post(self.conn, post_id, kind, f"words of {post_id}", in_reply_to=mention, created_at=at,
                                  format=fmt, producer=producer)
        for n, checkpoint in enumerate(checkpoints):
            self.conn.execute("INSERT INTO x_post_metrics (post_id, checkpoint, at, likes, replies, reposts, quotes,"
                              " impressions) VALUES (?, ?, ?, ?, 1, 0, 0, ?)",
                              (post_id, checkpoint, at + DAY, likes if n == len(checkpoints) - 1 else 0, views))
        self.conn.commit()

    def test_the_dream_sees_x_by_kind_format_best_worst_and_reach(self):
        now = T0 + 10 * DAY
        self.post("1", 30, 400, now - 1 * DAY, producer="studio", fmt="image", checkpoints=(1, 24))
        self.post("2", 1, 50, now - 2 * DAY, producer="studio", fmt="post")
        self.post("3", 9, 200, now - 10 * DAY, fmt="proof")  # 14 days only
        for n, (who, day) in enumerate([("ana", 1), ("ana", 2), ("bo", 1), ("ana", 1)]):
            self.post(f"r{n}", 0, 10, now - day * DAY - n, kind="reply", to=who)
        lines = x_study.summary_lines()
        text = "\n".join(lines)
        self.assertIn("X by kind/producer, last 7d: post/studio n=2 avg views 225 eng 16.5", text)
        self.assertIn("X by kind/producer, last 7d: reply/legacy n=4", text)
        self.assertIn("X originals by format, last 7d: image n=1 avg views 400 eng 31", text)
        self.assertIn("X originals by format, last 7d: post n=1", text)
        self.assertIn("X originals by format, last 14d: image n=1", text)
        self.assertIn("X originals by format, last 14d: proof n=1 avg views 200 eng 10", text)
        best = [l for l in lines if l.startswith("X best original")]
        worst = [l for l in lines if l.startswith("X worst original")]
        self.assertEqual(len(best), 3)
        self.assertIn('31 eng, 400 views (09-23, studio, image): "words of 1"', best[0])
        self.assertEqual(worst, [])  # three originals: all are the best three
        self.assertIn("X replies, last 7d: 4 measured to 2 accounts; 1 regulars (answered on 2+ days): @ana", text)

    def test_a_losing_format_is_kept_when_there_are_many(self):
        # Live shape: one joined line per table ran past the 300-character clip,
        # and the losing formats, sorted last, were the ones cut.
        now = T0 + 10 * DAY
        for n in range(1, 9):
            self.post(f"f{n}", n, n * 10, now - DAY, fmt=f"format{n}")
        lines = x_study.summary_lines()
        originals = [l for l in lines if l.startswith("X originals by format, last 7d:")]
        self.assertEqual(len(originals), x_study.GROUPS_SHOWN)
        self.assertIn("X originals by format, last 7d: format8 n=1", originals[0])
        self.assertIn("X originals by format, last 7d: format1 n=1", originals[-1])
        self.assertTrue(all(len(l) < 300 for l in lines))

    def test_the_reply_reach_comes_before_the_ranked_examples(self):
        # A section is cut from its end: the regulars must sit above the examples.
        now = T0 + 10 * DAY
        for n in range(4):
            self.post(f"p{n}", 5, 50, now - (n + 1) * DAY, fmt="post")
        self.post("r1", 0, 10, now - DAY, kind="reply", to="ana")
        self.post("r2", 0, 10, now - 2 * DAY, kind="reply", to="ana")
        lines = x_study.summary_lines()
        reach = next(i for i, l in enumerate(lines) if l.startswith("X replies, last 7d"))
        best = next(i for i, l in enumerate(lines) if l.startswith("X best original"))
        self.assertLess(reach, best)

    def test_the_window_ends_at_now_so_later_posts_stay_out(self):
        # The dream runs days after the study's `now`: a post made since must not join either window.
        now = T0 + 10 * DAY
        self.post("1", 4, 40, now - DAY, fmt="post")
        self.post("later", 90, 900, now + DAY, fmt="post")
        got = x_study.summary(now)
        self.assertEqual([w["id"] for w in got["windows"][7]], ["1"])

    def test_nothing_measured_is_no_lines(self):
        self.assertEqual(x_study.summary_lines(), [])


if __name__ == "__main__":
    unittest.main()
