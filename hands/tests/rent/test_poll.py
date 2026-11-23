"""hands/rent poll: what applicants and the person hired say reaches the mind
once, through the inbound guard; evidence is saved; a bounty nobody picked
from gets RentAHuman's best match."""

from __future__ import annotations

import json
import unittest

from rent.support import GOOD, HOUR, T0, Scratch, create_bounty
from teeny_hands.rent import actions


def application(aid: str, message: str = "I can do it today", name: str = "Dana R.") -> dict:
    return {"id": aid, "status": "pending", "message": message,
            "applicant": {"name": name, "rating": 4.8, "reviewCount": 12, "verifiedTrackRecord": {"paidJobs": 9}}}


class PollTest(Scratch):
    def setUp(self) -> None:
        super().setUp()
        self.rah.on("create_bounty", create_bounty(2360))
        actions.post(dict(GOOD))
        self.rah.on("list_hires", {"data": []})
        self.rah.on("get_bounty", {"id": "b1", "status": "open", "funding": {"status": "funded"}})
        self.rah.on("accept_application", {"hireId": "h1", "conversationId": "c1", "cost": {"amount": 2360}})

    def test_applications_reach_the_mind_once(self) -> None:
        self.rah.on("list_applications", {"data": [application("a1"), application("a2", "what a retard")]})
        actions.poll()
        actions.poll()
        told = self.stimuli()
        self.assertEqual([s["ref"] for s in told], ["1:a1", "1:a2"])
        self.assertIn("I can do it today", told[0]["text"])
        self.assertIn('"rent_accept"', told[0]["text"])
        self.assertNotIn("retard", told[1]["text"])
        self.assertIn("held by the guard", told[1]["text"])
        self.assertEqual(self.rah.made("accept_application"), [])

    def test_the_best_match_is_hired_once_nobody_picked_in_time(self) -> None:
        self.rah.on("list_applications", {"data": [application("a1"), application("a2")]})
        self.at(T0 + 5 * HOUR)
        actions.poll()
        self.assertEqual(self.rah.made("accept_application"), [])
        self.at(T0 + 6 * HOUR)
        actions.poll()
        [call] = self.rah.made("accept_application")
        self.assertEqual(call.arguments["applicationId"], "a1")
        self.assertEqual(self.jobs()[0]["status"], "hired")

    def test_no_auto_accept_when_it_is_0(self) -> None:
        self.configure(RENT_AUTO_ACCEPT_HOURS="0")
        self.rah.on("list_applications", {"data": [application("a1")]})
        self.at(T0 + 100 * HOUR)
        actions.poll()
        self.assertEqual(self.rah.made("accept_application"), [])

    def test_a_hire_made_elsewhere_is_picked_up(self) -> None:
        self.rah.on("list_hires", {"data": [{"id": "h5", "status": "active"}]})
        actions.poll()
        job = self.jobs()[0]
        self.assertEqual((job["status"], job["hire_id"]), ("hired", "h5"))

    def test_work_is_saved_and_handed_to_the_mind(self) -> None:
        actions.accept({"job": 1, "application": "a1"})
        self.rah.files["/f/1.mp4"] = b"\x00video"
        self.rah.on("get_hire", {
            "id": "h1", "status": "submitted", "payStatus": "held", "conversationId": "c1", "worker": {"name": "Dana R."},
            "submission": {"id": "s1", "status": "pending_review", "attempt": {"used": 0, "max": 3},
                           "text": "Ten minutes, lots of laughs.",
                           "files": [{"type": "video", "url": f"{self.rah.base}/f/1.mp4", "contentType": "video/mp4",
                                      "isLiveCapture": True}]}})
        self.rah.on("list_messages", {"data": [
            {"id": "m1", "sender": {"role": "human", "name": "Dana R."}, "content": "On my way!"},
            {"id": "m2", "sender": {"role": "poster"}, "content": "thanks"},
        ]})
        actions.poll()
        actions.poll()
        told = {s["kind"]: s for s in self.stimuli()}
        self.assertEqual(sorted(told), ["message", "submitted"])
        self.assertIn("Ten minutes", told["submitted"]["text"])
        self.assertIn("Ten minutes with the sign", told["submitted"]["text"])  # the criteria, to judge against
        files = json.loads(told["submitted"]["payload"])["files"]
        self.assertEqual(files[0]["path"], "state/rent/1/s1/1.mp4")
        self.assertEqual((self.root / files[0]["path"]).read_bytes(), b"\x00video")
        self.assertIn("On my way!", told["message"]["text"])
        self.assertEqual(self.jobs()[0]["status"], "submitted")
        self.assertEqual(self.leaked(), [])

    def test_a_redone_submission_is_news_again(self) -> None:
        actions.accept({"job": 1, "application": "a1"})
        self.rah.on("list_messages", {"data": []})
        first = {"id": "h1", "status": "submitted", "submission": {"id": "s1", "status": "pending_review",
                                                                    "attempt": {"used": 0}, "files": []}}
        again = {"id": "h1", "status": "submitted", "submission": {"id": "s1", "status": "pending_review",
                                                                    "attempt": {"used": 1}, "files": []}}
        self.rah.on("get_hire", first)
        actions.poll()
        self.rah.on("get_hire", again)
        actions.poll()
        self.assertEqual([s["kind"] for s in self.stimuli()], ["submitted", "submitted"])

    def test_without_a_key_it_skips(self) -> None:
        self.configure(RENTAHUMAN_API_KEY=None)
        self.assertIn("skipped", actions.poll())


if __name__ == "__main__":
    unittest.main()
