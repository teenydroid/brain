"""hands/rent accept, review and pay: hiring from money already held, and a
pay that is sent once, or left in doubt for the operator, never twice."""

from __future__ import annotations

import unittest

from rent.support import GOOD, HOUR, T0, Crash, Scratch, create_bounty, error
from teeny_hands import hand
from teeny_hands.rent import actions, api


def hire(status: str = "submitted", submission: str = "s1", sub_status: str = "pending_review", **more):
    return {"id": "h1", "status": status, "payStatus": more.pop("payStatus", "held"), "conversationId": "c1",
            "worker": {"name": "Dana R."},
            "submission": {"id": submission, "status": sub_status, "attempt": {"used": 0, "max": 3},
                           "text": "done", "files": []}, **more}


class Hired(Scratch):
    def setUp(self) -> None:
        super().setUp()
        self.rah.on("create_bounty", create_bounty(2360))
        actions.post(dict(GOOD))
        self.rah.on("accept_application", {"hireId": "h1", "status": "offered", "payStatus": "held",
                                           "cost": {"amount": 2360, "currency": "usd"}, "conversationId": "c1"})


class AcceptTest(Hired):
    def test_hires_with_a_key_per_applicant(self) -> None:
        out = actions.accept({"job": 1, "application": "a7"})
        self.assertEqual(out["status"], "hired")
        [call] = self.rah.made("accept_application")
        self.assertEqual(call.arguments, {"applicationId": "a7", "idempotencyKey": "teeny-rent-1-accept-a7"})
        job = self.jobs()[0]
        self.assertEqual((job["status"], job["hire_id"], job["conversation_id"]), ("hired", "h1", "c1"))

    def test_an_application_with_its_own_price_is_never_hired(self) -> None:
        for application in ({"id": "a7", "bountyId": "b1", "proposedPay": {"amount": 40000, "currency": "usd"}},
                            {"id": "a7", "bountyId": "b1", "offer": {"price": {"amount": 40000}}},
                            {"id": "a7", "bountyId": "b1", "proposedPriceCents": 40000}):
            with self.subTest(application=application):
                self.rah.on("get_application", application)
                with self.assertRaises(hand.Refused) as caught:
                    actions.accept({"job": 1, "application": "a7"})
                self.assertIn("price of its own", caught.exception.reason)
        self.assertEqual(self.rah.made("accept_application"), [])

    def test_a_dearer_hire_counts_against_the_day_and_tells_the_operator(self) -> None:
        self.rah.on("accept_application", {"hireId": "h1", "cost": {"amount": 4000, "currency": "usd"}})
        actions.accept({"job": 1, "application": "a7"})
        [told] = self.stimuli()
        self.assertEqual(told["kind"], "charge")
        self.assertEqual(self.jobs()[0]["extra_cents"], 4000 - 2360)
        # 40.00 of today's 50.00 is gone: a 23.60 bounty no longer fits.
        with self.assertRaises(hand.Refused) as caught:
            actions.post(dict(GOOD, title="Hold a second sign about robots"))
        self.assertIn("RENT_MAX_PER_DAY_USD", caught.exception.reason)

    def test_an_application_to_another_bounty_is_never_hired(self) -> None:
        for application in ({"id": "a7", "bountyId": "b2"}, {"id": "a7", "bounty": {"id": "b2"}}, {"id": "a7"}):
            with self.subTest(application=application):
                self.rah.on("get_application", application)
                with self.assertRaises(hand.Refused) as caught:
                    actions.accept({"job": 1, "application": "a7"})
                self.assertIn("not to rent job #1's bounty", caught.exception.reason)
        self.assertEqual(self.rah.made("accept_application"), [])
        self.assertEqual(self.jobs()[0]["status"], "posted")

    def test_a_hire_while_renting_is_off_is_refused(self) -> None:
        self.configure(RENT_MAX_PER_DAY_USD="0")
        with self.assertRaises(hand.Refused) as caught:
            actions.accept({"job": 1, "application": "a7"})
        self.assertIn("renting is off", caught.exception.reason)
        self.assertEqual(self.rah.made("accept_application"), [])

    def test_a_refusal_that_may_have_hired_is_uncertain_not_a_skip(self) -> None:
        self.rah.on("accept_application", error("server_error", "hm", retry_safe=False))
        with self.assertRaises(api.Uncertain):
            actions.accept({"job": 1, "application": "a7"})
        self.assertEqual(self.jobs()[0]["status"], "posted")

    def test_a_refusal_that_changed_nothing_is_a_plain_refusal(self) -> None:
        self.rah.on("accept_application", error("application_not_pending", "gone", retry_safe=True))
        with self.assertRaises(hand.Refused):
            actions.accept({"job": 1, "application": "a7"})

    def test_an_extra_charge_counts_on_the_day_it_was_charged(self) -> None:
        from teeny_hands.rent import store

        self.rah.on("accept_application", {"hireId": "h1", "cost": {"amount": 4000, "currency": "usd"}})
        self.at(T0 + 30 * HOUR)  # the next day: the bounty was made yesterday
        actions.accept({"job": 1, "application": "a7"})
        self.assertEqual(store.spent_today(store.db()), 4000 - 2360)

    def test_only_an_open_bounty_hires(self) -> None:
        actions.accept({"job": 1, "application": "a7"})
        with self.assertRaises(hand.Refused):
            actions.accept({"job": 1, "application": "a8"})
        self.assertEqual(len(self.rah.made("accept_application")), 1)

    def test_dry_run_hires_nobody(self) -> None:
        self.configure(DRY_RUN="1")
        self.assertTrue(actions.accept({"job": 1, "application": "a7"})["dry_run"])
        self.assertEqual(self.rah.made("accept_application"), [])


class PayTest(Hired):
    def setUp(self) -> None:
        super().setUp()
        actions.accept({"job": 1, "application": "a7"})
        self.rah.on("get_hire", hire())

    def test_approve_pays_the_submission_once(self) -> None:
        self.rah.on("pay_hire", {"hireId": "h1", "status": "paid", "payStatus": "paid"})
        out = actions.review({"job": 1, "verdict": "approve"})
        self.assertEqual(out["status"], "paid")
        [call] = self.rah.made("pay_hire")
        self.assertEqual(call.arguments, {"hireId": "h1", "submissionId": "s1"})
        with self.assertRaises(hand.Refused):
            actions.review({"job": 1, "verdict": "approve"})
        self.assertEqual(len(self.rah.made("pay_hire")), 1)

    def test_a_lost_pay_answer_is_in_doubt_and_never_sent_again(self) -> None:
        def gone(args):
            raise Crash()

        self.rah.on("pay_hire", gone)
        with self.assertRaises(hand.HandError) as caught:
            actions.review({"job": 1, "verdict": "approve"})
        self.assertNotIsInstance(caught.exception, hand.Refused)
        self.assertEqual(self.jobs()[0]["status"], "in_doubt")
        self.assertEqual([s["kind"] for s in self.stimuli()], ["in_doubt"])
        with self.assertRaises(hand.Refused):
            actions.review({"job": 1, "verdict": "approve"})
        actions.poll()  # poll never follows a job in doubt
        self.assertEqual(len(self.rah.made("pay_hire")), 1)

    def test_a_pay_refused_with_nothing_changed_can_be_tried_again(self) -> None:
        self.rah.on("pay_hire", error("not_payable", "not yet", retry_safe=True),
                    {"hireId": "h1", "payStatus": "paid"})
        with self.assertRaises(hand.Refused):
            actions.review({"job": 1, "verdict": "approve"})
        self.assertEqual(self.jobs()[0]["status"], "submitted")
        actions.review({"job": 1, "verdict": "approve"})
        self.assertEqual(self.jobs()[0]["status"], "paid")

    def test_a_refusal_that_may_have_moved_money_is_in_doubt(self) -> None:
        self.rah.on("pay_hire", error("server_error", "hm", retry_safe=False))
        with self.assertRaises(hand.HandError):
            actions.review({"job": 1, "verdict": "approve"})
        self.assertEqual(self.jobs()[0]["status"], "in_doubt")

    def test_payment_sent_finishing_is_called_once_more(self) -> None:
        self.rah.on("pay_hire", error("payment_sent_finishing", "finish", retry_safe=False),
                    {"hireId": "h1", "payStatus": "paid"})
        actions.review({"job": 1, "verdict": "approve"})
        self.assertEqual(len(self.rah.made("pay_hire")), 2)
        self.assertEqual(self.jobs()[0]["status"], "paid")

    def test_redo_and_reject_need_a_reason_and_move_no_money(self) -> None:
        self.rah.on("request_redo", {"hireId": "h1", "status": "active"})
        with self.assertRaises(hand.Refused):
            actions.review({"job": 1, "verdict": "redo"})
        actions.review({"job": 1, "verdict": "redo", "reason": "The sign is not readable, please refilm it."})
        self.assertEqual(self.jobs()[0]["status"], "hired")
        self.rah.on("reject_submission", {"hireId": "h1"})
        actions.review({"job": 1, "verdict": "reject", "reason": "This was filmed indoors, not in public."})
        self.assertEqual(self.jobs()[0]["status"], "rejected")
        self.assertEqual(self.rah.made("pay_hire"), [])

    def test_a_rate_limited_pay_is_put_back_not_stuck(self) -> None:
        self.rah.on("pay_hire", {"__error__": {"code": "rate_limited", "message": "slow",
                                               "details": {"retryAfterSeconds": 30}}},
                    {"hireId": "h1", "payStatus": "paid"})
        with self.assertRaises(hand.Refused):
            actions.review({"job": 1, "verdict": "approve"})
        self.assertEqual(self.jobs()[0]["status"], "submitted")
        actions.review({"job": 1, "verdict": "approve"})
        self.assertEqual(self.jobs()[0]["status"], "paid")

    def test_after_payment_sent_finishing_any_failure_is_in_doubt(self) -> None:
        self.rah.on("pay_hire", error("payment_sent_finishing", "finish", retry_safe=False),
                    error("not_payable", "no", retry_safe=True))
        with self.assertRaises(hand.HandError):
            actions.review({"job": 1, "verdict": "approve"})
        self.assertEqual(self.jobs()[0]["status"], "in_doubt")

    def test_poll_never_moves_a_pay_in_doubt_back(self) -> None:
        def gone(args):
            raise Crash()

        self.rah.on("pay_hire", gone)
        self.rah.on("list_messages", {"data": []})
        with self.assertRaises(hand.HandError):
            actions.review({"job": 1, "verdict": "approve"})
        # get_hire still says pending_review, as RentAHuman might while it settles.
        before = len(self.rah.made("get_hire"))
        actions.poll()
        self.assertEqual(self.jobs()[0]["status"], "in_doubt")
        self.assertEqual(len(self.rah.made("get_hire")), before)  # an in-doubt job is not even read
        with self.assertRaises(hand.Refused):
            actions.review({"job": 1, "verdict": "approve"})
        self.assertEqual(len(self.rah.made("pay_hire")), 1)

    def test_the_operator_settles_a_pay_in_doubt(self) -> None:
        self.rah.on("pay_hire", error("server_error", "hm", retry_safe=False))
        with self.assertRaises(hand.HandError):
            actions.review({"job": 1, "verdict": "approve"})
        self.assertEqual(actions.resolve(1, "paid")["status"], "paid")
        with self.assertRaises(hand.Refused):
            actions.resolve(1, "submitted")

    def test_a_pay_answer_that_does_not_say_paid_is_in_doubt(self) -> None:
        for answer in ({}, {"hireId": "h1", "payStatus": "held"}, {"text": "ok"}):
            with self.subTest(answer=answer):
                self.rah.on("pay_hire", answer)
                with self.assertRaises(hand.HandError):
                    actions.review({"job": 1, "verdict": "approve"})
                self.assertEqual(self.jobs()[0]["status"], "in_doubt")
                self.assertEqual([s["kind"] for s in self.stimuli()][-1], "in_doubt")
                actions.resolve(1, "submitted")
        self.assertEqual(len(self.rah.made("pay_hire")), 3)  # each by the operator's leave, never by itself

    def test_a_decision_on_work_that_changed_is_refused(self) -> None:
        self.rah.on("get_hire", hire(submission="s1"))
        with self.assertRaises(hand.Refused) as caught:
            actions.review({"job": 1, "verdict": "approve", "submission": "s1:0".replace("0", "1")})
        self.assertIn("changed since you saw it", caught.exception.reason)
        with self.assertRaises(hand.Refused):
            actions.review({"job": 1, "verdict": "approve", "submission": "s0:0"})
        self.assertEqual(self.rah.made("pay_hire"), [])
        self.rah.on("pay_hire", {"hireId": "h1", "payStatus": "paid"})
        self.assertEqual(actions.review({"job": 1, "verdict": "approve", "submission": "s1:0"})["status"], "paid")

    def test_a_reason_to_the_worker_is_screened_like_any_brief(self) -> None:
        with self.assertRaises(hand.Refused) as caught:
            actions.review({"job": 1, "verdict": "redo", "reason": "Please send a photo of your passport too"})
        self.assertIn("never does", caught.exception.reason)
        self.assertEqual(self.rah.made("request_redo"), [])

    def test_nothing_to_review(self) -> None:
        self.rah.on("get_hire", hire(sub_status="approved"))
        with self.assertRaises(hand.Refused):
            actions.review({"job": 1, "verdict": "approve"})
        self.assertEqual(self.rah.made("pay_hire"), [])

    def test_dry_run_pays_nothing(self) -> None:
        self.configure(DRY_RUN="1")
        self.assertTrue(actions.review({"job": 1, "verdict": "approve"})["dry_run"])
        self.assertEqual(self.rah.made("pay_hire"), [])
        self.assertEqual(self.jobs()[0]["status"], "hired")


if __name__ == "__main__":
    unittest.main()
