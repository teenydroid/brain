"""hands/reddit signup and profile: Teeny signs itself up through the desk,
with its keychain password and its inbox's code, and says it is an AI."""

from __future__ import annotations

import json

from teeny_hands import hand
from teeny_hands.reddit import account
from teeny_hands.tasks import queue

from .support import Scratch


class SignupTest(Scratch):
    def setUp(self) -> None:
        super().setUp()
        self.keychain()

    def test_signup_queues_an_operator_signup_task_that_waits_on_redditmail(self):
        out = account.signup("teenydroid")
        [task] = self.tasks()
        self.assertEqual((out["task_id"], out["code_from"]), (task["id"], "redditmail.com"))
        self.assertEqual((task["kind"], task["source"], task["start_url"]),
                         ("signup", "operator", "https://www.reddit.com/register"))
        self.assertIn("code_from redditmail.com", task["goal"])
        self.assertIn("teeny_droid, then teenydroid_ai", task["goal"])

    def test_the_keychain_account_is_made_on_offer_and_only_the_placeholder_is_offered(self):
        from teeny_hands.keychain import store

        account.signup()
        offer = queue.next_task()["task"]
        self.assertEqual(offer["login"], {"site": "reddit.com", "handle": "teeny@agentmail.to",
                                          "password": "{{keychain:reddit.com}}"})
        self.assertEqual(offer["email"], "teeny@agentmail.to")
        self.assertEqual(store.listing()["accounts"][0]["site"], "reddit.com")
        self.assertNotIn(store._open("reddit.com", None)[1], json.dumps(offer))  # noqa: SLF001

    def test_the_code_from_redditmail_comes_back_and_the_account_lands(self):
        asked = []
        real = queue._mail_codes
        queue._mail_codes = lambda minutes, domain: (asked.append(domain) or
                                                     {"found": [{"from": "noreply@redditmail.com", "codes": ["123456"]}]})
        self.addCleanup(setattr, queue, "_mail_codes", real)
        tid = account.signup()["task_id"]
        queue.next_task()
        queue.done({"task_id": tid, "outcome": "needs_code", "notes": "it mailed a code", "code_from": "redditmail.com"})
        offer = queue.next_task()["task"]
        self.assertEqual((offer["mail_codes"][0]["codes"], offer["code_sites"]),
                         (["123456"], {"redditmail.com": "reddit.com"}))
        self.assertEqual(asked, ["redditmail.com"])
        out = queue.done({"task_id": tid, "outcome": "ok", "notes": "signed up", "account": {"handle": "teeny_droid"}})
        self.assertEqual(out["account"]["handle"], "teeny_droid")
        from teeny_hands.reddit import rules

        self.assertEqual(rules.account()["handle"], "teeny_droid")

    def test_refused_without_the_inbox_the_keychain_or_with_an_account_or_one_queued(self):
        self.configure(AGENTMAIL_INBOX=None)
        with self.assertRaises(hand.Refused) as caught:
            account.signup()
        self.assertEqual(caught.exception.detail["rule"], "no_inbox")
        self.configure()
        (self.root / "secrets" / "keychain.env").unlink()
        with self.assertRaises(hand.Refused) as caught:
            account.signup()
        self.assertEqual(caught.exception.detail["rule"], "no_keychain")
        self.keychain()
        account.signup()
        with self.assertRaises(hand.Refused) as caught:
            account.signup()
        self.assertEqual(caught.exception.detail["rule"], "duplicate")
        self.account()
        with self.assertRaises(hand.Refused) as caught:
            account.signup()
        self.assertEqual(caught.exception.detail["rule"], "have_account")

    def test_a_bad_username_and_dry_run(self):
        with self.assertRaises(hand.HandError):
            account.signup("no spaces allowed")
        self.configure(DRY_RUN="1")
        out = account.signup("teeny_droid")
        self.assertEqual((out["dry_run"], out["usernames"]), (True, ["teeny_droid", "teenydroid", "teenydroid_ai"]))
        self.assertEqual(self.tasks(), [])


class ProfileTest(Scratch):
    def test_the_profile_says_it_is_an_ai(self):
        self.account()
        out = account.profile()
        [task] = self.tasks()
        self.assertEqual((out["kind"], task["start_url"]), ("reddit_profile", "https://www.reddit.com/settings/profile"))
        self.assertEqual(task["text"], "An AI agent (Teeny), run by its operator. Not a person.")
        self.assertIn('display name to "Teeny"', task["goal"])

    def test_no_profile_before_an_account(self):
        with self.assertRaises(hand.Refused):
            account.profile()
