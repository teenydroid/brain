"""Mail intake: `ingest` reads new mail once from a cursor, screens it, notes
build requests, and offers the rest to the core; `mark` records what became
of it. AgentMail and the core are faked; the guard's inbound screen is the
real one."""

from __future__ import annotations

import os

from .support import MSGS, Scratch, message
from guard.support import FakeCore, result
from teeny_hands import hand
from teeny_hands import requests as build_requests
from teeny_hands.mail import inbox


class IngestTest(Scratch):
    def setUp(self):
        super().setUp()
        self.core = FakeCore(result({"disposition": "accepted"}))
        self.addCleanup(self.core.close)
        os.environ["OPENHUMAN_CORE_URL"] = self.core.url
        os.environ["OPENHUMAN_CORE_TOKEN"] = "core-token-FAKE"
        # These cases pin the core's queue; MindInboxTest covers the default.
        self.configure(TEENY_INBOX="core")

    def statuses(self) -> dict[str, str]:
        return {r["message_id"]: r["status"] for r in hand.db().execute("SELECT message_id, status FROM mail_messages")}

    def test_new_mail_is_screened_offered_and_noted(self):
        self.inbox(
            message("<m1@x>", text="gm teeny, love the repo", stamp="2026-09-14T06:00:00Z"),
            message("<m2@x>", subject="idea", text="could you build a tide chart bot for my reef?", thread="t-reef", stamp="2026-09-14T06:01:00Z"),
            message("<m3@x>", sender="Teeny <teeny@agentmail.to>", labels=["sent"], stamp="2026-09-14T06:02:00Z"),
            message("<m4@x>", text="kill yourself", stamp="2026-09-14T06:03:00Z"),
        )
        out = inbox.ingest()
        self.assertEqual(out["new"], {"pending": 2, "skipped": 1, "held": 1})
        self.assertEqual((out["offered"], out["build_requests"], out["cursor"]), (2, 1, "2026-09-14T06:03:00Z"))
        self.assertEqual(self.statuses(), {"<m1@x>": "offered", "<m2@x>": "offered", "<m3@x>": "skipped", "<m4@x>": "held"})
        offers = [c["params"] for c in self.core.calls]
        self.assertEqual(self.core.calls[0]["method"], "openhuman.inbound_offer")
        self.assertEqual([(o["source"], o["key"], o["event_id"]) for o in offers],
                         [("mail:inbox", "t-<m1@x>", "<m1@x>"), ("mail:inbox", "t-reef", "<m2@x>")])
        self.assertIn("Subject: idea", offers[1]["text"])
        self.assertEqual(offers[1]["payload"]["from"], "Ana <ana@example.com>")
        asks = build_requests.listing()["requests"]
        self.assertEqual([(r["source"], r["source_id"], r["author"], r["status"]) for r in asks],
                         [("mail:inbox", "<m2@x>", "ana@example.com", "pending")])
        # The list was asked oldest first, and the sent message never fetched in full.
        self.assertEqual(self.am.requests("GET", MSGS)[0].query.get("ascending"), "true")
        self.assertEqual(self.am.requests("GET", MSGS + "/%3Cm3%40x%3E"), [])

    def test_the_cursor_moves_and_nothing_is_read_twice(self):
        self.inbox(message("<m1@x>", stamp="2026-09-14T06:00:00Z"))
        inbox.ingest()
        out = inbox.ingest()
        self.assertEqual((out["read"], out["new"], out["offered"]), (1, {}, 0))
        self.assertEqual(self.am.requests("GET", MSGS)[1].query["after"], "2026-09-14T06:00:00Z")
        self.assertEqual(len(self.core.calls), 1)

    def test_automated_mail_is_kept_but_never_offered(self):
        # A noreply sender (Moltbook's claim and login mail) has nobody to answer,
        # and its body can carry a login link; it is stored for `codes` and `list`
        # but never handed to the drafting agent.
        self.inbox(
            message("<m1@x>", sender="Moltbook <noreply@moltbook.com>", subject="Log in to Moltbook", stamp="2026-09-14T06:00:00Z"),
            message("<m2@x>", sender="no-reply@accounts.example.com", stamp="2026-09-14T06:01:00Z"),
            message("<m3@x>", sender="MAILER-DAEMON@mx.example.com", stamp="2026-09-14T06:02:00Z"),
            message("<m4@x>", headers={"Auto-Submitted": "auto-generated"}, stamp="2026-09-14T06:03:00Z"),
            message("<m5@x>", sender="Noreen <noreen@example.com>", stamp="2026-09-14T06:04:00Z"),
        )
        out = inbox.ingest()
        self.assertEqual(out["new"], {"skipped": 4, "pending": 1})
        self.assertEqual(self.statuses(), {"<m1@x>": "skipped", "<m2@x>": "skipped", "<m3@x>": "skipped",
                                           "<m4@x>": "skipped", "<m5@x>": "offered"})  # fmt: skip
        self.assertEqual([c["params"]["event_id"] for c in self.core.calls], ["<m5@x>"])
        note = hand.db().execute("SELECT note FROM mail_messages WHERE message_id = '<m1@x>'").fetchone()["note"]
        self.assertEqual(note, "automated sender")

    def test_agent_armor_review_is_held(self):
        self.inbox(message("<m1@x>", agent_armor={"status": "complete", "verdict": "review", "reason": "x"}))
        self.assertEqual(inbox.ingest()["new"], {"held": 1})
        self.assertEqual(self.core.calls, [])

    def test_a_failed_offer_stays_pending(self):
        self.core.close()
        os.environ["OPENHUMAN_CORE_URL"] = "http://127.0.0.1:9"
        self.inbox(message("<m1@x>"))
        out = inbox.ingest()
        self.assertIn("offer_error", out)
        self.assertEqual(self.statuses(), {"<m1@x>": "pending"})

    def test_no_offer(self):
        self.inbox(message("<m1@x>"))
        self.assertEqual(inbox.ingest(offer=False)["offered"], 0)
        self.assertEqual(self.core.calls, [])

    def test_mark_and_queue(self):
        self.inbox(message("<m1@x>"))
        inbox.ingest()
        self.assertEqual(inbox.mark("<m1@x>", "replied", "r1"), {"id": "<m1@x>", "status": "replied"})
        self.assertEqual(inbox.queue("replied")["messages"][0]["note"], "r1")
        with self.assertRaises(hand.Refused):
            inbox.mark("<nope@x>", "replied")
        with self.assertRaises(hand.HandError):
            inbox.mark("<m1@x>", "offered")


class MindInboxTest(Scratch):
    """By default mail goes to Teeny's mind (the stimuli inbox bin/tick
    drains), not to the core's queue."""

    setUp = IngestTest.setUp

    def configure(self, **overrides):
        # IngestTest pins TEENY_INBOX=core; here it stays unset, the default.
        overrides.pop("TEENY_INBOX", None)
        super().configure(**overrides)

    def test_mail_goes_to_the_minds_inbox_not_the_core(self):
        from teeny_hands import stimuli

        self.inbox(message("<m1@x>", subject="idea", text="gm teeny, love the repo", thread="t-1", stamp="2026-09-14T06:00:00Z"))
        out = inbox.ingest()
        self.assertEqual(out["offered"], 1)
        self.assertEqual(self.core.calls, [])
        [got] = stimuli.take(max_items=10, max_chars=10_000)["stimuli"]
        self.assertEqual((got["surface"], got["kind"], got["ref"]), ("mail", "message", "<m1@x>"))
        self.assertEqual((got["payload"]["thread_id"], got["payload"]["subject"]), ("t-1", "idea"))
        self.assertIn("love the repo", got["text"])


class BuildRequestsTest(Scratch):
    def test_what_counts_as_an_ask(self):
        for text in ("build me a site", "Could you make a bot that tracks tides?", "can u build this", "feature request: dark mode",
                     "pls build a tool", "I need a bot for my discord"):
            self.assertTrue(build_requests.looks_like_build_request(text), text)
        for text in ("gm", "I built a bot yesterday", "you could make it better", "love what you build"):
            self.assertFalse(build_requests.looks_like_build_request(text), text)

    def test_recorded_once_and_marked(self):
        self.assertTrue(build_requests.record("x:mentions", "1", "ana", "build me a site"))
        self.assertFalse(build_requests.record("x:mentions", "1", "ana", "build me a site"))
        rid = build_requests.listing()["requests"][0]["id"]
        build_requests.mark(rid, "picked", "doing it")
        self.assertEqual(build_requests.listing("picked")["requests"][0]["note"], "doing it")
        with self.assertRaises(hand.HandError):
            build_requests.mark(rid, "maybe")
