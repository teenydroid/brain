"""hands/hn's reads: HN's HTML as plain text, the story feeds hydrated, an
item, a bounded thread, a user, Algolia's search, and the read cache."""

from __future__ import annotations

from .support import HOUR, MINUTE, T0, Scratch, comment, story
from teeny_hands import hand
from teeny_hands.hn import reads


class PlainTextTest(Scratch):
    def test_entities_are_decoded_and_paragraphs_are_new_lines(self):
        self.assertEqual(
            reads.plain("It&#x27;s &quot;fine&quot; &amp; 2 &gt; 1<p>Second <i>para</i>.<p>Third"),
            "It's \"fine\" & 2 > 1\n\nSecond para.\n\nThird",
        )

    def test_a_link_keeps_its_address_as_text(self):
        html = ('See <a href="https:&#x2F;&#x2F;example.com&#x2F;a?b=1&amp;c=2" rel="nofollow">'
                'https:&#x2F;&#x2F;example.com&#x2F;a?b=1&amp;c=2</a> and '
                '<a href="https:&#x2F;&#x2F;example.com&#x2F;very&#x2F;long&#x2F;path" rel="nofollow">'
                'https:&#x2F;&#x2F;example.com&#x2F;very&#x2F;l...</a> or <a href="https://x.org/">this one</a>.')
        self.assertEqual(
            reads.plain(html),
            "See https://example.com/a?b=1&c=2 and https://example.com/very/long/path or this one (https://x.org/).",
        )

    def test_code_and_escaped_tags_stay_as_written(self):
        self.assertEqual(reads.plain("<pre><code>  x = 1 &lt;b&gt;\n</code></pre>"), "x = 1 <b>")
        self.assertEqual(reads.plain(None), "")


class FeedsTest(Scratch):
    def test_a_feed_is_its_stories_hydrated_dead_and_deleted_left_out(self):
        self.feed("top", [3, 2, 1, 9])
        self.items(
            story(3, "Show HN: a crab", by="crabby", text="ignored &amp; dropped"),
            {"id": 2, "deleted": True, "type": "story", "time": int(T0)},
            story(1, "Ask HN: why?", dead=True),
            story(9, "An old one"),
        )
        got = reads.stories("top", 2)
        self.assertEqual(got["feed"], "top")
        self.assertEqual(got["stories"], [
            {"id": 3, "title": "Show HN: a crab", "url": "https://example.com/3", "by": "crabby", "score": 10,
             "descendants": 0, "time": int(T0 - HOUR)},
            {"id": 9, "title": "An old one", "url": "https://example.com/9", "by": "pg", "score": 10,
             "descendants": 0, "time": int(T0 - HOUR)},
        ])

    def test_feeds_and_items_are_cached(self):
        self.feed("new", [1])
        self.items(story(1))
        reads.stories("new", 5)
        reads.stories("new", 5)
        self.assertEqual((len(self.hn.requests("GET", "/v0/newstories.json")), self.fetched(1)), (1, 1))
        self.at(T0 + 3 * MINUTE)  # items keep 2 minutes, feeds 5
        reads.stories("new", 5)
        self.assertEqual((len(self.hn.requests("GET", "/v0/newstories.json")), self.fetched(1)), (1, 2))
        self.at(T0 + 6 * MINUTE)
        reads.stories("new", 5)
        self.assertEqual(len(self.hn.requests("GET", "/v0/newstories.json")), 2)

    def test_only_the_named_feeds(self):
        for name in ("top", "new", "best", "ask", "show"):
            self.feed(name, [])
            self.assertEqual(reads.stories(name, 3)["stories"], [])
        with self.assertRaises(hand.HandError):
            reads.stories("jobs", 3)


class ItemTest(Scratch):
    def test_an_item_is_plain_text(self):
        self.items(comment(5, 1, "It&#x27;s <i>good</i><p>Really", kids=[6, 7]))
        self.assertEqual(reads.item("5")["item"], {
            "id": 5, "type": "comment", "by": "dang", "time": int(T0 - HOUR), "parent": 1,
            "text": "It's good\n\nReally", "kids": [6, 7],
            "link": "https://news.ycombinator.com/item?id=5",
        })

    def test_a_missing_or_deleted_item_is_an_error(self):
        self.hn.route("GET", "/v0/item/404.json", None)
        self.items({"id": 2, "deleted": True}, story(3, dead=True))
        for missing in ("404", "2", "3"):
            with self.assertRaises(hand.HandError) as caught:
                reads.item(missing)
            self.assertIn(f"no Hacker News item {missing}", str(caught.exception))
        with self.assertRaises(hand.HandError):
            reads.item("abc")


class ThreadTest(Scratch):
    def setUp(self):
        super().setUp()
        # 1 ─ 2 ─ 4 (deleted) ─ 5
        #   │   └ 6
        #   └ 3 (dead) ─ 7
        self.items(
            story(1, "the root", kids=[2, 3]),
            comment(2, 1, "first", kids=[4, 6]),
            {"id": 4, "type": "comment", "deleted": True, "parent": 2, "kids": [5], "time": int(T0)},
            comment(5, 4, "under a deleted one", by="ana"),
            comment(6, 2, "second &amp; last"),
            comment(3, 1, "flagged", dead=True, kids=[7]),
            comment(7, 3, "under a dead one"),
        )

    def test_a_thread_reads_in_page_order_without_dead_or_deleted(self):
        got = reads.thread("1", depth=3, max_items=30)
        self.assertEqual(got["item"]["title"], "the root")
        self.assertEqual([(c["id"], c["depth"]) for c in got["comments"]], [(2, 1), (5, 3), (6, 2), (7, 2)])
        self.assertEqual(got["comments"][2], {"id": 6, "by": "dang", "time": int(T0 - HOUR), "parent": 2, "depth": 2,
                                              "text": "second & last"})
        self.assertFalse(got["truncated"])

    def test_depth_and_max_bound_the_walk(self):
        self.assertEqual([c["id"] for c in reads.thread("1", depth=2, max_items=30)["comments"]], [2, 6, 7])
        self.assertEqual(self.fetched(5), 0)
        self.configure(HN_CACHE_ENABLED="0")
        before = self.fetched()
        got = reads.thread("1", depth=3, max_items=3)
        self.assertEqual(([c["id"] for c in got["comments"]], got["truncated"]), ([2, 5], True))
        self.assertEqual(self.fetched() - before, 4)  # the root, then 2, 4 and 5


class UserAndSearchTest(Scratch):
    def test_a_user(self):
        self.user("pg", list(range(100, 70, -1)), about="Bug fixer.<p>Essays: <a href=\"http://paulgraham.com\">here</a>")
        got = reads.user("pg")["user"]
        self.assertEqual(got, {"id": "pg", "created": 1_600_000_000, "karma": 12, "submitted": 30,
                               "recent": list(range(100, 90, -1)),
                               "about": "Bug fixer.\n\nEssays: here (http://paulgraham.com)"})
        self.hn.route("GET", "/v0/user/nobody.json", None)
        with self.assertRaises(hand.HandError):
            reads.user("nobody")
        with self.assertRaises(hand.HandError):
            reads.user("../item/1")

    def test_search_goes_to_algolia(self):
        self.hn.route("GET", "/api/v1/search", {"hits": [
            {"objectID": "11", "_tags": ["story", "author_ana", "story_11"], "title": "OpenHuman", "url": "https://oh.dev",
             "author": "ana", "points": 50, "num_comments": 3, "created_at_i": int(T0), "story_text": None},
            {"objectID": "12", "_tags": ["comment", "author_bo"], "author": "bo", "created_at_i": int(T0),
             "comment_text": "I &lt;3 it", "story_id": 11, "parent_id": 11, "story_title": "OpenHuman"},
        ]})
        got = reads.search("openhuman", None, 5)
        self.assertEqual(self.hn.requests("GET", "/api/v1/search")[0].query, {"query": "openhuman", "hitsPerPage": "5"})
        self.assertEqual(got["results"], [
            {"id": 11, "type": "story", "title": "OpenHuman", "url": "https://oh.dev", "by": "ana", "points": 50,
             "num_comments": 3, "time": int(T0), "link": "https://news.ycombinator.com/item?id=11"},
            {"id": 12, "type": "comment", "by": "bo", "time": int(T0), "text": "I <3 it", "story_id": 11,
             "parent_id": 11, "story_title": "OpenHuman", "link": "https://news.ycombinator.com/item?id=12"},
        ])
        reads.search("openhuman", "comment", 5)
        self.assertEqual(self.hn.requests("GET", "/api/v1/search")[1].query["tags"], "comment")
        with self.assertRaises(hand.HandError):
            reads.search(" ", None, 5)
        with self.assertRaises(hand.HandError):
            reads.search("x", "poll", 5)
