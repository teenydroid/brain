"""`like` and `unsay`: no guard (nothing is said), but DRY_RUN holds for both
and unsay previews unless confirmed. A like that went out is written to the
`x_likes` ledger, which bin/receipts reads. Ported from the like/unsay cases of
crates/teeny-x/src/publisher_tests.rs and writes_tests.rs."""

from __future__ import annotations

from .support import Scratch, me
from teeny_hands import hand
from teeny_hands.x import posting_rules, publish


def likes() -> list[dict]:
    conn = hand.db()
    posting_rules.ensure_schema(conn)
    return [dict(r) for r in conn.execute("SELECT post_id, liked_at FROM x_likes")]


class LikeTest(Scratch):
    def setUp(self):
        super().setUp()
        self.configure(X_LIKES="1")
        me(self.x, "1000")
        self.liked = self.x.route("POST", "/2/users/1000/likes", {"data": {"liked": True}})

    def test_likes_a_post_as_the_account(self):
        self.assertEqual(publish.like("77"), {"liked": "77"})
        seen = self.x.requests("POST", "/2/users/1000/likes")[0]
        self.assertEqual(seen.json(), {"tweet_id": "77"})
        self.assertTrue(seen.is_oauth())

    def test_a_like_that_went_out_is_ledgered_once(self):
        self.at(1_789_358_400)
        publish.like("77")
        publish.like("77")
        self.assertEqual(likes(), [{"post_id": "77", "liked_at": 1_789_358_400.0}])

    def test_a_dry_run_likes_nothing_and_asks_x_nothing(self):
        self.configure(DRY_RUN="1")
        self.assertEqual(publish.like("77"), {"dry_run": True, "would_like": "77"})
        self.assertEqual(self.x.requests(), [])
        self.assertEqual(likes(), [])

    def test_a_like_x_rejected_is_not_ledgered(self):
        self.liked.status = 403
        with self.assertRaises(hand.HandError):
            publish.like("77")
        self.assertEqual(likes(), [])

    def test_likes_are_off_unless_the_operator_turns_them_on(self):
        # X's developer guidelines: a like must be user-initiated, and every
        # caller of this verb is a model (docs/roadmap.md, P1).
        for setting in (None, "0"):
            self.configure(X_LIKES=setting)
            for dry in ("0", "1"):
                self.configure(DRY_RUN=dry)
                with self.assertRaises(hand.Refused) as refused:
                    publish.like("77")
                self.assertEqual(refused.exception.extra.get("rule"), "likes_off")
        self.assertEqual(self.x.requests(), [])
        self.assertEqual(likes(), [])

    def test_the_post_must_be_an_x_id(self):
        with self.assertRaises(hand.HandError):
            publish.like("https://x.com/a/status/77")


class UnsayTest(Scratch):
    def setUp(self):
        super().setUp()
        me(self.x, "1000")
        self.x.route("GET", "/2/tweets/55", {"data": {"id": "55", "text": "oops", "author_id": "1000"}})
        self.x.route("GET", "/2/tweets/56", {"data": {"id": "56", "text": "theirs", "author_id": "42"}})
        self.deleted = self.x.route("DELETE", "/2/tweets/55", {"data": {"deleted": True}})

    def test_previews_unless_confirmed(self):
        result = publish.unsay("55", confirm=False)
        self.assertEqual(result["preview"], True)
        self.assertEqual(result["would_delete"], "55")
        self.assertEqual(result["post"]["text"], "oops")
        self.assertEqual(self.x.writes(), [])

    def test_deletes_an_own_post_when_confirmed_and_live(self):
        self.assertEqual(publish.unsay("55", confirm=True), {"id": "55", "deleted": True})
        self.assertTrue(self.x.requests("DELETE", "/2/tweets/55")[0].is_oauth())

    def test_a_dry_run_deletes_nothing(self):
        self.configure(DRY_RUN="1")
        self.assertEqual(publish.unsay("55", confirm=True), {"dry_run": True, "would_delete": "55"})
        self.assertEqual(self.x.writes(), [])

    def test_refuses_a_post_the_account_did_not_write(self):
        with self.assertRaises(hand.Refused):
            publish.unsay("56", confirm=True)
        self.assertEqual(self.x.writes(), [])

    def test_refuses_a_post_x_does_not_have(self):
        self.x.route("GET", "/2/tweets/57", {"errors": [{"title": "Not Found Error"}]})
        with self.assertRaises(hand.Refused):
            publish.unsay("57", confirm=True)
        self.assertEqual(self.x.writes(), [])


if __name__ == "__main__":
    import unittest

    unittest.main()
