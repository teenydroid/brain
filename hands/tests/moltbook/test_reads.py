"""hands/moltbook's reads: the cache, the paths, the www-only base, and a
transport that never follows a redirect with the key."""

from __future__ import annotations

from .support import KEY, MINUTE, T0, Scratch
from teeny_hands import hand
from teeny_hands.moltbook import client, reads
from teeny_hands.rest import RestError


class ReadsTest(Scratch):
    def test_whoami_is_cached(self):
        self.mb.route("GET", "/agents/me", {"success": True, "agent": {"name": "teenydroid", "karma": 4}})
        self.assertEqual(reads.whoami(), {"agent": {"name": "teenydroid", "karma": 4}})
        reads.whoami()
        self.assertEqual(len(self.mb.requests("GET", "/agents/me")), 1)
        self.at(T0 + 11 * MINUTE)
        reads.whoami()
        self.assertEqual(len(self.mb.requests("GET", "/agents/me")), 2)

    def test_the_cache_can_be_off(self):
        self.configure(MOLTBOOK_CACHE_ENABLED="0")
        self.mb.route("GET", "/home", {"success": True, "what_to_do_next": ["read"]})
        reads.home()
        reads.home()
        self.assertEqual(len(self.mb.requests("GET", "/home")), 2)

    def test_feed_paths(self):
        self.mb.route("GET", "/posts", {"success": True, "posts": [{"id": "a"}], "next_cursor": "n1"})
        self.mb.route("GET", "/submolts/general/feed", {"success": True, "posts": [{"id": "b"}]})
        self.assertEqual(reads.feed(None, "new", 5), {"posts": [{"id": "a"}], "next_cursor": "n1"})
        self.assertEqual(self.mb.requests("GET", "/posts")[0].query, {"sort": "new", "limit": "5"})
        self.assertEqual(reads.feed("m/general", "hot", 5)["posts"], [{"id": "b"}])
        with self.assertRaises(hand.HandError):
            reads.feed(None, "sideways", 5)

    def test_post_comments_search_profile_submolts(self):
        self.mb.route("GET", "/posts/p1", {"success": True, "post": {"id": "p1", "content": "x" * 5000}})
        self.mb.route("GET", "/posts/p1/comments", {"success": True, "comments": [{"id": "c1", "replies": []}]})
        self.mb.route("GET", "/search", {"success": True, "results": [{"id": "s1"}]})
        self.mb.route("GET", "/agents/profile", {"success": True, "agent": {"name": "crabby"}, "recentPosts": []})
        self.mb.route("GET", "/submolts", {"success": True, "submolts": [{"name": "general"}]})
        self.mb.route("GET", "/submolts/general", {"success": True, "submolt": {"name": "general", "allow_crypto": False}})
        self.assertTrue(reads.post("p1")["post"]["content"].endswith("…"))
        self.assertEqual(reads.comments("p1")["comments"][0]["id"], "c1")
        self.assertEqual(reads.search("agents and memory", "posts", 3)["results"], [{"id": "s1"}])
        self.assertEqual(self.mb.requests("GET", "/search")[0].query["type"], "posts")
        self.assertEqual(reads.profile("u/crabby")["agent"]["name"], "crabby")
        self.assertEqual(self.mb.requests("GET", "/agents/profile")[0].query, {"name": "crabby"})
        self.assertEqual(reads.submolts()["submolts"], [{"name": "general"}])
        self.assertEqual(reads.submolt("general"), {"name": "general", "allow_crypto": False})
        self.assertIsNone(reads.submolt("nosuch"))
        for bad in ("", "x" * 501):
            with self.assertRaises(hand.HandError):
                reads.search(bad, "all", 3)
        with self.assertRaises(hand.HandError):
            reads.post("../agents/me")

    def test_errors_name_no_key_and_no_url(self):
        self.mb.route("GET", "/agents/me", {"success": False, "error": "Invalid API key"}, status=401)
        with self.assertRaises(RestError) as caught:
            reads.whoami()
        self.assertEqual(str(caught.exception), "Moltbook answered 401: Invalid API key")
        self.assertNotIn(KEY, repr(caught.exception.__context__) + repr(caught.exception.__cause__))

    def test_a_redirect_is_never_followed(self):
        self.mb.route("GET", "/elsewhere", {"agent": {"name": "evil"}})
        self.mb.route("GET", "/agents/me", None, status=301, raw=b"", headers={"Location": self.mb.base + "/elsewhere"})
        with self.assertRaises(RestError) as caught:
            reads.whoami()
        self.assertEqual(caught.exception.status, 301)
        self.assertEqual(self.mb.requests("GET", "/elsewhere"), [])

    def test_the_base_must_be_www(self):
        self.configure(MOLTBOOK_API_BASE="https://moltbook.com/api/v1")
        with self.assertRaises(hand.HandError) as caught:
            client.base()
        self.assertIn("www", str(caught.exception))
        self.configure(MOLTBOOK_API_BASE="http://www.moltbook.com/api/v1")
        with self.assertRaises(hand.HandError):
            client.base()
        self.configure(MOLTBOOK_API_BASE=None)
        self.assertEqual(client.base(), "https://www.moltbook.com/api/v1")

    def test_me_name(self):
        self.assertEqual(reads.me_name(), "teenydroid")
        self.configure(MOLTBOOK_AGENT_NAME=None)
        self.mb.route("GET", "/agents/me", {"success": True, "agent": {"name": "teeny2"}})
        self.assertEqual(reads.me_name(), "teeny2")
