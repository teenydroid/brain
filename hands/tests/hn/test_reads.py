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
