"""bin/lib/notes_keep.py: snapshots of the mind's notes, so a file its
`file_write` replaced can be read back (JOURNAL.md lost about 19 KB on
2026-11-24 at 02:36Z, and several notes files vanished on 2026-11-23).

Each test runs against a scratch TEENY_ROOT, in-process, with the clock
pinned by TEENY_NOW."""

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
sys.path.insert(0, str(REPO / "bin" / "lib"))

import notes_keep  # noqa: E402

T0 = 1_795_996_800.0  # 2026-11-30T00:00:00Z
HOUR, DAY = 3600, 86400


def stamp(t):
    return notes_keep.stamp_of(t)


class KeepCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.scratch = Path(self.tmp.name)
        self.root = self.scratch / "root"
        self.notes = self.root / "state" / "home" / "notes"
        (self.notes / "inbox").mkdir(parents=True)
        self.keep = self.notes / ".keep"
        self.env = mock.patch.dict(os.environ, {"TEENY_ROOT": str(self.root), "TEENY_NOW": str(T0)})
        self.env.start()

    def tearDown(self):
        self.env.stop()
        self.tmp.cleanup()

    def at(self, t):
        os.environ["TEENY_NOW"] = str(t)

    def journal(self, lines):
        (self.notes / "JOURNAL.md").write_text("".join(f"entry {i}\n" for i in range(lines)))

    def stamps(self):
        return sorted(p.name for p in self.keep.iterdir() if p.is_dir()) if self.keep.exists() else []


class SnapshotTest(KeepCase):
    def test_a_snapshot_copies_the_notes_markdown_and_nothing_else(self):
        self.journal(3)
        (self.notes / "TASKS.md").write_text("- a task\n")
        (self.notes / "challenge-recount.md").write_text("a draft\n")
        (self.notes / "oh-compare.json").write_text("{}")
        (self.notes / "oh-android-compile.yml").write_text("x: 1\n")
        (self.notes / "huge.md").write_text("x" * (notes_keep.MAX_FILE + 1))
        (self.notes / ".hidden.md").write_text("dot\n")
        (self.notes / "inbox" / "tick-1.md").write_text("inbox\n")
        (self.notes / "sub").mkdir()
        (self.notes / "sub" / "deep.md").write_text("deep\n")
        out = notes_keep.snapshot()
        self.assertEqual(out["taken"], f"notes/.keep/{stamp(T0)}")
        self.assertEqual(self.stamps(), [stamp(T0)])
        copy = self.keep / stamp(T0)
        self.assertEqual(sorted(p.name for p in copy.iterdir()), ["JOURNAL.md", "TASKS.md", "challenge-recount.md"])
        self.assertEqual((copy / "JOURNAL.md").read_text(), (self.notes / "JOURNAL.md").read_text())
        self.assertIn("huge.md", out["skipped_files"])

    def test_nothing_changed_means_no_new_snapshot_and_a_change_means_one(self):
        self.journal(3)
        notes_keep.snapshot()
        self.at(T0 + 600)
        out = notes_keep.snapshot()
        self.assertIsNone(out["taken"])
        self.assertEqual(out["unchanged_since"], f"notes/.keep/{stamp(T0)}")
        self.assertEqual(self.stamps(), [stamp(T0)])
        self.at(T0 + 1200)
        self.journal(4)
        self.assertEqual(notes_keep.snapshot()["taken"], f"notes/.keep/{stamp(T0 + 1200)}")
        self.assertEqual(self.stamps(), [stamp(T0), stamp(T0 + 1200)])
        self.at(T0 + 1800)
        (self.notes / "TASKS.md").unlink(missing_ok=True)
        (self.notes / "new.md").write_text("a new file\n")
        self.assertIsNotNone(notes_keep.snapshot()["taken"])

    def test_no_notes_at_all_takes_no_snapshot(self):
        self.assertIsNone(notes_keep.snapshot()["taken"])
        self.assertEqual(self.stamps(), [])

    def test_pruning_keeps_six_hours_whole_then_one_an_hour_to_48_hours_then_one_a_day_to_14_days(self):
        now = T0 + 30 * DAY  # a midnight, UTC
        kept_ages = [0, 600, 5 * HOUR,  # the last six hours: every one
                     7 * HOUR + 600,    # 16:50 kept over 16:40, the same hour
                     47 * HOUR,         # its own hour, inside 48 hours
                     49 * HOUR,         # 23:00 kept over 22:00, the same day
                     3 * DAY + 10]      # 23:59:50 kept over 22:00, the same day
        gone_ages = [7 * HOUR + 1200, 50 * HOUR, 3 * DAY + 2 * HOUR, 15 * DAY]
        for age in kept_ages + gone_ages:
            folder = self.keep / stamp(now - age)
            folder.mkdir(parents=True)
            (folder / "JOURNAL.md").write_text("age 0\n" if age == 0 else f"age {age}\n")
        (self.keep / ".partial-20261101T000000Z").mkdir()
        (self.keep / "not-a-stamp").mkdir()
        self.at(now)
        (self.notes / "JOURNAL.md").write_text("age 0\n")
        out = notes_keep.snapshot()
        self.assertIsNone(out["taken"])
        self.assertEqual(set(self.stamps()), {stamp(now - a) for a in kept_ages} | {"not-a-stamp"})
        self.assertEqual(sorted(out["pruned"]), sorted(stamp(now - a) for a in gone_ages))
        self.assertFalse((self.keep / ".partial-20261101T000000Z").exists())

    def test_the_history_is_bounded(self):
        for i in range(24 * 20):  # twenty days of a change every hour
            self.at(T0 + i * HOUR)
            self.journal(i + 1)
            notes_keep.snapshot()
        # 48 hourly ones (the six-hour window lies inside them) and about 13 daily ones.
        self.assertLessEqual(len(self.stamps()), 48 + 15)


class SymlinkTest(KeepCase):
    def test_a_symlink_in_notes_is_never_followed(self):
        secret = self.scratch / "teeny.env"
        secret.write_text("KEY=secret\n")
        outside_dir = self.scratch / "outside"
        outside_dir.mkdir()
        (outside_dir / "x.md").write_text("outside\n")
        (self.notes / "JOURNAL.md").symlink_to(secret)
        (self.notes / "env.md").symlink_to(secret)
        (self.notes / "dir.md").symlink_to(outside_dir)
        (self.notes / "TASKS.md").write_text("real\n")
        out = notes_keep.snapshot()
        copy = self.keep / stamp(T0)
        self.assertEqual(sorted(p.name for p in copy.iterdir()), ["TASKS.md"])
        for path in copy.rglob("*"):
            self.assertNotIn("secret", path.read_text())
        self.assertIn("JOURNAL.md", out["skipped_files"])

    def test_a_hard_link_into_notes_is_not_copied(self):
        other = self.root / "state" / "home" / "elsewhere.md"
        other.write_text("linked\n")
        os.link(other, self.notes / "linked.md")
        (self.notes / "TASKS.md").write_text("real\n")
        notes_keep.snapshot()
        self.assertEqual(sorted(p.name for p in (self.keep / stamp(T0)).iterdir()), ["TASKS.md"])

    def test_a_planted_keep_link_or_stamp_link_is_not_written_through_or_pruned_through(self):
        outside = self.scratch / "outside"
        outside.mkdir()
        (outside / "precious").write_text("keep\n")
        self.keep.symlink_to(outside)
        self.journal(2)
        notes_keep.snapshot()
        self.assertFalse(self.keep.is_symlink())
        self.assertEqual(sorted(p.name for p in outside.iterdir()), ["precious"])
        # An old stamp that is a link to a folder outside: pruning removes the
        # link, never what it points at.
        old = self.keep / stamp(T0 - 20 * DAY)
        old.symlink_to(outside)
        self.at(T0 + 600)
        self.journal(3)
        notes_keep.snapshot()
        self.assertFalse(old.exists() or old.is_symlink())
        self.assertEqual((outside / "precious").read_text(), "keep\n")


class ShrinkTest(KeepCase):
    def test_a_shrunken_journal_is_reported_with_the_last_full_copy_and_nothing_is_restored(self):
        self.journal(40)
        notes_keep.snapshot()
        self.at(T0 + 600)
        (self.notes / "JOURNAL.md").write_text("one line after a file_write\n")
        out = notes_keep.snapshot()
        full = f"notes/.keep/{stamp(T0)}/JOURNAL.md"
        [alert] = out["shrunk"]
        self.assertEqual((alert["file"], alert["lines_before"], alert["lines_now"], alert["last_full_copy"]),
                         ("JOURNAL.md", 40, 1, full))
        self.assertEqual((self.notes / "JOURNAL.md").read_text(), "one line after a file_write\n")
        self.assertEqual(notes_keep.alerts(), [alert])
        note = (self.notes / "inbox" / "keep-JOURNAL.md").read_text()
        self.assertIn(full, note)
        self.assertIn("40", note)
        # The full copy outlives the hourly and daily rounds while the alert is open.
        for i in range(1, 60):
            self.at(T0 + 600 + i * HOUR)
            (self.notes / "JOURNAL.md").write_text("".join(f"new {j}\n" for j in range(min(i, 30))))
            notes_keep.snapshot()
        self.assertIn(stamp(T0), self.stamps())

    def test_restoring_the_journal_closes_the_alert(self):
        self.journal(40)
        notes_keep.snapshot()
        self.at(T0 + 600)
        self.journal(2)
        notes_keep.snapshot()
        self.assertEqual(len(notes_keep.alerts()), 1)
        self.at(T0 + 1200)
        self.journal(41)
        notes_keep.snapshot()
        self.assertEqual(notes_keep.alerts(), [])

    def test_a_small_trim_is_not_reported(self):
        self.journal(40)
        notes_keep.snapshot()
        self.at(T0 + 600)
        self.journal(40 - notes_keep.SHRINK_LINES)
        self.assertEqual(notes_keep.snapshot()["shrunk"], [])
        self.assertFalse((self.notes / "inbox" / "keep-JOURNAL.md").exists())

    def test_a_vanished_notes_file_is_reported(self):
        self.journal(3)
        (self.notes / "PROJECTS.md").write_text("".join(f"project {i}\n" for i in range(12)))
        notes_keep.snapshot()
        self.at(T0 + 600)
        (self.notes / "PROJECTS.md").unlink()
        [alert] = notes_keep.snapshot()["shrunk"]
        self.assertEqual((alert["file"], alert["lines_before"], alert["lines_now"]), ("PROJECTS.md", 12, 0))
        self.assertEqual(alert["last_full_copy"], f"notes/.keep/{stamp(T0)}/PROJECTS.md")

    def test_the_pulse_carries_the_open_alerts(self):
        import pulse
        self.journal(40)
        notes_keep.snapshot()
        self.assertNotIn("notes_shrunk", pulse.pulse())
        self.at(T0 + 600)
        self.journal(1)
        notes_keep.snapshot()
        out = pulse.pulse()
        self.assertEqual([a["file"] for a in out["notes_shrunk"]], ["JOURNAL.md"])
        self.assertEqual(out["notes_shrunk"][0]["last_full_copy"], f"notes/.keep/{stamp(T0)}/JOURNAL.md")
        json.dumps(out)


class ReviewFindingsTest(KeepCase):
    """The review of #545: what an empty notes/, an overlapping run, a long
    quiet spell, a malformed stamp or a failed inbox write must not lose."""

    def test_every_notes_file_gone_is_still_reported(self):
        self.journal(10)
        (self.notes / "TASKS.md").write_text("- a task\n")
        notes_keep.snapshot()
        self.at(T0 + 600)
        (self.notes / "JOURNAL.md").unlink()
        (self.notes / "TASKS.md").unlink()
        out = notes_keep.snapshot()
        self.assertIsNone(out["taken"], "an empty notes/ makes no empty snapshot")
        self.assertEqual(sorted(a["file"] for a in out["shrunk"]), ["JOURNAL.md", "TASKS.md"])
        self.assertEqual(sorted(a["file"] for a in notes_keep.alerts()), ["JOURNAL.md", "TASKS.md"])
        self.assertTrue((self.notes / "inbox" / "keep-JOURNAL.md").exists())

    def test_a_run_while_another_holds_the_keep_lock_touches_nothing(self):
        import fcntl
        self.journal(3)
        notes_keep.snapshot()
        partial = self.keep / ".partial-20261130T001000Z"
        partial.mkdir()
        (partial / "JOURNAL.md").write_text("being written\n")
        fd = os.open(self.keep, os.O_RDONLY | os.O_DIRECTORY)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX)
            self.at(T0 + 600)
            self.journal(4)
            out = notes_keep.snapshot()
            self.assertTrue(out["busy"])
            self.assertIsNone(out["taken"])
            self.assertTrue((partial / "JOURNAL.md").exists(), "another run's staging folder is left alone")
        finally:
            os.close(fd)
        self.assertIsNotNone(notes_keep.snapshot()["taken"])

    def test_an_alert_after_a_long_quiet_spell_stays_open_and_pins_its_copy(self):
        self.journal(40)
        notes_keep.snapshot()
        self.at(T0 + 20 * DAY)  # nothing changed for twenty days
        self.journal(1)
        [alert] = notes_keep.snapshot()["shrunk"]
        self.assertEqual(alert["stamp"], stamp(T0))
        self.assertEqual([a["file"] for a in notes_keep.alerts()], ["JOURNAL.md"])
        self.at(T0 + 20 * DAY + 600)
        self.journal(2)
        notes_keep.snapshot()
        self.assertIn(stamp(T0), self.stamps())
        self.assertTrue((self.keep / stamp(T0) / "JOURNAL.md").exists())

    def test_a_stamp_shaped_name_that_is_no_date_is_ignored(self):
        (self.keep / "20261301T000000Z").mkdir(parents=True)
        (self.keep / "20261301T000000Z" / "JOURNAL.md").write_text("bogus\n")
        self.journal(3)
        out = notes_keep.snapshot()
        self.assertEqual(out["taken"], f"notes/.keep/{stamp(T0)}")
        self.at(T0 + 600)
        self.journal(4)
        self.assertIsNotNone(notes_keep.snapshot()["taken"])
        self.assertTrue((self.keep / "20261301T000000Z").exists())

    def test_an_inbox_write_that_fails_keeps_the_alert_and_is_retried(self):
        self.journal(40)
        notes_keep.snapshot()
        self.at(T0 + 600)
        self.journal(1)
        with mock.patch.object(notes_keep, "_inbox", side_effect=OSError("disk full")):
            with self.assertRaises(OSError):
                notes_keep.snapshot()
        self.assertEqual([a["file"] for a in notes_keep.alerts()], ["JOURNAL.md"])
        self.assertFalse((self.notes / "inbox" / "keep-JOURNAL.md").exists())
        self.at(T0 + 1200)
        notes_keep.snapshot()
        self.assertIn(f"notes/.keep/{stamp(T0)}/JOURNAL.md", (self.notes / "inbox" / "keep-JOURNAL.md").read_text())
        self.assertIn(stamp(T0), self.stamps())


if __name__ == "__main__":
    unittest.main()
