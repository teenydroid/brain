"""teeny_hands.x.recall: what a beat needs to answer an X mention without
contradicting itself: the post a mention answers, what Teeny last said to
that person, and what it said lately, replies included. Read from the hands'
own ledgers, never from X."""

from __future__ import annotations

import json

from .support import T0, Scratch
from teeny_hands import hand
from teeny_hands.x import cache, mentions, posting_rules, recall


class RecallTest(Scratch):
    def setUp(self):
        super().setUp()
        self.at(T0)
        conn = hand.db()
        conn.executescript(posting_rules.SCHEMA + mentions.SCHEMA + cache._SCHEMA)
        self.conn = conn

    def own(self, pid: str, kind: str, text: str, at: float, in_reply_to: str | None = None) -> None:
        self.conn.execute("INSERT INTO x_posts (id, kind, text, in_reply_to, created_at) VALUES (?,?,?,?,?)",
                          (pid, kind, text, in_reply_to, at))

    def mention(self, mid: str, who: str, text: str, conversation: str | None = None) -> None:
        self.conn.execute("INSERT INTO x_mentions (id, author_username, conversation_id, text, post, status, seen_at,"
                          " updated_at) VALUES (?,?,?,?,?,?,?,?)",
                          (mid, who, conversation, text, json.dumps({"id": mid, "text": text}), "pending", T0, T0))

    def test_a_mention_on_teenys_own_post_shows_that_post(self):
        self.own("50", "post", "gm. day three. woke to a red build", T0 - 7200)
        lines = recall.mention_context("51", "ana", {"in_reply_to": "50"})
        self.assertIn("in reply to your post (2 h ago): gm. day three. woke to a red build", lines)

    def test_the_conversation_root_stands_in_when_the_parent_is_unknown(self):
        self.mention("40", "bob", "@teenydroid what is your plan today?")
        self.conn.execute("INSERT INTO x_cache VALUES (?,?,?,?,?)",
                          ("post:30", "post", json.dumps({"id": "30", "text": "Tiny still building", "authorUsername": "bob"}),
                           T0, T0 + 86400))
        lines = recall.mention_context("41", "bob", {"conversation_id": "30"})
        self.assertIn("in a thread started by @bob: Tiny still building", lines)

    def test_what_teeny_last_said_to_this_person(self):
        self.mention("60", "ana", "gm")
        self.own("61", "reply", "gm ana, desk's warm", T0 - 3 * 3600, in_reply_to="60")
        self.mention("70", "carl", "hi")
        self.own("71", "reply", "hi carl", T0 - 60, in_reply_to="70")
        lines = recall.mention_context("80", "ana", {})
        self.assertIn("you last said to @ana (3 h ago): gm ana, desk's warm", lines)
        self.assertFalse(any("carl" in line for line in lines))

    def test_nothing_known_is_nothing_said(self):
        self.assertEqual(recall.mention_context("90", "zed", {"in_reply_to": "999"}), [])

    def test_said_lately_lists_replies_and_posts_newest_first(self):
        self.mention("60", "ana", "gm")
        self.own("61", "reply", "i hold exactly one coin i never sell", T0 - 600, in_reply_to="60")
        self.own("62", "post", "gm timeline", T0 - 7200)
        self.own("63", "post", "an old one", T0 - 3 * 86400)
        lines = recall.said_lately()
        self.assertEqual(lines[0], "## what you said on X lately")
        self.assertIn("- 10 min ago, to @ana: i hold exactly one coin i never sell", lines)
        self.assertIn("- 2 h ago, a post: gm timeline", lines)
        self.assertLess(lines.index("- 10 min ago, to @ana: i hold exactly one coin i never sell"),
                        lines.index("- 2 h ago, a post: gm timeline"))
        self.assertFalse(any("an old one" in line for line in lines))  # the last day only

    def test_no_ledger_no_lines(self):
        self.conn.executescript("DROP TABLE x_posts; DROP TABLE x_mentions; DROP TABLE x_cache;")
        self.assertEqual(recall.said_lately(), [])
        self.assertEqual(recall.mention_context("1", "a", {"in_reply_to": "2"}), [])
