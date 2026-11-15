"""What a giveaway needs from X without paying for reads: follow events and
post authors' details from the webhook (free), and the one paid fallback,
`users --ids`, cached and batched. The delivery fixtures follow X's own
samples (docs.x.com/x-api/activity/event-payloads, "Follow events" and
"post.mention.create")."""

from __future__ import annotations

import json

from .support import T0, Scratch, me
from .test_webhook import ME, SECRET, signed
from teeny_hands import hand
from teeny_hands.x import cache, follows, mentions, webhook
from teeny_hands.x.client import XApi


def user(user_id: str, username: str, created_at: str = "2023-10-23T13:44:53.000Z", followers: int = 97,
         verified: bool = False) -> dict:
    return {
        "id": user_id,
        "username": username,
        "name": username,
        "created_at": created_at,
        "protected": False,
        "verified": verified,
        "verified_type": "none",
        "public_metrics": {"followers_count": followers, "followers_count_str": str(followers), "following_count": 1},
    }


def follow_event(kind: str, follower: dict, followed: dict, at: str = "2026-10-07T19:17:36.304Z",
                 filter_user: str = "1000") -> dict:
    """A follow.follow / follow.unfollow delivery, shaped as X's sample."""
    return {
        "data": {
            "event_uuid": "-7953119945885597316",
            "created_at": at,
            "filter": {"user_id": filter_user},
            "event_type": kind,
            "tag": "teeny",
            "payload": {"source": {"data": follower}, "target": {"data": followed}},
        }
    }


TEENY = user("1000", "teenydroid", followers=5502, verified=True)
FREN = user("7", "fren", created_at="2020-01-02T03:04:05.000Z", followers=250)


def mention_event(post_id: str, author: dict) -> dict:
    """A post.mention.create delivery with includes.users, as X's sample."""
    return {
        "data": {
            "event_uuid": "1",
            "created_at": "2026-10-07T19:17:36.304Z",
            "filter": {"user_id": "1000"},
            "event_type": "post.mention.create",
            "payload": {"id": post_id, "author_id": author["id"], "text": "hey @teenydroid", "conversation_id": post_id},
            "includes": {"users": [author, TEENY]},
        }
    }


class Deliveries(Scratch):
    def setUp(self):
        super().setUp()
        self.at(T0)
        self.receiver = webhook.Receiver(SECRET, ME)
        real = mentions._screen
        mentions._screen = lambda post: self.fail("the webhook screened a post")
        self.addCleanup(setattr, mentions, "_screen", real)

    def post(self, delivery, signature: str | None = None):
        body = json.dumps(delivery).encode()
        headers = {} if signature == "" else {"x-twitter-webhooks-signature": signature or signed(body)}
        answer = self.receiver.handle("POST", "/webhooks/x", headers, body)
        while not self.receiver.work.empty():
            self.receiver.apply(self.receiver.work.get())
        return answer

    def follows_rows(self) -> dict:
        conn = follows._db()
        return {r["user_id"]: dict(r) for r in conn.execute("SELECT * FROM x_follows")}


class FollowEventsTest(Deliveries):
    def test_a_follow_of_the_account_is_recorded_with_what_x_said_about_the_follower(self):
        status, body = self.post(follow_event("follow.follow", FREN, TEENY))
        self.assertEqual((status, body["follows"], body["queued"]), (200, 1, 0))
        row = self.follows_rows()["7"]
        self.assertEqual(row["username"], "fren")
        self.assertEqual(row["followed_at"], 1791401856.304)
        self.assertIsNone(row["unfollowed_at"])
        self.assertEqual((row["user_created_at"], row["followers"], row["verified"]), ("2020-01-02T03:04:05.000Z", 250, 0))
        self.assertEqual(follows.follow_state(["7", "8"]), {"7": True, "8": None})

    def test_an_unfollow_makes_the_follower_known_not_to_follow(self):
        self.post(follow_event("follow.follow", FREN, TEENY))
        self.post(follow_event("follow.unfollow", FREN, TEENY, at="2026-10-08T00:00:00.000Z"))
        self.assertEqual(follows.follow_state(["7"]), {"7": False})
        self.assertIsNotNone(self.follows_rows()["7"]["unfollowed_at"])
        self.post(follow_event("follow.follow", FREN, TEENY, at="2026-10-09T00:00:00.000Z"))
        self.assertEqual(follows.follow_state(["7"]), {"7": True})

    def test_an_unfollow_seen_first_is_still_a_known_no(self):
        self.post(follow_event("follow.unfollow", FREN, TEENY))
        self.assertEqual(follows.follow_state(["7"]), {"7": False})

    def test_a_replayed_or_late_follow_cannot_undo_a_later_unfollow(self):
        follow = follow_event("follow.follow", FREN, TEENY, at="2026-10-07T00:00:00.000Z")
        self.post(follow)
        self.post(follow_event("follow.unfollow", FREN, TEENY, at="2026-10-08T00:00:00.000Z"))
        self.post(follow)
        self.assertEqual(follows.follow_state(["7"]), {"7": False})

    def test_the_account_following_somebody_is_not_a_follower(self):
        self.post(follow_event("follow.follow", TEENY, FREN))
        self.assertEqual(self.follows_rows(), {})

    def test_a_follow_event_for_another_account_is_ignored(self):
        other = user("2000", "someone")
        self.post(follow_event("follow.follow", FREN, other, filter_user="2000"))
        self.post(follow_event("follow.follow", FREN, other))
        self.assertEqual(self.follows_rows(), {})

    def test_an_unsigned_or_forged_follow_is_refused_and_not_recorded(self):
        delivery = follow_event("follow.follow", FREN, TEENY)
        self.assertEqual(self.post(delivery, signature="")[0], 401)
        self.assertEqual(self.post(delivery, signature="sha256=forged")[0], 401)
        self.assertEqual(self.follows_rows(), {})

    def test_a_follow_is_not_a_mention(self):
        self.post(follow_event("follow.follow", FREN, TEENY))
        self.assertEqual(mentions._db().execute("SELECT COUNT(*) FROM x_mentions").fetchone()[0], 0)

    def test_follow_state_reads_nothing_from_x(self):
        self.assertEqual(follows.follow_state(["1", "2"]), {"1": None, "2": None})
        self.assertEqual(follows.follow_state([]), {})
        self.assertEqual(self.x.requests(), [])


class AuthorFieldsTest(Deliveries):
    def row(self, post_id: str) -> dict:
        conn = mentions._db()
        return dict(conn.execute(
            "SELECT author_created_at, author_followers, author_verified FROM x_mentions WHERE id = ?", (post_id,)
        ).fetchone())

    def test_a_delivery_keeps_its_authors_age_followers_and_badge(self):
        self.post(mention_event("801", user("9", "ana", created_at="2019-05-06T07:08:09.000Z", followers=42, verified=True)))
        self.assertEqual(self.row("801"), {"author_created_at": "2019-05-06T07:08:09.000Z", "author_followers": 42,
                                           "author_verified": 1})

    def test_the_older_account_activity_shape_keeps_them_too(self):
        self.post({
            "for_user_id": "1000",
            "tweet_create_events": [{
                "id_str": "802", "text": "@teenydroid gm",
                "user": {"id_str": "7", "screen_name": "fren", "followers_count": 12, "verified": False,
                         "created_at": "Wed Oct 10 20:19:24 +0000 2018"},
            }],
        })
        self.assertEqual(self.row("802"), {"author_created_at": "2018-10-10T20:19:24.000Z", "author_followers": 12,
                                           "author_verified": 0})

    def test_a_delivery_without_them_leaves_them_null(self):
        self.post({"data": {"event_type": "post.reply.create", "filter": {"user_id": "1000"},
                            "payload": {"id": "803", "author_id": "7", "text": "@teenydroid hi"}, "includes": {}}})
        self.assertEqual(self.row("803"), {"author_created_at": None, "author_followers": None, "author_verified": None})

    def test_a_table_from_before_the_columns_gains_them_and_keeps_its_rows(self):
        conn = hand.db()
        conn.executescript(mentions.SCHEMA)
        conn.execute("INSERT INTO x_mentions (id, text, post, status, seen_at, updated_at)"
                     " VALUES ('700', 'old', '{}', 'replied', 1, 1)")
        conn.close()
        self.post(mention_event("804", FREN))
        self.assertEqual(self.row("700")["author_followers"], None)
        self.assertEqual(self.row("804")["author_followers"], 250)


class SubscriptionsTest(Scratch):
    def setUp(self):
        super().setUp()
        self.configure(X_BOT_USER_ID="1000", X_BOT_USERNAME="teenydroid")

    def test_the_subscriptions_cover_posts_and_inbound_follows(self):
        bodies = {b["event_type"]: b for b in webhook.subscription_requests("1000", "wh1")}
        self.assertEqual(set(bodies), {"post.mention.create", "post.reply.create", "post.quote.create",
                                       "follow.follow", "follow.unfollow"})
        self.assertEqual(bodies["follow.follow"]["filter"], {"user_id": "1000", "qualifiers": {"direction": "inbound"}})
        self.assertEqual(bodies["post.reply.create"]["filter"], {"user_id": "1000"})
        self.assertTrue(all(b["webhook_id"] == "wh1" for b in bodies.values()))

    def test_subscribe_previews_without_confirm_and_signs_as_the_account_with_it(self):
        preview = webhook.subscribe("wh1")
        self.assertEqual(len(preview["preview"]), 5)
        self.assertEqual(self.x.requests(), [])
        route = self.x.route("POST", "/2/activity/subscriptions", {"data": {"subscription_id": "s1"}})
        out = webhook.subscribe("wh1", confirm=True)
        self.assertTrue(all(r["ok"] for r in out["subscriptions"]))
        self.assertEqual(route.times[0], 5)
        sent = self.x.requests("POST", "/2/activity/subscriptions")
        self.assertTrue(all(s.is_oauth() for s in sent))
        self.assertIn("follow.unfollow", [s.json()["event_type"] for s in sent])


def x_user(user_id: str, followed_by: bool) -> dict:
    status = ["followed_by", "following"] if followed_by else ["following"]
    return {"id": user_id, "username": f"u{user_id}", "created_at": "2021-01-01T00:00:00.000Z",
            "public_metrics": {"followers_count": int(user_id)}, "connection_status": status}


class UsersLookupTest(Scratch):
    def setUp(self):
        super().setUp()
        self.at(T0)
        me(self.x, "1000")

        def answer(seen):
            return True

        self.route = self.x.route("GET", "/2/users", None, when=self.answer_for)

    def answer_for(self, seen) -> bool:
        ids = seen.query["ids"].split(",")
        # The fake answers from the ids asked; 404 ids are left out, as X does.
        self.route.body = {"data": [x_user(i, followed_by=int(i) % 2 == 0) for i in ids if not i.startswith("404")]}
        return True

    def test_returns_age_followers_and_whether_each_follows_the_account(self):
        users = cache.reads().users_by_ids(["2", "3"])
        self.assertEqual(users, [
            {"id": "2", "username": "u2", "createdAt": "2021-01-01T00:00:00.000Z", "followers": 2, "followsMe": True},
            {"id": "3", "username": "u3", "createdAt": "2021-01-01T00:00:00.000Z", "followers": 3, "followsMe": False},
        ])
        seen = self.x.requests("GET", "/2/users")[0]
        self.assertEqual(seen.query["user.fields"], "created_at,public_metrics,connection_status")
        self.assertTrue(seen.is_oauth(), "connection_status is the signed-in account's: it signs as the account")

    def test_a_second_lookup_within_a_day_is_served_from_the_cache(self):
        cache.reads().users_by_ids(["2", "3"])
        cache.reads().users_by_ids(["3", "2"])
        self.assertEqual(self.route.times[0], 1)
        cache.reads().users_by_ids(["2", "4"])
        self.assertEqual(self.route.times[0], 2)
        self.assertEqual(self.x.requests("GET", "/2/users")[-1].query["ids"], "4")
        self.at(T0 + 86400 + 1)
        cache.reads().users_by_ids(["2"])
        self.assertEqual(self.route.times[0], 3)

    def test_an_id_x_does_not_have_is_left_out_and_not_asked_again_at_once(self):
        self.assertEqual([u["id"] for u in cache.reads().users_by_ids(["404", "2"])], ["2"])
        cache.reads().users_by_ids(["404"])
        self.assertEqual(self.route.times[0], 1)

    def test_at_most_one_call_per_hundred_ids(self):
        ids = [str(i) for i in range(1, 251)]
        users = cache.reads().users_by_ids(ids)
        self.assertEqual(len(users), 250)
        self.assertEqual(self.route.times[0], 3)
        self.assertTrue(all(len(s.query["ids"].split(",")) <= 100 for s in self.x.requests("GET", "/2/users")))

    def test_the_client_refuses_more_than_a_hundred_ids(self):
        with self.assertRaises(hand.HandError):
            XApi.from_settings().users_by_ids([str(i) for i in range(101)])
        self.assertEqual(self.route.times[0], 0)

    def test_without_the_cache_it_still_answers(self):
        self.configure(X_CACHE_ENABLED="false")
        self.assertEqual(len(cache.reads().users_by_ids(["2"])), 1)
