"""The X profile disclosure check (docs/roadmap.md, P1): the bio must say the
account is automated and name the operator. bin/doctor reads only the cached
row, so it never calls X at boot, and it warns, never fails."""

from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path

from . import support  # noqa: F401  (puts hands/lib on the path)
from teeny_hands.x import profile

KEYS = {"X_API_KEY": "k", "X_API_SECRET": "s", "X_ACCESS_TOKEN": "t", "X_ACCESS_TOKEN_SECRET": "ts"}
NOW = 1_789_369_200.0


class DisclosureTest(unittest.TestCase):
    def test_a_bio_that_says_automated_and_names_the_operator_passes(self):
        ok, why = profile.disclosure("tiny crab. automated account, run by @steven", "steven")
        self.assertTrue(ok, why)
        self.assertTrue(profile.disclosure("AI agent. Automated by @Steven", "steven")[0])

    def test_naming_the_operator_is_not_enough_without_saying_automated(self):
        ok, why = profile.disclosure("tiny crab, friends with @steven", "steven")
        self.assertFalse(ok)
        self.assertIn("automated", why)

    def test_saying_automated_is_not_enough_without_the_operator(self):
        ok, why = profile.disclosure("an automated crab", "steven")
        self.assertFalse(ok)
        self.assertIn("@steven", why)
        self.assertFalse(profile.disclosure("a bot by @stevenson", "steven")[0])  # a longer handle is not the operator

    def test_without_an_operator_setting_any_run_by_line_counts(self):
        self.assertTrue(profile.disclosure("a bot, run by @someone", None)[0])
        self.assertFalse(profile.disclosure("a bot", None)[0])


class DoctorCheckTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.db = Path(self.tmp.name) / "hands.db"

    def keep(self, description, at=NOW - 3600, handle="teenydroid"):
        conn = sqlite3.connect(self.db)
        profile.remember(conn, handle, {"id": "1", "description": description}, at)
        conn.commit()
        conn.close()

    def check(self, **settings):
        return profile.check(self.db, {**KEYS, "X_OPERATOR_HANDLE": "@steven", **settings}, now=NOW)

    def test_no_x_keys_is_ok(self):
        self.assertEqual(profile.check(self.db, {}, now=NOW)[0], "ok")

    def test_no_cached_profile_warns_and_never_calls_x(self):
        status, detail = self.check()
        self.assertEqual(status, "warn")
        self.assertIn("no cached profile", detail)
        self.assertFalse(self.db.exists())  # read-only: nothing was created

    def test_a_bio_without_the_disclosure_line_warns(self):
        self.keep("tiny crab learning to be alive")
        status, detail = self.check()
        self.assertEqual(status, "warn")
        self.assertIn("automated", detail)
        self.assertIn("Automated label", detail)

    def test_a_bio_with_the_line_is_ok(self):
        self.keep("tiny crab. automated, run by @steven")
        status, detail = self.check()
        self.assertEqual(status, "ok", detail)

    def test_a_stale_profile_warns(self):
        self.keep("tiny crab. automated, run by @steven", at=NOW - 10 * 86400)
        status, detail = self.check()
        self.assertEqual(status, "warn")
        self.assertIn("stale", detail)

    def test_the_profile_of_the_configured_handle_is_read(self):
        self.keep("automated, run by @steven", handle="otherbot")
        self.assertEqual(self.check(X_BOT_USERNAME="otherbot")[0], "ok")
        self.assertEqual(self.check()[0], "warn")

    def test_a_bad_operator_setting_warns(self):
        self.keep("automated, run by @steven")
        self.assertEqual(self.check(X_OPERATOR_HANDLE="not a handle!")[0], "warn")


if __name__ == "__main__":
    unittest.main()
