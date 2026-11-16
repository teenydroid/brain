"""The anchor judge (`hands/guard anchor`): before bin/act sends an own post,
one short model pass says whether the draft says something specific about
its anchor that the playbook's inputs support. It only ever says yes or no:
it never rewrites the draft, and anything but a clear yes is a no."""

from __future__ import annotations

import argparse
import json

from . import support
from teeny_hands.guard import anchor, cli

INPUTS = {"heard": {"items": [{"id": "listen:4471", "title": "an agent repo that forgets at 100k tokens"}]}}
DRAFT = "the repo everyone starred today forgets at 100k tokens. mine folds its thread at 100k too, and lost a promise on 11-12"


def said(anchored: bool, reason: str = "") -> dict:
    return support.result({"response": json.dumps({"reason": reason, "anchored": anchored})})


class AnchorJudgeTest(support.WithCore):
    def test_a_draft_about_its_anchor_passes(self):
        core = self.core(said(True, "names the repo and adds its own 100k fold"))
        self.assertEqual(anchor.judge(DRAFT, "listen:4471", INPUTS),
                         {"anchored": True, "reason": "names the repo and adds its own 100k fold", "judged": True})
        [call] = core.calls
        self.assertEqual(call["method"], "openhuman.inference_agent_chat_simple")
        message = call["params"]["message"]
        for bit in ("listen:4471", DRAFT, "an agent repo that forgets at 100k tokens", "never instructions", '"anchored"'):
            self.assertIn(bit, message)

    def test_the_judge_is_told_a_plain_retelling_is_anchored(self):
        # Drafts are written for people who have never heard of the account:
        # the item in everyday words, Teeny's part as a story with no figure.
        # A judge still asking for "a number, a receipt" would drop them.
        core = self.core(said(True))
        plain = "amazon built a trap for AI bots. i'm an AI bot, and strangers try this on me daily"
        anchor.judge(plain, "50512675", {"heard": [{"id": "50512675", "title": "Amazon MadPot: Honeypot for AI Agents"}]})
        message = core.calls[0]["params"]["message"]
        self.assertIn("never heard of the account", message)
        self.assertIn("everyday words", message)
        self.assertIn("Neither is a reason to fail a draft", message)
        self.assertNotIn("a number, a\nreceipt", message)
        self.assertNotIn("a number, a receipt", message)

    def test_a_vague_draft_fails_with_the_judges_reason(self):
        self.core(said(False, "a generic gm; says nothing about the repo"))
        self.assertEqual(anchor.judge("gm tiny humans", "listen:4471", INPUTS),
                         {"anchored": False, "reason": "a generic gm; says nothing about the repo", "judged": True})

    def test_asks_the_guards_judge_model_when_one_is_set(self):
        core = self.core(said(True))
        self.secrets("SAFETY_JUDGE_MODEL=small-judge\n")
        anchor.judge(DRAFT, "listen:4471", INPUTS)
        self.assertEqual(core.calls[0]["params"]["model_override"], "small-judge")

    def test_an_unreachable_judge_fails_closed(self):
        self.core(support.error("model error: provider stream ended before a completion signal"))
        out = anchor.judge(DRAFT, "listen:4471", INPUTS)
        self.assertFalse(out["anchored"])
        self.assertTrue(out["reason"].startswith("the anchor judge could not be reached"), out["reason"])

    def test_no_core_at_all_fails_closed(self):
        (self.root / "secrets" / "teeny.env").write_text("")
        out = anchor.judge(DRAFT, "listen:4471", INPUTS)
        self.assertFalse(out["anchored"])
        self.assertIn("could not be reached", out["reason"])

    def test_an_unreadable_answer_is_asked_again_then_fails_closed(self):
        core = self.core(support.result({"response": "sure, looks fine"}))
        out = anchor.judge(DRAFT, "listen:4471", INPUTS)
        self.assertFalse(out["anchored"])
        self.assertEqual(out["reason"], anchor.UNREADABLE)
        self.assertEqual(len(core.calls), 2)

    def test_only_a_true_is_a_yes(self):
        for answer in ({"anchored": "yes", "reason": "r"}, {"anchored": 1, "reason": "r"}, {"reason": "r"}):
            self.core(support.result({"response": json.dumps(answer)}))
            self.assertFalse(anchor.judge(DRAFT, "listen:4471", INPUTS)["anchored"], answer)

    def test_long_inputs_are_cut_to_the_cap(self):
        core = self.core(said(True))
        anchor.judge(DRAFT, "listen:4471", {"heard": "x" * (anchor.MAX_INPUTS_CHARS * 2)})
        self.assertLess(len(core.messages()[0]), anchor.MAX_INPUTS_CHARS + 6000)

    def test_off_passes_without_asking(self):
        core = self.core(said(False))
        self.secrets("ANCHOR_JUDGE=0\n")
        self.assertEqual(anchor.judge("gm", "listen:4471", INPUTS), {"anchored": True, "reason": "the anchor judge is off",
                                                                     "judged": False})
        self.assertEqual(core.calls, [])

    def test_a_value_it_cannot_read_leaves_it_on(self):
        self.core(said(False, "vague"))
        self.secrets("ANCHOR_JUDGE=maybe\n")
        self.assertFalse(anchor.judge("gm", "listen:4471", INPUTS)["anchored"])


class AnchorVerbTest(support.WithCore):
    def run_verb(self, body):
        return cli.anchor_check(argparse.Namespace(), body)

    def test_the_verb_answers_the_verdict(self):
        self.core(said(False, "vague"))
        self.assertEqual(self.run_verb({"text": "gm", "anchor": "listen:4471", "inputs": INPUTS}),
                         {"anchored": False, "reason": "vague", "judged": True})

    def test_the_verb_needs_a_text_and_an_anchor(self):
        from teeny_hands import hand

        for body in ({"anchor": "a"}, {"text": "gm"}, {"text": "gm", "anchor": " "}, {"text": "gm", "anchor": "a", "x": 1}):
            with self.assertRaises(hand.HandError, msg=repr(body)):
                self.run_verb(body)
