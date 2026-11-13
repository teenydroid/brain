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
