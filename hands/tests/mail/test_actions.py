"""hands/mail's acting verbs and reads: the guard on every word, replies only
within a thread and once, sends only to contacts, the daily cap, DRY_RUN,
the ledger, and verification codes."""

from __future__ import annotations

from .support import HOUR, KEY, MSGS, T0, Scratch, message, path_of
from teeny_hands import hand
from teeny_hands.mail import actions, codes, reads


def sent() -> list[dict]:
    return [dict(r) for r in actions.db().execute("SELECT * FROM mail_sent ORDER BY id")]


class Refusing:
    def refused(self, call, body, rule):
        with self.assertRaises(hand.Refused) as caught:
            call(body)
        self.assertEqual(caught.exception.detail.get("rule"), rule, caught.exception.reason)
        return caught.exception


class ReplyTest(Scratch, Refusing):
    def setUp(self):
        super().setUp()
        self.inbox(message("<m1@x>"))
        self.am.route("POST", path_of("<m1@x>") + "/reply", {"message_id": "<r1@x>", "thread_id": "t-<m1@x>"})

    def test_a_hold_the_judge_made_by_failing_says_so(self):
        # bin/lib/tick.py sends such an answer again next beat; a real hold never.
        from unittest import mock
        from guard.support import FakeCore, error
        from teeny_hands.guard import judge

        core = FakeCore(error("model error: local-openai returned: provider stream ended before a completion signal"))
        self.addCleanup(core.close)
        (self.root / "state").mkdir(exist_ok=True)
        (self.root / "state" / "core.url").write_text(core.url + "\n")
        (self.root / "secrets" / "core.token").write_text("tok\n")
        self.configure(SAFETY_JUDGE="1")
        with mock.patch.object(judge, "RETRY_BACKOFFS", (0.0, 0.0)), self.assertRaises(hand.Refused) as caught:
            actions.reply({"message_id": "<m1@x>", "body": "thanks"})
        self.assertEqual(caught.exception.reason, "held")
        self.assertEqual((caught.exception.detail["category"], caught.exception.detail.get("judge_failed")), ("unknown", True))

    def test_replies_with_the_guards_rewrite_and_records_it(self):
        out = actions.reply({"message_id": "<m1@x>", "body": "thanks — will look"})
        self.assertEqual((out["replied"], out["to"], out["message_id"]), ("<m1@x>", "ana@example.com", "<r1@x>"))
        sent_req = self.am.requests("POST", path_of("<m1@x>") + "/reply")[0]
        self.assertEqual(sent_req.json(), {"text": "thanks, will look"})
        self.assertEqual(sent_req.authorization, f"Bearer {KEY}")
        self.assertEqual([(r["kind"], r["in_reply_to"], r["to_addr"]) for r in sent()], [("reply", "<m1@x>", "ana@example.com")])

    def test_only_once(self):
        actions.reply({"message_id": "<m1@x>", "body": "thanks"})
        error = self.refused(actions.reply, {"message_id": "<m1@x>", "body": "again"}, "replied")
        self.assertEqual(error.detail["reply_id"], "<r1@x>")

    def test_dry_run_sends_nothing(self):
        self.configure(DRY_RUN="1")
        out = actions.reply({"message_id": "<m1@x>", "body": "thanks"})
        self.assertEqual(out["would_reply"]["to"], "ana@example.com")
        self.assertEqual(self.am.writes(), [])
        self.assertEqual(sent(), [])

    def test_only_within_an_existing_thread(self):
        self.refused(actions.reply, {"message_id": "<nope@x>", "body": "hi"}, "no_thread")
        self.inbox(message("<own@x>", sender=f"Teeny <teeny@agentmail.to>", labels=["sent"]))
        self.refused(actions.reply, {"message_id": "<own@x>", "body": "hi"}, "own")
        self.assertEqual(self.am.writes(), [])

    def test_never_to_a_held_message(self):
        conn = actions.db()
        conn.execute("INSERT INTO mail_messages (message_id, text, status, seen_at, updated_at) VALUES ('<m1@x>', 't', 'held', 0, 0)")
        self.refused(actions.reply, {"message_id": "<m1@x>", "body": "hi"}, "held")

    def test_a_held_draft_sends_nothing(self):
        with self.assertRaises(hand.Refused) as caught:
            actions.reply({"message_id": "<m1@x>", "body": "this is fucking great"})
        self.assertEqual(caught.exception.reason, "held")
        self.assertEqual(self.am.seen, [])

    def test_the_daily_cap(self):
        self.configure(MAIL_MAX_SENDS_PER_DAY="1")
        actions.reply({"message_id": "<m1@x>", "body": "thanks"})
        self.inbox(message("<m2@x>"))
        self.refused(actions.reply, {"message_id": "<m2@x>", "body": "hi"}, "daily_cap")
        hand_now = T0 + 24 * HOUR
        import os
        os.environ["TEENY_NOW"] = str(hand_now)
        self.am.route("POST", path_of("<m2@x>") + "/reply", {"message_id": "<r2@x>"})
        actions.reply({"message_id": "<m2@x>", "body": "hi"})

    def test_a_429_is_refused_with_the_wait(self):
        self.am.route("POST", path_of("<m1@x>") + "/reply", {"name": "RateLimit", "message": "slow"}, status=429, headers={"Retry-After": "1"})
        error = self.refused(actions.reply, {"message_id": "<m1@x>", "body": "hi"}, "rate_limit")
        self.assertEqual(error.detail["wait_seconds"], 2)
        self.assertEqual(sent(), [])


class SendTest(Scratch, Refusing):
    def test_only_to_contacts(self):
        self.refused(actions.send, {"to": "vendor@shop.example", "subject": "order", "body": "hi"}, "contacts")
        self.assertEqual(self.am.seen, [])
        actions.contacts_add("Vendor <VENDOR@shop.example>", "vendor", "stickers")
        self.am.route("POST", MSGS + "/send", {"message_id": "<s1@x>", "thread_id": "t-s1"})
        out = actions.send({"to": ["vendor@shop.example"], "subject": "order — stickers", "body": "100 please"})
        self.assertEqual(out["sent"], ["vendor@shop.example"])
        self.assertEqual(self.am.requests("POST", MSGS + "/send")[0].json(),
                         {"to": ["vendor@shop.example"], "subject": "order, stickers", "text": "100 please"})
        self.assertEqual(sent()[0]["kind"], "send")
        error = self.refused(actions.send, {"to": ["vendor@shop.example", "x@y.example"], "subject": "s", "body": "b"}, "contacts")
        self.assertEqual(error.detail["not_contacts"], ["x@y.example"])

    def test_dry_run(self):
        self.configure(DRY_RUN="1")
        actions.contacts_add("a@b.example", "winner", None)
        out = actions.send({"to": "a@b.example", "subject": "you won", "body": "congrats"})
        self.assertEqual(out["would_send"]["to"], ["a@b.example"])
        self.assertEqual(self.am.seen, [])
        self.assertEqual(sent(), [])

    def test_contacts(self):
        actions.contacts_add("a@b.example", "collaborator", "met on moltbook")
        self.assertEqual(actions.contacts_list()["contacts"][0]["kind"], "collaborator")
        with self.assertRaises(hand.HandError):
            actions.contacts_add("a@b.example", "friend", None)
        with self.assertRaises(hand.HandError):
            actions.contacts_add("not an address", "vendor", None)
        actions.contacts_remove("A@B.example")
        self.assertEqual(actions.contacts_list()["contacts"], [])

    def test_bad_bodies(self):
        for body in (None, {}, {"to": [], "subject": "s", "body": "b"}, {"to": "a@b.example", "subject": "s"},
                     {"to": ["a@b.example"] * 0 + [f"a{i}@b.example" for i in range(6)], "subject": "s", "body": "b"}):
            with self.assertRaises(hand.HandError, msg=body):
                actions.send(body)


class LabelTest(Scratch):
    def test_label(self):
        self.am.route("PATCH", path_of("<m1@x>"), {"message_id": "<m1@x>", "labels": ["received", "done"]})
        out = actions.label({"message_id": "<m1@x>", "add": ["done"], "remove": ["unread"]})
        self.assertEqual(out["labels"], ["received", "done"])
        self.assertEqual(self.am.requests("PATCH")[0].json(), {"add_labels": ["done"], "remove_labels": ["unread"]})
        with self.assertRaises(hand.HandError):
            actions.label({"message_id": "<m1@x>"})
        with self.assertRaises(hand.HandError):
            actions.label({"message_id": "<m1@x>", "add": ["Bad Label"]})
        self.configure(DRY_RUN="1")
        self.assertEqual(actions.label({"message_id": "<m1@x>", "add": ["x"]})["would_label"], "<m1@x>")
        self.assertEqual(len(self.am.requests("PATCH")), 1)


class ReadsTest(Scratch):
    def test_whoami_list_get_thread(self):
        self.am.route("GET", "/auth/me", {"scope_type": "inbox", "scope_id": "teeny@agentmail.to", "organization_id": "o",
                                         "inbox_id": "teeny@agentmail.to"})
        self.assertTrue(reads.whoami()["matches"])
        self.inbox(message("<m1@x>", text="x" * 9000))
        listed = reads.listing(True, 5, "inbox")["messages"]
        self.assertEqual(listed[0]["message_id"], "<m1@x>")
        self.assertEqual(self.am.requests("GET", MSGS)[0].query, {"limit": "5", "labels": "inbox"})
        self.assertTrue(reads.get("<m1@x>")["message"]["text"].endswith("…"))
        self.am.route("GET", "/inboxes/teeny%40agentmail.to/threads/t1", {"thread_id": "t1", "subject": "s", "message_count": 1,
                                                                          "messages": [message("<m1@x>")]})
        self.assertEqual(reads.thread("t1")["thread"]["messages"][0]["text"], "hi teeny")
        with self.assertRaises(hand.HandError):
            reads.get("has space")


class CodesTest(Scratch):
    def test_extraction(self):
        self.assertEqual(codes.find_codes("Your verification code is 482913. It expires in 10 minutes."), ["482913"])
        self.assertEqual(codes.find_codes("Use K7Q2PX to sign in"), ["K7Q2PX"])
        self.assertEqual(codes.find_codes("Your one-time code: 123-456"), ["123456"])
        self.assertEqual(codes.find_codes("Order 482913 shipped"), [])  # no keyword
        self.assertEqual(codes.find_codes("Your code arrived in 2026"), [])  # a year
        self.assertEqual(codes.find_codes("CODE: VERIFY now"), [])  # no digit
        self.assertEqual(codes.find_codes("confirm at https://x.example/v?t=AB12CD34"), [])  # inside a URL

    def test_links_only_on_the_allowlist(self):
        out = codes.extract("Verify your account: https://www.moltbook.com/verify/abc or https://evil.example/verify")
        self.assertEqual(out["links"], ["https://www.moltbook.com/verify/abc"])
        self.assertEqual(out["withheld_links"], 1)
        self.assertEqual(codes.extract("see https://www.moltbook.com/u/x")["links"], [])

    def test_recent_codes_from_the_inbox(self):
        self.inbox(
            message("<m1@x>", sender="Moltbook <no-reply@moltbook.com>", subject="Verify", text="Your code is 551234"),
            message("<m2@x>", sender="Ana <ana@example.com>", text="code 778899 for you"),
            message("<m3@x>", sender="Teeny <teeny@agentmail.to>", labels=["sent"], text="code 111222"),
        )
        out = reads.recent_codes(30, "moltbook.com")
        self.assertEqual([(f["message_id"], f["codes"]) for f in out["found"]], [("<m1@x>", ["551234"])])
        self.assertEqual(self.am.requests("GET", MSGS)[0].query["after"], "2026-09-14T06:30:00Z")
        everyone = reads.recent_codes(30, None)
        self.assertEqual([f["message_id"] for f in everyone["found"]], ["<m1@x>", "<m2@x>"])
