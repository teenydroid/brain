"""The anchor judge (`hands/guard anchor`): before bin/act sends an own post,
one short model pass says whether the draft says something specific about
its anchor that the playbook's inputs support. It only ever says yes or no:
it never rewrites the draft, and anything but a clear yes is a no."""

from __future__ import annotations

import argparse
import json
from unittest import mock

from . import support
from teeny_hands import hand
from teeny_hands.guard import anchor, cli
from teeny_hands.guard import judge as judge_module

INPUTS = {"heard": {"items": [{"id": "listen:4471", "title": "an agent repo that forgets at 100k tokens"}]}}
# What the core said, live, on 2026-11-16 when the 23:00 show post was lost.
STREAM_CUT = "model error: local-openai returned: provider stream ended before a completion signal"
# The guard judge's waits (support.WithCore sets them to nothing; the tests
# that look at the waits put these back, on a fake clock).
BACKOFFS = judge_module.RETRY_BACKOFFS
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
        self.assertEqual(len(core.calls), judge_module.UNREADABLE_ASKS)

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


class AnchorJudgeRetryTest(support.WithCore):
    """2026-11-16: the 23:00 show post was skipped as "not anchored: the
    anchor judge could not be reached ... provider stream ended" inside one
    of Surplus's drop windows, its two asks seconds apart. The anchor judge
    now waits between asks the way the guard judge does."""

    def clocked(self, core):
        """A fake clock that sleeps and asks advance; CORE answers each ask.
        Returns the clock and the waits made."""
        self.core(said(True))
        clock = [1000.0]
        slept: list[float] = []

        def sleep(seconds):
            slept.append(seconds)
            clock[0] += seconds

        patches = [mock.patch.object(judge_module, "RETRY_BACKOFFS", BACKOFFS),
                   mock.patch.object(judge_module.time, "sleep", sleep),
                   mock.patch.object(judge_module.time, "monotonic", lambda: clock[0]),
                   mock.patch.object(judge_module.hand, "core", lambda method, params, timeout: core(clock, timeout))]
        for patch in patches:
            patch.start()
            self.addCleanup(patch.stop)
        return clock, slept

    def test_a_dropped_stream_is_waited_out_and_judged(self):
        asks: list[float] = []

        def core(clock, timeout):
            clock[0] += 1.0
            asks.append(clock[0])
            if len(asks) <= 2:
                raise hand.CoreError(STREAM_CUT)
            return {"response": json.dumps({"reason": "names the repo", "anchored": True})}

        _, slept = self.clocked(core)
        self.assertEqual(anchor.judge(DRAFT, "listen:4471", INPUTS),
                         {"anchored": True, "reason": "names the repo", "judged": True})
        self.assertEqual(slept, [2.0, 6.0])
        self.assertEqual(len(asks), 3)

    def test_a_permanent_error_is_not_asked_again(self):
        calls: list[float] = []

        def core(clock, timeout):
            calls.append(timeout)
            raise hand.CoreError("model error: 401 unauthorized")

        _, slept = self.clocked(core)
        out = anchor.judge(DRAFT, "listen:4471", INPUTS)
        self.assertFalse(out["anchored"])
        self.assertTrue(out["reason"].startswith(f"{anchor.UNREACHABLE}: "), out["reason"])
        self.assertEqual((len(calls), slept), (1, []))

    def test_a_judge_that_never_answers_fails_closed_within_the_budget(self):
        calls: list[float] = []

        def core(clock, timeout):
            calls.append(timeout)
            clock[0] += 1.0
            raise hand.CoreError(STREAM_CUT)

        clock, slept = self.clocked(core)
        out = anchor.judge(DRAFT, "listen:4471", INPUTS)
        self.assertEqual(out["anchored"], False)
        self.assertEqual(out["judged"], True)
        self.assertTrue(out["reason"].startswith(f"{anchor.UNREACHABLE}: "), out["reason"])
        self.assertIn("provider stream ended", out["reason"])
        self.assertEqual(slept, [2.0, 6.0, 15.0, 30.0])
        self.assertEqual(len(calls), judge_module.MAX_ASKS)
        self.assertLessEqual(clock[0] - 1000.0, anchor.BUDGET)

    def test_asks_that_time_out_stay_within_the_budget(self):
        # bin/act runs inside the 300 s the core gives a cron shell call, and
        # the draft turn has already used most of it: the judge's whole wait
        # is bounded by BUDGET, which is less than the two 60 s asks it had.
        timeouts: list[float] = []

        def core(clock, timeout):
            timeouts.append(timeout)
            clock[0] += timeout
            raise hand.CoreError("core unreachable: timed out")

        clock, _ = self.clocked(core)
        self.assertFalse(anchor.judge(DRAFT, "listen:4471", INPUTS)["anchored"])
        self.assertEqual(timeouts, [anchor.TIMEOUT, anchor.BUDGET - anchor.TIMEOUT - 2.0])
        self.assertLessEqual(clock[0] - 1000.0, anchor.BUDGET)
        self.assertLessEqual(anchor.BUDGET, 90.0)

    def test_an_unreadable_answer_is_asked_again_at_once(self):
        replies = [{"response": "sure, looks fine"}, {"response": json.dumps({"reason": "r", "anchored": True})}]
        calls: list[float] = []

        def core(clock, timeout):
            calls.append(timeout)
            return replies.pop(0)

        _, slept = self.clocked(core)
        self.assertTrue(anchor.judge(DRAFT, "listen:4471", INPUTS)["anchored"])
        self.assertEqual((len(calls), slept), (2, []))


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
