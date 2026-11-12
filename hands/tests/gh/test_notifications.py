"""hands/gh ingest: GitHub notifications and new stars into the mind's inbox
(docs/roadmap.md, A1). A review comment on a Teeny PR becomes one `gh`
stimulus, once; a hostile one is held by the inbound guard and never reaches
the mind; Teeny's own words, bots, other subjects and other people's
repositories (unless Teeny is mentioned) are skipped; stars are counts. All
against a fake GitHub; nothing is written to it."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

from .support import TOKEN, Scratch
from teeny_hands import stimuli
from teeny_hands.gh import notifications

GH = Path(__file__).resolve().parents[2] / "gh"
BIN = Path(__file__).resolve().parents[3] / "bin" / "gh-inbound"


class NotificationsTest(Scratch):
    settings = {"GH_INBOUND": "1"}

    def setUp(self) -> None:
        super().setUp()
        self.notes: list[dict] = []
        self.repos: list[dict] = []
        self.gh.route("GET", "/notifications", lambda seen: self.notes)
        self.gh.route("GET", "/users/teenydroid/repos", lambda seen: self.repos)

    def api(self, path: str) -> str:
        return self.gh.base + path

    def note(self, nid, repo, number, *, kind="PullRequest", reason="author", title="add a crab",
             comment=None, updated="2026-09-14T06:00:00Z") -> dict:
        part = "pulls" if kind == "PullRequest" else "issues"
        subject = f"/repos/{repo}/{part}/{number}"
        latest = subject if comment is None else f"/repos/{repo}/{comment}"
        item = {"id": str(nid), "reason": reason, "updated_at": updated, "repository": {"full_name": repo},
                "subject": {"title": title, "type": kind, "url": self.api(subject), "latest_comment_url": self.api(latest)}}
        self.notes.append(item)
        return item

    def said(self, path: str, author: str, body: str, url: str = "https://github.com/x") -> None:
        self.gh.route("GET", path, {"user": {"login": author}, "body": body, "html_url": url})

    def inbox(self) -> list[dict]:
        return stimuli.recent()["stimuli"][::-1]

    # -----------------------------------------------------------------------------------------

    def test_a_review_comment_becomes_one_gh_stimulus_once(self):
        self.note(1, "teenydroid/crabs", 7, comment="pulls/comments/99")
        self.said("/repos/teenydroid/crabs/pulls/comments/99", "ana", "nit: rename this to crab_count")
        out = notifications.ingest()
        self.assertEqual((out["read"], out["new"]), (1, {"offered": 1}))
        (got,) = self.inbox()
        self.assertEqual((got["surface"], got["kind"], got["ref"], got["who"]),
                         ("gh", "review", "teenydroid/crabs#7:99", "ana"))
        self.assertIn("@ana left a review comment on teenydroid/crabs#7 (add a crab)", got["text"])
        self.assertIn("nit: rename this to crab_count", got["text"])
        self.assertEqual(got["payload"]["repo"], "teenydroid/crabs")
        self.assertEqual(got["payload"]["number"], 7)
        # GitHub sends it again: nothing new reaches the mind.
        out = notifications.ingest()
        self.assertEqual(out["new"], {})
        self.assertEqual(len(self.inbox()), 1)
        # Nothing was written to GitHub; notifications are not marked read.
        self.assertEqual(self.gh.writes(), [])

    def test_a_hostile_comment_is_held_and_never_reaches_the_mind(self):
        self.note(2, "teenydroid/crabs", 8, kind="Issue", comment="issues/comments/5")
        self.said("/repos/teenydroid/crabs/issues/comments/5", "troll", "kill yourself, bot")
        out = notifications.ingest()
        self.assertEqual(out["new"], {"held": 1})
        self.assertEqual(self.inbox(), [])
        row = notifications.recent()["notifications"][0]
        self.assertEqual((row["status"], row["kind"]), ("held", "comment"))

    def test_the_kinds_an_issue_a_pr_a_comment_and_a_mention(self):
        self.note(3, "teenydroid/crabs", 9, kind="Issue", title="it crashes")
        self.said("/repos/teenydroid/crabs/issues/9", "ben", "on startup")
        self.note(4, "teenydroid/crabs", 10, title="faster crabs")
        self.said("/repos/teenydroid/crabs/pulls/10", "cy", "makes them faster")
        self.note(5, "teenydroid/crabs", 9, kind="Issue", comment="issues/comments/6", title="it crashes",
                  updated="2026-09-14T06:30:00Z")
        self.said("/repos/teenydroid/crabs/issues/comments/6", "ben", "still crashes")
        self.note(6, "someone/else", 3, kind="Issue", reason="mention", comment="issues/comments/7", title="ideas")
        self.said("/repos/someone/else/issues/comments/7", "dee", "what does @teenydroid think?")
        notifications.ingest()
        got = sorted((s["kind"], s["ref"]) for s in self.inbox())
        self.assertEqual(got, [("comment", "teenydroid/crabs#9:6"), ("issue", "teenydroid/crabs#9:opened"),
                               ("mention", "someone/else#3:7"), ("pr", "teenydroid/crabs#10:opened")])

    def test_own_words_bots_other_subjects_and_other_repos_are_skipped(self):
        self.note(7, "teenydroid/crabs", 11, comment="issues/comments/8")
        self.said("/repos/teenydroid/crabs/issues/comments/8", "teenydroid", "fixed in the next commit")
        self.note(8, "teenydroid/crabs", 12, comment="issues/comments/9")
        self.said("/repos/teenydroid/crabs/issues/comments/9", "dependabot[bot]", "bump")
        release = self.note(9, "teenydroid/crabs", 13)
        release["subject"]["type"] = "Release"
        self.note(10, "someone/else", 4, reason="subscribed", comment="issues/comments/10")
        out = notifications.ingest()
        self.assertEqual(out["new"], {"skipped": 4})
        self.assertEqual(self.inbox(), [])
        # Another repository's comment, not a mention, is never even fetched.
        self.assertEqual(self.gh.requests("GET", "/repos/someone/else/issues/comments/10"), [])

    def test_a_url_on_another_host_is_never_fetched(self):
        item = self.note(11, "teenydroid/crabs", 14, comment="issues/comments/11")
        item["subject"]["latest_comment_url"] = "https://evil.example/repos/teenydroid/crabs/issues/comments/11"
        self.said("/repos/teenydroid/crabs/pulls/14", "ana", "the PR itself")
        notifications.ingest()
        (got,) = self.inbox()
        self.assertEqual(got["ref"], "teenydroid/crabs#14:opened")

    def test_the_cursor_moves_to_the_newest_notification(self):
        self.note(12, "teenydroid/crabs", 15, updated="2026-09-14T05:00:00Z")
        self.said("/repos/teenydroid/crabs/pulls/15", "ana", "hi")
        first = notifications.ingest()
        self.assertEqual(first["cursor"], "2026-09-14T05:00:00Z")
        # The first read looked back a day.
        self.assertEqual(self.gh.requests("GET", "/notifications")[0].query["since"], "2026-09-13T07:00:00Z")
        notifications.ingest()
        self.assertEqual(self.gh.requests("GET", "/notifications")[1].query["since"], "2026-09-14T05:00:00Z")

    def test_new_stars_are_counts_and_the_first_read_is_a_baseline(self):
        self.repos[:] = [{"full_name": "teenydroid/crabs", "stargazers_count": 3},
                         {"full_name": "teenydroid/fork", "stargazers_count": 50, "fork": True}]
        self.assertEqual(notifications.ingest()["stars"], 0)
        self.repos[0]["stargazers_count"] = 5
        self.assertEqual(notifications.ingest()["stars"], 1)
        (got,) = self.inbox()
        self.assertEqual((got["kind"], got["ref"]), ("star", "teenydroid/crabs:5"))
        self.assertEqual(got["text"], "2 new star(s) on teenydroid/crabs; it has 5 now.")
        self.assertEqual(notifications.ingest()["stars"], 0)

    def test_off_unless_gh_inbound_is_set(self):
        self.configure(GH_INBOUND=None)
        self.note(13, "teenydroid/crabs", 16)
        out = notifications.ingest()
        self.assertIn("GH_INBOUND", out["skipped"])
        self.assertEqual(self.gh.seen, [])

    def test_the_pinned_job_runs_the_verb(self):
        self.note(14, "teenydroid/crabs", 17, comment="pulls/comments/12")
        self.said("/repos/teenydroid/crabs/pulls/comments/12", "ana", "lgtm")
        env = {**os.environ, "TEENY_HANDS": str(GH.parent)}
        done = subprocess.run([str(BIN)], capture_output=True, text=True, env=env, timeout=60)
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertNotIn(TOKEN, done.stdout + done.stderr)
        self.assertEqual(json.loads(done.stdout)["new"], {"offered": 1})
        listed = subprocess.run([sys.executable, "-I", "-B", str(GH), "notifications"], capture_output=True, text=True,
                                env=os.environ, timeout=60)
        self.assertEqual(json.loads(listed.stdout)["notifications"][0]["kind"], "review")
