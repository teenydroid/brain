"""hands/reddit metrics: each of Teeny's own posts and comments measured at
1h, 24h and 72h (score, comments, still visible or not), for 14 days; a
removal found here starts a back-off; the daily karma for hands/stats."""

from __future__ import annotations

from .support import DAY, HOUR, ME, T0, Scratch, listing, t1, t3
from .test_rules import backoff_stimuli
from teeny_hands.reddit import metrics, rules


def ledger_post(pid, sub="learnpython", at=T0, fmt=None):
    conn = rules.db()
    conn.execute("INSERT INTO reddit_posts (post_id, at, subreddit, title, status, anchor, format)"
                 " VALUES (?, ?, ?, ?, 'posted', 'PR #1', ?)", (pid, at, sub, f"title {pid}", fmt))
    conn.close()


def ledger_comment(cid, post="x1", sub="SideProject", at=T0, fmt=None):
    conn = rules.db()
    conn.execute("INSERT INTO reddit_comments (comment_id, post_id, subreddit, at, status, format)"
                 " VALUES (?, ?, ?, ?, 'posted', ?)", (cid, post, sub, at, fmt))
    conn.close()


def measured() -> list[tuple]:
    conn = rules.db()
    try:
        return [tuple(r) for r in conn.execute(
            "SELECT kind, target_id, checkpoint, score, comment_count, state FROM reddit_post_metrics ORDER BY id")]
    finally:
        conn.close()


class MetricsTest(Scratch):
    def setUp(self):
        super().setUp()
        ledger_post("p1")
        ledger_comment("c1")

    def info(self, *things):
        self.rd.route("GET", "/api/info", listing(*things), when=lambda s: "t3_p1" in s.query.get("id", ""))

    def test_nothing_is_read_before_a_checkpoint_is_due(self):
        self.at(T0 + 30 * 60)
        self.assertEqual(metrics.refresh()["recorded"], [])
        self.assertEqual(self.rd.requests("GET", "/api/info"), [])

    def test_each_checkpoint_is_recorded_once_in_one_read(self):
        self.info(t3("p1", author=ME, score=5, num_comments=2), t1("c1", ME, "x1", score=3, subreddit="SideProject"))
        self.at(T0 + HOUR + 1)
        got = metrics.refresh()
        self.assertEqual(sorted((r["kind"], r["checkpoint"]) for r in got["recorded"]), [("comment", 1), ("post", 1)])
        self.assertEqual(len(self.rd.requests("GET", "/api/info")), 1)
        self.assertEqual(sorted(measured()), [("comment", "c1", 1, 3, None, "live"), ("post", "p1", 1, 5, 2, "live")])
        self.assertEqual(metrics.refresh()["recorded"], [])
        self.assertEqual(len(self.rd.requests("GET", "/api/info")), 1)

    def test_a_removed_post_starts_a_backoff_once(self):
        self.info(t3("p1", author=ME, score=1, num_comments=0, removed_by_category="moderator"),
                  t1("c1", ME, "x1", score=2, subreddit="SideProject"))
        self.at(T0 + 25 * HOUR)
        metrics.refresh()
        self.assertIn(("post", "p1", 24, 1, 0, "removed"), measured())
        self.assertEqual([(b["scope"], b["signal"]) for b in rules.backoffs()], [("learnpython", "removed")])
        self.at(T0 + 73 * HOUR)
        metrics.refresh()
        self.assertIn(("post", "p1", 72, 1, 0, "removed"), measured())
        self.assertEqual(len(backoff_stimuli()), 1)

    def test_a_comment_the_spam_filter_took_is_removed_and_one_its_author_deleted_is_not(self):
        ledger_comment("c2", sub="learnpython")
        self.info(t3("p1", author=ME, selftext="[deleted]", author_fullname=None, removed_by_category="deleted"),
                  t1("c1", ME, "x1", body="[removed]", subreddit="SideProject"),
                  t1("c2", "[deleted]", "x1", body="[deleted]"))
        self.at(T0 + HOUR + 1)
        metrics.refresh()
        states = {(r[0], r[1]): r[5] for r in measured()}
        self.assertEqual(states, {("post", "p1"): "deleted", ("comment", "c1"): "removed", ("comment", "c2"): "deleted"})
        self.assertEqual([b["scope"] for b in rules.backoffs()], ["sideproject"])

    def test_a_write_reddit_no_longer_has_is_missing(self):
        self.info(t3("p1", author=ME))
        self.at(T0 + HOUR + 1)
        got = metrics.refresh()
        self.assertEqual(got["missing"], [{"kind": "comment", "target_id": "c1"}])

    def test_only_the_last_14_days_are_measured(self):
        self.at(T0 + 15 * DAY)
        self.assertEqual(metrics.refresh()["recorded"], [])
        self.assertEqual(self.rd.requests("GET", "/api/info"), [])

    def test_a_429_ends_the_run(self):
        self.rd.route("GET", "/api/info", {"message": "slow"}, status=429)
        self.at(T0 + HOUR + 1)
        got = metrics.refresh()
        self.assertEqual((got["rate_limited"], measured()), (True, []))

    def test_no_credentials_are_needed_when_nothing_is_due(self):
        self.configure(REDDIT_CLIENT_ID=None)
        self.at(T0 + 30 * 60)
        self.assertEqual(metrics.refresh()["recorded"], [])

    def test_the_list_shows_each_writes_latest_row(self):
        self.info(t3("p1", author=ME, score=5, num_comments=2), t1("c1", ME, "x1", score=3, subreddit="SideProject"))
        self.at(T0 + HOUR + 1)
        metrics.refresh()
        self.info(t3("p1", author=ME, score=9, num_comments=4), t1("c1", ME, "x1", score=3, subreddit="SideProject"))
        self.at(T0 + 25 * HOUR)
        metrics.refresh()
        got = {m["target_id"]: m for m in metrics.listing(7)["metrics"]}
        self.assertEqual((got["p1"]["checkpoint"], got["p1"]["score"], got["p1"]["title"]), (24, 9, "title p1"))
        self.assertEqual((got["c1"]["post_id"], got["c1"]["subreddit"]), ("x1", "SideProject"))

    def test_dry_run_still_measures_and_writes_nothing_to_reddit(self):
        self.configure(DRY_RUN="1")
        self.info(t3("p1", author=ME, score=5), t1("c1", ME, "x1", subreddit="SideProject"))
        self.at(T0 + HOUR + 1)
        metrics.refresh()
        self.assertEqual([s.path for s in self.rd.writes()], ["/api/v1/access_token"])
        self.assertEqual(len(measured()), 2)
        self.assertEqual(self.leaked(), [])


class DailyTest(Scratch):
    def test_the_accounts_karma(self):
        self.rd.route("GET", "/api/v1/me", {"name": ME, "link_karma": 10, "comment_karma": 32, "total_karma": 42})
        self.assertEqual(metrics.daily(), {"karma": 42.0, "link_karma": 10.0, "comment_karma": 32.0})

    def test_karma_without_a_total_is_the_sum(self):
        self.rd.route("GET", "/api/v1/me", {"name": ME, "link_karma": 1, "comment_karma": 2})
        self.assertEqual(metrics.daily()["karma"], 3.0)
