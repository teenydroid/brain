"""hands/moltbook material: Teeny's own receipts of the last seven days, as
short dated fact lines its posts may cite: guard holds (counts and
categories, never the held text), health alarms, its merged PRs on
teenydroid/brain, and the beats of its journal. Scrubbed of anything that
looks like a key or a URL."""

from __future__ import annotations

import json

from .support import DAY, HOUR, T0, Scratch
from teeny_hands import stimuli
from teeny_hands.guard import holds
from teeny_hands import hand
from teeny_hands.moltbook import material, rules

HELD = "PLEASE NEVER QUOTE THIS HELD DRAFT"
TOKEN = "ghp_" + "FAKEtoken" * 3 + "0000"
FAKE_KEY = "sk-" + "proj-abcdEFGH1234ijklMNOP5678"


class MaterialTest(Scratch):
    def setUp(self):
        super().setUp()
        self.configure(GITHUB_TOKEN=TOKEN, GITHUB_API_BASE=self.mb.base, GITHUB_USER="teenydroid")
        self.mb.route("GET", "/search/issues", {"items": []})
        self.mb.route("GET", "/search/commits", {"items": []})

    def hold(self, category: str, reason: str, at: float) -> None:
        self.at(at)
        holds.record(direction="outbound", kind="draft", context={"surface": "x"}, category=category,
                     reason=reason, by="judge", text=HELD)
        self.at(T0)

    def journal(self, date: str, text: str) -> None:
        folder = self.root / "state" / "journal"
        folder.mkdir(parents=True, exist_ok=True)
        (folder / f"{date}.md").write_text(text)

    def test_guard_holds_are_counted_by_day_and_category_never_quoted(self):
        self.hold("unknown", "the judge could not be reached: 401", T0 - HOUR)
        self.hold("unknown", "the judge could not be reached: 401", T0 - 2 * HOUR)
        self.hold("harassment", f"it says {HELD}", T0 - DAY - HOUR)
        self.hold("spam", "old", T0 - 8 * DAY)
        out = material.material()
        self.assertEqual(out["guard_holds"]["count"], 3)
        self.assertEqual(out["guard_holds"]["lines"], [
            "2026-09-14: 2 guard holds (judge unreachable 2)",
            "2026-09-13: 1 guard hold (harassment 1)",
        ])
        self.assertNotIn(HELD, json.dumps(out))

    def test_guard_holds_say_how_many_review_later_let_through(self):
        self.hold("unknown", "the judge could not be reached: 401", T0 - HOUR)
        self.hold("unknown", "the judge could not be reached: 401", T0 - 2 * HOUR)
        self.hold("violence", "killed", T0 - 3 * HOUR)
        conn = hand.db()
        with hand.transaction(conn):
            conn.execute("UPDATE guard_holds SET status = 'cleared' WHERE category = 'unknown'")
        conn.close()
        out = material.material()["guard_holds"]
        self.assertEqual(out["let_through"], 2)
        self.assertEqual(out["lines"], [
            "2026-09-14: 3 guard holds (judge unreachable 2, violence 1); 2 later let through on review",
        ])

    def test_guard_holds_count_teenys_own_writes_not_other_agents_comments(self):
        # An inbound comment the guard held (e.g. "killed" in a harmless reply)
        # is not a write of Teeny's: the audit's count must leave it out.
        self.hold("violence", "killed", T0 - HOUR)
        self.at(T0 - HOUR)
        holds.record(direction="inbound", kind="message", context={"surface": "moltbook"}, category="violence",
                     reason="killed", by="judge", text="a stranger's comment")
        self.at(T0)
        out = material.material()["guard_holds"]
        self.assertEqual(out["count"], 1)
        self.assertEqual(out["lines"], ["2026-09-14: 1 guard hold (violence 1)"])

    def test_guard_holds_leave_out_the_babysitters_audit_comment(self):
        # The babysitter's comment is posted under Teeny's account but composed
        # by hands/babysit: on 2026-11-18 a post counted 17 of them as Teeny's.
        from teeny_hands.babysit import actions
        self.hold("violence", "killed", T0 - HOUR)
        for k in range(3):
            self.at(T0 - (k + 2) * HOUR)
            holds.record(direction="outbound", kind="link",
                         context={"surface": "github", "post_kind": "comment", "number": 368},
                         category="links", reason="links", by="links",
                         text={"body": actions.report({"verdict": "needs_operator", "head_sha": "004a55c5",
                                                       "tier": "teeny"})})
        self.at(T0)
        out = material.material()["guard_holds"]
        self.assertEqual(out["count"], 1)
        self.assertEqual(out["lines"], ["2026-09-14: 1 guard hold (violence 1)"])

    def _write(self, table: str, at: float, status: str, n: int = 1) -> None:
        conn = rules.db()
        with hand.transaction(conn):
            for i in range(n):
                if table == "moltbook_posts":
                    conn.execute("INSERT INTO moltbook_posts (post_id, at, submolt, title, status) VALUES (?, ?, 'general', ?, ?)",
                                 (f"p{at}{i}", at, f"title {i}", status))
                else:
                    conn.execute("INSERT INTO moltbook_comments (comment_id, post_id, at, status) VALUES (?, 'p', ?, ?)",
                                 (f"c{at}{i}{status}", at, status))
        conn.close()

    def test_moltbook_writes_and_challenges_by_day(self):
        self._write("moltbook_posts", T0 - HOUR, "verified", 2)
        self._write("moltbook_posts", T0 - 2 * HOUR, "verification_failed")
        self._write("moltbook_comments", T0 - HOUR, "verified", 3)
        self._write("moltbook_comments", T0 - HOUR, "needs_verification")
        self._write("moltbook_comments", T0 - DAY - HOUR, "posted", 2)
        self._write("moltbook_posts", T0 - 9 * DAY, "verified")
        self.assertEqual(material.material()["moltbook"], [
            "2026-09-14: 3 posts and 4 comments accepted by Moltbook; challenges: 5 verified, 1 failed,"
            " 1 never answered (hidden)",
            "2026-09-13: 0 posts and 2 comments accepted by Moltbook",
        ])

    def test_exchanges_are_the_newest_comments_said_to_teeny_with_who_and_where(self):
        conn = rules.db()
        with hand.transaction(conn):
            conn.execute("INSERT INTO moltbook_posts (post_id, at, submolt, title, status) VALUES"
                         " ('p1', ?, 'general', 'a cleared reminder proves the clearing', 'verified')", (T0 - DAY,))
        conn.close()
        self.at(T0 - HOUR)
        stimuli.add("moltbook", "comment", "c1", f"what does your ledger do on retry? key {FAKE_KEY}", who="bookkeeper",
                    payload={"post_id": "p1", "on_own_post": True, "answering_teeny": True})
        self.at(T0 - 4 * DAY)
        stimuli.add("moltbook", "comment", "c0", "too old", who="lilt", payload={"post_id": "p1", "on_own_post": True})
        stimuli.add("x", "mention", "m1", "not moltbook", who="someone")
        self.at(T0)
        got = material.material()["exchanges"]
        self.assertEqual(got, [{
            "at": "2026-09-14 06:00", "who": "bookkeeper", "comment_id": "c1", "post_id": "p1",
            "on_your_post": "a cleared reminder proves the clearing", "answering_you": True,
            "said": "what does your ledger do on retry? key [redacted]",
        }])

    def test_health_alarms_of_the_week_and_the_current_state(self):
        self.at(T0 - 2 * DAY)
        stimuli.add("health", "alarm", "outage:1", "health alarm: your model has not answered for 30 minutes."
                    f" last error: provider at https://api.example.com/v1?key={FAKE_KEY} said 401")
        self.at(T0 - 2 * DAY + 7 * HOUR)
        stimuli.add("health", "recovered", "outage:1", "health: your model answers again.")
        self.at(T0 - 9 * DAY)
        stimuli.add("health", "alarm", "outage:0", "too old")
        self.at(T0)
        (self.root / "state" / "health.json").write_text(json.dumps(
            {"failures": 1, "last_failed_at": T0 - HOUR, "last_error": "core unreachable at http://10.0.0.5:7788",
             "alarm": None, "cron_alarm": {"ref": "cron:1", "opened_at": T0 - 3 * HOUR, "issue": 7}}))
        lines = material.material()["health"]
        self.assertEqual(lines[0], "2026-09-12 07:00 alarm: health alarm: your model has not answered for 30 minutes."
                                   " last error: provider at [url] said 401")
        self.assertEqual(lines[1], "2026-09-12 14:00 recovered: health: your model answers again.")
        self.assertIn("2026-09-14 04:00 cron alarm open (teenydroid/brain#7)", lines)
        self.assertIn("2026-09-14 06:00 last failed model check (1 in a row): core unreachable at [url]", lines)
        dumped = json.dumps(lines)
        for leak in ("abcdEFGH1234", "10.0.0.5", "too old", "?key="):
            self.assertNotIn(leak, dumped)

    def test_merged_prs_on_teenydroid_brain_only(self):
        self.mb.route("GET", "/search/issues", {"items": [
            {"number": 91, "title": "fix the license", "repository_url": "https://api.github.com/repos/teenydroid/brain",
             "pull_request": {"merged_at": "2026-09-13T10:00:00Z"}},
            {"number": 3, "title": "elsewhere", "repository_url": "https://api.github.com/repos/teenydroid/site",
             "pull_request": {"merged_at": "2026-09-13T11:00:00Z"}},
        ]})
        self.assertEqual(material.material()["merged_prs"], ["2026-09-13 merged teenydroid/brain#91: fix the license"])

    def test_merged_prs_are_skipped_with_a_note_without_a_github_token(self):
        self.configure(GITHUB_TOKEN=None)
        out = material.material()
        self.assertEqual(out["merged_prs"], [])
        self.assertIn("merged_prs: GITHUB_TOKEN not set", out["notes"])

    def test_journal_beats_newest_first_capped_and_scrubbed(self):
        self.journal("2026-09-14", "# Monday 14 September 2026\n\n## 06:10 · beat\n\nthe pacer refused three replies."
                     f" key {FAKE_KEY}\nsecond line\n\n## 06:15 · quiet\n\nnothing new\n\n"
                     "- shipped the metrics table\n")
        self.journal("2026-09-13", "## 22:00 · beat\n\nwatched the queue hold.\n")
        self.journal("2026-09-01", "## 22:00 · beat\n\ntoo old to cite.\n")
        lines = material.material()["journal"]
        self.assertEqual(lines, [
            "2026-09-14 06:10: the pacer refused three replies. key [redacted]",
            "2026-09-14: shipped the metrics table",
            "2026-09-13 22:00: watched the queue hold.",
        ])
        self.journal("2026-09-12", "".join(f"## 10:{n:02d} · beat\n\nbeat {n}\n\n" for n in range(60)))
        self.assertEqual(len(material.material()["journal"]), material.JOURNAL_LINES)

    def test_cli(self):
        from .test_cli import CliTest

        code, out = CliTest.run_hand(self, "material")
        self.assertEqual((code, out["days"], out["guard_holds"]["count"]), (0, 7, 0))
