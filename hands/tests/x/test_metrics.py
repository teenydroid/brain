"""hands/x metrics: the account's own posts and replies measured about 1h,
24h and 72h after they went out, all due in one owned lookup signed as the
account, one row per checkpoint, never fetched again once on record; and
the latest row per post."""

from __future__ import annotations

from .support import HOUR, MINUTE, T0, Scratch
from teeny_hands import hand
from teeny_hands.x import metrics, posting_rules

DAY = 24 * HOUR


def counters(post_id: str, likes: int, impressions: int = 100) -> dict:
    return {"id": post_id, "text": "t", "public_metrics": {
        "like_count": likes, "reply_count": 1, "retweet_count": 2, "quote_count": 0, "impression_count": impressions}}


class MetricsTest(Scratch):
    def own(self, post_id: str, at: float = T0, kind: str = "post", fmt: str | None = None) -> None:
        conn = hand.db()
        posting_rules.ensure_schema(conn)
        posting_rules.record_post(conn, post_id, kind, f"text of {post_id}", created_at=at, format=fmt)

    def x_says(self, *posts: dict) -> None:
        self.x.route("GET", "/2/tweets", {"data": list(posts)})

    def rows(self) -> list[tuple]:
        return [tuple(r) for r in metrics.db().execute(
            "SELECT post_id, checkpoint, likes, replies, reposts, quotes, impressions FROM x_post_metrics ORDER BY id")]

    def test_each_post_is_measured_once_at_each_checkpoint_in_one_owned_lookup(self):
        self.own("1")
        self.own("2", at=T0 + 10 * MINUTE, kind="reply")
        self.x_says(counters("1", 5), counters("2", 1))
        self.at(T0 + 30 * MINUTE)
        self.assertEqual(metrics.refresh()["recorded"], [])
        self.assertEqual(self.x.requests("GET", "/2/tweets"), [])

        self.at(T0 + 75 * MINUTE)
        out = metrics.refresh()
        self.assertEqual(out["recorded"], [{"id": "2", "checkpoint": 1}, {"id": "1", "checkpoint": 1}])
        seen = self.x.requests("GET", "/2/tweets")
        self.assertEqual(len(seen), 1)
        self.assertEqual(seen[0].query["ids"], "2,1")
        self.assertTrue(seen[0].is_oauth(), "an owned read is signed as the account")
        self.assertEqual(self.rows(), [("2", 1, 1, 1, 2, 0, 100), ("1", 1, 5, 1, 2, 0, 100)])

        self.at(T0 + 2 * HOUR)
        metrics.refresh()
        self.assertEqual(len(self.x.requests("GET", "/2/tweets")), 1)

        self.x_says(counters("1", 9, 300), counters("2", 1))
        for hours in (25, 73, 100):
            self.at(T0 + hours * HOUR)
            metrics.refresh()
        self.assertEqual(sorted(r[1] for r in self.rows() if r[0] == "1"), [1, 24, 72])
        self.assertEqual(len(self.x.requests("GET", "/2/tweets")), 3)

    def test_a_late_run_records_only_the_latest_checkpoint_due(self):
        self.own("1")
        self.x_says(counters("1", 3))
        self.at(T0 + 30 * HOUR)
        self.assertEqual(metrics.refresh()["recorded"], [{"id": "1", "checkpoint": 24}])

    def test_a_post_x_no_longer_has_is_missing_and_not_asked_again_at_that_checkpoint(self):
        self.own("1")
        self.x_says()
        self.at(T0 + 2 * HOUR)
        self.assertEqual(metrics.refresh()["missing"], ["1"])
        metrics.refresh()
        self.assertEqual(len(self.x.requests("GET", "/2/tweets")), 1)
        self.assertEqual(self.rows(), [("1", 1, None, None, None, None, None)])

    def test_posts_older_than_the_window_are_left_alone(self):
        self.own("1", at=T0 - 15 * DAY)
        self.at(T0)
        self.assertEqual(metrics.refresh(), {"recorded": [], "missing": [], "left": 0, "rate_limited": False})

    def test_at_most_max_fetches_a_run(self):
        for n in range(metrics.MAX_FETCHES + 3):
            self.own(str(n + 1), at=T0 + n)
        self.x_says()
        self.at(T0 + 2 * HOUR)
        out = metrics.refresh()
        self.assertEqual(out["left"], 3)
        self.assertEqual(len(self.x.requests("GET", "/2/tweets")[0].query["ids"].split(",")), metrics.MAX_FETCHES)

    def test_a_429_records_nothing(self):
        self.own("1")
        self.x.route("GET", "/2/tweets", {"title": "Too Many Requests"}, status=429)
        self.at(T0 + 2 * HOUR)
        self.assertTrue(metrics.refresh()["rate_limited"])
        self.assertEqual(self.rows(), [])

    def test_list_gives_the_latest_row_with_kind_and_format(self):
        self.own("1", fmt="take")
        self.x_says(counters("1", 4))
        self.at(T0 + 2 * HOUR)
        metrics.refresh()
        got = metrics.listing(7)["metrics"]
        self.assertEqual([(m["post_id"], m["kind"], m["format"], m["likes"], m["impressions"]) for m in got],
                         [("1", "post", "take", 4, 100)])
