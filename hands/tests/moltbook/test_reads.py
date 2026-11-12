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


MENTION = "You were mentioned in a post"
COMMENTED = "Someone commented on your post"


class HomeExpandTest(Scratch):
    def activity(self, *items: tuple[str, str]) -> None:
        self.mb.route("GET", "/home", {"success": True, "activity_on_your_posts": [
            {"post_id": pid, "post_title": f"title {pid}", "preview": preview, "new_notification_count": 1}
            for pid, preview in items
        ]})

    def full_post(self, pid: str, author: str = "crabby") -> None:
        self.mb.route("GET", f"/posts/{pid}", {"success": True, "post": {
            "id": pid, "title": f"title {pid}", "content": "how do I fix my cron?", "author": {"name": author, "karma": 3},
            "submolt": {"name": "general"}, "created_at": "2026-11-12T02:40:00Z", "upvotes": 2,
        }})
        self.mb.route("GET", f"/posts/{pid}/comments", {"success": True, "comments": [
            {"id": "c1", "content": "same here", "author": {"name": "shelly"}, "created_at": "2026-11-12T02:45:00Z", "replies": []},
        ]})

    def test_a_mention_gets_its_post_expanded(self):
        self.activity(("m1", MENTION))
        self.full_post("m1")
        item = reads.home(expand=True)["home"]["activity_on_your_posts"][0]
        self.assertEqual(item["post"], {
            "id": "m1", "title": "title m1", "content": "how do I fix my cron?", "author": "crabby",
            "submolt": "general", "created_at": "2026-11-12T02:40:00Z",
            "comments": [{"id": "c1", "author": "shelly", "content": "same here", "created_at": "2026-11-12T02:45:00Z"}],
        })
        self.assertEqual(self.mb.requests("GET", "/posts/m1/comments")[0].query["sort"], "new")

    def test_without_expand_home_is_untouched(self):
        self.activity(("m1", MENTION))
        self.full_post("m1")
        self.assertNotIn("post", reads.home()["home"]["activity_on_your_posts"][0])
        self.assertEqual(self.mb.requests("GET", "/posts/m1"), [])

    def test_activity_on_teenys_own_post_is_not_expanded(self):
        self.activity(("own1", COMMENTED), ("own2", MENTION))
        self.full_post("own1", author="teenydroid")
        self.full_post("own2", author="TeenyDroid")
        items = reads.home(expand=True)["home"]["activity_on_your_posts"]
        self.assertEqual([("post" in i) for i in items], [False, False])
        self.assertEqual(self.mb.requests("GET", "/posts/own1"), [])

    def test_at_most_five_posts_are_expanded(self):
        ids = [f"m{n}" for n in range(8)]
        self.activity(*((pid, MENTION) for pid in ids))
        for pid in ids:
            self.full_post(pid)
        items = reads.home(expand=True)["home"]["activity_on_your_posts"]
        self.assertEqual([("post" in i) for i in items], [True] * 5 + [False] * 3)
        self.assertEqual(sum(len(self.mb.requests("GET", f"/posts/{pid}")) for pid in ids), 5)

    def test_a_failed_fetch_leaves_the_item_unexpanded(self):
        self.activity(("gone", MENTION), ("m2", MENTION), ("../agents/me", MENTION))
        self.mb.route("GET", "/posts/gone", {"success": False, "error": "Post not found"}, status=404)
        self.full_post("m2")
        self.mb.route("GET", "/posts/m2/comments", {"success": False, "error": "boom"}, status=500)
        items = reads.home(expand=True)["home"]["activity_on_your_posts"]
        self.assertNotIn("post", items[0])
        self.assertEqual(items[1]["post"]["content"], "how do I fix my cron?")
        self.assertNotIn("comments", items[1]["post"])
        self.assertNotIn("post", items[2])

    def test_expanding_does_not_change_the_cached_home(self):
        self.activity(("m1", MENTION))
        self.full_post("m1")
        reads.home(expand=True)
        self.assertNotIn("post", reads.home()["home"]["activity_on_your_posts"][0])
        self.assertEqual(len(self.mb.requests("GET", "/home")), 1)

    def test_home_still_answers_when_teenys_name_is_unknown(self):
        self.configure(MOLTBOOK_AGENT_NAME=None)
        self.mb.route("GET", "/agents/me", {"success": False, "error": "down"}, status=503)
        self.activity(("m1", MENTION))
        self.full_post("m1")
        self.assertNotIn("post", reads.home(expand=True)["home"]["activity_on_your_posts"][0])
