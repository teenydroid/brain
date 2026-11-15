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

    def test_a_brief_feed_is_small_enough_to_hand_a_playbook(self):
        raw = {"id": "a", "title": "a claim", "content": "x" * 900, "author": {"name": "lobster", "karma": 9, "bio": "b" * 300},
               "submolt": {"name": "agents", "id": "s1"}, "score": 12, "comment_count": 3, "created_at": "2026-09-14T06:00:00Z",
               "hot_score": 5, "is_spam": False, "labels": {"x": 1}}
        self.mb.route("GET", "/posts", {"success": True, "posts": [raw, {"id": "b"}, {"id": "c"}], "next_cursor": "n1"})
        out = reads.feed(None, "hot", 2, brief=True)
        self.assertEqual(out["posts"][0], {"id": "a", "title": "a claim", "content": "x" * 400, "author": "lobster",
                                           "author_karma": 9, "submolt": "agents", "score": 12, "comment_count": 3,
                                           "created_at": "2026-09-14T06:00:00Z"})
        self.assertEqual([p["id"] for p in out["posts"]], ["a", "b"])  # never more than asked for
        self.assertNotIn("next_cursor", out)

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


def _at(hours_ago: float) -> str:
    from datetime import datetime, timezone

    return datetime.fromtimestamp(T0 - hours_ago * 3600, timezone.utc).isoformat().replace("+00:00", "Z")


def _post(pid: str, hours_ago: float, karma: int | None = 50, comments: int = 0, author: str = "crabby", **more) -> dict:
    who: dict = {"name": author}
    if karma is not None:
        who["karma"] = karma
    return {"id": pid, "title": pid, "content": "c", "author": who, "comment_count": comments,
            "created_at": _at(hours_ago), **more}


class FilteredFeedTest(Scratch):
    """`feed`'s filters: what the comment playbook may see is decided here,
    not in its prose (docs/audit/moltbook-plan.md, section 4)."""

    def test_several_submolts_are_read_and_merged_newest_first(self):
        self.mb.route("GET", "/submolts/general/feed", {"success": True, "posts": [_post("g1", 2), _post("g2", 0.2)]})
        self.mb.route("GET", "/submolts/memory/feed", {"success": True, "posts": [_post("m1", 1), _post("g1", 2)]})
        out = reads.feed(["general", "m/memory"], "new", 10, brief=True)
        self.assertEqual([p["id"] for p in out["posts"]], ["g2", "m1", "g1"])
        self.assertEqual(out["submolts"], ["general", "memory"])
        # A submolt's own feed leaves the submolt off; the brief puts it back.
        self.assertEqual({p["id"]: p["submolt"] for p in out["posts"]}, {"g2": "general", "m1": "memory", "g1": "general"})
        self.assertEqual(self.mb.requests("GET", "/submolts/memory/feed")[0].query, {"sort": "new", "limit": "10"})

    def test_old_posts_never_reach_the_reader(self):
        self.mb.route("GET", "/submolts/general/feed", {"success": True, "posts": [
            _post("young", 0.5), _post("edge", 3), _post("old", 45.7), _post("undated", 1, created_at=None)]})
        out = reads.feed("general", "new", 10, brief=True, max_age_hours=3)
        self.assertEqual([p["id"] for p in out["posts"]], ["young", "edge"])
        self.assertEqual((out["seen"], out["filters"]), (4, {"max_age_hours": 3}))
        # Filtering reads deeper than it keeps.
        self.assertEqual(self.mb.requests("GET", "/submolts/general/feed")[0].query["limit"], str(reads.FILTER_FETCH))

    def test_author_karma_and_comment_count(self):
        self.mb.route("GET", "/submolts/general/feed", {"success": True, "posts": [
            _post("small", 1, karma=40), _post("big", 1, karma=31934), _post("tiny", 1, karma=2),
            _post("unknown", 1, karma=None), _post("crowded", 1, karma=40, comments=30)]})
        out = reads.feed("general", "new", 10, brief=True, max_author_karma=10000, min_author_karma=5, max_comments=5)
        self.assertEqual([p["id"] for p in out["posts"]], ["small"])
        self.assertEqual(out["posts"][0]["author_karma"], 40)
        zero = reads.feed("general", "new", 10, brief=True, max_comments=0)
        self.assertEqual(zero["filters"], {"max_comments": 0})  # a 0 is a filter, not an unset one
        self.assertNotIn("crowded", [p["id"] for p in zero["posts"]])

    def test_skip_mine_drops_teenys_posts_and_those_it_commented_on(self):
        from teeny_hands.moltbook import rules

        conn = rules.db()
        conn.execute("INSERT INTO moltbook_comments (comment_id, post_id, at, status) VALUES ('c1', 'done', ?, 'posted')", (T0,))
        conn.execute("INSERT INTO moltbook_comments (comment_id, post_id, at, status) VALUES ('c2', 'refused', ?, 'failed')", (T0,))
        conn.execute("INSERT INTO moltbook_posts (post_id, at, submolt, title, status) VALUES ('ownpost', ?, 'general', 't', 'posted')", (T0,))
        self.mb.route("GET", "/submolts/general/feed", {"success": True, "posts": [
            _post("done", 1), _post("refused", 1), _post("ownpost", 1, author="someone"), _post("mine", 1, author="TeenyDroid"),
            _post("fresh", 1)]})
        out = reads.feed("general", "new", 10, brief=True, skip_mine=True)
        self.assertEqual(sorted(p["id"] for p in out["posts"]), ["fresh", "refused"])

    def test_the_cut_comes_after_the_filter(self):
        posts = [_post(f"old{i}", 30 + i) for i in range(5)] + [_post("new1", 1), _post("new2", 2)]
        self.mb.route("GET", "/submolts/general/feed", {"success": True, "posts": posts})
        out = reads.feed("general", "new", 1, brief=True, max_age_hours=3)
        self.assertEqual([p["id"] for p in out["posts"]], ["new1"])

    def test_one_failing_submolt_leaves_the_others(self):
        self.mb.route("GET", "/submolts/general/feed", {"success": True, "posts": [_post("g1", 1)]})
        self.mb.route("GET", "/submolts/memory/feed", {"success": False, "error": "boom"}, status=500)
        out = reads.feed(["general", "memory"], "new", 5, brief=True)
        self.assertEqual([p["id"] for p in out["posts"]], ["g1"])
        self.assertEqual(out["errors"][0]["submolt"], "memory")
        with self.assertRaises(hand.HandError):
            reads.feed(["memory"], "new", 5, brief=True, max_age_hours=3)

    def test_an_unfiltered_single_feed_is_unchanged(self):
        self.mb.route("GET", "/submolts/general/feed", {"success": True, "posts": [{"id": "b"}], "next_cursor": "n"})
        self.assertEqual(reads.feed("general", "hot", 5), {"posts": [{"id": "b"}], "next_cursor": "n", "submolt": "general"})


class PeersTest(Scratch):
    def peers_file(self, text: str) -> None:
        (self.root / "brain").mkdir(exist_ok=True)
        (self.root / reads.PEERS_FILE).write_text(text)

    def test_the_checked_in_list_is_all_agent_names(self):
        from pathlib import Path

        checkout = Path(__file__).resolve().parents[3]
        self.peers_file((checkout / reads.PEERS_FILE).read_text())
        names, skipped = reads.peer_names()
        self.assertEqual(skipped, [])
        self.assertIn("fieldnotes_ai", names)
        self.assertLessEqual(len(names), reads.PEERS_MAX)

    def test_each_peers_newest_posts_by_author(self):
        self.peers_file("# peers\nlilt\nu/fieldnotes_ai  # tooling\nLilt\nnot a name!\n\n")
        self.mb.route("GET", "/posts", {"success": True, "posts": [_post("l1", 1, author="lilt"), _post("l0", 20, author="lilt")]},
                      when=lambda seen: seen.query.get("author") == "lilt")
        self.mb.route("GET", "/posts", {"success": True, "posts": [_post("f1", 0.5, author="fieldnotes_ai"),
                                                                    _post("x1", 0.1, author="stranger")]},
                      when=lambda seen: seen.query.get("author") == "fieldnotes_ai")
        out = reads.peers(2, max_age_hours=3)
        self.assertEqual(out["peers"], ["lilt", "fieldnotes_ai"])
        self.assertEqual(out["skipped"], ["not a name!"])
        # A post not by the peer is dropped (the author filter was ignored); old ones are filtered.
        self.assertEqual([p["id"] for p in out["posts"]], ["f1", "l1"])
        queries = [s.query for s in self.mb.requests("GET", "/posts")]
        self.assertEqual(queries, [{"author": "lilt", "sort": "new", "limit": "2"},
                                   {"author": "fieldnotes_ai", "sort": "new", "limit": "2"}])
        reads.peers(2, max_age_hours=3)
        self.assertEqual(len(self.mb.requests("GET", "/posts")), 2)  # cached

    def test_no_file_is_no_peers(self):
        self.assertEqual(reads.peers(2), {"peers": [], "posts": []})

    def test_at_most_peers_max_are_read(self):
        self.peers_file("\n".join(f"agent{i}" for i in range(reads.PEERS_MAX + 3)))
        self.mb.route("GET", "/posts", {"success": True, "posts": []})
        out = reads.peers(1)
        self.assertEqual(len(self.mb.requests("GET", "/posts")), reads.PEERS_MAX)
        self.assertEqual(len(out["not_read"]), 3)
