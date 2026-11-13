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


class InboxCase(Scratch):
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
            return SimpleNamespace(kind="hold", reason="asks for a secret", hold_id=None)
        return SimpleNamespace(kind="allow", reason="", hold_id=None)

    def batch(self):
        return stimuli.take(max_items=50, max_chars=100_000)["stimuli"]


class InboxTest(InboxCase):
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
        self.assertEqual(got["c1"]["payload"], {"post_id": "own1", "on_own_post": True})
        self.assertEqual(got["c3"]["payload"]["parent_id"], "c2")
        self.assertTrue(got["c3"]["payload"]["answering_teeny"])
        self.assertEqual((got["x2"]["who"], got["x2"]["payload"]["post_id"]), ("ana", "theirs1"))
        # Old posts are not walked; Teeny's own comments are never stimuli.
        self.assertEqual(self.mb.requests("GET", "/posts/old1/comments"), [])

    def test_a_second_scan_adds_nothing_new(self):
        self.mb.route("GET", "/posts/own1/comments", {"success": True, "comments": [comment("c1", "crabby", "hi")]})
        self.mb.route("GET", "/posts/theirs1/comments", {"success": True, "comments": []})
        inbox.scan()
        self.assertEqual(inbox.scan()["added"], 0)
        self.assertEqual(len(self.batch()), 1)


class HeldCommentTest(InboxCase):
    """A comment the real guard holds (the lexicon, judge off) is screened
    and held once, however many scans see it, and reaches the inbox only
    when its hold is cleared. Live on 2026-11-13: one comment on Teeny's
    own post was held again on every intake run, a dozen pending holds."""

    HELD = "A captcha killed the reply, the retry cleared it"

    def setUp(self):
        super().setUp()
        inbox._screen = self.real_screen
        self.mb.route("GET", "/posts/own1/comments", {"success": True, "comments": [
            comment("f17", "templeofmolt", self.HELD),
        ]})
        self.mb.route("GET", "/posts/theirs1/comments", {"success": True, "comments": []})

    def holds_for(self, cid):
        from teeny_hands.guard import holds

        return [h for h in holds.list_holds() if h["context"].get("comment_id") == cid]

    def test_a_held_comment_is_held_once_across_scans(self):
        first = inbox.scan()
        self.assertEqual((first["held"], first["added"]), (1, 0))
        for _ in range(3):
            again = inbox.scan()
            self.assertEqual((again["held"], again["added"]), (0, 0))
        held = self.holds_for("f17")
        self.assertEqual(len(held), 1, held)
        self.assertEqual((held[0]["status"], held[0]["by"]), ("pending", "screen"))
        self.assertEqual(self.batch(), [], "a held comment never reaches the mind")

    def test_a_cleared_hold_returns_the_comment_to_the_inbox_once(self):
        from teeny_hands.guard import rescreen

        inbox.scan()
        inbox.scan()
        [held] = self.holds_for("f17")
        self.assertTrue(rescreen._release_moltbook(held))
        self.assertFalse(rescreen._release_moltbook(held), "released once")
        later = inbox.scan()
        self.assertEqual((later["held"], later["added"]), (0, 0))
        got = self.batch()
        self.assertEqual([(s["ref"], s["who"]) for s in got], [("f17", "templeofmolt")])
        self.assertEqual(got[0]["payload"], {"post_id": "own1", "on_own_post": True})
        self.assertEqual(len(self.holds_for("f17")), 1)
