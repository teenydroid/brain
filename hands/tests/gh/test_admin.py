"""The token-scope probe (docs/roadmap.md, G5): a token that can administer
a repository the babysitter merges into can edit the ruleset behind the
frozen gate, and the doctor says so."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "lib"))

from teeny_hands.gh import admin  # noqa: E402
from teeny_hands.gh.client import GitHubError  # noqa: E402


class FakeApi:
    def __init__(self, answers):
        self.answers = answers
        self.calls = []

    def call(self, method, path, query=(), body=None):
        self.calls.append((method, path))
        answer = self.answers[path]
        if isinstance(answer, Exception):
            raise answer
        return answer


PROBE = "/repos/teenydroid/brain/actions/permissions"


class AdminTest(unittest.TestCase):
    def test_a_token_github_answers_can_administer_the_repository(self):
        api = FakeApi({PROBE: {"enabled": True}})
        self.assertIs(admin.can_administer(api, "teenydroid/brain"), True)
        self.assertEqual(api.calls, [("GET", PROBE)])

    def test_a_refused_token_cannot_administer(self):
        for status in (401, 403, 404):
            api = FakeApi({PROBE: GitHubError(f"GitHub answered {status}", status)})
            self.assertIs(admin.can_administer(api, "teenydroid/brain"), False, status)

    def test_a_rate_limit_403_is_not_a_refusal(self):
        # GitHub answers 403 for a spent rate limit too: that says nothing
        # about what the token may do, so the check must not report ok.
        for message in ("GitHub answered 403: API rate limit exceeded for user ID 1.",
                        "GitHub answered 403: You have exceeded a secondary rate limit."):
            api = FakeApi({PROBE: GitHubError(message, 403)})
            self.assertIsNone(admin.can_administer(api, "teenydroid/brain"), message)
        self.assertEqual(admin.check(FakeApi({PROBE: GitHubError("GitHub answered 429: rate limit", 429)}),
                                     ["teenydroid/brain"])[0], "warn")

    def test_an_unreachable_github_is_not_an_answer(self):
        api = FakeApi({PROBE: GitHubError("could not reach GitHub (URLError)")})
        self.assertIsNone(admin.can_administer(api, "teenydroid/brain"))
        api = FakeApi({PROBE: GitHubError("GitHub answered 500", 500)})
        self.assertIsNone(admin.can_administer(api, "teenydroid/brain"))

    def test_the_check_warns_while_the_token_administers_a_watched_repository(self):
        status, detail = admin.check(FakeApi({PROBE: {}}), ["teenydroid/brain"])
        self.assertEqual(status, "warn")
        self.assertIn("can administer teenydroid/brain", detail)
        self.assertIn("ruleset", detail)

    def test_the_check_is_ok_once_github_refuses_the_token(self):
        api = FakeApi({PROBE: GitHubError("GitHub answered 403", 403)})
        self.assertEqual(admin.check(api, ["teenydroid/brain"]),
                         ("ok", "the token cannot administer teenydroid/brain"))

    def test_the_check_warns_when_it_could_not_ask(self):
        api = FakeApi({PROBE: GitHubError("could not reach GitHub (URLError)")})
        status, detail = admin.check(api, ["teenydroid/brain"])
        self.assertEqual((status, detail), ("warn", "could not ask GitHub about teenydroid/brain"))

    def test_nothing_watched_is_nothing_to_check(self):
        api = FakeApi({})
        self.assertEqual(admin.check(api, [])[0], "ok")
        self.assertEqual(api.calls, [])

    def test_only_a_repository_name_reaches_the_path(self):
        with self.assertRaises(Exception):
            admin.can_administer(FakeApi({}), "teenydroid/brain/../../user")


if __name__ == "__main__":
    unittest.main()
