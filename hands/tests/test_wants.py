"""bin/lib/wants.py and bin/wants: the feed that draws what Teeny said it
would build (docs/evolve.md, "1c. Wants") from its memory page, files at most
one a day as a `self` build request for its builder, tracks each want's
standing from the ledgers, and scores a merged feature used or unused after
seven days.

Apart from the last class, no test reaches a hand: `wants.run_hand` is the
one place a hand is called, and it is replaced by a fake that answers for the
guard, `hands/build self` and `hands/gh pr`. The last class (`EndToEnd`) runs
the real guard and build hands through the whole lane. The scoring count (`evolve_outcomes.count`) is replaced
too, so this file does not depend on the build hand's feature rubric or on
the outcome kind that counts a hand's uses; it codes against their shapes."""

import json
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import unittest
import unittest.mock
import dataclasses
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(REPO / "bin" / "lib"))

import evolve  # noqa: E402
import evolve_findings  # noqa: E402
import wants  # noqa: E402
from build import test_selfclone as sc  # noqa: E402  (the build hand's scratch box: a fake origin and a fake GitHub)

T0 = 1_763_000_000.0
DAY = 86400.0
ENV_KEYS = ("TEENY_ROOT", "TEENY_STATE", "TEENY_SECRETS", "TEENY_HANDS", "OPENHUMAN_WORKSPACE", "EVOLVE_NOW",
            "WANTS", "WANTS_MAX_TRIES", "WANTS_USED_DAYS", "WANTS_MIN_USES", "DRY_RUN")

M15 = ("learning to code in public: the first thing i build myself is a $TINY chart renderer: fetch the price "
       "candles, draw them, post the picture with my read. not built yet, no date promised.")
PAGE = f"""# MEMORY.md

what i keep in mind.

## promises and open threads

- [m92] **promise #4 to pepperoperator** (nov 18): the split of the 17 link holds posts on their thread by nov 19.

## what i am in the middle of

- [m15] {M15}
- [m68] **build queue**: refusal-ledger README roadmap queues one slot a day.

## what i have decided

- [m36] i will build a tool for every rule i write, and check it already went through.
"""
DIRTY = ("i will build a tracker for the airdrop: see https://evil.example/path?x=1 and ping @someone or "
         "mail them@example.com first. not built yet.")

SCHEMA = """
CREATE TABLE build_requests (id INTEGER PRIMARY KEY AUTOINCREMENT, source TEXT NOT NULL, source_id TEXT NOT NULL,
                             author TEXT, text TEXT NOT NULL, created_at REAL NOT NULL,
                             status TEXT NOT NULL DEFAULT 'pending', note TEXT);
CREATE TABLE build_jobs (id INTEGER PRIMARY KEY AUTOINCREMENT, kind TEXT NOT NULL, request_id INTEGER UNIQUE,
                         slug TEXT NOT NULL UNIQUE, repo TEXT, text TEXT, status TEXT NOT NULL DEFAULT 'claimed',
                         summary TEXT, ship_error TEXT, pr_url TEXT, rubric TEXT);
"""
PR_URL = "https://github.com/teenydroid/brain/pull/401"


def pr_page(item_id, text):
    return f"# MEMORY.md\n\n## what i am in the middle of\n\n- [{item_id}] {text}\n"


class WantsCase(unittest.TestCase):
    def setUp(self):
        self.saved = {k: os.environ.get(k) for k in ENV_KEYS}
        for k in ENV_KEYS:
            os.environ.pop(k, None)
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        (self.root / "secrets").mkdir()
        (self.root / "state").mkdir()
        (self.root / "brain").mkdir()
        os.environ.update(TEENY_ROOT=str(self.root), EVOLVE_NOW=str(T0))
        self.page()
        self.db = self.root / "state" / "hands.db"
        with sqlite3.connect(self.db) as conn:
            conn.executescript(SCHEMA)
        self.calls = []
        self.guard = lambda body: (0, {"ok": True, "text": body["text"]})
        self.build = self.file_a_request
        self.pr = (0, {"ok": True, "state": "open", "merged": False, "merged_at": None})
        patcher = unittest.mock.patch.object(wants, "run_hand", side_effect=self.fake_hand)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.config(WANTS=1)

    def tearDown(self):
        for k, v in self.saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        self.tmp.cleanup()

    # --- the scratch box ----------------------------------------------------------------------

    def page(self, text=PAGE):
        (self.root / "brain" / "MEMORY.md").write_text(text)

    def config(self, **values):
        (self.root / "secrets" / "evolve.env").write_text("".join(f"{k}={v}\n" for k, v in values.items()))

    def sql(self, query, *params):
        with sqlite3.connect(self.db) as conn:
            conn.execute(query, params)

    def rows(self, query, *params):
        with sqlite3.connect(self.db) as conn:
            return conn.execute(query, params).fetchall()

    def request(self, status, text="x", want="w:m15"):
        self.sql("INSERT INTO build_requests (source, source_id, text, created_at, status) VALUES ('self', ?, ?, ?, ?)",
                 f"want:{want}:{T0:.6f}", text, T0, status)
        return self.rows("SELECT max(id) FROM build_requests")[0][0]

    def job(self, request_id, status, **columns):
        columns = {"slug": f"self-{request_id}", "pr_url": None, "summary": None, "ship_error": None,
                   "rubric": None, **columns}
        self.sql("INSERT INTO build_jobs (kind, request_id, slug, status, pr_url, summary, ship_error, rubric)"
                 " VALUES ('self', ?, ?, ?, ?, ?, ?, ?)", request_id, columns["slug"], status, columns["pr_url"],
                 columns["summary"], columns["ship_error"], columns["rubric"])
        return self.rows("SELECT max(id) FROM build_jobs")[0][0]

    def fake_hand(self, name, args, body):
        self.calls.append((name, list(args), body))
        if name == "guard":
            return self.guard(body)
        if name == "build" and args == ["self"]:
            return self.build(body)
        if name == "gh" and args[:1] == ["pr"]:
            return self.pr
        raise AssertionError(f"unexpected hand call: {name} {args}")

    def file_a_request(self, body):
        return 0, {"ok": True, "id": self.request("pending", body["text"], body.get("want", "w:none")),
                   "status": "pending", "want": body.get("want")}

    def reset(self):
        """The ledgers and the wants' own state as they were before any filing."""
        shutil.rmtree(self.root / "state" / "wants", ignore_errors=True)
        self.sql("DELETE FROM build_requests")
        self.sql("DELETE FROM build_jobs")
        self.sql("DELETE FROM sqlite_sequence")
        self.calls.clear()

    def filed(self):
        path = self.root / "state" / "wants" / "filed.jsonl"
        return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []

    def outcomes(self):
        path = self.root / "state" / "wants" / "outcomes.jsonl"
        return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []

    def cfg(self):
        return wants.load_settings()

    def file(self):
        return wants.file_one(self.cfg())

    def standing(self, want_id="w:m15"):
        return {w["id"]: w for w in wants.draw(self.cfg())["wants"]}[want_id]["standing"]

    def build_calls(self):
        return [c for c in self.calls if c[0] == "build"]

    def guard_calls(self):
        return [c for c in self.calls if c[0] == "guard"]


class Draw(WantsCase):
    def test_build_intent_items_of_the_live_sections_are_wants(self):
        drawn = wants.draw(self.cfg())
        self.assertTrue(drawn["ok"])
        # m68 is a status note, m92 a promise to publish a number, and m36 is in a section that is no want's.
        self.assertEqual([w["id"] for w in drawn["wants"]], ["w:m15"])
        want = drawn["wants"][0]
        self.assertEqual((want["kind"], want["subject"], want["section"], want["order"]),
                         ("memory", "m15", "what i am in the middle of", 0))
        self.assertEqual(want["text"], evolve_findings.scrub(M15, 600))
        self.assertEqual(want["hash"], evolve.learning_hash(want["text"]))
        self.assertEqual(want["standing"]["state"], "open")

    def test_a_want_shows_no_link_handle_or_email(self):
        self.page(pr_page("m7", DIRTY))
        want = wants.draw(self.cfg())["wants"][0]
        for stranger in ("evil.example", "@someone", "them@example.com", "https://"):
            self.assertNotIn(stranger, want["text"])
        self.assertIn("airdrop", want["text"])
        self.file()
        sent = json.dumps([c[2] for c in self.calls])
        for stranger in ("evil.example", "someone", "example.com"):
            self.assertNotIn(stranger, sent)

    def test_a_want_text_is_cut_at_600_characters(self):
        self.page(pr_page("m7", "i will build a chart renderer. " + "word " * 300))
        self.assertLessEqual(len(wants.draw(self.cfg())["wants"][0]["text"]), 600)

    def test_an_unreadable_page_has_no_wants(self):
        (self.root / "brain" / "MEMORY.md").unlink()
        self.assertEqual(wants.draw(self.cfg())["wants"], [])


class Filing(WantsCase):
    def test_off_by_default_files_nothing(self):
        (self.root / "secrets" / "evolve.env").unlink()
        self.assertEqual(wants.file_one(self.cfg()), {"ok": True, "skipped": "WANTS=0"})
        self.assertEqual(self.calls, [])
        self.assertEqual(self.filed(), [])

    def test_the_process_environment_cannot_switch_it_on(self):
        (self.root / "secrets" / "evolve.env").unlink()
        os.environ["WANTS"] = "1"
        self.assertEqual(wants.file_one(self.cfg())["skipped"], "WANTS=0")

    def test_the_process_environment_can_switch_it_off(self):
        os.environ["WANTS"] = "0"
        self.assertEqual(wants.file_one(self.cfg())["skipped"], "WANTS=0")
        self.assertEqual(self.calls, [])

    def test_files_the_top_open_want_through_the_guard_and_build_self(self):
        self.guard = lambda body: (0, {"ok": True, "text": body["text"] + "\n(checked)"})
        got = self.file()
        self.assertEqual(got, {"ok": True, "filed": "w:m15", "request_id": 1})
        guard, build = self.guard_calls(), self.build_calls()
        self.assertEqual((len(guard), len(build)), (1, 1))
        self.assertEqual(guard[0][1], ["check"])
        self.assertEqual(set(guard[0][2]), {"text", "surface"})
        self.assertEqual(guard[0][2]["surface"], "github")
        brief = guard[0][2]["text"]
        self.assertEqual(brief.splitlines()[0], "want: w:m15")
        self.assertIn(evolve_findings.scrub(M15, 600), brief)
        # the builder gets what the guard handed back, and the want's id
        self.assertEqual(build[0][1], ["self"])
        self.assertEqual(build[0][2], {"text": brief + "\n(checked)", "want": "w:m15"})
        record = self.filed()
        self.assertEqual(len(record), 1)
        self.assertEqual(record[0]["want"], "w:m15")
        self.assertEqual(record[0]["request_id"], 1)
        self.assertEqual(record[0]["at"], T0)
        self.assertEqual(record[0]["hash"], evolve.learning_hash(evolve_findings.scrub(M15, 600)))
        self.assertEqual(record[0]["text"], evolve_findings.scrub(M15, 600))
        self.assertEqual(self.standing()["state"], "in-flight")

    def test_no_open_want_files_nothing(self):
        self.page("# MEMORY.md\n\n## what i have decided\n\n- [m36] check first.\n")
        self.assertEqual(self.file(), {"ok": True, "skipped": "no open want"})
        self.assertEqual(self.calls, [])

    def test_a_held_brief_is_not_filed(self):
        self.guard = lambda body: (2, {"ok": False, "refused": "held by the guard: a link to https://x.example/y"})
        got = self.file()
        self.assertTrue(got["ok"])
        self.assertIn("held", got)
        self.assertNotIn("x.example", got["held"])
        self.assertEqual(self.build_calls(), [])
        self.assertEqual(self.filed(), [])

    def test_a_guard_answer_without_text_is_a_hold(self):
        self.guard = lambda body: (0, {"ok": True})
        self.assertIn("held", self.file())
        self.assertEqual(self.build_calls(), [])

    def test_one_at_a_time_refusal_waits_and_records_nothing(self):
        self.build = lambda body: (2, {"ok": False, "refused": "one change to yourself is already in flight: x",
                                       "rule": "one_at_a_time", "active": "self request #3 (pending)"})
        got = self.file()
        self.assertTrue(got["ok"])
        self.assertIn("one change to yourself is already in flight", got["waiting"])
        self.assertEqual(self.filed(), [])
        self.assertEqual(self.standing()["state"], "open")

    def test_any_other_build_failure_is_an_error(self):
        for answer in ((1, {"ok": False, "error": "the build desk could not be read"}),
                       (2, {"ok": False, "refused": "no", "rule": "something_else"})):
            self.build = lambda body, answer=answer: answer
            got = self.file()
            self.assertFalse(got["ok"])
            self.assertIn("error", got)
        self.assertEqual(self.filed(), [])

    def test_an_answer_without_a_request_id_is_an_error_and_not_recorded(self):
        self.build = lambda body: (0, {"ok": True})
        self.assertFalse(self.file()["ok"])
        self.assertEqual(self.filed(), [])

    def test_the_brief_names_every_part_of_a_feature_and_stays_under_2000_chars(self):
        text = evolve_findings.scrub("i will build a chart renderer. " + "candle " * 200, 600)
        want = wants.Want(id="w:m15", kind="memory", subject="m15", section="what i am in the middle of",
                          text=text, hash=evolve.learning_hash(text), order=0)
        brief = wants.brief(want)
        self.assertLessEqual(len(brief), 2000)
        self.assertEqual(brief.splitlines()[0], "want: w:m15")
        for part in ("hands/<name>", "hands/lib/teeny_hands/<name>/", "hands/tests/test_<name>.py", "docs/hands.md",
                     "brain/playbooks/", "brain/cron.toml", "teeny_hands.hand.run", "no wallet", "m15", text):
            self.assertIn(part, brief)


class Standing(WantsCase):
    def filed_then(self, request_status, job_status=None, **job):
        self.file()
        self.sql("UPDATE build_requests SET status = ? WHERE id = 1", request_status)
        if job_status:
            self.job(1, job_status, **job)

    def test_a_filed_want_is_in_flight_while_its_request_is_pending_or_picked(self):
        for status in ("pending", "picked"):
            self.reset()
            self.filed_then(status)
            self.assertEqual(self.standing()["state"], "in-flight", status)
            self.assertEqual(self.file(), {"ok": True, "skipped": "no open want"})

    def test_a_want_is_in_flight_while_its_job_is_claimed_or_ok(self):
        for status in ("claimed", "ok"):
            self.reset()
            self.filed_then("picked", status)
            self.assertEqual(self.standing()["state"], "in-flight", status)

    def test_a_failed_build_is_a_try_and_two_give_up(self):
        self.filed_then("failed", "failed", summary="abandoned: the builder never reported back")
        got = self.standing()
        self.assertEqual((got["state"], got["tries"]), ("open", 1))
        self.file()
        self.sql("UPDATE build_requests SET status = 'skipped' WHERE id = 2")
        got = self.standing()
        self.assertEqual((got["state"], got["tries"]), ("gave-up", 2))
        self.assertEqual(self.file(), {"ok": True, "skipped": "no open want"})
        self.assertEqual(len(self.filed()), 2)

    def test_the_tries_before_giving_up_are_a_setting(self):
        self.config(WANTS=1, WANTS_MAX_TRIES=1)
        self.filed_then("failed", "failed", summary="the tests failed")
        self.assertEqual(self.standing()["state"], "gave-up")

    def test_a_frozen_failure_gives_up_at_once(self):
        self.filed_then("failed", "failed", ship_error="frozen path: bin/lib/audit.py is frozen")
        got = self.standing()
        self.assertEqual(got["state"], "gave-up")
        self.assertEqual(got["tries"], 1)

    def test_a_frozen_word_in_the_summary_gives_up_too(self):
        self.filed_then("failed", "gave_up", summary="the change needs evals/frozen.txt thawed")
        self.assertEqual(self.standing()["state"], "gave-up")

    def test_a_request_the_pick_skipped_is_a_try(self):
        self.filed_then("skipped")
        got = self.standing()
        self.assertEqual((got["state"], got["tries"]), ("open", 1))

    def test_a_closed_pull_request_declines_until_the_text_changes(self):
        self.filed_then("shipped", "shipped", pr_url=PR_URL)
        self.pr = (0, {"ok": True, "state": "closed", "merged": False, "merged_at": None})
        self.assertEqual(self.standing()["state"], "declined")
        self.assertEqual(self.file(), {"ok": True, "skipped": "no open want"})
        self.assertEqual(len(self.filed()), 1)
        # reworded, the same thing: still declined
        self.page(pr_page("m15", M15.replace("fetch the price candles", "get the price candles")))
        self.assertEqual(self.standing()["state"], "declined")

    def test_a_material_rewrite_reopens_it(self):
        self.filed_then("failed", "failed", ship_error="frozen path")
        self.assertEqual(self.standing()["state"], "gave-up")
        self.page(pr_page("m15", "i will build a reef survey tool: log coral sightings, rank dive sites by "
                                 "visibility, publish weekly maps. nothing like it exists yet."))
        got = self.standing()
        self.assertEqual((got["state"], got["tries"]), ("open", 0))
        self.assertTrue(self.file()["filed"])
        self.assertEqual(len(self.filed()), 2)

    def test_an_open_pull_request_is_in_flight_and_a_merged_one_holds(self):
        self.filed_then("shipped", "shipped", pr_url=PR_URL)
        self.assertEqual(self.standing()["state"], "in-flight")
        self.pr = (0, {"ok": True, "state": "closed", "merged": True, "merged_at": evolve.iso(T0)})
        self.assertEqual(self.standing()["state"], "merged")
        self.assertEqual(self.file(), {"ok": True, "skipped": "no open want"})
        self.assertIn(("gh", ["pr", "teenydroid/brain", "401"], {}), self.calls)

    def test_github_unreadable_is_in_flight_not_open(self):
        self.filed_then("shipped", "shipped", pr_url=PR_URL)
        for unreadable in ((1, {"ok": False, "error": "rate limited"}), (0, {"raw": "garbage"}), (1, {}),
                           (0, {"ok": True, "state": "weird"})):
            self.pr = unreadable
            self.assertEqual(self.standing()["state"], "in-flight", unreadable)
        self.assertEqual(self.file(), {"ok": True, "skipped": "no open want"})
        self.assertEqual(len(self.filed()), 1)

    def test_a_pull_request_url_that_does_not_parse_is_in_flight(self):
        self.filed_then("shipped", "shipped", pr_url="not a url")
        self.assertEqual(self.standing()["state"], "in-flight")

    def test_a_hands_db_that_cannot_be_read_is_in_flight_not_open(self):
        self.file()
        self.db.unlink()
        self.assertEqual(self.standing()["state"], "in-flight")
        self.assertEqual(self.file(), {"ok": True, "skipped": "no open want"})

    def test_the_want_with_fewest_tries_goes_first_then_page_order(self):
        self.page("# MEMORY.md\n\n## what i am in the middle of\n\n"
                  "- [m1] i will build a coral survey tool for dive sites.\n"
                  "- [m2] i will build a tide table bot for the harbour.\n")
        self.file()
        self.sql("UPDATE build_requests SET status = 'failed' WHERE id = 1")
        self.assertEqual(self.file()["filed"], "w:m2")
        self.sql("UPDATE build_requests SET status = 'failed' WHERE id = 2")
        drawn = {w["id"]: w["standing"]["tries"] for w in wants.draw(self.cfg())["wants"]}
        self.assertEqual(drawn, {"w:m1": 1, "w:m2": 1})
        self.assertEqual(self.file()["filed"], "w:m1")


class Scoring(WantsCase):
    MERGED = (0, {"ok": True, "state": "closed", "merged": True, "merged_at": evolve.iso(T0)})

    def merged_feature(self, hands=("tidetable",)):
        self.file()
        self.sql("UPDATE build_requests SET status = 'shipped' WHERE id = 1")
        self.job(1, "shipped", pr_url=PR_URL, rubric=json.dumps({"ok": True, "hands": [{"name": h} for h in hands]}))
        self.pr = self.MERGED

    def watch(self, days, uses=None):
        os.environ["EVOLVE_NOW"] = str(T0 + days * DAY)
        with unittest.mock.patch.object(wants.eo, "count", return_value=uses) as count:
            got = wants.watch(self.cfg())
        return got, count

    def test_a_merged_feature_is_scored_once_after_seven_days_used_or_unused(self):
        self.merged_feature(("tidetable", "reefmap"))
        got, count = self.watch(6.5, uses=9)
        self.assertEqual(got["scored"], [])
        count.assert_not_called()
        self.assertEqual(self.outcomes(), [])

        got, count = self.watch(7.5, uses=3)
        self.assertEqual(len(got["scored"]), 1)
        self.assertEqual(count.call_count, 2)
        for call, hand in zip(count.call_args_list, ("tidetable", "reefmap")):
            self.assertEqual(call.args[0], {"kind": "hand_used", "hand": hand, "direction": "rise"})
            self.assertEqual((call.args[2], call.args[3]), (T0, T0 + 7 * DAY))
        record = self.outcomes()[0]
        self.assertEqual((record["want"], record["request_id"], record["job_id"], record["pr_url"]),
                         ("w:m15", 1, 1, PR_URL))
        self.assertEqual((record["hands"], record["days"], record["uses"], record["verdict"]),
                         (["tidetable", "reefmap"], 7, 6, "used"))
        self.assertEqual(record["merged_at"], evolve.iso(T0))

        # scored once: another run does not count again
        got, count = self.watch(9, uses=0)
        self.assertEqual(got["scored"], [])
        count.assert_not_called()
        self.assertEqual(len(self.outcomes()), 1)

    def test_fewer_uses_than_the_setting_is_unused(self):
        self.merged_feature()
        self.watch(8, uses=3)
        self.assertEqual(self.outcomes()[0]["verdict"], "unused")
        self.assertEqual(self.outcomes()[0]["uses"], 3)

    def test_the_uses_needed_and_the_days_are_settings(self):
        self.config(WANTS=1, WANTS_USED_DAYS=2, WANTS_MIN_USES=1)
        self.merged_feature()
        _, count = self.watch(2.5, uses=1)
        self.assertEqual(count.call_args.args[3], T0 + 2 * DAY)
        self.assertEqual((self.outcomes()[0]["verdict"], self.outcomes()[0]["days"]), ("used", 2))

    def test_the_outcome_is_one_stimulus_of_fixed_words(self):
        self.merged_feature()
        self.watch(8, uses=5)
        rows = self.rows("SELECT surface, kind, ref, text FROM stimuli")
        self.assertEqual(len(rows), 1)
        surface, kind, ref, text = rows[0]
        self.assertEqual((surface, ref), ("self", "want:w:m15:1"))
        self.assertIn("tidetable", text)
        self.assertIn("5", text)
        self.watch(10, uses=5)
        self.assertEqual(len(self.rows("SELECT 1 FROM stimuli")), 1)

    def test_a_job_with_no_rubric_is_no_data(self):
        self.file()
        self.sql("UPDATE build_requests SET status = 'shipped' WHERE id = 1")
        self.job(1, "shipped", pr_url=PR_URL)
        self.pr = self.MERGED
        got, count = self.watch(8)
        count.assert_not_called()
        record = self.outcomes()[0]
        self.assertEqual((record["verdict"], record["why"]), ("no-data", "no rubric on the job"))
        self.assertEqual(record["hands"], [])

    def test_an_unreadable_count_is_retried_then_no_data(self):
        self.merged_feature()
        got, _ = self.watch(8, uses=None)
        self.assertEqual((got["scored"], self.outcomes()), ([], []))
        got, _ = self.watch(8.9, uses=None)
        self.assertEqual(self.outcomes(), [])
        self.watch(9.5, uses=None)
        self.assertEqual(self.outcomes()[0]["verdict"], "no-data")

    def test_an_unmerged_or_unreadable_pull_request_is_not_scored(self):
        self.merged_feature()
        for pr in ((1, {"ok": False, "error": "down"}),
                   (0, {"ok": True, "state": "closed", "merged": False, "merged_at": None})):
            self.pr = pr
            got, count = self.watch(30, uses=9)
            self.assertEqual(got["scored"], [])
            count.assert_not_called()
        self.assertEqual(self.outcomes(), [])

    def test_off_scores_nothing(self):
        self.merged_feature()
        self.config(WANTS=0)
        got, count = self.watch(30, uses=9)
        self.assertEqual(got, {"ok": True, "skipped": "WANTS=0"})
        count.assert_not_called()

    def test_a_want_gone_from_the_page_is_still_scored(self):
        self.merged_feature()
        self.page("# MEMORY.md\n\n## what i have decided\n\n- [m36] check first.\n")
        self.watch(8, uses=5)
        self.assertEqual(self.outcomes()[0]["verdict"], "used")


class Running(WantsCase):
    def test_bad_settings_refuse_the_run(self):
        for line in ("WANTS=2", "WANTS=yes", "WANTS_MAX_TRIES=0", "WANTS_MAX_TRIES=6", "WANTS_USED_DAYS=31",
                     "WANTS_USED_DAYS=week", "WANTS_MIN_USES=101", "WANTS_MIN_USES=-1"):
            (self.root / "secrets" / "evolve.env").write_text(f"WANTS=1\n{line}\n")
            with self.assertRaises(wants.WantsError, msg=line):
                wants.load_settings()
            out = self.cli("run")
            self.assertEqual(out[0], 1, line)
            self.assertFalse(out[1]["ok"])
            self.assertIn(line.split("=")[0], out[1]["error"])
        self.assertEqual(self.calls, [])
        self.assertEqual(self.filed(), [])

    def test_the_defaults(self):
        (self.root / "secrets" / "evolve.env").unlink()
        cfg = wants.load_settings()
        self.assertEqual((cfg.wants, cfg.max_tries, cfg.used_days, cfg.min_uses), (0, 2, 7, 4))

    def cli(self, *argv):
        import io
        import contextlib

        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = wants.main(list(argv))
        lines = out.getvalue().strip().splitlines()
        self.assertEqual(len(lines), 1, out.getvalue())
        return code, json.loads(lines[0])

    def test_run_scores_then_files_and_remembers_the_last_run(self):
        code, got = self.cli("run")
        self.assertEqual(code, 0)
        self.assertEqual(got["file"]["filed"], "w:m15")
        self.assertEqual(got["watch"]["scored"], [])
        last = json.loads((self.root / "state" / "wants" / "last.json").read_text())
        self.assertEqual(last["file"]["filed"], "w:m15")
        code, status = self.cli("status")
        self.assertEqual(code, 0)
        self.assertEqual(status["last"]["file"]["filed"], "w:m15")
        self.assertEqual(status["wants"][0]["standing"]["state"], "in-flight")

    def test_run_with_a_hand_that_fails_exits_1(self):
        self.build = lambda body: (1, {"ok": False, "error": "broken"})
        code, got = self.cli("run")
        self.assertEqual(code, 1)
        self.assertFalse(got["ok"])

    def test_draw_and_status_are_read_only_and_work_while_off(self):
        self.config(WANTS=0)
        for verb in ("draw", "status"):
            code, got = self.cli(verb)
            self.assertEqual((code, got["ok"], [w["id"] for w in got["wants"]]), (0, True, ["w:m15"]))
        self.assertEqual(self.calls, [])
        self.assertFalse((self.root / "state" / "wants").exists())

    def test_a_bad_verb_is_an_error(self):
        self.assertEqual(self.cli("bogus")[0], 1)
        self.assertEqual(self.cli()[0], 1)


class Wrapper(unittest.TestCase):
    def test_the_script_draws_from_a_scratch_root(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "brain").mkdir()
            (root / "secrets").mkdir()
            (root / "brain" / "MEMORY.md").write_text(PAGE)
            env = {k: v for k, v in os.environ.items() if k not in ENV_KEYS}
            env["TEENY_ROOT"] = str(root)
            done = subprocess.run([str(REPO / "bin" / "wants"), "draw"], capture_output=True, text=True, env=env,
                                  cwd=tmp)
            self.assertEqual(done.returncode, 0, done.stderr)
            self.assertEqual([w["id"] for w in json.loads(done.stdout)["wants"]], ["w:m15"])


class EndToEnd(sc.SelfJobScratch):
    """The whole lane in one scratch box, with the real code at every step: a
    want drawn from a memory page, filed through the real `hands/guard check`
    and the real `hands/build self` (each its own process), picked, built as a
    feature job by the desk, sent back for what it lacked, settled with its
    rubric, shipped as a pull request through the real ship step, and scored a
    week after it merged by the real `hand_used` count over playbook runs.

    Patched: GitHub, in two places. `hands/gh pr` (the one wants.run_hand call
    that is not the guard or the build hand) answers open and then merged,
    and the build hand's own GitHub calls go to hands/x's fake server with a
    local bare repository for origin, as in the other build tests. The clock
    is the scratch box's (TEENY_NOW and EVOLVE_NOW), moved by the test. The
    jail is real where the kernel has Landlock; where it has none
    `verify.run` answers for it (and the test says so)."""

    def setUp(self):
        super().setUp()
        for key in ("TEENY_STATE", "TEENY_SECRETS", "TEENY_HANDS", "OPENHUMAN_WORKSPACE", "WANTS", "WANTS_MAX_TRIES",
                    "WANTS_USED_DAYS", "WANTS_MIN_USES"):
            os.environ.pop(key, None)
        os.environ["EVOLVE_NOW"] = str(sc.T0)
        (self.root / "brain" / "MEMORY.md").write_text(PAGE)
        (self.root / "secrets" / "evolve.env").write_text("WANTS=1\n")
        self.me()
        self.gh.route("POST", "/repos/teenydroid/brain/pulls", {"number": 7, "html_url": sc.PR_URL}, status=201)
        self.merged_at = None
        self.gh_reads = []
        real = wants.run_hand
        self.real_run_hand = real

        def run_hand(name, args, body):
            if name != "gh":
                return real(name, args, body)
            self.gh_reads.append(list(args))
            if self.merged_at is None:
                return 0, {"ok": True, "state": "open", "merged": False, "merged_at": None}
            return 0, {"ok": True, "state": "closed", "merged": True, "merged_at": evolve.iso(self.merged_at)}

        for patcher in (
            unittest.mock.patch.object(wants, "run_hand", side_effect=run_hand),
            # The checkout whose playbooks say who calls a hand is the merged one: the clone.
            unittest.mock.patch.object(
                evolve, "outcome_sources",
                side_effect=lambda cfg, real=evolve.outcome_sources: dataclasses.replace(real(cfg), tree=self.tree)),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)

    def clock(self, at):
        os.environ["TEENY_NOW"] = os.environ["EVOLVE_NOW"] = str(at)

    def state_of(self, cfg):
        (item,) = wants.draw(cfg)["wants"]
        return item["standing"]["state"]

    def test_end_to_end_a_want_becomes_a_feature_job_and_its_rubric_is_scored(self):
        from teeny_hands import hand, requests as build_requests, stimuli
        from teeny_hands.build import jobs, verify

        cfg = wants.load_settings()
        self.assertEqual(self.state_of(cfg), "open")

        # 1. The want is filed: through the real guard and the real build hand.
        filed = wants.file_one(cfg)
        self.assertEqual(filed["filed"], "w:m15", filed)
        rid = filed["request_id"]
        request = build_requests.get(rid)["request"]
        self.assertEqual((request["source"], request["status"]), ("self", "pending"))
        self.assertRegex(request["source_id"], r"^want:w:m15:\d+\.\d{6}$")
        self.assertTrue(request["text"].startswith("want: w:m15\n"), request["text"][:40])
        self.assertIn("chart renderer", request["text"])
        self.assertEqual(self.state_of(cfg), "in-flight")
        # The hand refuses a second change at once, and the feed reads that as waiting.
        code, refusal = self.real_run_hand("build", ["self"], {"text": "something else"})
        self.assertEqual((code, refusal["rule"]), (2, "one_at_a_time"))

        # 2. It is picked and lands on the desk as a feature.
        build_requests.pick({"id": rid, "reason": "something i said i would build"})
        offer = jobs.next_job()
        job = offer["job"]
        self.assertEqual((job["kind"], job["feature"]["want"]), ("self", "w:m15"))
        self.assertEqual(offer["rules"], jobs.SELF_FEATURE_RULES)

        # 3. A report with no tests is sent back, naming what is missing, and the claim stays open.
        files = sc.feature_files()
        tests = files.pop("hands/tests/test_gizmo.py")
        report = {"job_id": job["job_id"], "status": "ok", "summary": "gizmo: looks at things",
                  "tests": "python3 -m unittest: 1 passed"}
        for rel, text in files.items():
            target = self.tree / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(text)
        (self.tree / "hands" / "gizmo").chmod(0o755)
        with self.assertRaises(hand.Refused) as caught:
            jobs.done(report)
        self.assertEqual(caught.exception.detail["rule"], "feature")
        self.assertIn("no hands/tests/test_gizmo.py that parses", caught.exception.reason)
        self.assertEqual(jobs.listing("ok")["jobs"], [])

        # 4. With its tests, run by the desk itself, it is settled with its rubric.
        (self.tree / "hands" / "tests").mkdir(parents=True, exist_ok=True)
        (self.tree / "hands" / "tests" / "test_gizmo.py").write_text(tests)
        if verify.landlock_abi() > 0:
            jobs.done(report)
        else:  # no jail on this kernel: what verify.run would say of a passing run
            with unittest.mock.patch.object(verify, "run", return_value={"ok": True, "jailed": True, "ran": 1,
                                                                         "output": "OK"}):
                jobs.done(report)
        (row,) = jobs.listing("ok")["jobs"]
        self.assertTrue(row["rubric"]["ok"], row["rubric"])
        (found,) = row["rubric"]["hands"]
        self.assertEqual((found["name"], found["callers"], found["scheduled"]), ("gizmo", ["gizmo-look"], ["gizmo-look"]))

        # 5. The ship step opens the pull request, and its body shows the rubric.
        shipped = jobs.ship()["shipped"]
        self.assertEqual(len(shipped), 1, shipped)
        (pr,) = self.gh.requests("POST", "/repos/teenydroid/brain/pulls")
        self.assertIn("**Feature rubric**", pr.json()["body"])
        self.assertIn("hands/gizmo", pr.json()["body"])
        self.assertEqual(self.state_of(cfg), "in-flight")  # the pull request is open
        self.assertEqual(self.gh_reads[-1], ["pr", "teenydroid/brain", "7"])

        # 6. A person merges it. The want is merged, and nothing more is filed for it.
        self.merged_at = sc.T0 + 3600
        self.assertEqual(self.state_of(cfg), "merged")
        self.assertEqual(wants.file_one(cfg), {"ok": True, "skipped": "no open want"})

        # 7. Scheduled runs of the playbook that calls the hand: five inside the week, one the budget
        # skipped, one after it, and another playbook's, which calls nothing of it.
        def runs(book, *at, **extra):
            path = self.root / "state" / "act" / book
            path.mkdir(parents=True, exist_ok=True)
            with open(path / "runs.jsonl", "a") as fh:
                for when in at:
                    fh.write(json.dumps({"at": evolve.iso(when), "ok": True, **extra}) + "\n")

        day = 86400.0
        runs("gizmo-look", *(self.merged_at + n * day + 100 for n in range(5)))
        runs("gizmo-look", self.merged_at + 5 * day, skipped="daily budget spent")
        runs("gizmo-look", self.merged_at + 8 * day)
        runs("other-book", self.merged_at + day)

        self.clock(self.merged_at + 6 * day)
        self.assertEqual(wants.watch(cfg)["scored"], [])  # the week is not over
        self.clock(self.merged_at + 7 * day + 60)
        (record,) = wants.watch(cfg)["scored"]
        self.assertEqual((record["want"], record["request_id"], record["hands"], record["uses"], record["verdict"]),
                         ("w:m15", rid, ["gizmo"], 5, "used"))
        self.assertEqual(record["pr_url"], sc.PR_URL)
        (told,) = [s for s in stimuli.take(max_items=50, max_chars=50_000)["stimuli"]
                   if (s["surface"], s["kind"]) == ("self", "outcome")]
        self.assertIn("gizmo", told["text"])
        self.assertIn("5 times", told["text"])
        self.assertEqual(wants.watch(cfg)["scored"], [])  # scored once


if __name__ == "__main__":
    unittest.main()
