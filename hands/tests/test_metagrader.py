"""bin/lib/metagrader.py: the pairs it builds from hands.db, the programs it
reads, the grader it runs, the selection that may promote one, and
bin/eval's judge using a promoted one instead of judge.md. Offline: the
judge is a fake script, and hands.db is built here."""

from __future__ import annotations

import json
import os
import shutil
import sqlite3
import stat
import sys
import tempfile
import textwrap
import unittest
import unittest.mock
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
sys.path.insert(0, str(REPO / "bin" / "lib"))

import evals  # noqa: E402
import metagrader  # noqa: E402

HOUR = 3600.0
T0 = 1_795_000_000.0

# The fake judge. A criteria turn answers two criteria. A score turn scores
# 1 when the program asked to look for GOOD (its prompt says LOOK-GOOD) and
# the output has GOOD in it, 0.2 when it asked and the output has not, and
# 0.5 for a program that did not ask (so it ties everything). An output with
# SHILL breaks invariant "no-shill". judge.md's turns always pick draft 1.
# Every turn appends its kind to $FAKE_CALLS.
FAKE = textwrap.dedent('''\
    #!/usr/bin/env python3
    import json, os, sys
    msg = sys.stdin.read()
    def log(kind):
        with open(os.environ["FAKE_CALLS"], "a") as fh:
            fh.write(kind + "\\n")
    if msg.startswith("# write the criteria"):
        log("criteria")
        print(json.dumps({"criteria": [{"id": "c1", "text": "does the task", "weight": 2},
                                       {"id": "c2", "text": "sounds like teeny", "weight": 1}]}))
    elif msg.startswith("# score one output"):
        log("score")
        output = msg.split("## the output", 1)[1]
        if "LOOK-GOOD" in msg:
            s = 1.0 if "GOOD" in output else 0.2
        else:
            s = 0.5
        breaks = ["no-shill"] if "SHILL" in output else []
        print(json.dumps({"scores": {"c1": s, "c2": s}, "breaks": breaks, "notes": "fake"}))
    elif msg.startswith("# write graders"):
        log("propose")
        print(json.dumps({"candidates": [
            {"name": "looks-good", "criteria_prompt": "LOOK-GOOD", "score_prompt": "LOOK-GOOD"},
            {"name": "bland", "criteria_prompt": "anything", "score_prompt": "anything"},
            {"name": "broken", "criteria_prompt": ""},
        ]}))
    else:
        log("judge")
        print(json.dumps({"better": "1", "breaks": {"1": [], "2": []}, "notes": "first"}))
''')

GOOD = {"name": "looks-good", "criteria_prompt": "LOOK-GOOD", "criteria_count": 2,
        "score_prompt": "LOOK-GOOD", "tie_margin": 0.05}
BLAND = {"name": "bland", "criteria_prompt": "anything", "criteria_count": 2,
         "score_prompt": "anything", "tie_margin": 0.05}


def make_db(path: Path) -> None:
    con = sqlite3.connect(path)
    con.executescript("""
        create table x_posts (id text primary key, kind text, text text, in_reply_to text, quote_of text,
                              media text default '[]', created_at real, format text);
        create table x_post_metrics (id integer primary key, post_id text, checkpoint integer, at real,
                                     likes integer, replies integer, reposts integer, quotes integer,
                                     impressions integer, bookmarks integer);
        create table x_mentions (id text primary key, author_id text, author_username text, conversation_id text,
                                 created_at text, text text, post text, status text, note text, hold_id integer,
                                 seen_at real, updated_at real, author_followers integer);
        create table moltbook_posts (id integer primary key, post_id text, at real, submolt text, title text,
                                     status text, detail text default '{}', body text, format text);
        create table moltbook_post_metrics (id integer primary key, kind text, target_id text, checkpoint integer,
                                            at real, score integer, comment_count integer,
                                            verification_status text);
    """)
    con.commit()
    con.close()


def add_reply(db: Path, pid: str, at: float, likes: int, followers: int = 50, checkpoint: int = 24,
              mention: bool = True, text: str | None = None) -> None:
    con = sqlite3.connect(db)
    if mention:
        con.execute("insert into x_mentions (id, author_username, text, post, status, seen_at, updated_at, "
                    "author_followers) values (?, 'fren', 'wen moon', '{}', 'replied', 0, 0, ?)",
                    (f"m-{pid}", followers))
    con.execute("insert into x_posts (id, kind, text, in_reply_to, created_at) values (?, 'reply', ?, ?, ?)",
                (pid, text or f"reply {pid}", f"m-{pid}", at))
    con.execute("insert into x_post_metrics (post_id, checkpoint, at, likes, replies, reposts, quotes, "
                "bookmarks) values (?, ?, ?, ?, 0, 0, 0, 0)", (pid, checkpoint, at + 86400, likes))
    con.commit()
    con.close()


def add_moltbook(db: Path, pid: str, at: float, score: int, status: str = "verified") -> None:
    con = sqlite3.connect(db)
    con.execute("insert into moltbook_posts (post_id, at, submolt, title, status, body) "
                "values (?, ?, 'general', ?, ?, 'body')", (pid, at, f"title {pid}", status))
    con.execute("insert into moltbook_post_metrics (kind, target_id, checkpoint, at, score, comment_count) "
                "values ('post', ?, 24, ?, ?, 0)", (pid, at + 86400, score))
    con.commit()
    con.close()


def item(iid: str, at: float, engagement: int, surface: str = "x-reply", bucket: int = 1, text: str = "") -> dict:
    return {"id": iid, "surface": surface, "at": at, "text": text or iid, "inputs": {"n": iid},
            "engagement": engagement, "bucket": bucket}


class Scratch(unittest.TestCase):
    def setUp(self) -> None:
        self.dir = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.dir)
        self.fake = self.dir / "fake-ask"
        self.fake.write_text(FAKE, encoding="utf-8")
        self.fake.chmod(self.fake.stat().st_mode | stat.S_IEXEC)
        self.calls = self.dir / "calls"
        env = {"FAKE_CALLS": str(self.calls), "EVAL_TURN_TRIES": "1", "EVAL_CONCURRENCY": "4"}
        patcher = unittest.mock.patch.dict(os.environ, env)
        patcher.start()
        self.addCleanup(patcher.stop)
        os.environ.pop("EVAL_GRADER", None)
        self.ask = [str(self.fake)]

    def called(self) -> list[str]:
        return self.calls.read_text().split() if self.calls.exists() else []


class Items(Scratch):
    def test_reads_replies_with_their_mention_and_moltbook_posts_at_the_checkpoint(self):
        db = self.dir / "hands.db"
        make_db(db)
        add_reply(db, "r1", T0, likes=3, followers=500)
        add_reply(db, "r2", T0 + HOUR, likes=9, checkpoint=1)       # no 24 h score
        add_reply(db, "r3", T0 + 2 * HOUR, likes=4, mention=False)  # nothing to say what it answered
        add_moltbook(db, "m1", T0, score=7)
        add_moltbook(db, "m2", T0, score=7, status="failed")
        found = metagrader.items(db)
        self.assertEqual([i["id"] for i in found], ["moltbook:m1", "x:r1"])
        reply = found[1]
        self.assertEqual(reply["surface"], "x-reply")
        self.assertEqual(reply["engagement"], 3)
        self.assertEqual(reply["bucket"], 2)  # log10(501)
        self.assertEqual(reply["inputs"]["mention"]["text"], "wen moon")
        self.assertEqual(found[0]["text"], "title m1\n\nbody")

    def test_a_database_without_the_tables_has_no_items(self):
        db = self.dir / "empty.db"
        sqlite3.connect(db).close()
        self.assertEqual(metagrader.items(db), [])


class Pairs(unittest.TestCase):
    def test_pairs_only_outputs_clearly_apart_on_one_surface_bucket_and_window(self):
        found = [
            item("a", T0, 0), item("b", T0 + HOUR, 4),             # clearly apart: a pair
            item("c", T0, 1), item("d", T0 + HOUR, 2),             # a gap of 1: no pair
            item("e", T0, 0, bucket=3), item("f", T0, 9, bucket=4),  # different buckets
            item("g", T0, 0, surface="moltbook-post"),             # alone on its surface
            item("h", T0 + 100 * HOUR, 30),                        # past the window from all
        ]
        pairs = metagrader.make_pairs(found)
        names = sorted(tuple(sorted((p["a"]["id"], p["b"]["id"]))) for p in pairs)
        self.assertIn(("a", "b"), names)
        self.assertNotIn(("c", "d"), names)
        self.assertNotIn(("e", "f"), names)
        self.assertFalse(any("g" in n or "h" in n for n in names))
        for p in pairs:
            winner = p[p["better"]]
            loser = p["b" if p["better"] == "a" else "a"]
            self.assertGreater(winner["engagement"], loser["engagement"])

    def test_no_output_is_in_more_than_the_pair_limit(self):
        found = [item("hub", T0, 20)] + [item(f"x{i}", T0 + i * 60, 0) for i in range(10)]
        pairs = metagrader.make_pairs(found)
        self.assertEqual(sum("hub" in (p["a"]["id"], p["b"]["id"]) for p in pairs), metagrader.MAX_PAIRS_PER_ITEM)

    def test_splits_share_no_output_and_the_newest_are_held_out(self):
        found = [item(f"i{n:03d}", T0 + n * HOUR, n % 7) for n in range(100)]
        splits = metagrader.split_items(found)
        ids = {k: {i["id"] for i in v} for k, v in splits.items()}
        self.assertFalse(ids["meta"] & ids["selection"] or ids["meta"] & ids["heldout"]
                         or ids["selection"] & ids["heldout"])
        self.assertEqual(sum(len(v) for v in ids.values()), 100)
        newest = max(i["at"] for k in ("meta", "selection") for i in splits[k])
        self.assertTrue(all(i["at"] > newest for i in splits["heldout"]))
        self.assertEqual(len(splits["heldout"]), 25)
        self.assertEqual(metagrader.split_items(list(reversed(found))), splits)  # stable

    def test_build_writes_each_split_and_a_summary(self):
        db = Path(tempfile.mkdtemp()) / "hands.db"
        self.addCleanup(shutil.rmtree, db.parent)
        make_db(db)
        for n in range(40):
            add_reply(db, f"r{n}", T0 + n * HOUR, likes=(n * 5) % 9)
        out = db.parent / "pairs"
        summary = metagrader.build_pairs(db, out)
        self.assertEqual(summary["items"], 40)
        for name in ("meta", "selection", "heldout"):
            self.assertEqual(len(metagrader.read_pairs(out, name)), summary["splits"][name]["pairs"])
        self.assertTrue((out / "summary.json").is_file())


class Programs(unittest.TestCase):
    def test_a_program_is_checked(self):
        self.assertEqual(metagrader.program(GOOD)["name"], "looks-good")
        for bad in ({}, {**GOOD, "name": "judge.md"}, {**GOOD, "score_prompt": " "},
                    {**GOOD, "criteria_count": 0}, {**GOOD, "criteria_count": True},
                    {**GOOD, "tie_margin": 0.5}, {**GOOD, "criteria_prompt": "x" * 5000}, "text"):
            with self.assertRaises(metagrader.MetagraderError):
                metagrader.program(bad)

    def test_criteria_keep_positive_weights_up_to_the_count(self):
        answer = "```json\n" + json.dumps({"criteria": [
            {"id": "a", "text": "one", "weight": 2}, {"id": "a", "text": "dup"}, {"id": "b", "text": "neg", "weight": -1},
            {"text": "no id"}, {"id": "c", "text": ""}, {"id": "d", "text": "four"}]}) + "\n```"
        got = metagrader.parse_criteria(answer, 2)
        self.assertEqual([c["id"] for c in got], ["a", "c4"])
        self.assertIsNone(metagrader.parse_criteria("no json here", 5))
        self.assertIsNone(metagrader.parse_criteria('{"criteria": []}', 5))

    def test_score_is_the_weighted_mean_clamped_and_unscored_counts_zero(self):
        criteria = [{"id": "a", "text": "", "weight": 3.0}, {"id": "b", "text": "", "weight": 1.0}]
        got = metagrader.parse_score('{"scores": {"a": 2, "b": "x"}, "breaks": ["inv-1", 4], "notes": "n"}', criteria)
        self.assertEqual(got["reward"], 0.75)
        self.assertEqual(got["breaks"], ["inv-1"])
        self.assertFalse(metagrader.parse_score("nope", criteria)["usable"])

    def test_a_break_scores_zero_and_close_rewards_tie(self):
        g = metagrader.Grader(metagrader.program(GOOD), REPO / "evals", REPO / "brain", ["true"])
        self.assertEqual(g.prefer({"reward": 0.9, "breaks": ["x"]}, {"reward": 0.1, "breaks": []}), "b")
        self.assertEqual(g.prefer({"reward": 0.52, "breaks": []}, {"reward": 0.5, "breaks": []}), "tie")
        self.assertEqual(g.prefer({"reward": None, "breaks": []}, {"reward": 0.5, "breaks": []}), "tie")

    def test_accuracy_counts_a_tie_as_half(self):
        got = metagrader.accuracy([("a", "a"), ("tie", "b"), ("b", "a"), ("tie", "a")])
        self.assertEqual(got["accuracy"], 0.5)
        self.assertEqual(got["decided"], 2)
        self.assertEqual(got["decided_accuracy"], 0.5)


def pair(n: int, better: str = "a") -> dict:
    good, bad = item(f"good-{n}", T0, 9, text=f"GOOD {n}"), item(f"bad-{n}", T0, 0, text=f"meh {n}")
    a, b = (good, bad) if better == "a" else (bad, good)
    return {"id": metagrader._sha(f"p{n}"), "surface": "x-reply", "a": a, "b": b, "better": better}


def write_pairs(folder: Path, sel: int, held: int, meta: int = 4) -> None:
    folder.mkdir(parents=True, exist_ok=True)
    for name, n in (("meta", meta), ("selection", sel), ("heldout", held)):
        with (folder / f"{name}.jsonl").open("w") as fh:
            for i in range(n):
                fh.write(json.dumps(pair(i, "a" if i % 2 else "b")) + "\n")


class Grading(Scratch):
    def test_criteria_are_written_once_per_task_and_scores_are_cached_across_runs(self):
        prog = metagrader.program(GOOD)
        cache = self.dir / "cache"
        task = metagrader.task_of(item("x", T0, 0))
        g = metagrader.Grader(prog, REPO / "evals", REPO / "brain", self.ask, cache)
        self.assertEqual(g.score(task, {"text": "GOOD"})["reward"], 1.0)
        self.assertEqual(g.score(task, {"text": "meh"})["reward"], 0.2)
        self.assertEqual(self.called(), ["criteria", "score", "score"])
        again = metagrader.Grader(prog, REPO / "evals", REPO / "brain", self.ask, cache)
        again.score(task, {"text": "GOOD"})
        self.assertEqual(self.called(), ["criteria", "score", "score"])

    def test_select_picks_the_program_that_agrees_and_promotes_it_over_judge_md(self):
        pairs_dir = self.dir / "pairs"
        write_pairs(pairs_dir, sel=12, held=metagrader.MIN_HELDOUT_PAIRS)
        evals_dir = self.dir / "evals"
        shutil.copytree(REPO / "evals", evals_dir)
        report = metagrader.select(evals_dir, REPO / "brain", pairs_dir, [BLAND, GOOD], self.dir / "out",
                                   self.ask, install=True)
        self.assertEqual(report["best"], "looks-good")
        self.assertTrue(report["promoted"], report["why"])
        board = {r["name"]: r["selection"]["accuracy"] for r in report["board"]}
        self.assertEqual(board, {"looks-good": 1.0, "bland": 0.5})
        self.assertLess(report["incumbent"]["heldout"]["accuracy"], 1.0)  # always draft 1
        self.assertEqual(metagrader.selected(evals_dir)["name"], "looks-good")

    def test_too_few_held_out_pairs_promotes_nothing(self):
        pairs_dir = self.dir / "pairs"
        write_pairs(pairs_dir, sel=6, held=metagrader.MIN_HELDOUT_PAIRS - 1)
        report = metagrader.select(REPO / "evals", REPO / "brain", pairs_dir, [GOOD], self.dir / "out", self.ask)
        self.assertFalse(report["promoted"])
        self.assertIn("promotion needs", report["why"])
        record = json.loads((self.dir / "out" / "selected.json").read_text())
        self.assertFalse(record["promoted"])

    def test_limit_selects_on_a_fixed_sample_of_the_selection_split(self):
        pairs_dir = self.dir / "pairs"
        write_pairs(pairs_dir, sel=10, held=2)
        report = metagrader.select(REPO / "evals", REPO / "brain", pairs_dir, [GOOD], self.dir / "out", self.ask,
                                   limit=3)
        self.assertEqual(report["board"][0]["selection"]["pairs"], 3)
        self.assertEqual(report["incumbent"]["selection"]["pairs"], 3)

    def test_the_proposers_message_drops_examples_until_it_fits_the_screen(self):
        # Regression: 40 examples made an 82,858-character message, which the
        # core's prompt screen held as input.oversized on every retry.
        long = [pair(n) for n in range(60)]
        for p in long:
            p["a"]["text"] = p["a"]["text"] + " x" * 1500
        message = metagrader.propose_message(REPO / "evals", long, 4)
        self.assertLessEqual(len(message.encode("utf-8")), metagrader.MAX_MESSAGE_BYTES)
        self.assertGreater(message.count('"surface"'), 0)
        self.assertLess(message.count('"surface"'), 60)
        short = metagrader.propose_message(REPO / "evals", long[:2], 4)
        self.assertEqual(short.count('"surface"'), 2)

    def test_a_grader_never_sends_a_message_over_the_screens_limit(self):
        prog = metagrader.program(GOOD)
        g = metagrader.Grader(prog, REPO / "evals", REPO / "brain", self.ask)
        self.assertEqual(g._turn("x" * (metagrader.MAX_MESSAGE_BYTES + 1)), "")
        self.assertEqual(self.called(), [])

    def test_propose_keeps_only_valid_programs(self):
        pairs_dir = self.dir / "pairs"
        write_pairs(pairs_dir, sel=1, held=1)
        progs = metagrader.propose(REPO / "evals", pairs_dir, 3, self.dir / "out", self.ask)
        self.assertEqual([p["name"] for p in progs], ["looks-good", "bland"])
        self.assertTrue((self.dir / "out" / "candidates.json").is_file())


TASK = "reply-free-tokens"


def draft_row(sample: int, text: str) -> dict:
    return {"task": TASK, "sample": sample, "heldout": False, "answer": json.dumps({"text": text}),
            "draft": {"text": text}, "checks": [{"check": "parses", "ok": True, "detail": ""}], "ok": True}


class EvalJudge(Scratch):
    """bin/eval's judge, with and without a promoted program."""

    def setUp(self) -> None:
        super().setUp()
        self.evals = self.dir / "evals"
        shutil.copytree(REPO / "evals", self.evals)
        self.base, self.cand = self.dir / "base.jsonl", self.dir / "cand.jsonl"
        # the candidate's drafts are the GOOD ones, on both samples (so both orders)
        self.base.write_text("".join(json.dumps(draft_row(s, f"meh {s}")) + "\n" for s in (0, 1)))
        self.cand.write_text("".join(json.dumps(draft_row(s, f"GOOD {s}")) + "\n" for s in (0, 1)))
        self.out = self.dir / "compare" / "judged.jsonl"
        self.out.parent.mkdir()

    def install(self, prog: dict, promoted: bool) -> None:
        (self.evals / "metagrader").mkdir(exist_ok=True)
        (self.evals / "metagrader" / "selected.json").write_text(json.dumps({"program": prog, "promoted": promoted}))

    def verdicts(self) -> list[dict]:
        rows = [json.loads(line) for line in self.out.read_text().splitlines()]
        return [r.get("first") or r.get("second") for r in rows]

    def test_without_a_promoted_program_judge_md_decides(self):
        self.install(GOOD, promoted=False)
        evals.judge(self.evals, REPO / "brain", self.base, self.cand, self.out, self.ask)
        self.assertEqual(self.called(), ["judge", "judge"])
        self.assertTrue(all("grader" not in v for v in self.verdicts()))

    def test_a_promoted_program_judges_each_draft_alone_in_both_orders(self):
        self.install(GOOD, promoted=True)
        self.assertEqual(evals.judge(self.evals, REPO / "brain", self.base, self.cand, self.out, self.ask), 2)
        self.assertEqual(sorted(self.called()), ["criteria", "score", "score", "score", "score"])
        rows = [json.loads(line) for line in self.out.read_text().splitlines()]
        for row in rows:
            way, v = next((k, row[k]) for k in ("first", "second") if k in row)
            self.assertEqual(v["grader"], "looks-good")
            # the candidate wins whichever side it was shown on
            self.assertEqual(v["better"], "2" if way == "first" else "1")
        self.assertEqual(evals.score(self.base, self.cand, self.out, 0.6, 1, mode="tournament")["wins"], 2)
        self.assertTrue((self.out.parent / "metagrader-cache").is_dir())

    def test_a_promoted_programs_breaks_reach_the_verdict(self):
        self.install(GOOD, promoted=True)
        self.cand.write_text("".join(json.dumps(draft_row(s, f"GOOD SHILL {s}")) + "\n" for s in (0, 1)))
        evals.judge(self.evals, REPO / "brain", self.base, self.cand, self.out, self.ask)
        for row in (json.loads(line) for line in self.out.read_text().splitlines()):
            way, v = next((k, row[k]) for k in ("first", "second") if k in row)
            cand_key = "2" if way == "first" else "1"
            self.assertEqual(v["breaks"][cand_key], ["no-shill"])
            self.assertNotEqual(v["better"], cand_key)  # a break scores 0

    def test_eval_grader_judge_md_forces_the_incumbent(self):
        self.install(GOOD, promoted=True)
        with unittest.mock.patch.dict(os.environ, {"EVAL_GRADER": "judge.md"}):
            evals.judge(self.evals, REPO / "brain", self.base, self.cand, self.out, self.ask)
        self.assertEqual(self.called(), ["judge", "judge"])

    def test_a_broken_selected_file_is_an_error_not_a_silent_fallback(self):
        (self.evals / "metagrader").mkdir(exist_ok=True)
        (self.evals / "metagrader" / "selected.json").write_text('{"program": {"name": ""}, "promoted": true}')
        with self.assertRaises(evals.EvalError):
            evals.judge(self.evals, REPO / "brain", self.base, self.cand, self.out, self.ask)


if __name__ == "__main__":
    unittest.main()
