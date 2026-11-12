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
from teeny_hands.moltbook import material

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
