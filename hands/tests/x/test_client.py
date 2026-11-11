"""The X v2 client against a fake X, ported from crates/teeny-x/src/
{client,wire,writes}_tests.rs. Plus what the hand owes on top: an error never
carries the URL, a header or a secret."""

from __future__ import annotations

import unittest

from . import support
from .support import SECRETS, Scratch, me
from teeny_hands import hand
from teeny_hands.x import model
from teeny_hands.x.client import XApi, XError

POST_FIELDS = "created_at,conversation_id,text,attachments,public_metrics,in_reply_to_user_id"
EXPANSIONS = "author_id,attachments.media_keys"


class ReadsTest(Scratch):
    def test_gets_a_post_with_its_expansions_on_the_app_bearer(self):
        self.x.route(
            "GET",
            "/2/tweets/77",
            {"data": {"id": "77", "text": "hello", "author_id": "42"}, "includes": {"users": [{"id": "42", "username": "someone"}]}},
        )
        post = XApi.from_settings().get_post("77")
        self.assertEqual(post["text"], "hello")
        self.assertEqual(post["authorUsername"], "someone")
        seen = self.x.requests("GET", "/2/tweets/77")[0]
        self.assertEqual(seen.authorization, "Bearer app-bearer-FAKE")
        self.assertEqual(seen.query["tweet.fields"], POST_FIELDS)
        self.assertEqual(seen.query["user.fields"], "username")
        self.assertEqual(seen.query["media.fields"], "media_key,type,url,preview_image_url,alt_text")
        self.assertEqual(seen.query["expansions"], EXPANSIONS)

    def test_a_post_x_does_not_have_is_none(self):
        self.x.route("GET", "/2/tweets/404", {"errors": [{"title": "Not Found Error", "resource_id": "404"}]})
        self.assertIsNone(XApi.from_settings().get_post("404"))

    def test_reads_sign_with_oauth_when_there_is_no_bearer(self):
        self.configure(X_BEARER_TOKEN=None)
        self.x.route("GET", "/2/tweets/1", {"data": {"id": "1", "text": "x"}})
        self.assertIsNotNone(XApi.from_settings().get_post("1"))
        self.assertTrue(self.x.requests("GET", "/2/tweets/1")[0].is_oauth())

    def test_searches_recent_posts_with_clamped_max_results_and_paging(self):
        self.x.route(
            "GET",
            "/2/tweets/search/recent",
            {"data": [{"id": "600", "text": "a"}, {"id": "601", "text": "b"}], "meta": {"next_token": "page3", "newest_id": "601", "result_count": 2}},
        )
        page = XApi.from_settings().search_page(
            "$TINY -is:retweet",
            model.SearchOptions(max_results=3, since_id="500", sort_order="recency", pagination_token="page2"),
        )
        self.assertEqual(len(page["posts"]), 2)
        self.assertEqual(page["nextToken"], "page3")
        self.assertEqual(page["newestId"], "601")
        q = self.x.requests("GET", "/2/tweets/search/recent")[0].query
        self.assertEqual(q["query"], "$TINY -is:retweet")
        self.assertEqual(q["max_results"], "10")
        self.assertEqual(q["since_id"], "500")
        self.assertEqual(q["sort_order"], "recency")
        self.assertEqual(q["pagination_token"], "page2")
        self.assertEqual(q["tweet.fields"], POST_FIELDS)
        self.assertEqual(q["expansions"], EXPANSIONS)

    def test_caps_a_search_at_one_hundred_and_an_empty_one_is_empty(self):
        self.x.route("GET", "/2/tweets/search/recent", {"meta": {"result_count": 0}})
        posts = XApi.from_settings().search_posts(
            "q", model.SearchOptions(max_results=500, start_time="2026-10-01T00:00:00Z")
        )
        self.assertEqual(posts, [])
        q = self.x.requests("GET", "/2/tweets/search/recent")[0].query
        self.assertEqual(q["max_results"], "100")
        self.assertEqual(q["start_time"], "2026-10-01T00:00:00Z")

    def test_reads_a_conversation_by_its_id(self):
        self.x.route("GET", "/2/tweets/search/recent", {"data": [{"id": "901", "text": "r"}]})
        posts = XApi.from_settings().get_conversation("900")
        self.assertEqual(posts[0]["id"], "901")
        q = self.x.requests("GET", "/2/tweets/search/recent")[0].query
        self.assertEqual((q["query"], q["max_results"]), ("conversation_id:900", "25"))

    def test_identifies_the_account_as_the_account(self):
        self.x.route(
            "GET",
            "/2/users/me",
            {"data": {"id": "2094854245468000256", "username": "teenydroid", "public_metrics": {"followers_count": 321}}},
        )
        identity = XApi.from_settings().identity()
        self.assertEqual(identity, {"id": "2094854245468000256", "username": "teenydroid", "followers": 321})
        seen = self.x.requests("GET", "/2/users/me")[0]
        self.assertTrue(seen.is_oauth(), "signed as the account even with a bearer")
        self.assertEqual(seen.query["user.fields"], "username,public_metrics")

    def test_an_answer_without_the_account_is_an_error(self):
        self.x.route("GET", "/2/users/me", {"errors": [{"title": "Unauthorized"}]})
        with self.assertRaisesRegex(XError, "did not identify"):
            XApi.from_settings().identity()

    def test_looks_up_an_account_by_its_handle_on_the_app_bearer(self):
        self.x.route(
            "GET",
            "/2/users/by/username/stonksrocks",
            {
                "data": {
                    "id": "42",
                    "username": "StonksRocks",
                    "name": "Stonks",
                    "description": "an open-source dapp",
                    "created_at": "2021-03-04T05:06:07.000Z",
                    "verified": False,
                    "public_metrics": {"followers_count": 1200, "following_count": 30, "tweet_count": 999},
                }
            },
        )
        user = XApi.from_settings().user_by_username("@stonksrocks")
        self.assertEqual(
            user,
            {
                "id": "42",
                "username": "StonksRocks",
                "name": "Stonks",
                "description": "an open-source dapp",
                "createdAt": "2021-03-04T05:06:07.000Z",
                "verified": False,
                "followers": 1200,
                "following": 30,
                "posts": 999,
            },
        )
        seen = self.x.requests("GET", "/2/users/by/username/stonksrocks")[0]
        self.assertEqual(seen.query["user.fields"], "created_at,description,public_metrics,verified")
        self.assertEqual(seen.authorization, "Bearer app-bearer-FAKE")

    def test_an_account_x_does_not_have_is_none(self):
        self.x.route(
            "GET",
            "/2/users/by/username/nobody",
            {"errors": [{"title": "Not Found Error", "detail": "Could not find user with username: [nobody]."}]},
        )
        self.assertIsNone(XApi.from_settings().user_by_username("nobody"))


class ErrorsTest(Scratch):
    def test_an_error_status_is_an_http_error_with_x_s_title(self):
        self.x.route("GET", "/2/tweets/search/recent", {"title": "Too Many Requests", "status": 429}, status=429)
        with self.assertRaises(XError) as caught:
            XApi.from_settings().search_posts("q", model.SearchOptions())
        self.assertIsInstance(caught.exception, hand.HandError)
        self.assertEqual(caught.exception.status, 429)
        self.assertIn("429", str(caught.exception))
        self.assertIn("Too Many Requests", str(caught.exception))

    def test_an_error_never_carries_the_url_a_header_or_a_secret(self):
        self.x.route(
            "GET",
            "/2/tweets/search/recent",
            raw=b'{"title":"Forbidden","detail":"token at-FAKE-ACCESS-TOKEN echoed"}',
            status=403,
        )
        with self.assertRaises(XError) as caught:
            XApi.from_settings().search_posts("secret query", model.SearchOptions())
        message = str(caught.exception)
        self.assertIn("403", message)
        self.assertIn("Forbidden", message)
        for leak in [self.x.base, "127.0.0.1", "search/recent", "secret query", "Bearer", *SECRETS.values()]:
            self.assertNotIn(leak, message)

    def test_an_error_body_that_is_not_json_is_named_by_its_status_alone(self):
        self.x.route("GET", "/2/tweets/9", raw=b"<html>at-FAKE-ACCESS-TOKEN</html>", status=502, content_type="text/html")
        with self.assertRaises(XError) as caught:
            XApi.from_settings().get_post("9")
        self.assertIn("502", str(caught.exception))
        self.assertNotIn("FAKE", str(caught.exception))

    def test_x_unreachable_is_an_error_without_the_url(self):
        self.configure(X_API_BASE="http://127.0.0.1:9")
        with self.assertRaises(XError) as caught:
            XApi.from_settings().get_post("1")
        self.assertIn("could not reach X", str(caught.exception))
        self.assertNotIn("127.0.0.1", str(caught.exception))
        self.assertIsNone(caught.exception.__cause__, "the urllib error (and its URL) is not chained")

    def test_an_unreadable_answer_is_a_decode_error(self):
        self.x.route("GET", "/2/tweets/9", raw=b"not json", content_type="text/plain")
        with self.assertRaisesRegex(XError, "unreadable"):
            XApi.from_settings().get_post("9")

    def test_a_missing_key_names_the_key_and_nothing_else(self):
        self.configure(X_API_SECRET=None)
        with self.assertRaisesRegex(hand.HandError, "X_API_SECRET"):
            XApi.from_settings()

    def test_the_client_never_prints_its_secrets(self):
        printed = repr(XApi.from_settings())
        for secret in SECRETS.values():
            self.assertNotIn(secret, printed)


class WireTest(unittest.TestCase):
    def test_joins_authors_and_media_from_includes(self):
        posts = model.to_posts(
            [
                {
                    "id": "1",
                    "text": "gm",
                    "author_id": "42",
                    "conversation_id": "1",
                    "created_at": "2026-09-14T09:40:00.000Z",
                    "in_reply_to_user_id": "7",
                    "attachments": {"media_keys": ["3_1", "3_missing"]},
                    "public_metrics": {"like_count": 2, "reply_count": 1, "retweet_count": 3, "quote_count": 0, "impression_count": 99},
                }
            ],
            {
                "users": [{"id": "42", "username": "someone"}],
                "media": [{"media_key": "3_1", "type": "photo", "url": "https://pbs.twimg.com/a.jpg", "alt_text": "a cat"}],
            },
        )
        self.assertEqual(
            posts,
            [
                {
                    "id": "1",
                    "text": "gm",
                    "authorId": "42",
                    "authorUsername": "someone",
                    "conversationId": "1",
                    "createdAt": "2026-09-14T09:40:00.000Z",
                    "inReplyToUserId": "7",
                    "media": [{"type": "photo", "url": "https://pbs.twimg.com/a.jpg", "altText": "a cat"}],
                    "metrics": {"id": "1", "likes": 2, "replies": 1, "reposts": 3, "quotes": 0, "impressions": 99},
                }
            ],
        )

    def test_drops_posts_without_an_id_or_text_and_omits_what_x_left_out(self):
        posts = model.to_posts([{"id": "1"}, {"text": "orphan"}, {"id": "2", "text": "bare"}], None)
        self.assertEqual(posts, [{"id": "2", "text": "bare"}])


class WritesTest(Scratch):
    def test_creates_a_post_signed_as_the_account_even_with_a_bearer(self):
        self.x.route("POST", "/2/tweets", {"data": {"id": "100", "text": "gm"}}, status=201)
        self.assertEqual(XApi.from_settings().create_post("gm"), "100")
        seen = self.x.requests("POST", "/2/tweets")[0]
        self.assertEqual(seen.json(), {"text": "gm"})
        self.assertTrue(seen.is_oauth())

    def test_attaches_a_reply_target_a_quote_and_media(self):
        self.x.route("POST", "/2/tweets", {"data": {"id": "101"}}, status=201)
        XApi.from_settings().create_post("look", reply_to="7", quote="8", media_ids=["m1", "m2"])
        self.assertEqual(
            self.x.requests("POST", "/2/tweets")[0].json(),
            {"text": "look", "reply": {"in_reply_to_tweet_id": "7"}, "quote_tweet_id": "8", "media": {"media_ids": ["m1", "m2"]}},
        )

    def test_a_post_without_an_id_back_is_an_error(self):
        self.x.route("POST", "/2/tweets", {"errors": [{"message": "duplicate"}]})
        with self.assertRaisesRegex(XError, "duplicate"):
            XApi.from_settings().create_post("gm")

    def test_deletes_a_post_as_the_account(self):
        self.x.route("DELETE", "/2/tweets/55", {"data": {"deleted": True}})
        self.x.route("DELETE", "/2/tweets/56", {"errors": []})
        x = XApi.from_settings()
        self.assertTrue(x.delete_post("55"))
        self.assertFalse(x.delete_post("56"))
        self.assertTrue(self.x.requests("DELETE", "/2/tweets/55")[0].is_oauth())

    def test_likes_a_post_as_the_account(self):
        self.x.route("POST", "/2/users/1/likes", {"data": {"liked": True}})
        XApi.from_settings().like_post("77", user_id="1")
        seen = self.x.requests("POST", "/2/users/1/likes")[0]
        self.assertEqual(seen.json(), {"tweet_id": "77"})
        self.assertTrue(seen.is_oauth())

    def test_a_refused_like_is_an_error(self):
        self.x.route("POST", "/2/users/1/likes", {"errors": [{"title": "Forbidden"}]})
        with self.assertRaisesRegex(XError, "refused the like"):
            XApi.from_settings().like_post("77", user_id="1")


if __name__ == "__main__":
    unittest.main()
