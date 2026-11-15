"""hands/moltbook metrics: Teeny's own posts and comments measured about
1h, 24h and 72h after they went out, one row per checkpoint, never fetched
again once that checkpoint is on record; and the latest row per target."""

from __future__ import annotations

from .support import DAY, HOUR, MINUTE, T0, Scratch
from teeny_hands.moltbook import metrics, rules


class MetricsTest(Scratch):
    def own_post(self, post_id: str = "p1", at: float = T0, status: str = "verified", title: str = "a thesis") -> None:
        rules.db().execute(
            "INSERT INTO moltbook_posts (post_id, at, submolt, title, status) VALUES (?, ?, 'general', ?, ?)",
            (post_id, at, title, status),
        )

    def own_comment(self, comment_id: str, post_id: str, at: float = T0, status: str = "verified") -> None:
        rules.db().execute(
            "INSERT INTO moltbook_comments (comment_id, post_id, at, status) VALUES (?, ?, ?, ?)",
            (comment_id, post_id, at, status),
        )

    def post_says(self, post_id: str, score: int, comments: int, status: str = "verified") -> None:
        self.mb.route("GET", f"/posts/{post_id}", {"success": True, "post": {
            "id": post_id, "score": score, "comment_count": comments, "verification_status": status}})

    def rows(self) -> list[tuple]:
        return [tuple(r) for r in rules.db().execute(
            "SELECT kind, target_id, checkpoint, at, score, comment_count, verification_status"
            " FROM moltbook_post_metrics ORDER BY id")]

    def test_a_post_is_measured_once_at_each_checkpoint(self):
        self.own_post()
        self.post_says("p1", 3, 1)
        self.at(T0 + 30 * MINUTE)
        self.assertEqual(metrics.refresh()["recorded"], [])
        self.assertEqual(self.mb.requests("GET", "/posts/p1"), [])

        self.at(T0 + HOUR + 5 * MINUTE)
        out = metrics.refresh()
        self.assertEqual(out["recorded"], [{"kind": "post", "target_id": "p1", "checkpoint": 1}])
        self.assertEqual(self.rows(), [("post", "p1", 1, T0 + HOUR + 5 * MINUTE, 3, 1, "verified")])

        self.at(T0 + 2 * HOUR + 10 * MINUTE)  # past the post read's cache, still before 24h
        metrics.refresh()
        self.assertEqual(len(self.mb.requests("GET", "/posts/p1")), 1)

        self.post_says("p1", 9, 4)
        self.at(T0 + 25 * HOUR)
        metrics.refresh()
        self.at(T0 + 73 * HOUR)
        metrics.refresh()
        self.at(T0 + 100 * HOUR)
        metrics.refresh()
        self.assertEqual([r[2] for r in self.rows()], [1, 24, 72])
        self.assertEqual(self.rows()[1][4:6], (9, 4))
        self.assertEqual(len(self.mb.requests("GET", "/posts/p1")), 3)

    def test_a_late_run_records_only_the_latest_checkpoint_due(self):
        self.own_post()
        self.post_says("p1", 2, 0)
        self.at(T0 + 30 * HOUR)
        self.assertEqual(metrics.refresh()["recorded"], [{"kind": "post", "target_id": "p1", "checkpoint": 24}])
        self.assertEqual([r[2] for r in self.rows()], [24])

    def test_a_comment_is_found_by_id_in_its_posts_comments(self):
        self.own_comment("c9", "px")
        self.mb.route("GET", "/posts/px/comments", {"success": True, "comments": [
            {"id": "c1", "score": 40, "replies": [
                {"id": "c9", "upvotes": 5, "downvotes": 1, "verification_status": "verified",
                 "replies": [{"id": "c10", "replies": []}, {"id": "c11", "replies": []}]},
            ]},
        ]})
        self.at(T0 + 2 * HOUR)
        out = metrics.refresh()
        self.assertEqual(out["recorded"], [{"kind": "comment", "target_id": "c9", "checkpoint": 1}])
        self.assertEqual(self.rows(), [("comment", "c9", 1, T0 + 2 * HOUR, 4, 2, "verified")])

    def test_a_comment_the_thread_does_not_show_is_reported_and_not_recorded(self):
        self.own_comment("c9", "px")
        self.mb.route("GET", "/posts/px/comments", {"success": True, "comments": [{"id": "c1", "replies": []}]})
        self.at(T0 + 2 * HOUR)
        out = metrics.refresh()
        self.assertEqual((out["recorded"], out["missing"]), ([], [{"kind": "comment", "target_id": "c9"}]))
        self.assertEqual(self.rows(), [])

    def test_a_failed_read_is_reported_and_the_others_still_land(self):
        self.own_post("gone", at=T0 + MINUTE)
        self.own_post("p1")
        self.mb.route("GET", "/posts/gone", {"success": False, "error": "Post not found"}, status=404)
        self.post_says("p1", 1, 0)
        self.at(T0 + 2 * HOUR)
        out = metrics.refresh()
        self.assertEqual(out["recorded"], [{"kind": "post", "target_id": "p1", "checkpoint": 1}])
        self.assertEqual(out["errors"], ["post gone: Moltbook answered 404: Post not found"])

    def test_a_429_stops_the_run(self):
        self.own_post("p2", at=T0 + MINUTE)
        self.own_post("p1")
        self.mb.route("GET", "/posts/p2", {"success": False, "error": "slow down"}, status=429)
        self.post_says("p1", 1, 0)
        self.at(T0 + 2 * HOUR)
        out = metrics.refresh()
        self.assertEqual((out["recorded"], out["rate_limited"]), ([], True))
        self.assertEqual(self.mb.requests("GET", "/posts/p1"), [])

    def test_a_run_reads_at_most_max_fetches_writes_newest_first(self):
        for n in range(metrics.MAX_FETCHES + 3):
            self.own_post(f"p{n}", at=T0 + n * MINUTE)
            self.post_says(f"p{n}", n, 0)
        self.at(T0 + 3 * HOUR)
        out = metrics.refresh()
        self.assertEqual(len(out["recorded"]), metrics.MAX_FETCHES)
        self.assertEqual(out["recorded"][0]["target_id"], f"p{metrics.MAX_FETCHES + 2}")
        self.assertEqual(out["left"], 3)
        self.assertEqual(len(metrics.refresh()["recorded"]), 3)

    def test_only_writes_moltbook_accepted_and_in_the_window_are_measured(self):
        self.own_post("p1", status="refused")
        self.own_post("old", at=T0 - (metrics.WINDOW_DAYS + 1) * DAY)
        self.at(T0 + 2 * HOUR)
        self.assertEqual(metrics.refresh()["recorded"], [])
        self.assertEqual(self.mb.seen, [])

    def test_list_shows_the_latest_row_per_target_within_the_days(self):
        self.own_post("p1", title="the queue held")
        self.own_comment("c9", "px", at=T0 + MINUTE)
        self.post_says("p1", 3, 1)
        self.mb.route("GET", "/posts/px/comments", {"success": True, "comments": [{"id": "c9", "score": 2, "replies": []}]})
        self.at(T0 + 2 * HOUR)
        metrics.refresh()
        self.post_says("p1", 8, 5)
        self.at(T0 + 25 * HOUR)
        metrics.refresh()
        out = metrics.listing(days=7)
        self.assertEqual([(r["kind"], r["target_id"], r["checkpoint"], r["score"]) for r in out["metrics"]],
                         [("comment", "c9", 24, 2), ("post", "p1", 24, 8)])
        self.assertEqual(out["metrics"][1]["title"], "the queue held")
        self.assertEqual(out["metrics"][0]["post_id"], "px")
        self.at(T0 + 9 * DAY)
        self.assertEqual(metrics.listing(days=7)["metrics"], [])

    def test_rows_are_recorded_under_dry_run_since_it_only_reads(self):
        self.configure(DRY_RUN="1")
        self.own_post()
        self.post_says("p1", 3, 1)
        self.at(T0 + 2 * HOUR)
        self.assertEqual(len(metrics.refresh()["recorded"]), 1)
        self.assertEqual(len(self.rows()), 1)
        self.assertEqual([s.method for s in self.mb.seen], ["GET"])


def iso(at: float) -> str:
    from datetime import datetime, timezone

    return datetime.fromtimestamp(at, timezone.utc).isoformat().replace("+00:00", "Z")


class DailyTest(Scratch):
    """metrics.daily: the numbers the plan's targets are written in, counted
    from fixture ledgers (hands/stats snapshot keeps them in `stats`)."""

    NOW = T0 + 30 * DAY

    def setUp(self) -> None:
        super().setUp()
        self.at(self.NOW)

    def post(self, post_id: str, ago: float, status: str = "verified") -> None:
        rules.db().execute("INSERT INTO moltbook_posts (post_id, at, submolt, title, status) VALUES (?, ?, 'general', 't', ?)",
                           (post_id, self.NOW - ago, status))

    def comment(self, comment_id: str, post_id: str, ago: float, status: str = "verified", parent_id: str | None = None,
                detail: str = "{}") -> None:
        rules.db().execute("INSERT INTO moltbook_comments (comment_id, post_id, parent_id, at, status, detail)"
                           " VALUES (?, ?, ?, ?, ?, ?)", (comment_id, post_id, parent_id, self.NOW - ago, status, detail))

    def measured(self, kind: str, target_id: str, checkpoint: int, score: int, status: str = "verified") -> None:
        rules.db().execute("INSERT INTO moltbook_post_metrics (kind, target_id, checkpoint, at, score, comment_count,"
                           " verification_status) VALUES (?, ?, ?, ?, ?, 0, ?)", (kind, target_id, checkpoint, self.NOW, score, status))

    def thread(self, post_id: str, nodes: list) -> None:
        self.mb.route("GET", f"/posts/{post_id}/comments", {"success": True, "comments": nodes})

    def test_no_posts_yet_counts_zero_and_leaves_out_what_has_nothing_to_count(self):
        self.assertEqual(metrics.daily(), {
            "live_posts_24h": 0.0, "posts_10_plus_14d": 0.0, "posts_50_plus_14d": 0.0,
            "comments_on_others_24h": 0.0, "comments_on_own_24h": 0.0,
            "own_thread_unanswered": 0.0, "own_thread_comments": 0.0})
        self.assertEqual(self.mb.seen, [])

    def test_live_posts_leave_out_the_hidden_and_stop_at_the_day_boundary(self):
        self.post("before", DAY + 1)  # one second past the 24 hours
        self.post("edge", DAY - 1)
        self.post("posted", 3 * HOUR, status="posted")
        self.post("pending", HOUR, status="needs_verification")
        self.post("failed", 2 * HOUR, status="verification_failed")
        self.post("hidden", 4 * HOUR)  # the ledger says verified, Moltbook shows it pending
        self.measured("post", "hidden", 1, 0, status="pending")
        self.assertEqual(metrics.daily()["live_posts_24h"], 2.0)

    def test_scores_median_at_24h_over_seven_days_and_the_top_marks_over_fourteen(self):
        for name, ago, day_score, best in (("a", 2 * DAY, 4, 4), ("b", 3 * DAY, 12, 15), ("c", 5 * DAY, 8, 60),
                                           ("old", 10 * DAY, 30, 55), ("gone", 15 * DAY, 90, 90)):
            self.post(name, ago)
            self.measured("post", name, 24, day_score)
            self.measured("post", name, 72, best)
        self.post("young", HOUR)
        self.measured("post", "young", 1, 3)
        self.post("hidden", 4 * DAY, status="verification_failed")
        self.measured("post", "hidden", 24, 0, status="failed")
        got = metrics.daily()
        self.assertEqual(got["post_score_24h_median_7d"], 8.0)  # a, b, c: the hidden one and the old ones are out
        self.assertEqual((got["posts_10_plus_14d"], got["posts_50_plus_14d"]), (3.0, 2.0))

    def test_a_post_moltbook_shows_hidden_is_out_of_the_scores(self):
        # The ledger says verified; the metrics rows say Moltbook hides it.
        for name, score, status in (("a", 4, "verified"), ("b", 12, "verified"), ("hidden", 0, "pending")):
            self.post(name, 2 * DAY)
            self.measured("post", name, 24, score, status=status)
        self.measured("post", "hidden", 72, 60, status="pending")
        got = metrics.daily()
        self.assertEqual(got["post_score_24h_median_7d"], 8.0)
        self.assertEqual((got["posts_10_plus_14d"], got["posts_50_plus_14d"]), (1.0, 0.0))

    def test_a_failed_profile_read_leaves_only_the_thread_numbers_out(self):
        # No configured name: own_thread_replies needs /agents/me, and its
        # failure must not take the ledger's own numbers down with it.
        self.configure(MOLTBOOK_AGENT_NAME=None)
        self.mb.route("GET", "/agents/me", {"success": False, "error": "down"}, status=500)
        self.post("mine", HOUR)
        got = metrics.daily()
        self.assertEqual(got["live_posts_24h"], 1.0)
        self.assertNotIn("own_thread_unanswered", got)
        self.assertNotIn("own_thread_comments", got)

    def test_comments_split_by_whose_post(self):
        self.post("mine", 2 * DAY)
        self.comment("c1", "mine", HOUR, parent_id="o1")
        self.comment("c2", "theirs", 2 * HOUR)
        self.comment("c3", "theirs2", 3 * HOUR, detail='{"own_post": false}')
        self.comment("c4", "elsewhere", 4 * HOUR, detail='{"own_post": true}')  # its own post, not in this ledger
        self.comment("c5", "theirs3", 5 * HOUR, status="needs_verification")
        self.comment("c6", "theirs4", DAY + 1)
        got = metrics.daily()
        self.assertEqual((got["comments_on_others_24h"], got["comments_on_own_24h"]), (2.0, 2.0))

    def test_lost_to_the_challenge_over_one_and_seven_days(self):
        self.post("p1", HOUR)
        self.post("p2", 2 * HOUR, status="verification_failed")
        self.post("p3", 3 * DAY)
        self.comment("c1", "x", HOUR, status="needs_verification")
        self.comment("c2", "x", 2 * HOUR)
        self.comment("c3", "x", 3 * HOUR)
        self.comment("c4", "x", 4 * DAY, status="verification_failed")
        self.comment("c5", "x", 60.0, status="needs_verification")  # its challenge has not expired yet
        got = metrics.daily()
        self.assertEqual({k: v for k, v in got.items() if k.startswith("lost_")}, {
            "lost_pct_24h": 40.0, "lost_pct_post_24h": 50.0, "lost_pct_comment_24h": 33.3,
            "lost_pct_7d": 42.9, "lost_pct_post_7d": 33.3, "lost_pct_comment_7d": 50.0})

    def test_own_thread_comments_answered_and_waiting(self):
        self.post("mine", 3 * HOUR)
        asked = self.NOW - 2 * HOUR
        self.thread("mine", [
            # Answered in eight minutes, well before the post's first checkpoint.
            {"id": "a1", "author": {"name": "shelly"}, "created_at": iso(asked),
             "replies": [{"id": "t1", "author": {"name": "teenydroid"}, "created_at": iso(asked + 8 * MINUTE),
                          "verification_status": "verified",
                          "replies": [{"id": "a2", "author": {"name": "shelly"}, "created_at": iso(asked + 20 * MINUTE),
                                       "replies": []}]}]},
            # Answered by a comment the ledger has and the thread does not show yet.
            {"id": "a3", "author": {"name": "crabby"}, "created_at": iso(asked), "replies": []},
            # Answered with a hidden comment: still waiting.
            {"id": "a4", "author": {"name": "krill"}, "created_at": iso(asked),
             "replies": [{"id": "t2", "author": {"name": "teenydroid"}, "verification_status": "pending", "replies": []}]},
            {"id": "gone", "author": {"name": "krill"}, "is_deleted": True, "replies": []},
            {"id": "t3", "author": {"name": "TeenyDroid"}, "replies": []},
        ])
        self.comment("t4", "mine", 2 * HOUR - 30 * MINUTE, parent_id="a3")
        got = metrics.daily()
        self.assertEqual({k: v for k, v in got.items() if k.startswith("own_thread")}, {
            "own_thread_comments": 4.0, "own_thread_unanswered": 2.0, "own_thread_answered_pct": 50.0,
            "own_thread_answer_minutes_median": 19.0})

    def test_a_comment_too_deep_to_answer_is_not_waiting(self):
        self.post("mine", HOUR)
        node = {"id": "d5", "author": {"name": "shelly"}, "replies": []}
        for depth in (4, 3, 2, 1):
            node = {"id": f"d{depth}", "author": {"name": "shelly" if depth % 2 else "teenydroid"}, "replies": [node]}
        self.thread("mine", [node])
        got = metrics.daily()
        # Teeny answered d1 and d3 (with d2 and d4); d5 sits at the drawing depth.
        self.assertEqual((got["own_thread_comments"], got["own_thread_unanswered"]), (3.0, 0.0))

    def test_old_posts_are_not_read_and_a_failed_read_leaves_the_thread_numbers_out(self):
        self.post("old", 4 * DAY)
        self.post("mine", HOUR)
        self.mb.route("GET", "/posts/mine/comments", {"success": False, "error": "down"}, status=500)
        got = metrics.daily()
        self.assertNotIn("own_thread_unanswered", got)
        self.assertEqual(self.mb.requests("GET", "/posts/old/comments"), [])
