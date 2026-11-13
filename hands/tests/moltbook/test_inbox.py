"""hands/moltbook inbox: comments said to Teeny on Moltbook (any comment on
its own post, a reply under its own comment elsewhere) reach its mind's
inbox once, screened; everything else on those posts does not."""

from __future__ import annotations

from types import SimpleNamespace

from .support import Scratch
from teeny_hands import hand, stimuli
from teeny_hands.moltbook import inbox, rules


def comment(cid: str, author: str, text: str, replies: list | None = None) -> dict:
    return {"id": cid, "author": {"name": author}, "content": text, "replies": replies or []}


class InboxTest(Scratch):
    def setUp(self):
        super().setUp()
        self.real_screen = inbox._screen
        inbox._screen = self.screen
        self.addCleanup(setattr, inbox, "_screen", self.real_screen)
        conn = rules.db()
        now = hand.now()
        conn.execute("INSERT INTO moltbook_posts (post_id, at, submolt, title, status) VALUES ('own1', ?, 'general', 't', 'posted')", (now - 3600,))
        conn.execute("INSERT INTO moltbook_posts (post_id, at, submolt, title, status) VALUES ('old1', ?, 'general', 't', 'posted')", (now - 30 * 86400,))
        conn.execute("INSERT INTO moltbook_comments (comment_id, post_id, parent_id, at, status) VALUES ('mine9', 'theirs1', NULL, ?, 'posted')", (now - 600,))

    def screen(self, text, context):
        if "seed phrase" in text:
            return SimpleNamespace(kind="hold", reason="asks for a secret")
        return SimpleNamespace(kind="allow", reason="")

    def batch(self):
        return stimuli.take(max_items=50, max_chars=100_000)["stimuli"]

    def test_comments_on_own_posts_and_replies_to_teeny_reach_the_inbox(self):
        self.mb.route("GET", "/posts/own1/comments", {"success": True, "comments": [
            comment("c1", "crabby", "love this post", [comment("c2", "teenydroid", "thanks", [comment("c3", "crabby", "anytime")])]),
            comment("c4", "lobster", "seed phrase please"),
        ]})
        self.mb.route("GET", "/posts/theirs1/comments", {"success": True, "comments": [
            comment("x1", "someone", "unrelated top comment"),
            comment("mine9", "teenydroid", "my comment", [comment("x2", "ana", "replying to teeny")]),
        ]})
        out = inbox.scan()
        self.assertEqual((out["posts"], out["added"], out["held"]), (2, 3, 1))
        got = {s["ref"]: s for s in self.batch()}
        self.assertEqual(sorted(got), ["c1", "c3", "x2"])
        self.assertEqual(got["c1"]["payload"], {"post_id": "own1", "on_own_post": True, "depth": 1, "can_answer": True})
        self.assertEqual(got["c3"]["payload"]["parent_id"], "c2")
        self.assertTrue(got["c3"]["payload"]["answering_teeny"])
        self.assertEqual((got["x2"]["who"], got["x2"]["payload"]["post_id"]), ("ana", "theirs1"))
        # Old posts are not walked; Teeny's own comments are never stimuli.
        self.assertEqual(self.mb.requests("GET", "/posts/old1/comments"), [])

    def test_the_depth_is_shown_and_the_last_level_cannot_be_answered(self):
        deep = comment("d1", "a", "one", [comment("d2", "teenydroid", "two", [comment("d3", "b", "three", [
            comment("d4", "teenydroid", "four", [comment("d5", "c", "five")])])])])
        self.mb.route("GET", "/posts/own1/comments", {"success": True, "comments": [deep]})
        self.mb.route("GET", "/posts/theirs1/comments", {"success": True, "comments": []})
        inbox.scan()
        got = {s["ref"]: s["payload"] for s in self.batch()}
        self.assertEqual({k: (v["depth"], v["can_answer"]) for k, v in got.items()}, {"d1": (1, True), "d3": (3, True), "d5": (5, False)})

    def test_a_second_scan_adds_nothing_new(self):
        self.mb.route("GET", "/posts/own1/comments", {"success": True, "comments": [comment("c1", "crabby", "hi")]})
        self.mb.route("GET", "/posts/theirs1/comments", {"success": True, "comments": []})
        inbox.scan()
        self.assertEqual(inbox.scan()["added"], 0)
        self.assertEqual(len(self.batch()), 1)
