"""`hands/guard holds release`: an operator approves a withheld draft by
publishing it, once, through the hand that owns its surface, against a fake X."""

from __future__ import annotations

import argparse

from x.support import T0, Scratch, me
from teeny_hands import hand
from teeny_hands.guard import cli, gate, holds


def release(hold_id: int, note: str | None = None):
    return cli.holds_release(argparse.Namespace(id=hold_id, note=note), None)


class ReleaseTest(Scratch):
    settings = {"SAFETY_JUDGE": "0"}

    def setUp(self):
        super().setUp()
        self.at(T0)
        self.created = self.x.route("POST", "/2/tweets", {"data": {"id": "500"}}, status=201)

    def held_post(self, text="this is fucking great"):
        verdict = gate.check_outbound(text)
        self.assertEqual(verdict.kind, "hold")
        return verdict.hold_id

    def test_publishes_an_approved_post_once(self):
        hold_id = self.held_post()
        out = release(hold_id, "fine in context")
        self.assertEqual(out["hold"]["status"], "approved")
        self.assertEqual(out["hold"]["effect"], "published on X as 500")
        self.assertEqual([s.json() for s in self.x.requests("POST", "/2/tweets")], [{"text": "this is fucking great"}])
        # A second release answers the hold as it stands and publishes nothing.
        self.assertEqual(release(hold_id)["hold"]["status"], "approved")
        self.assertEqual(len(self.x.requests("POST", "/2/tweets")), 1)

    def test_releases_a_reply_to_the_post_it_answered(self):
        me(self.x)
        self.x.route("GET", "/2/tweets/77", {"data": {"id": "77", "text": "hi", "author_id": "9", "conversation_id": "77"}})
        verdict = gate.check_outbound("this is fucking great", kind="reply", context={"post_id": "77"})
        out = release(verdict.hold_id)
        self.assertIn("a reply to 77", out["hold"]["effect"])
        sent = self.x.requests("POST", "/2/tweets")[0].json()
        self.assertEqual(sent["reply"], {"in_reply_to_tweet_id": "77"})

    def test_dry_run_claims_nothing(self):
        self.configure(DRY_RUN="1")
        hold_id = self.held_post()
        out = release(hold_id)
        self.assertEqual((out["dry_run"], out["would_release"]), (True, hold_id))
        self.assertEqual(holds.get(hold_id)["status"], "pending")
        self.assertEqual(self.x.writes(), [])

    def test_a_failed_publish_goes_back_to_pending(self):
        self.x.route("POST", "/2/tweets", {"title": "Forbidden"}, status=403)
        hold_id = self.held_post()
        with self.assertRaises(hand.HandError):
            release(hold_id)
        held = holds.get(hold_id)
        self.assertEqual(held["status"], "pending")
        self.assertIn("release failed", held["effect"])

    def test_refuses_what_it_cannot_release(self):
        github = gate.check_fields({"title": "t", "body": "this is fucking great"}, surface="github")
        with self.assertRaises(hand.Refused):
            release(github.hold_id)
        self.assertEqual(holds.get(github.hold_id)["status"], "pending")
        with self.assertRaises(hand.HandError):
            release(9999)
