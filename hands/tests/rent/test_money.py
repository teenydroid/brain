"""hands/rent accept, review and pay: hiring from money already held, and a
pay that is sent once, or left in doubt for the operator, never twice."""

from __future__ import annotations

import unittest

from rent.support import GOOD, Crash, Scratch, create_bounty, error
from teeny_hands import hand
from teeny_hands.rent import actions


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

    def test_a_dearer_hire_tells_the_operator(self) -> None:
        self.rah.on("accept_application", {"hireId": "h1", "cost": {"amount": 4000, "currency": "usd"}})
        actions.accept({"job": 1, "application": "a7"})
        [told] = self.stimuli()
        self.assertEqual(told["kind"], "charge")

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
