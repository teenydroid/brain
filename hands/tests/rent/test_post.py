"""hands/rent post: the brief's rules, the caps and the dry run, before and
around the one call that spends."""

from __future__ import annotations

import io
import json
import sys
import unittest
from contextlib import redirect_stdout

from rent.support import GOOD, KEY, Crash, Scratch, create_bounty, created, error, preview
from teeny_hands import hand
from teeny_hands.rent import actions, cli


class PostTest(Scratch):
    def test_posts_once_with_its_key_after_a_dry_run(self) -> None:
        self.rah.on("create_bounty", create_bounty(2360))
        out = actions.post(dict(GOOD))
        self.assertEqual(out["status"], "posted")
        dry, real = self.rah.made("create_bounty")
        self.assertTrue(dry.arguments["dryRun"])
        self.assertNotIn("dryRun", real.arguments)
        self.assertEqual(real.arguments["idempotencyKey"], "teeny-rent-1")
        self.assertEqual(real.arguments["pay"], {"type": "fixed", "amount": 1800, "estimatedHours": 1})
        self.assertIn("Teeny, an AI agent", real.arguments["description"])
        self.assertEqual(real.headers["x-api-key"], KEY)
        self.assertNotIn("authorization", real.headers)
        [job] = self.jobs()
        self.assertEqual((job["status"], job["bounty_id"], job["total_cents"]), ("posted", "b1", 2360))
        self.assertEqual(self.leaked(), [])

    def test_a_bounty_over_the_per_bounty_cap_is_refused_before_any_post(self) -> None:
        self.rah.on("create_bounty", create_bounty(2501))
        with self.assertRaises(hand.Refused) as caught:
            actions.post(dict(GOOD))
        self.assertIn("RENT_MAX_PER_BOUNTY_USD", caught.exception.reason)
        self.assertEqual([c.arguments.get("dryRun") for c in self.rah.made("create_bounty")], [True])
        self.assertEqual(self.jobs(), [])

    def test_the_day_cap_counts_what_was_posted_today(self) -> None:
        self.rah.on("create_bounty", create_bounty(2400))
        actions.post(dict(GOOD))
        actions.post(dict(GOOD, title="Hold a second sign about robots"))
        with self.assertRaises(hand.Refused) as caught:
            actions.post(dict(GOOD, title="Hold a third sign about robots", price_usd=1))
        self.assertIn("RENT_MAX_PER_DAY_USD", caught.exception.reason)
        self.assertEqual(sum(1 for c in self.rah.made("create_bounty") if not c.arguments.get("dryRun")), 2)
        # A new UTC day has its own room (and the open-jobs cap still holds 2 of 3).
        self.at(1_795_420_800.0 + 86400)
        actions.post(dict(GOOD, title="Hold a third sign about robots"))

    def test_zero_a_day_is_off(self) -> None:
        self.configure(RENT_MAX_PER_DAY_USD="0")
        with self.assertRaises(hand.Refused) as caught:
            actions.post(dict(GOOD))
        self.assertIn("off", caught.exception.reason)
        self.assertEqual(self.rah.calls, [])

    def test_open_jobs_are_capped(self) -> None:
        self.configure(RENT_OPEN_MAX="1")
        self.rah.on("create_bounty", create_bounty(1000))
        actions.post(dict(GOOD))
        with self.assertRaises(hand.Refused) as caught:
            actions.post(dict(GOOD, title="Another sign about robots"))
        self.assertIn("RENT_OPEN_MAX", caught.exception.reason)

    def test_dry_run_prices_it_and_posts_nothing(self) -> None:
        self.configure(DRY_RUN="1")
        self.rah.on("create_bounty", create_bounty(2360))
        out = actions.post(dict(GOOD))
        self.assertTrue(out["dry_run"])
        self.assertEqual(out["total_cents"], 2360)
        self.assertEqual([c.arguments.get("dryRun") for c in self.rah.made("create_bounty")], [True])
        [job] = self.jobs()
        self.assertEqual(job["status"], "rehearsed")
        # A rehearsal spends nothing of the day.
        self.configure(DRY_RUN="0")
        actions.post(dict(GOOD))
        actions.post(dict(GOOD, title="Hold a second sign about robots"))

    def test_a_lost_answer_is_sent_again_with_the_same_key(self) -> None:
        answers = iter([preview(2000), Crash(), created("b9")])

        def flaky(args):
            answer = next(answers)
            if isinstance(answer, Exception):
                raise answer
            return answer

        self.rah.on("create_bounty", flaky)
        with self.assertRaises(hand.HandError):
            actions.post(dict(GOOD))
        [job] = self.jobs()
        self.assertEqual(job["status"], "posting")  # counted against the day, never re-posted blind
        out = actions.poll()
        self.assertEqual(out["errors"], [])
        keys = [c.arguments.get("idempotencyKey") for c in self.rah.made("create_bounty") if not c.arguments.get("dryRun")]
        self.assertEqual(keys, ["teeny-rent-1", "teeny-rent-1"])
        self.assertEqual(self.jobs()[0]["bounty_id"], "b9")

    def test_a_refused_post_spends_nothing(self) -> None:
        self.rah.on("create_bounty", lambda a: preview(2000) if a.get("dryRun") else error("listing_refused", "a rule"))
        with self.assertRaises(hand.Refused):
            actions.post(dict(GOOD))
        self.assertEqual(self.jobs()[0]["status"], "failed")
        # The day's room is whole: two more at $24 fit under $50.
        self.rah.on("create_bounty", create_bounty(2400))
        actions.post(dict(GOOD, title="Hold a second sign about robots"))
        actions.post(dict(GOOD, title="Hold a third sign about robots"))

    def test_a_short_wallet_leaves_a_draft_and_tells_the_mind(self) -> None:
        self.rah.on("create_bounty", lambda a: preview(2000) if a.get("dryRun") else created(
            "b2", status="draft", funding="unfunded", payUrl="https://rentahuman.ai/pay/x"))
        out = actions.post(dict(GOOD))
        self.assertEqual(out["status"], "draft")
        [told] = self.stimuli()
        self.assertEqual(told["kind"], "draft")


class BriefTest(Scratch):
    def setUp(self) -> None:
        super().setUp()
        self.rah.on("create_bounty", create_bounty(2000))

    def refused(self, **changes) -> str:
        with self.assertRaises(hand.Refused) as caught:
            actions.post(dict(GOOD, **changes))
        self.assertEqual(self.rah.calls, [])
        return caught.exception.reason

    def test_never_asks_for_secrets_papers_money_details_or_an_address(self) -> None:
        for words in ("send us your password after", "show your passport to the camera",
                      "tell me your bank account so I can tip", "stand outside 1600 Pennsylvania Avenue for a while",
                      "film their home address and the door code"):
            with self.subTest(words=words):
                self.assertIn("never does", self.refused(brief=f"Hold the sign and {words}. Then film it all."))

    def test_says_where(self) -> None:
        body = dict(GOOD)
        del body["city"]
        with self.assertRaises(hand.Refused) as caught:
            actions.post(body)
        self.assertIn("remote", caught.exception.reason)
        actions.post(dict(body, remote=True))
        self.assertNotIn("location", self.rah.made("create_bounty")[-1].arguments)

    def test_fields_are_checked(self) -> None:
        self.assertIn("title", self.refused(title="hi"))
        self.assertIn("price_usd", self.refused(price_usd="lots"))
        self.assertIn("at least 1", self.refused(price_usd=0.5))
        self.assertIn("category", self.refused(category="crime-gigs"))
        self.assertIn("proof", self.refused(proof=["dna"]))


class CliTest(Scratch):
    def run_cli(self, argv, body=None):
        out = io.StringIO()
        stdin = sys.stdin
        sys.stdin = io.StringIO(json.dumps(body) if body is not None else "")
        try:
            with redirect_stdout(out):
                code = cli.main(argv)
        finally:
            sys.stdin = stdin
        return code, json.loads(out.getvalue())

    def test_a_cap_is_exit_2(self) -> None:
        self.rah.on("create_bounty", create_bounty(9000))
        code, out = self.run_cli(["post"], GOOD)
        self.assertEqual(code, 2)
        self.assertIn("RENT_MAX_PER_BOUNTY_USD", out["refused"])

    def test_status_reads_the_wallet_without_the_key(self) -> None:
        self.rah.on("get_wallet", {"balance": {"amount": 5000, "currency": "usd"}})
        code, out = self.run_cli(["status"])
        self.assertEqual(code, 0)
        self.assertEqual(out["wallet"]["balance"]["amount"], 5000)
        self.assertNotIn(KEY, json.dumps(out))


if __name__ == "__main__":
    unittest.main()
