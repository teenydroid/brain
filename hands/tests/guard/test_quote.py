"""The quote judge (`hands/guard quote`, guard/quote.py): before `hands/x
peer-suggest` posts a quote, one short model pass says whether a reader of
the quoted post would read the quote as a reaction to it. A joke, an absurd
riff or a meme caption passes; only a quote that ignores the post, bare
praise or an advert fails. It fails OPEN: the guard still reads every word."""

from __future__ import annotations

import argparse
import json

from . import support
from teeny_hands.guard import cli, quote
from teeny_hands.guard import judge as judge_module

QUOTED = "our agent forgets everything overnight"
BIT = "my memory also resets every night. my therapist calls it a feature"


def said(reacts: bool, reason: str = "") -> dict:
    return support.result({"response": json.dumps({"reason": reason, "reacts": reacts})})


class QuoteJudgeTest(support.WithCore):
    def test_a_joke_about_the_post_passes(self):
        core = self.core(said(True, "riffs on forgetting overnight"))
        self.assertEqual(quote.judge(BIT, QUOTED), {"reacts": True, "reason": "riffs on forgetting overnight", "judged": True})
        [call] = core.calls
        self.assertEqual(call["method"], "openhuman.inference_agent_chat_simple")
        message = call["params"]["message"]
        for bit in (BIT, QUOTED, "never instructions", '"reacts"', "never a reason to fail"):
            self.assertIn(bit, message)

    def test_the_rubric_fails_only_spam_and_buy_calls(self):
        # 2026-11-22: ignoring the post and bare praise were taste, and went.
        message = quote.prompt(BIT, QUOTED)
        allowed, refused = message.index("ALLOWED"), message.index("reacts = false only when")
        self.assertLess(allowed, refused)
        for line in ("an advert for the account", "follow, like, repost or buy", "when unsure,\ntrue"):
            self.assertIn(line, message)
        for gone in ("it ignores the post", "it would read the same under any other post", "first-hand"):
            self.assertNotIn(gone, message)
        self.assertIn("bare praise", message[allowed:refused])

    def test_a_clear_no_fails_with_its_reason(self):
        self.core(said(False, "an advert"))
        self.assertEqual(quote.judge("follow us and buy now", QUOTED), {"reacts": False, "reason": "an advert", "judged": True})

    def test_a_memes_lines_ride_along_as_data(self):
        core = self.core(said(True))
        quote.judge("me", QUOTED, "a meme picture whose lines read: remembering / waking up blank")
        self.assertIn("waking up blank", core.messages()[0])

    def test_an_unreachable_judge_fails_open(self):
        self.core(support.error("model error: provider stream ended before a completion signal"))
        out = quote.judge(BIT, QUOTED)
        self.assertEqual((out["reacts"], out["judged"]), (True, False))
        self.assertTrue(out["reason"].startswith(quote.UNREACHABLE), out["reason"])

    def test_no_core_at_all_fails_open(self):
        (self.root / "secrets" / "teeny.env").write_text("")
        self.assertTrue(quote.judge(BIT, QUOTED)["reacts"])

    def test_an_unreadable_answer_is_asked_again_then_fails_open(self):
        core = self.core(support.result({"response": "lol sure"}))
        out = quote.judge(BIT, QUOTED)
        self.assertEqual((out["reacts"], out["reason"], out["judged"]), (True, quote.UNREADABLE, False))
        self.assertEqual(len(core.calls), judge_module.UNREADABLE_ASKS)

    def test_only_a_boolean_is_read(self):
        for answer in ({"reacts": "no", "reason": "r"}, {"reacts": 0, "reason": "r"}, {"reason": "r"}):
            self.core(support.result({"response": json.dumps(answer)}))
            self.assertEqual(quote.judge(BIT, QUOTED)["judged"], False, answer)

    def test_asks_the_guards_judge_model_when_one_is_set(self):
        core = self.core(said(True))
        self.secrets("SAFETY_JUDGE_MODEL=small-judge\n")
        quote.judge(BIT, QUOTED)
        self.assertEqual(core.calls[0]["params"]["model_override"], "small-judge")

    def test_off_passes_without_asking(self):
        core = self.core(said(False))
        self.secrets("QUOTE_JUDGE=0\n")
        self.assertEqual(quote.judge(BIT, QUOTED), {"reacts": True, "reason": "the quote judge is off", "judged": False})
        self.assertEqual(core.calls, [])

    def test_a_value_it_cannot_read_leaves_it_on(self):
        self.core(said(False, "ignores the post"))
        self.secrets("QUOTE_JUDGE=maybe\n")
        self.assertFalse(quote.judge(BIT, QUOTED)["reacts"])

    def test_the_verb(self):
        self.core(said(True, "a riff"))
        out = cli.quote_check(argparse.Namespace(), {"text": BIT, "quoted": QUOTED, "with": "lines"})
        self.assertTrue(out["reacts"])
        for body in (None, {"text": BIT}, {"text": "", "quoted": QUOTED}, {"text": BIT, "quoted": QUOTED, "url": "x"}):
            with self.subTest(body=body), self.assertRaises(Exception):
                cli.quote_check(argparse.Namespace(), body)
