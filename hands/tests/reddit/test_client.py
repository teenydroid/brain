"""hands/reddit's transport: the two OAuth grants, the User-Agent, no
redirect followed, no secret in any message, and the reads' shapes."""

from __future__ import annotations

import base64

from .support import ME, PASSWORD, SECRET, TOKEN, Scratch, form, listing, t1, t3
from teeny_hands import hand
from teeny_hands.reddit import client, reads


class TokenTest(Scratch):
    def test_the_password_grant_with_basic_auth(self):
        self.rd.route("GET", "/api/v1/me", {"name": ME, "total_karma": 3})
        self.assertEqual(reads.whoami()["me"]["name"], ME)
        sent = self.rd.requests("POST", "/api/v1/access_token")[0]
        self.assertEqual(sent.authorization, "Basic " + base64.b64encode(f"client-id-FAKE:{SECRET}".encode()).decode())
        self.assertEqual(form(sent), {"grant_type": "password", "username": ME, "password": PASSWORD})
        me = self.rd.requests("GET", "/api/v1/me")[0]
        self.assertEqual((me.authorization, me.query["raw_json"]), (f"Bearer {TOKEN}", "1"))

    def test_a_refresh_token_wins_over_the_password(self):
        self.configure(REDDIT_REFRESH_TOKEN="refresh-FAKE")
        self.rd.route("GET", "/api/v1/me", {"name": ME})
        reads.whoami()
        self.assertEqual(form(self.rd.requests("POST", "/api/v1/access_token")[0]), {"grant_type": "refresh_token", "refresh_token": "refresh-FAKE"})

    def test_one_token_per_process(self):
        self.rd.route("GET", "/api/v1/me", {"name": ME})
        self.rd.route("GET", "/r/learnpython/hot", listing())
        self.configure(REDDIT_CACHE_ENABLED="false")
        reads.whoami()
        reads.feed("learnpython", "hot", 5)
        self.assertEqual(len(self.rd.requests("POST", "/api/v1/access_token")), 1)

    def test_a_bad_password_names_no_secret(self):
        self.rd.route("POST", "/api/v1/access_token", {"error": "invalid_grant"})
        with self.assertRaises(hand.HandError) as caught:
            reads.whoami()
        message = str(caught.exception)
        self.assertIn("invalid_grant", message)
        for secret in (SECRET, PASSWORD):
            self.assertNotIn(secret, message)

    def test_a_redirect_is_not_followed(self):
        self.rd.route("GET", "/api/v1/me", {}, status=302, headers={"Location": "https://evil.example/"})
        with self.assertRaises(hand.HandError) as caught:
            reads.whoami()
        self.assertIn("redirect", str(caught.exception))
        self.assertNotIn(TOKEN, str(caught.exception))

    def test_the_api_must_be_https(self):
        self.configure(REDDIT_API_BASE="http://oauth.reddit.com")
        with self.assertRaises(hand.HandError):
            reads.whoami()

    def test_a_password_grant_needs_the_username_in_the_user_agent(self):
        self.assertEqual(client.user_agent(), f"linux:ai.tinyhumans.teeny:v1 (by /u/{ME})")

    def test_ids(self):
        self.assertEqual(client.base_id("t3_Abc12", "x", "t3"), "abc12")
        self.assertEqual(client.base_id("abc12", "x", "t1"), "abc12")
        for bad in ("t1_abc", "../x", "", "a" * 20):
            with self.assertRaises(hand.HandError):
                client.base_id(bad, "x", "t3")


class ReadsTest(Scratch):
    def test_a_feed_is_cut_down_to_what_a_draft_reads(self):
        self.rd.route("GET", "/r/learnpython/new", listing(t3("p1", over_18=False, locked=True, junk="x" * 10)))
        got = reads.feed("learnpython", "new", 5)
        self.assertEqual(got["posts"][0]["id"], "p1")
        self.assertTrue(got["posts"][0]["locked"])
        self.assertNotIn("junk", got["posts"][0])

    def test_a_thread_keeps_its_replies(self):
        reply = t1("c2", "bob", "p1", parent="t1_c1")
        top = t1("c1", "ana", "p1", replies=listing(reply))
        self.rd.route("GET", "/comments/p1", [listing(t3("p1")), listing(top)])
        got = reads.thread("t3_p1")
        self.assertEqual(got["post"]["title"], "post p1")
        self.assertEqual(got["comments"][0]["replies"][0]["author"], "bob")

    def test_a_subreddit_carries_its_rules(self):
        self.community()
        got = reads.subreddit("r/learnpython")
        self.assertEqual(got["rules"][0]["name"], "Be nice")
        self.rd.route("GET", "/r/gone/about", {"message": "Forbidden"}, status=403)
        self.assertIsNone(reads.subreddit("gone"))
