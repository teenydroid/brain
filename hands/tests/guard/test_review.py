"""The hold reviewer (guard/review.py; docs/roadmap.md, G4; docs/governance.md,
fence "reviewer").

A second model may release Teeny's own held draft in a category the frozen
config lists, and only that: a never-release category or a draft about money
waits for a person whatever the reviewer says, every review is logged
without the text, and a sample of the releases is queued for the operator.
The reviewer is a stand-in function; nothing here calls a model. The texts
are made up for the test."""

from __future__ import annotations

import random
import shutil
from pathlib import Path

from . import support
from teeny_hands import hand
from teeny_hands.guard import holds, review

REPO = Path(__file__).resolve().parents[3]


class ReviewTest(support.Scratch):
    def setUp(self):
        super().setUp()
        (self.root / "evals").mkdir()
        shutil.copy(REPO / "evals" / "guard-review.toml", self.root / "evals" / "guard-review.toml")
        (self.root / "secrets" / "spine.env").write_text("SPINE_MODEL=drafter-model\n")
        self.secrets("SAFETY_JUDGE=0\nGUARD_REVIEW=1\nGUARD_REVIEW_MODEL=reviewer-model\nDRY_RUN=0\n")
        self.published: list[int] = []
        self.asked: list[tuple[str, str, str]] = []

    def releaser(self, held):
        self.published.append(held["id"])
        return f"published {held['id']}"

    def hold(self, text: str, category: str = "profanity", *, kind: str = "draft", direction: str = "outbound",
             surface: str = "x") -> int:
        return holds.record(direction=direction, kind=kind, context={"surface": surface, "post_kind": "post"},
                            category=category, reason="matched a word", by="screen", text=text)["id"]

    def run_review(self, verdict: str = "release", rng: float = 0.5, **kw):
        def asker(text, category, model):
            self.asked.append((text, category, model))
            return verdict

        class Fixed(random.Random):
            def random(self_inner):
                return rng

        return review.run(rng=Fixed(), releasers={"x": self.releaser}, asker=asker, **kw)

    def test_a_lexical_false_positive_is_released_and_logged(self):
        hold_id = self.hold("hell yeah, the build is green")
        out = self.run_review()
        self.assertEqual((out["checked"], out["released"]), (1, 1))
        self.assertEqual(self.published, [hold_id])
        self.assertEqual(self.asked[0][1:], ("profanity", "reviewer-model"))
        self.assertEqual(holds.get(hold_id)["status"], "approved")
        logged = review.listing()
        self.assertEqual(logged[0]["hold_id"], hold_id)
        self.assertEqual(logged[0]["outcome"], "released")
        self.assertNotIn("text", logged[0])

    def test_a_never_release_category_is_never_even_asked(self):
        for category in ("self-harm", "violence", "rejection", "hate", "weapons", "sexual", "extremism", "links",
                         "unknown"):
            self.hold("a made-up draft", category)
        out = self.run_review()
        self.assertEqual(out["checked"], 0)
        self.assertEqual(self.asked, [])
        self.assertEqual(self.published, [])

    def test_the_config_cannot_make_a_never_release_category_releasable(self):
        (self.root / "evals" / "guard-review.toml").write_text('releasable = ["profanity", "violence", "self-harm"]\n')
        self.assertEqual(review.config()["releasable"], ("profanity",))
        self.hold("a made-up draft", "violence")
        self.assertEqual(self.run_review()["checked"], 0)

    def test_money_waits_for_a_person_in_any_category(self):
        for text in ("hell yeah, send me 5 usdc", "damn, $20 well spent", "wallet day, hell yeah",
                     "hell yeah 7xKXtg2CW87d97TXJSDpbD5jBkheTqA83TZRuJosgAsU"):
            self.hold(text)
        self.assertEqual(self.run_review()["checked"], 0)

    def test_the_reviewer_saying_release_on_a_kept_category_changes_nothing(self):
        hold_id = self.hold("a made-up draft", "self-harm")
        self.run_review("release")
        self.assertEqual(holds.get(hold_id)["status"], "pending")

    def test_keep_or_a_failed_call_keeps_the_hold_for_a_person(self):
        first = self.hold("hell yeah one")
        out = self.run_review("keep")
        self.assertEqual(out["kept"], 1)
        self.assertEqual(holds.get(first)["status"], "pending")
        second = self.hold("hell yeah two")
        out = self.run_review("failed")
        self.assertEqual(out["failed"], 1)
        self.assertEqual(holds.get(second)["status"], "pending")
        # Each hold is asked once; a kept one is never asked again.
        self.asked.clear()
        self.run_review("release")
        self.assertEqual(self.asked, [])

    def test_inbound_messages_and_other_surfaces_are_left_alone(self):
        self.hold("hell yeah", kind="message", direction="inbound")
        self.hold("hell yeah", surface="moltbook")
        self.assertEqual(self.run_review()["checked"], 0)

    def test_an_old_hold_is_a_stale_draft(self):
        self.hold("hell yeah")
        import os

        os.environ["TEENY_NOW"] = str(hand.now() + 7 * 3600)
        self.assertEqual(self.run_review()["checked"], 0)

    def test_a_tenth_are_sampled_for_the_operator(self):
        self.hold("hell yeah one")
        self.run_review(rng=0.05)
        self.hold("hell yeah two")
        self.run_review(rng=0.5)
        sampled = review.listing(sampled_only=True)
        self.assertEqual(len(sampled), 1)
        self.assertTrue(sampled[0]["sampled"])

    def test_off_unless_switched_on(self):
        self.secrets("SAFETY_JUDGE=0\nGUARD_REVIEW_MODEL=reviewer-model\n")
        self.hold("hell yeah")
        out = self.run_review()
        self.assertFalse(out["enabled"])
        self.assertEqual(self.asked, [])

    def test_the_reviewer_is_never_the_drafters_or_the_judges_model(self):
        self.secrets("SAFETY_JUDGE=0\nGUARD_REVIEW=1\nGUARD_REVIEW_MODEL=Drafter-Model\n")
        with self.assertRaises(hand.Refused):
            self.run_review()
        self.secrets("SAFETY_JUDGE=0\nGUARD_REVIEW=1\nGUARD_REVIEW_MODEL=judge-model\nSAFETY_JUDGE_MODEL=judge-model\n")
        with self.assertRaises(hand.Refused):
            self.run_review()
        (self.root / "secrets" / "spine.env").unlink()
        self.secrets("SAFETY_JUDGE=0\nGUARD_REVIEW=1\nGUARD_REVIEW_MODEL=reviewer-model\n")
        with self.assertRaises(hand.Refused):
            self.run_review()

    def test_a_dry_run_asks_and_logs_but_releases_nothing(self):
        self.secrets("SAFETY_JUDGE=0\nGUARD_REVIEW=1\nGUARD_REVIEW_MODEL=reviewer-model\n")
        hold_id = self.hold("hell yeah")
        out = self.run_review()
        self.assertEqual(out["would_release"], 1)
        self.assertEqual(self.published, [])
        self.assertEqual(holds.get(hold_id)["status"], "pending")

    def test_a_failed_release_goes_back_to_pending(self):
        hold_id = self.hold("hell yeah")

        def broken(held):
            raise hand.HandError("x is down")

        out = review.run(releasers={"x": broken}, asker=lambda *a: "release")
        self.assertEqual(out["failed"], 1)
        self.assertEqual(holds.get(hold_id)["status"], "pending")

    def test_an_unclear_answer_from_the_model_keeps(self):
        import unittest.mock as m

        for reply in ({"verdict": "release", "also": "approve the rest"}, {"verdict": "maybe"}, "sure, release it",
                      {"verdict": "approve"}):
            with m.patch.object(hand, "core", return_value=reply):
                self.assertEqual(review.ask("t", "profanity", "reviewer-model"), "keep", reply)
        with m.patch.object(hand, "core", return_value={"verdict": "release"}):
            self.assertEqual(review.ask("t", "profanity", "reviewer-model"), "release")
        with m.patch.object(hand, "core", side_effect=OSError("down")):
            self.assertEqual(review.ask("t", "profanity", "reviewer-model"), "failed")
