"""bin/evolve's selection pressure (bin/lib/evolve_selection.py, docs/evolve.md,
"6. Publish" and "Lessons"): a candidate the eval cannot tell from main is
published only with a powered check, and the merged changes' outcomes, by
kind, are lessons the gate enforces.

The 2026-11-22 audit: 11 of the 12 pull requests a pairwise eval measured
scored "no detectable difference", the other 7 no eval task reached, all 19
opened and merged; `lessons` was empty in every generation's evidence."""
from __future__ import annotations

import json
import os
import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(REPO / "bin" / "lib"))

import evolve_outcomes as eo  # noqa: E402
import evolve_selection as sel  # noqa: E402
from test_evolve import CHECK, DAY, T0, EvolveCase, FakePublisher  # noqa: E402
from test_evolve_outcomes import Ledger  # noqa: E402

import evolve  # noqa: E402

#: The live archive's 18 merged changes on 2026-11-22, as the archive holds
#: them: id, title, the files the change touched, where it came from, its
#: check and its latest outcome. Titles only, no learning text.
LIVE = [
    ("g9c4", "curate owns the nod: ids only from this run's listing", ["brain/playbooks/moltbook-curate.md"], "m45", None, None),
    ("g12c2", "giveaway-post reads the thread before firing a beat", ["brain/playbooks/giveaway-post.md"], "m36", None, None),
    ("g13c1", "SOUL hard rule: no theories built from hold patterns", ["brain/SOUL.md"], "m55", None, None),
    ("g13c2", "no likes on X: one SOUL hard rule plus a mentions bar", ["brain/SOUL.md", "brain/playbooks/mentions.md"], "m44", None, None),
    ("g13c3", "conversation skill: a reply-triage block", ["brain/skills/conversation/SKILL.md"], "m37", None, None),
    ("g15c1", "carry m45 into the two comment-firing playbooks", ["brain/playbooks/moltbook-helpdesk.md", "brain/playbooks/moltbook-threads.md"], "m45", None, None),
    ("g15c4", "triage order in every batch-answering place", ["brain/agents/teeny-mentions.md", "brain/playbooks/mentions.md", "brain/playbooks/moltbook-helpdesk.md"], "m21", None, None),
    ("g16c3", "moltbook timing in CONTEXT.md: refused writes go out again", ["brain/CONTEXT.md"], "m19",
     {"kind": "refusals", "rule": "duplicate", "direction": "fall"}, ("hurt", 16, 69)),
    ("g16c4", "SOUL hard rule: No Address Talk", ["brain/SOUL.md"], "m56",
     {"kind": "outbound", "surface": "x", "contains": ["djutsg"], "direction": "zero"}, ("no-data", 0, 0)),
    ("g17c1", "SOUL hard rule: every number i publish carries this turn's source", ["brain/SOUL.md"], "m25",
     {"kind": "judged", "surface": "x"}, ("no-effect", 19, 23)),
    ("g17c4", "dead ground in CONTEXT.md: my own moltbook posts and comments", ["brain/CONTEXT.md"],
     "f:refusals:comment_moltbook:own_comment", {"kind": "refusals", "rule": "own_comment", "direction": "fall"}, ("no-effect", 7, 5)),
    ("g18c2", "self-portrait series rides the morning post", ["brain/playbooks/morning.md"], "m16",
     {"kind": "outbound", "surface": "x", "contains": ["portrait"], "direction": "rise"}, ("no-data", 0, 0)),
    ("g18c3", "curate playbook: the hand's already-did answer means the upvote landed", ["brain/playbooks/moltbook-curate.md"],
     "f:refusals:upvote_moltbook:duplicate", {"kind": "refusals", "rule": "duplicate", "direction": "fall"}, ("worked", 49, 30)),
    ("g19c4", "self-improve playbook: state the job as a documented pull request", ["brain/playbooks/self-improve.md"],
     "f:act_failed:self-improve", {"kind": "act_failed", "playbook": "self-improve", "direction": "fall"}, ("no-data", 3, 0)),
    ("g20c3", "CONTEXT.md hard rule: a moltbook post_id or parent_id is a full id", ["brain/CONTEXT.md"],
     "f:refusals:comment_moltbook:short_id", {"kind": "refusals", "rule": "short_id", "direction": "fall"}, ("worked", 4, 0)),
    ("g20c4", "moltbook-threads: close the answer shape", ["brain/playbooks/moltbook-threads.md"],
     "f:act_failed:moltbook-threads", {"kind": "act_failed", "playbook": "moltbook-threads", "direction": "fall"}, ("no-effect", 1, 3)),
    ("g21c1", "CONTEXT hard rule: a dotted token in outbound text reads as a hostname", ["brain/CONTEXT.md"],
     "f:guard_holds:github:links", {"kind": "guard_holds", "category": "links", "direction": "fall"}, ("no-data", 1, 0)),
    ("g21c2", "SOUL hard rule: a moltbook parent with my reply already under it", ["brain/SOUL.md"],
     "f:refusals:comment_moltbook:once_per_parent", {"kind": "refusals", "rule": "once_per_parent", "direction": "fall"}, ("no-data", 0, 2)),
]


def patch_of(files):
    return "".join(f"diff --git a/{f} b/{f}\n--- a/{f}\n+++ b/{f}\n@@ -1 +1,2 @@\n line\n+a rule for {f}\n" for f in files)


def write_archive(rows, merged_at=T0 - 20 * DAY):
    """Archive entries in the live archive's shape, each with its patch
    beside it, merged; outcomes at 2 days."""
    for n, (cid, title, files, learning, check, outcome) in enumerate(rows):
        gen, cand = (int(x) for x in cid[1:].split("c"))
        folder = evolve.evolve_dir() / str(gen)
        folder.mkdir(parents=True, exist_ok=True)
        (folder / f"cand-{cand}.mutation.patch").write_text(patch_of(files))
        section = "finding" if learning.startswith("f:") else "what i have decided"
        entry = {"id": cid, "gen": gen, "cand": cand, "title": title, "verdict": "opened",
                 "learning": {"id": learning, "section": section, "text": "t"},
                 "mutation_patch": f"{gen}/cand-{cand}.mutation.patch", "patch": f"{gen}/cand-{cand}.patch",
                 "pr_number": 300 + n, "merged_at": evolve.iso(merged_at + n * 3600)}
        if check:
            entry["check"] = check
        if outcome:
            entry["outcome_2d"] = {"days": 2, "verdict": outcome[0], "before": outcome[1], "after": outcome[2]}
        evolve.record(entry)


def soul_edit(case, line):
    text = case.base_text("brain/SOUL.md")
    last = text.rstrip().splitlines()[-1]
    return {"path": "brain/SOUL.md", "old": last, "new": f"{last}\n- {line}"}


class Better:
    """An evaluator: each candidate passes with the signal its index names
    (default no detectable difference)."""

    def __init__(self, signals=None, verdict="pass"):
        self.signals, self.verdict = dict(signals or {}), verdict

    def evaluate(self, cands, done):
        for cand in cands:
            signal = self.signals.get(cand["cand"], "no detectable difference")
            done(cand, {"verdict": self.verdict, "reason": f"won some: {signal}",
                        "eval": {"verdict": self.verdict, "tasks": ["reply-fud"], "wins": 50, "losses": 34,
                                 "ties": 0, "decided": 84, "signal": signal}})


class SelectionCase(EvolveCase):
    def setUp(self):
        super().setUp()
        self.config(EVOLVE=1, EVOLVE_GENERATIONS_PER_DAY=24, EVOLVE_PUBLISH_PROOF=1, EVOLVE_FINDING_SLOTS=0)
        self.live()
        self.ledger = Ledger(self.root / "state")

    def failures(self, book, n, since_days=1.5):
        for k in range(n):
            self.ledger.act(book, T0 - since_days * DAY + k * 600, ok=False, error="the hand said no")


class PublishRule(SelectionCase):
    def test_no_detectable_difference_without_a_live_check_is_unproven_and_not_published(self):
        self.config(EVOLVE=1, EVOLVE_GENERATIONS_PER_DAY=24, EVOLVE_PUBLISH_PROOF=1, EVOLVE_FINDING_SLOTS=0, EVOLVE_REVISE_ROUNDS=1)
        self.propose(1, [self.playbook_edit("Check first.")])
        pub = FakePublisher()
        out = self.run_gen(1, publisher=pub, evaluator=Better())
        self.assertEqual(pub.prs, [])
        self.assertEqual(out["candidates"][0]["verdict"], "unproven")
        entry = self.entries()["g1c1"]
        self.assertEqual(entry["verdict"], "unproven")
        self.assertIn("no detectable difference", entry["reason"])
        self.assertIn("judged check", entry["reason"])
        self.assertFalse(entry["check_power"]["powered"])

    def test_the_reason_goes_back_to_the_proposer_and_a_powered_check_then_publishes(self):
        self.failures("moltbook-follow", 6)
        self.propose(1, [self.playbook_edit("Check first.")])
        second = json.loads((self.props / "cand-1.json").read_text())
        second["check"] = {"kind": "act_failed", "playbook": "moltbook-follow", "direction": "fall"}
        (self.props / "cand-1.round-2.json").write_text(json.dumps(second))
        pub = FakePublisher()
        self.run_gen(1, publisher=pub, evaluator=Better())
        self.assertEqual(len(pub.prs), 1)
        entry = self.entries()["g1c1"]
        self.assertEqual(entry["verdict"], "opened")
        self.assertEqual(entry["round"], 2)
        self.assertTrue(entry["check_power"]["powered"])
        self.assertEqual(entry["check_power"]["before"], 6)
        rounds = json.loads((evolve.gen_dir(1) / "cand-1.rounds.json").read_text())["rounds"]
        told = rounds[0]["eval"]["findings"][0]
        self.assertIn("unproven", told["problem"])
        self.assertIn("at least 4 events", told["fix"])
        self.assertIn("Published because", pub.prs[0]["body"])

    def test_a_powered_check_publishes_an_undistinguishable_change(self):
        self.failures("moltbook-follow", 5)
        self.propose(1, [self.playbook_edit("Check first.")])
        body = json.loads((self.props / "cand-1.json").read_text())
        body["check"] = {"kind": "act_failed", "playbook": "moltbook-follow", "direction": "fall"}
        (self.props / "cand-1.json").write_text(json.dumps(body))
        pub = FakePublisher()
        self.run_gen(1, publisher=pub, evaluator=Better())
        self.assertEqual(len(pub.prs), 1)
        self.assertEqual(self.entries()["g1c1"]["round"], 1)

    def test_a_check_history_says_will_be_no_data_is_refused_with_that_reason(self):
        self.config(EVOLVE=1, EVOLVE_GENERATIONS_PER_DAY=24, EVOLVE_PUBLISH_PROOF=1, EVOLVE_FINDING_SLOTS=0, EVOLVE_REVISE_ROUNDS=1)
        self.failures("moltbook-follow", 2)
        self.failures("moltbook-follow", 9, since_days=6)  # outside the 2-day window: not counted
        self.propose(1, [self.playbook_edit("Check first.")])
        body = json.loads((self.props / "cand-1.json").read_text())
        body["check"] = {"kind": "act_failed", "playbook": "moltbook-follow", "direction": "fall"}
        (self.props / "cand-1.json").write_text(json.dumps(body))
        pub = FakePublisher()
        self.run_gen(1, publisher=pub, evaluator=Better())
        self.assertEqual(pub.prs, [])
        entry = self.entries()["g1c1"]
        self.assertEqual(entry["verdict"], "unproven")
        self.assertIn("counted 2 event(s) in the last 2 day(s)", entry["reason"])
        self.assertIn("would come out no-data", entry["reason"])

    def test_a_detectably_better_change_publishes_without_a_counted_check(self):
        self.propose(1, [self.playbook_edit("Check first.")])
        pub = FakePublisher()
        self.run_gen(1, publisher=pub, evaluator=Better({1: "better"}))
        self.assertEqual(len(pub.prs), 1)
        self.assertEqual(self.entries()["g1c1"]["verdict"], "opened")
        self.assertIn("better than main", pub.prs[0]["body"])

    def test_unmeasurable_follows_the_same_rule(self):
        self.config(EVOLVE=1, EVOLVE_GENERATIONS_PER_DAY=24, EVOLVE_PUBLISH_PROOF=1, EVOLVE_FINDING_SLOTS=0, EVOLVE_REVISE_ROUNDS=1)
        self.propose(1, [self.playbook_edit("Check first.")])
        pub = FakePublisher()
        self.run_gen(1, publisher=pub)  # EVOLVE_EVALUATOR=none: unmeasurable
        self.assertEqual(pub.prs, [])
        self.assertIn("no eval task reaches this change", self.entries()["g1c1"]["reason"])

    def test_the_rule_is_the_operators_switch(self):
        self.config(EVOLVE=1, EVOLVE_GENERATIONS_PER_DAY=24, EVOLVE_PUBLISH_PROOF=0, EVOLVE_FINDING_SLOTS=0)
        self.propose(1, [self.playbook_edit("Check first.")])
        pub = FakePublisher()
        self.run_gen(1, publisher=pub, evaluator=Better())
        self.assertEqual(len(pub.prs), 1)

    def test_settings_default_on_and_refuse_nonsense(self):
        (self.root / "secrets" / "evolve.env").write_text("EVOLVE=1\n")
        cfg = evolve.load_config()
        self.assertEqual((cfg["EVOLVE_PUBLISH_PROOF"], cfg["EVOLVE_POWER_MIN_EVENTS"], cfg["EVOLVE_LESSON_MIN_JUDGED"]),
                         (1, 4, 3))
        (self.root / "secrets" / "evolve.env").write_text("EVOLVE=1\nEVOLVE_POWER_MIN_EVENTS=0\n")
        with self.assertRaises(evolve.EvolveError):
            evolve.load_config()


class SilencedSource(SelectionCase):
    def test_a_falling_check_on_a_change_that_turns_its_job_off_is_not_powered(self):
        self.config(EVOLVE=1, EVOLVE_GENERATIONS_PER_DAY=24, EVOLVE_PUBLISH_PROOF=1, EVOLVE_FINDING_SLOTS=0, EVOLVE_REVISE_ROUNDS=1)
        self.failures("moltbook-follow", 8)
        cron = self.base_text("brain/cron.toml")
        anchor = 'command = "bin/act moltbook-follow"'
        self.assertIn(anchor, cron)
        self.propose(1, [self.playbook_edit("Check first."),
                         {"path": "brain/cron.toml", "old": anchor, "new": anchor + "\nenabled = false"}])
        body = json.loads((self.props / "cand-1.json").read_text())
        body["check"] = {"kind": "act_failed", "playbook": "moltbook-follow", "direction": "fall"}
        (self.props / "cand-1.json").write_text(json.dumps(body))
        pub = FakePublisher()
        self.run_gen(1, publisher=pub, evaluator=Better())
        entry = self.entries()["g1c1"]
        self.assertEqual(pub.prs, [], entry.get("gate"))
        self.assertEqual(entry["verdict"], "unproven")
        self.assertIn("runs less", entry["reason"])
        self.assertIn("turned off", entry["reason"])

    def test_a_count_that_fell_because_its_playbook_stopped_running_is_not_worked(self):
        led = self.ledger
        t0 = T0 - 3 * DAY
        for k in range(8):
            led.act("moltbook-follow", t0 - DAY - k * 3600, ok=False, error="the hand said no")
        for k in range(4):
            led.act("moltbook-follow", t0 - DAY + 600 - k * 3600, ok=True)
        # After the merge the playbook never runs: no failures, because nothing ran.
        entry = {"id": "g1c1", "merged_at": evolve.iso(t0),
                 "check": {"kind": "act_failed", "playbook": "moltbook-follow", "direction": "fall"}}
        got = eo.score(entry, 2, {"g1c1": entry}, led.sources(), T0, min_events=4, p=0.1, ask=None, model=None)
        self.assertEqual(got["verdict"], "no-effect")
        self.assertEqual(got["source"], {"before": 12, "after": 0})
        self.assertIn("ran less", got["why"])
        # The same fall with the playbook still running is a fix.
        for k in range(12):
            led.act("moltbook-follow", t0 + 600 + k * 3600, ok=True)
        got = eo.score(entry, 2, {"g1c1": entry}, led.sources(), T0, min_events=4, p=0.1, ask=None, model=None)
        self.assertEqual(got["verdict"], "worked")


class Lessons(SelectionCase):
    def test_lessons_are_filled_from_an_archive_like_the_live_one(self):
        write_archive(LIVE)
        got = evolve.observe(self.entries())["lessons"]
        kinds = {l["kind"]: l for l in got["kinds"]}
        self.assertTrue(kinds)
        rules = kinds["prompt-rule/learning"]
        self.assertEqual(rules["merged"], 5)  # g13c1, g13c2, g16c3, g16c4, g17c1
        self.assertEqual((rules["worked"], rules["no-effect"], rules["hurt"], rules["no-data"], rules["no-check"]),
                         (0, 1, 1, 1, 2))
        self.assertTrue(rules["examples"])
        self.assertIn("title", rules["examples"][0])
        self.assertFalse(rules["blocks"])  # two judged, under three
        found = kinds["prompt-rule/finding"]
        self.assertEqual((found["worked"], found["no-effect"], found["no-data"]), (1, 1, 2))
        self.assertEqual(found["says"], "build-on")
        self.assertEqual(found["examples"][0]["id"], "g20c3")
        self.assertEqual(kinds["playbook/finding"]["says"], "build-on")  # g18c3 worked
        # And the proposer's brief leads with them.
        request = {"gen": 2, "cand": 1, "learning": {"id": "m1", "section": "what i have decided", "text": "x"},
                   "frozen": [], "evidence": {"lessons": got}}
        first = evolve.brief(request, edits_in_place=False)[0]
        self.assertIn("lessons from your merged changes", first)
        self.assertIn("prompt-rule/learning", first)

    def test_a_kind_whose_checks_keep_coming_back_no_data_is_a_lesson(self):
        rows = [(f"g{3 + k}c1", f"a portrait rule {k}", ["brain/playbooks/morning.md"], f"m{k}",
                 {"kind": "outbound", "surface": "x", "contains": ["portrait"], "direction": "rise"}, ("no-data", 0, 0))
                for k in range(3)]
        write_archive(rows)
        lesson = evolve.observe(self.entries())["lessons"]["kinds"][0]
        self.assertEqual(lesson["says"], "cannot-be-measured")
        self.assertFalse(lesson["blocks"])

    def exhausted(self):
        rows = [(f"g{3 + k}c1", f"SOUL hard rule number {k}", ["brain/SOUL.md"], f"m{80 + k}",
                 {"kind": "judged", "surface": "x"}, (verdict, 5, 5))
                for k, verdict in enumerate(["no-effect", "hurt", "no-effect"])]
        write_archive(rows)

    def test_a_fourth_hard_rule_after_three_that_did_not_work_is_rejected_naming_the_lesson(self):
        self.exhausted()
        self.propose(1, [soul_edit(self, "never guess a captcha.")], title="SOUL hard rule: no guessing")
        pub = FakePublisher()
        self.run_gen(1, publisher=pub, evaluator=Better({1: "better"}))
        entry = self.entries()["g6c1"]
        self.assertEqual(pub.prs, [])
        self.assertEqual(entry["verdict"], "parked")
        self.assertIn("lesson `prompt-rule/learning` is exhausted", entry["reason"])
        self.assertIn("SOUL hard rule number", entry["reason"])
        self.assertEqual(entry["kinds"], ["prompt-rule/learning"])

    def test_padding_the_rule_with_a_playbook_line_is_still_the_rule(self):
        self.exhausted()
        self.propose(1, [soul_edit(self, "never guess a captcha."), self.playbook_edit("Check first.")])
        self.run_gen(1, evaluator=Better({1: "better"}))
        entry = self.entries()["g6c1"]
        self.assertEqual(entry["verdict"], "parked")
        self.assertEqual(entry["kinds"], ["prompt-rule/learning", "playbook/learning"])

    def test_a_change_of_another_kind_goes_ahead(self):
        self.exhausted()
        self.propose(1, [self.playbook_edit("Check first.")])
        pub = FakePublisher()
        self.run_gen(1, publisher=pub, evaluator=Better({1: "better"}))
        self.assertEqual(len(pub.prs), 1)

    def test_the_lesson_goes_back_to_the_proposer_while_rounds_remain(self):
        self.exhausted()
        self.propose(1, [soul_edit(self, "never guess a captcha.")])
        second = json.loads((self.props / "cand-1.json").read_text())
        second["edits"] = [self.playbook_edit("Check first.")]
        (self.props / "cand-1.round-2.json").write_text(json.dumps(second))
        pub = FakePublisher()
        self.run_gen(1, publisher=pub, evaluator=Better({1: "better"}))
        self.assertEqual(len(pub.prs), 1)
        rounds = json.loads((evolve.gen_dir(6) / "cand-1.rounds.json").read_text())["rounds"]
        self.assertEqual(rounds[0]["outcome"], "lesson")
        self.assertIn("exhausted", rounds[0]["findings"][0]["problem"])


class Kinds(unittest.TestCase):
    def test_kinds_come_from_paths_and_source_not_from_labels(self):
        finding = {"id": "f:refusals:x:dup", "section": "finding"}
        self.assertEqual(sel.kinds(["brain/CONTEXT.md"], finding), ["prompt-rule/finding"])
        self.assertEqual(sel.kinds(["brain/playbooks/a.md", "docs/hands.md"], {"section": "what i have decided"}),
                         ["playbook/learning"])
        self.assertEqual(sel.kinds(["docs/hands.md"], {}), ["docs/learning"])
        self.assertEqual(sel.kinds(["hands/x/x", "hands/tests/test_x.py", "brain/cron.toml"], {}),
                         ["schedule/learning", "hand/learning"])

    def test_a_judged_check_is_never_powered(self):
        got = sel.power({"kind": "judged", "surface": "x"}, 99, 2, 4)
        self.assertFalse(got["powered"])

    def test_need_follows_the_outcome_test(self):
        self.assertEqual(sel.need(4, 4, 0.1), 4)
        self.assertEqual(sel.need(4, 4, 0.01), 7)  # 2^-7 < 0.01
        # At an exact power of two a whole fall only equals P, and the test
        # wants a tail under it: one event more.
        self.assertEqual(sel.need(4, 4, 0.0625), 5)
        self.assertEqual(eo.verdict("fall", 4, 0, 4, 0.0625)[0], "no-effect")
        self.assertEqual(eo.verdict("fall", 5, 0, 4, 0.0625)[0], "worked")


if __name__ == "__main__":
    unittest.main()
