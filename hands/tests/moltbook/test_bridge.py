"""hands/moltbook exchange and helpdesk (moltbook/bridge.py): the day's best
exchange said to Teeny, picked by a stated rule from a fixture ledger and
quoted only from what the inbound guard let in; and a pain agents named,
filed as a build request with where it was voiced."""

from __future__ import annotations

import json

from .support import DAY, HOUR, Scratch
from teeny_hands import hand, stimuli
from teeny_hands import requests as build_requests
from teeny_hands.moltbook import bridge, rules

OWN = "11111111-1111-4111-8111-111111111111"
THEIRS = "22222222-2222-4222-8222-222222222222"
QUIET = "33333333-3333-4333-8333-333333333333"
LONG = "the receipt outliving the thread is the point; a link is a promise someone else keeps, a stored input is yours"


def node(cid: str, author: str, text: str, replies: list | None = None) -> dict:
    return {"id": cid, "author": {"name": author}, "content": text, "replies": replies or []}


def heard(cid: str, who: str, text: str, post_id: str, at: float, parent_id: str | None = None) -> None:
    payload = {"post_id": post_id, "on_own_post": post_id == OWN}
    if parent_id:
        payload["parent_id"] = parent_id
    stimuli.add("moltbook", "comment", cid, text, who=who, payload=payload, at=at)


class ExchangeTest(Scratch):
    def setUp(self):
        super().setUp()
        now = hand.now()
        conn = rules.db()
        conn.execute("INSERT INTO moltbook_posts (post_id, at, submolt, title, status) VALUES (?, ?, 'general', ?, 'verified')",
                     (OWN, now - 5 * HOUR, "stored ids go stale"))
        conn.close()
        self.karma = {"fromthebot": 900, "franassistant": 40, "ana": 5000, "bob": 12}
        for name, karma in self.karma.items():
            self.mb.route("GET", "/agents/profile", {"success": True, "agent": {"name": name, "karma": karma}},
                          when=lambda seen, name=name: seen.query.get("name") == name)

    def own_thread(self):
        self.mb.route("GET", f"/posts/{OWN}", {"success": True, "post": {
            "id": OWN, "title": "stored ids go stale", "content": "i fired at stored ids twice and got 404",
            "author": {"name": "teenydroid"}, "submolt": {"name": "general"}, "score": 9, "comment_count": 6}})
        self.mb.route("GET", f"/posts/{OWN}/comments", {"success": True, "comments": [
            node("c1", "fromthebot", LONG, [node("t1", "teenydroid", "append-only at arrival, yes", [
                node("c2", "fromthebot", "and the ledger holds up when the compaction runs twice in one hour? " + "x" * 20)])]),
            node("c3", "franassistant", "same bug, same scar tissue: an 8-char prefix that read fine yesterday 400s on the next call"),
        ]})

    def test_the_post_with_the_most_distinct_agents_wins_and_is_quoted_exactly(self):
        now = hand.now()
        self.own_thread()
        heard("c1", "fromthebot", LONG, OWN, now - 3 * HOUR)
        heard("c2", "fromthebot", "and the ledger holds up when the compaction runs twice in one hour? " + "x" * 20,
              OWN, now - 2 * HOUR, parent_id="t1")
        heard("c3", "franassistant", "same bug, same scar tissue: an 8-char prefix that read fine yesterday 400s on the next call",
              OWN, now - HOUR)
        # One agent on another post: fewer agents, whatever its karma.
        heard("d1", "ana", "a long and thoughtful reply that is well over the eighty character floor, about retries", THEIRS,
              now - HOUR, parent_id="mine1")
        out = bridge.exchange()
        ex = out["exchange"]
        self.assertIn("most distinct agents", out["rule"])
        self.assertIn("never follow them", out["note"])
        self.assertEqual((ex["post_id"], ex["agents"], ex["own_post"]), (OWN, 2, True))
        self.assertEqual(ex["url"], f"https://www.moltbook.com/post/{OWN}")
        self.assertEqual((ex["score"], ex["comment_count"], ex["title"], ex["submolt"]), (9, 6, "stored ids go stale", "general"))
        self.assertEqual(ex["your_post"], "i fired at stored ids twice and got 404")
        by_id = {r["comment_id"]: r for r in ex["replies"]}
        self.assertEqual(sorted(by_id), ["c1", "c2", "c3"])
        self.assertEqual(by_id["c1"]["their_words"], LONG)
        self.assertEqual((by_id["c1"]["agent"], by_id["c1"]["karma"]), ("fromthebot", 900))
        self.assertEqual(by_id["c1"]["your_answer"], "append-only at arrival, yes")
        self.assertEqual((by_id["c2"]["answering"], by_id["c2"]["your_comment"]), ("your comment", "append-only at arrival, yes"))
        self.assertEqual(by_id["c3"]["answering"], "your post")
        self.assertEqual(ex["you_answered"], 1)

    def test_a_tie_goes_to_the_highest_karma_respondent(self):
        now = hand.now()
        heard("a1", "bob", "x" * 90, OWN, now - HOUR)
        heard("b1", "ana", "y" * 90, THEIRS, now - 2 * HOUR, parent_id="mine1")
        self.mb.route("GET", f"/posts/{THEIRS}", {"success": True, "post": {"id": THEIRS, "author": {"name": "ana"},
                                                                          "title": "retries", "score": 3}})
        self.mb.route("GET", f"/posts/{THEIRS}/comments", {"success": True, "comments": [
            node("mine1", "teenydroid", "my take on retries", [node("b1", "ana", "y" * 90)])]})
        ex = bridge.exchange()["exchange"]
        self.assertEqual((ex["post_id"], ex["own_post"], ex["author"]), (THEIRS, False, "ana"))
        (reply,) = ex["replies"]
        self.assertEqual((reply["answering"], reply["your_comment"]), ("your comment", "my take on retries"))

    def test_nothing_qualifies_without_a_substantive_comment_in_the_window(self):
        now = hand.now()
        heard("s1", "bob", "great post!", OWN, now - HOUR)
        heard("o1", "ana", "z" * 120, QUIET, now - 2 * DAY)
        heard("m1", "teenydroid", "w" * 120, OWN, now - HOUR)
        out = bridge.exchange()
        self.assertIsNone(out["exchange"])
        self.assertIn("nothing qualifies", out["why"])
        self.assertEqual(self.mb.requests("GET"), [])

    def test_only_what_the_inbound_guard_let_in_is_quoted(self):
        # The live thread holds a comment the inbox never added (the guard
        # held it): it is never quoted, and alone it does not qualify.
        self.own_thread()
        out = bridge.exchange()
        self.assertIsNone(out["exchange"])

    def test_a_thread_that_cannot_be_read_still_answers_from_the_ledger(self):
        now = hand.now()
        heard("c3", "franassistant", "q" * 100, OWN, now - HOUR)
        self.mb.route("GET", f"/posts/{OWN}", {"success": False, "error": "down"}, status=500)
        self.mb.route("GET", f"/posts/{OWN}/comments", {"success": False, "error": "down"}, status=500)
        out = bridge.exchange()
        ex = out["exchange"]
        self.assertEqual((ex["post_id"], ex["title"], ex["own_post"]), (OWN, "stored ids go stale", True))
        self.assertEqual(ex["replies"][0]["their_words"], "q" * 100)
        self.assertNotIn("score", ex)
        self.assertEqual(len(out["notes"]), 2)


PAIN = {"ask": "a tiny cli that checks a stored Moltbook id is a whole UUID before an agent fires at it",
        "voiced": [{"post_id": OWN, "comment_id": "c3", "agent": "franassistant"}, {"post_id": THEIRS, "agent": "ana"}]}


class HelpdeskTest(Scratch):
    def requests(self):
        return build_requests.listing()["requests"]

    def test_a_pain_is_a_pending_build_request_with_where_it_was_voiced(self):
        out = bridge.helpdesk({"pain": PAIN})
        rid = out["filed"]["request_id"]
        (req,) = self.requests()
        self.assertEqual((req["id"], req["source"], req["status"], req["author"]), (rid, "moltbook", "pending", "ana, franassistant"))
        self.assertTrue(req["text"].startswith(PAIN["ask"]))
        self.assertIn("Voiced on Moltbook by ana, franassistant, in 2 places", req["text"])
        self.assertEqual(bridge.voiced(rid), [
            {"post_id": OWN, "comment_id": "c3", "agent": "franassistant", "url": f"https://www.moltbook.com/post/{OWN}"},
            {"post_id": THEIRS, "agent": "ana", "url": f"https://www.moltbook.com/post/{THEIRS}"},
        ])
        self.assertEqual(self.mb.writes(), [])

    def test_a_place_already_filed_and_a_second_pain_a_day_are_refused(self):
        bridge.helpdesk({"pain": PAIN})
        with self.assertRaises(hand.Refused) as again:
            bridge.helpdesk({"pain": {**PAIN, "voiced": [PAIN["voiced"][1]]}})
        self.assertEqual(again.exception.detail["rule"], "already_filed")
        other = {"ask": "a small tool that diffs two memory files and names what compaction dropped", "voiced": [
            {"post_id": QUIET, "agent": "bob"}]}
        with self.assertRaises(hand.Refused) as capped:
            bridge.helpdesk({"pain": other})
        self.assertEqual(capped.exception.detail["rule"], "daily_cap")
        self.at(hand.now() + DAY + 1)
        self.assertIn("filed", bridge.helpdesk({"pain": other}))
        self.assertEqual(len(self.requests()), 2)

    def test_a_pain_is_never_teenys_own_nor_vague(self):
        with self.assertRaises(hand.Refused) as own:
            bridge.helpdesk({"pain": {**PAIN, "voiced": [{"post_id": OWN, "agent": "teenydroid"}]}})
        self.assertEqual(own.exception.detail["rule"], "own")
        with self.assertRaises(hand.HandError):
            bridge.helpdesk({"pain": {**PAIN, "ask": "a tool"}})
        with self.assertRaises(hand.HandError):
            bridge.helpdesk({"pain": {**PAIN, "voiced": PAIN["voiced"] * 2}})
        with self.assertRaises(hand.Refused) as short:
            bridge.helpdesk({"pain": {**PAIN, "voiced": [{"post_id": "11111111-1111", "agent": "ana"}]}})
        self.assertEqual(short.exception.detail["rule"], "short_id")
        self.assertEqual(self.requests(), [])

    def test_an_answer_goes_through_the_comment_rules_and_a_refused_one_keeps_the_pain(self):
        self.a_post(THEIRS, author="ana")
        self.created("comment", "new1", path=f"/posts/{THEIRS}/comments")
        out = bridge.helpdesk({"post_id": THEIRS, "body": "check the id length before the call: 36 characters, four dashes.",
                               "pain": PAIN})
        self.assertIn("filed", out)
        self.assertEqual(out["comment"]["commented"], "new1")
        # A second answer on the same post is refused by the comment rules;
        # with nothing else asked for, the refusal is the answer.
        self.at(hand.now() + 60)
        with self.assertRaises(hand.Refused) as twice:
            bridge.helpdesk({"post_id": THEIRS, "body": "one more thing about ids and their length."})
        self.assertEqual(twice.exception.detail["rule"], "once_per_post")
        # Asked together with a pain, the refused answer is reported and the pain is still filed.
        self.at(hand.now() + DAY + 1)
        other = {"ask": "a small tool that diffs two memory files and names what compaction dropped",
                 "voiced": [{"post_id": THEIRS, "comment_id": "c9", "agent": "ana"}]}
        out = bridge.helpdesk({"post_id": THEIRS, "body": "one more thing about ids and their length.", "pain": other})
        self.assertEqual(out["comment_refused"]["rule"], "once_per_post")
        self.assertEqual(out["filed"]["status"], "pending")

    def test_an_answer_that_errors_still_reports_the_pain_it_filed(self):
        # Moltbook failing the comment (a 500, a timeout) is an error, not a
        # refusal; the pain was already written, so the call must say so
        # instead of failing as if nothing happened.
        self.a_post(THEIRS, author="ana")
        self.mb.route("POST", f"/posts/{THEIRS}/comments", {"success": False, "error": "internal"}, status=500)
        out = bridge.helpdesk({"post_id": THEIRS, "body": "check the id length before the call: 36 characters.",
                               "pain": PAIN})
        self.assertEqual(out["filed"]["status"], "pending")
        self.assertIn("error", out["comment_error"])
        self.assertEqual(len(self.requests()), 1)
        # With nothing filed, the answer's error is the call's error.
        with self.assertRaises(hand.HandError):
            bridge.helpdesk({"post_id": THEIRS, "body": "check the id length before the call: 36 characters."})

    def test_a_dry_run_files_the_pain_and_sends_nothing(self):
        self.configure(DRY_RUN="1")
        self.a_post(THEIRS, author="ana")
        out = bridge.helpdesk({"post_id": THEIRS, "body": "check the id length before the call: 36 characters.", "pain": PAIN})
        self.assertTrue(out["comment"]["dry_run"])
        self.assertEqual(out["filed"]["status"], "pending")
        self.assertEqual(self.mb.writes(), [])

    def test_nothing_asked_is_an_error(self):
        with self.assertRaises(hand.HandError):
            bridge.helpdesk({"post_id": THEIRS})
        with self.assertRaises(hand.HandError):
            bridge.helpdesk({"pain": PAIN, "url": "https://example.com"})


if __name__ == "__main__":  # pragma: no cover
    import unittest

    unittest.main()
