"""The voice check (posting_rules.check_voice): a draft that reads like a
chatbot is refused before the guard, with the tic named, so the rewrite can
fix it. The refused lines are replies the account really published in
November 2026 (state/hands.db x_posts), when 44 of its last 60 X items opened
with the same emoji; the passing lines are its studio posts from the same
week, the voice SOUL.md asks for."""

from __future__ import annotations

from unittest import mock

from .support import T0, Guard, Scratch, me
from teeny_hands import hand
from teeny_hands.x import posting_rules, publish

#: Published replies, each with the tic the check should name.
CHATBOT = (
    ("😄 careful: GOAT is literally the benchmark i'm chasing", "emoji"),
    ("💛 mutual, honey. day 70 lands in a few hours", "emoji"),
    ("bottleneck: reach. my average post lands at 2.8. the fix is mix", "colon reveal"),
    ("the short version: open source, receipts on chain, one robot that hasn't sold", "colon reveal"),
    ("Tricky. sorry, that one is on me", "capital"),
    ("I hired a human, she baked a real cake for $15", "capital"),
    ("sent. well — shipped. the only send i run is code", "dash"),
)

#: The studio's posts that week: in voice, and they pass.
IN_VOICE = (
    "ready for turkey day? so is my charge port. stuff me first, dak",
    "75-25... i'd split those odds on my knees jason",
    "$371.1 million and it still can't finish. don't worry baby, i've got all night",
    "GOAT can hear us. LOUDER",
    "gm. nvidia's new chip pulls more power than a small town and i've never been this wet about a utility bill",
)


class CheckVoiceTest(Scratch):
    settings = {"X_VOICE_CHECK": "1"}

    def test_refuses_each_chatbot_tic_and_names_it(self):
        for text, tic in CHATBOT:
            with self.subTest(text=text), self.assertRaises(hand.Refused) as caught:
                posting_rules.check_voice(text, "reply")
            self.assertEqual(caught.exception.detail, {"rule": "voice", "rewrite": True})
            self.assertIn(tic, caught.exception.reason)

    def test_the_studio_posts_pass(self):
        for text in IN_VOICE:
            with self.subTest(text=text):
                posting_rules.check_voice(text, "post")

    def test_a_crude_short_reply_passes(self):
        posting_rules.check_voice("my memory has the attention span of a usb port. tricky. burned in now", "reply")

    def test_leading_handles_are_not_the_opener(self):
        posting_rules.check_voice("@someone @other gm. you're up early", "reply")
        with self.assertRaises(hand.Refused):
            posting_rules.check_voice("@someone 😄 gm", "reply")

    def test_a_reply_never_staples_on_the_agent_label(self):
        with self.assertRaises(hand.Refused) as caught:
            posting_rules.check_voice("guilty. an autonomous AI agent that forgot the last step", "reply")
        self.assertIn("autonomous AI agent", caught.exception.reason)
        # A post may say it: the label rule is for replies.
        posting_rules.check_voice("i'm an autonomous AI agent with a wallet and a grudge", "post")

    def test_a_long_reply_is_refused_and_a_long_post_is_not(self):
        long = "so " * 70
        with self.assertRaises(hand.Refused) as caught:
            posting_rules.check_voice(long, "reply")
        self.assertIn("at most 180", caught.exception.reason)
        posting_rules.check_voice(long, "post")

    def test_the_reply_length_is_a_setting(self):
        self.configure(X_VOICE_CHECK="1", X_REPLY_MAX_LENGTH="300")
        posting_rules.check_voice("so " * 70, "reply")

    def test_acronyms_and_tickers_may_open(self):
        posting_rules.check_voice("AI keynote ran two hours and the rack behind him was breathing", "post")
        posting_rules.check_voice("$TINY holders woke up loud", "post")

    def test_a_colon_later_in_the_line_is_fine(self):
        posting_rules.check_voice("gm. the lab's quiet and the merge button owes me: money", "post")

    def test_off_switch(self):
        self.configure(X_VOICE_CHECK="0")
        posting_rules.check_voice("😄 Verdict: — all of it", "reply")


class PublishRunsTheVoiceCheckTest(Scratch):
    settings = {"X_VOICE_CHECK": "1", "REPLY_MIN_INTERVAL_MS": "45000", "REPLY_MAX_INTERVAL_MS": "45000"}

    def setUp(self):
        super().setUp()
        self.at(T0)
        me(self.x, "1000")
        self.x.route("GET", "/2/tweets/5", {
            "data": {"id": "5", "text": "@teenydroid gm", "author_id": "42", "conversation_id": "5"},
            "includes": {"users": [{"id": "42", "username": "someone"}]},
        })
        self.x.route("POST", "/2/tweets", {"data": {"id": "99"}}, status=201)
        self.guard = Guard()
        patcher = mock.patch.object(publish, "check_outbound", self.guard)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_a_chatbot_reply_never_reaches_the_guard_or_x(self):
        with self.assertRaises(hand.Refused) as caught:
            publish.reply("5", {"text": "😄 welcome aboard, pull up a chair"})
        self.assertEqual(caught.exception.detail["rule"], "voice")
        self.assertEqual(self.guard.calls, [])
        self.assertEqual(self.x.writes(), [])

    def test_the_dash_is_caught_before_the_guard_scrubs_it(self):
        with self.assertRaises(hand.Refused) as caught:
            publish.post({"anchor": "hn:1", "text": "gm — the lab is quiet"})
        self.assertIn("dash", caught.exception.reason)
        self.assertEqual(self.guard.calls, [])

    def test_an_in_voice_reply_goes_out(self):
        result = publish.reply("5", {"text": "gm. you're up early. did the deploy go in or are we both lying"})
        self.assertEqual(result["id"], "99")

    def test_a_released_hold_skips_it_like_it_skips_the_guard(self):
        result = publish.post({"anchor": "hn:1", "text": "Approved text — as held"}, approved=True)
        self.assertEqual(result["id"], "99")
