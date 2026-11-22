"""teeny_hands.authorship: which outbound guard holds are on text code wrote
(the babysitter's audit comment), not Teeny's model. On 2026-11-18, 17 of
the 21 github `links` holds evolve showed Teeny as "your drafts" were the
babysitter's comment on PR #368, and two generations proposed a SOUL.md
rule to write around a bug that was never Teeny's.

The recogniser's markers are pinned against the babysitter's own report(),
rendered here, so the two cannot drift apart silently."""

import json
import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "lib"))

from teeny_hands import authorship  # noqa: E402
from teeny_hands.babysit import actions  # noqa: E402

GITHUB_COMMENT = {"field": "body", "fields": ["body"], "number": 368, "post_kind": "comment",
                  "repo": "teenydroid/brain", "surface": "github"}


def audit_comment(**over):
    """The babysitter's comment, as hands/babysit renders it."""
    result = {"verdict": "needs_operator", "head_sha": "004a55c535d6aa", "tier": "teeny",
              "findings": [{"severity": "operator", "rule": "frozen", "where": "bin/lib/evolve.py"}], **over}
    return actions.report(result)


def held(body):
    """The `text` column as the guard files a one-field comment."""
    return json.dumps({"body": body})


class BabysitterAuditComment(unittest.TestCase):
    def test_every_verdict_of_the_babysitters_report_is_recognised_as_code_written(self):
        for verdict in ("blocked", "failing", "needs_operator", "needs_review", "waiting", "ready"):
            for extra in ({}, {"injections": 2}, {"self_merge": True}, {"approved_by": ["someone"]}):
                body = audit_comment(verdict=verdict, **extra)
                self.assertEqual(authorship.code_wrote(json.dumps(GITHUB_COMMENT), held(body)), "babysit_audit",
                                 (verdict, extra))
                self.assertFalse(authorship.teenys_own("outbound", json.dumps(GITHUB_COMMENT), held(body)))

    def test_the_markers_are_the_ones_the_babysitter_writes(self):
        body = audit_comment()
        self.assertTrue(body.startswith(authorship.BABYSIT_HEAD), body[:60])
        self.assertEqual(body.rstrip().splitlines()[-1], authorship.BABYSIT_FOOT)

    def test_a_plain_string_text_column_is_read_as_the_body(self):
        self.assertEqual(authorship.code_wrote(GITHUB_COMMENT, audit_comment()), "babysit_audit")

    def test_a_draft_of_teenys_that_quotes_one_marker_is_still_teenys(self):
        for body in ("the babysitter signs off with " + authorship.BABYSIT_FOOT,
                     "**babysitter** · i think this one is fine, merging",
                     authorship.BABYSIT_FOOT):
            self.assertIsNone(authorship.code_wrote(GITHUB_COMMENT, held(body)), body)
            self.assertTrue(authorship.teenys_own("outbound", GITHUB_COMMENT, held(body)))

    def test_the_audit_text_anywhere_but_a_github_comment_is_not_the_babysitters(self):
        for context in ({**GITHUB_COMMENT, "surface": "moltbook"}, {**GITHUB_COMMENT, "post_kind": "pr"}, {}):
            self.assertIsNone(authorship.code_wrote(context, held(audit_comment())), context)

    def test_evolves_own_pull_request_text_is_teenys(self):
        """The proposer model wrote it: a hold on it is Teeny's to change."""
        context = {"field": "body", "fields": ["title", "body"], "head": "teeny/gen-19-c3", "post_kind": "pr",
                   "repo": "teenydroid/brain", "surface": "github"}
        text = json.dumps({"title": "gen-19: SOUL rule widened", "body": "**What my ledgers show** ..."})
        self.assertTrue(authorship.teenys_own("outbound", json.dumps(context), text))

    def test_an_inbound_hold_is_never_teenys_own(self):
        self.assertFalse(authorship.teenys_own("inbound", {"surface": "moltbook"}, "a stranger's comment"))

    def test_unreadable_columns_read_as_teenys_words(self):
        for context, text in ((None, None), ("not json", "not json"), ("[1, 2]", "[1, 2]"), ("{}", "")):
            self.assertIsNone(authorship.code_wrote(context, text))


if __name__ == "__main__":
    unittest.main()
