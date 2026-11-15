"""hands/reddit study: per allowlisted subreddit, the week's top posts and
what Teeny's own writes there scored, by kind and format. Deterministic,
and its reading of Reddit cached six hours."""

from __future__ import annotations

from .support import DAY, HOUR, ME, T0, Scratch, listing, t3
from .test_metrics import ledger_comment, ledger_post
from teeny_hands.reddit import rules, study


def metric(kind, target, checkpoint, score, state="live", comments=None):
    conn = rules.db()
    conn.execute("INSERT INTO reddit_post_metrics (kind, target_id, checkpoint, at, score, comment_count, state)"
                 " VALUES (?, ?, ?, ?, ?, ?, ?)", (kind, target, checkpoint, T0, score, comments, state))
    conn.close()


class StudyTest(Scratch):
    def setUp(self):
        super().setUp()
        week = lambda s: s.query.get("t") == "week"  # noqa: E731
        self.rd.route("GET", "/r/learnpython/top", listing(
            t3("a", title="small win", score=50, num_comments=4, created_utc=T0 - 2 * DAY),
            t3("b", title="big win", score=120, num_comments=30, created_utc=T0 - 3 * DAY),
        ), when=week)
        self.rd.route("GET", "/r/sideproject/top", listing(t3("s", subreddit="SideProject", score=7)), when=week)
        ledger_post("p1", at=T0 - 3 * DAY, fmt="show")
        ledger_post("p2", at=T0 - 2 * DAY, fmt="show")
        ledger_comment("c1", sub="learnpython", at=T0 - DAY)
        ledger_comment("c2", sub="learnpython", at=T0 - DAY, fmt="answer")
        metric("post", "p1", 24, 10, comments=3)
        metric("post", "p1", 72, 14, comments=5)
        metric("post", "p2", 24, 2, state="removed")
        metric("comment", "c1", 24, 4)
        metric("comment", "c2", 24, 6)

    def test_the_weeks_top_posts_per_subreddit(self):
        got = study.study()["subreddits"]
        self.assertEqual(sorted(got), ["learnpython", "sideproject"])
        top = got["learnpython"]["top_week"]
        self.assertEqual([(p["id"], p["title"], p["score"], p["num_comments"], p["age_hours"]) for p in top],
                         [("b", "big win", 120, 30, 72.0), ("a", "small win", 50, 4, 48.0)])

    def test_own_writes_by_kind_and_format(self):
        own = study.study()["subreddits"]["learnpython"]["own"]
        self.assertEqual(
            [(g["kind"], g["format"], g["n"], g["median_score"], g["best_score"], g["removed"]) for g in own],
            [("comment", "answer", 1, 6, 6, 0), ("comment", None, 1, 4, 4, 0), ("post", "show", 2, 8, 14, 1)],
        )
        self.assertEqual(study.study()["subreddits"]["sideproject"]["own"], [])

    def test_it_is_cached_six_hours_and_deterministic(self):
        first = study.study()
        self.assertEqual(study.study(), first)
        self.assertEqual(len(self.rd.requests("GET", "/r/learnpython/top")), 1)
        self.at(T0 + 7 * HOUR)
        study.study()
        self.assertEqual(len(self.rd.requests("GET", "/r/learnpython/top")), 2)

    def test_a_429_answers_what_it_has_and_caches_nothing(self):
        self.rd.route("GET", "/r/sideproject/top", {"message": "slow"}, status=429)
        got = study.study()
        self.assertTrue(got["rate_limited"])
        study.study()
        self.assertEqual(len(self.rd.requests("GET", "/r/sideproject/top")), 2)

    def test_an_empty_allowlist_studies_nothing(self):
        self.configure(REDDIT_SUBREDDITS="")
        self.assertEqual(study.study()["subreddits"], {})
        self.assertEqual(self.rd.requests("GET"), [])
