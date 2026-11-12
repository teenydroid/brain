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
