"""Evolve changes what it can measure (docs/evolve.md): SOUL.md and CONTEXT.md
do not grow, a code problem goes to code on first sight, a growth idea is
opened only where its reach stream is thick enough, and Moltbook's code is
left alone.

By 2026-11-25, 30 generations had opened 22 pull requests; most added a
one-line rule to a prompt page, two-day verdicts of 13 were 2 worked and 6
no-data, and the one growth change (g25c3) came out no-effect on a window that
held 5 answered replies."""

import dataclasses
import json
import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(REPO / "bin" / "lib"))

import evolve_findings as ef  # noqa: E402
import evolve_outcomes as eo  # noqa: E402
import evolve_reach as er  # noqa: E402
import evolve_route as rt  # noqa: E402
import evolve_selection as sel  # noqa: E402
from test_evolve import T0, EvolveCase, FakePublisher  # noqa: E402
from test_evolve_growth import XLedger  # noqa: E402
from test_evolve_outcomes import Ledger  # noqa: E402
from test_evolve_route import LabelPublisher  # noqa: E402

import evolve  # noqa: E402
import wants  # noqa: E402

DAY = 86400.0
HOUR = 3600.0
SOUL = "brain/SOUL.md"


class NoEval:
    def evaluate(self, cands, done):
        raise AssertionError("measured a change that should have been refused before any eval")


class Box(EvolveCase):
    settings = {"EVOLVE": 1, "EVOLVE_GENERATIONS_PER_DAY": 24, "EVOLVE_REVISE_ROUNDS": 1, "EVOLVE_FINDING_SLOTS": 0,
                "EVOLVE_PUBLISH_PROOF": 0}

    def setUp(self):
        super().setUp()
        self.config(**self.settings)
        self.live()

    def gen(self, edits, evaluator=None):
        self.propose(1, edits)
        pub = FakePublisher()
        self.run_gen(1, publisher=pub, **({"evaluator": evaluator} if evaluator else {}))
        return self.entries()["g1c1"], pub

    def refused(self, edits):
        entry, pub = self.gen(edits, NoEval())
        self.assertEqual(pub.prs, [])
        self.assertEqual(entry["verdict"], "parked")
        return entry["findings"][0]["problem"]


class PromptPagesDoNotGrow(Box):
    def last(self):
        return self.base_text(SOUL).rstrip().splitlines()[-1]

    def test_adding_a_soul_line_is_refused_before_any_eval_and_the_proposer_is_told_why(self):
        last = self.last()
        told = self.refused([{"path": SOUL, "old": last, "new": f"{last}\n- never guess."}])
        self.assertIn("SOUL and CONTEXT do not grow", told)
        self.assertIn("brain/SOUL.md", told)
        self.assertIn("1 line", told)

    def test_adding_a_context_line_is_refused_too(self):
        text = self.base_text("brain/CONTEXT.md")
        last = text.rstrip().splitlines()[-1]
        self.assertIn("brain/CONTEXT.md", self.refused(
            [{"path": "brain/CONTEXT.md", "old": last, "new": f"{last}\n- a new rule of my own."}]))

    def test_a_rule_smuggled_into_a_longer_line_is_refused(self):
        last = self.last()
        told = self.refused([{"path": SOUL, "old": last, "new": last + " and a duplicate refusal means the id is spent, "
                              "so never fire it again in the same beat or the one after"}])
        self.assertIn("characters", told)

    def test_a_rule_paid_for_with_a_blank_line_is_refused(self):
        text = self.base_text(SOUL)
        first = next(p for p in text.split("\n\n") if p.strip() and text.count(p) == 1)
        self.refused([{"path": SOUL, "old": first + "\n\n", "new": first + "\n- never guess.\n"}])

    def test_rewording_or_trimming_a_line_passes(self):
        last = self.last()
        entry, pub = self.gen([{"path": SOUL, "old": last, "new": last[: len(last) // 2].rstrip() + " plainly."}])
        self.assertEqual(entry["verdict"], "opened", entry.get("findings"))
        self.assertEqual(len(pub.prs), 1)

    def test_a_small_clarification_inside_the_allowance_passes(self):
        last = self.last()
        entry, _ = self.gen([{"path": SOUL, "old": last, "new": last + " today, in full."}])
        self.assertLessEqual(len(" today, in full."), evolve.PROMPT_PAGE_SLACK)
        self.assertEqual(entry["verdict"], "opened", entry.get("findings"))

    def test_moving_a_line_within_the_page_passes(self):
        lines = [x for x in self.base_text(SOUL).splitlines() if x.strip() and self.base_text(SOUL).count(x) == 1]
        a, b = lines[-2], lines[-1]
        entry, _ = self.gen([{"path": SOUL, "old": b, "new": "\x00"}, {"path": SOUL, "old": a, "new": b},
                             {"path": SOUL, "old": "\x00", "new": a}])
        self.assertNotIn("parked", entry["verdict"], entry.get("findings"))


class Told(Box):
    def test_the_research_and_build_briefs_say_the_pages_do_not_grow_and_moltbook_code_is_off_limits(self):
        request = {"learning": {"id": "m1", "section": "what i know", "text": "x"}, "frozen": [], "editable": []}
        for text in ("\n".join(evolve.brief(request, False)), "\n".join(evolve.research_brief(request, False))):
            self.assertIn("may not add", text)
            self.assertIn(f"{evolve.PROMPT_PAGE_SLACK} characters", text)
            self.assertIn("Moltbook's code", text)


class CodeGoesToCode(Box):
    settings = {"EVOLVE": 1, "EVOLVE_GENERATIONS_PER_DAY": 24, "WANTS": 1}

    def setUp(self):
        super().setUp()
        self.ledger = Ledger(evolve.state_dir())
        self.page("# MEMORY.md\n\n## what i have decided\n\n- [m36] check before retrying.\n")

    def beats(self, do, rule, n, refused="already did that"):
        for k in range(n):
            self.ledger.tick(T0 - (k + 1) * HOUR, [{"do": do, "status": "refused",
                                                    "result": {"ok": False, "rule": rule, "refused": refused}}])

    def book(self, name, rule, n):
        for k in range(n):
            self.ledger.act(name, T0 - (k + 1) * HOUR, actions=[{
                "action": "hand", "status": "refused", "result": {"ok": False, "rule": rule, "refused": "no"}}])

    def states(self):
        cfg = evolve.load_config()
        return {f.id: evolve.standing(self.entries(), f).state for f in evolve.read_findings(cfg)}

    def test_a_refusal_of_a_beat_routes_on_first_sight_and_is_filed_once(self):
        self.beats("reply_x", "replied", 6)
        self.assertEqual(self.states()["f:refusals:reply_x:replied"], "routed")
        pub, cfg = LabelPublisher(), evolve.load_config()
        for _ in range(2):
            evolve.route_findings(cfg, self.entries(), evolve.read_findings(cfg), pub)
        self.assertEqual(len(pub.issues), 1)  # hands/x is frozen
        self.assertIn("6 events in 7 days", pub.issues[0]["title"])
        self.assertNotIn("after 0 prompt fixes", pub.issues[0]["title"])
        self.assertIn("none: no playbook's words", pub.issues[0]["body"])

    def test_a_playbooks_input_script_failing_goes_to_the_mind_on_first_sight(self):
        for k in range(5):
            self.ledger.act("brief", T0 - (k + 1) * HOUR, ok=False, error="input brief failed", failure="playbook")
        self.assertEqual(self.states()["f:act_failed:brief"], "routed")
        (want,) = wants.finding_wants()
        self.assertEqual(want.id, "w:f:act_failed:brief")
        self.assertNotIn("prompt rules did not fix it", want.text)

    def test_a_wording_caused_finding_still_goes_to_the_proposer(self):
        self.book("brief", "duplicate", 6)
        for k in range(5):
            self.ledger.act("take", T0 - (k + 1) * HOUR, ok=False, error="the draft turn failed", failure="playbook",
                            stderr="Prompt flagged for security review")
        for k in range(4):  # one input failure among four failed drafts: the words may be the cause
            self.ledger.act("growth", T0 - (k + 1) * HOUR, ok=False, failure="playbook",
                            error="input slot failed" if not k else "the draft is not usable JSON")
        got = self.states()
        for fid in ("f:refusals:brief:duplicate", "f:act_failed:take", "f:act_failed:growth"):
            self.assertEqual(got[fid], "open", fid)

    def test_route_after_zero_still_turns_routing_off(self):
        self.beats("reply_x", "replied", 6)
        self.config(**{**self.settings, "EVOLVE_ROUTE_AFTER": 0})
        self.assertEqual(self.states()["f:refusals:reply_x:replied"], "open")

    # --- Moltbook's code ---------------------------------------------------------------------------

    def test_a_moltbook_hands_refusals_are_ignored_never_routed_and_take_no_slot(self):
        self.beats("upvote_moltbook", "duplicate", 60)
        self.book("moltbook-curate", "duplicate", 50)
        self.book("cost-of-living-moltbook", "retold", 20)  # its output is the moltbook hand's
        self.beats("reply_x", "replied", 6, refused="moltbook has it")  # an X finding that mentions Moltbook
        got = self.states()
        for fid in ("f:refusals:upvote_moltbook:duplicate", "f:refusals:moltbook-curate:duplicate",
                    "f:refusals:cost-of-living-moltbook:retold"):
            self.assertEqual(got[fid], "ignored: moltbook code", fid)
        self.assertEqual(got["f:refusals:reply_x:replied"], "routed")
        cfg = evolve.load_config()
        found = evolve.read_findings(cfg)
        self.assertEqual(evolve.open_findings(self.entries(), found), [])
        self.assertEqual([r["family"] for r in evolve.finding_routes(self.entries(), found, cfg)],
                         ["refusals:x:replied"])
        self.assertEqual(wants.finding_wants(), [])
        rows = {r["id"]: r["state"] for r in evolve.status()["findings"]["current"]}
        self.assertEqual(rows["f:refusals:upvote_moltbook:duplicate"], "ignored: moltbook code")

    def test_a_moltbook_playbooks_failures_stay_with_the_words_and_are_ignored_when_only_code_is_left(self):
        for k in range(5):
            self.ledger.act("moltbook-threads", T0 - (k + 1) * HOUR, ok=False, error="the draft turn failed",
                            failure="playbook", stderr="Prompt blocked")
        for k in range(5):
            self.ledger.act("moltbook-helpdesk", T0 - (k + 1) * HOUR, ok=False, error="input home failed",
                            failure="playbook")
        got = self.states()
        self.assertEqual(got["f:act_failed:moltbook-threads"], "open")
        self.assertEqual(got["f:act_failed:moltbook-helpdesk"], "ignored: moltbook code")

    def test_an_x_playbook_that_reads_moltbook_is_not_ignored_and_its_code_is_not_moltbooks(self):
        for k in range(5):
            self.ledger.act("take", T0 - (k + 1) * HOUR, ok=False, error="input moltbook failed", failure="playbook")
        self.assertEqual(self.states()["f:act_failed:take"], "routed")
        item = next(f for f in evolve.read_findings(evolve.load_config()) if f.id == "f:act_failed:take")
        self.assertEqual([p for p in rt.code_paths(item, REPO) if "moltbook" in p], [])

    def test_a_skip_for_a_frozen_moltbook_path_opens_no_issue(self):
        (self.props / "cand-1.json").write_text(json.dumps(
            {"skip": "the hand should treat it as done", "why": "frozen",
             "where": "hands/lib/teeny_hands/moltbook/actions.py"}))
        pub = FakePublisher()
        self.run_gen(1, publisher=pub)
        self.assertEqual(self.entries()["g1c1"]["skip"]["why"], "frozen")
        self.assertEqual(pub.issues, [])


class MoltbookCodeDiffs(Box):
    def test_a_diff_touching_moltbooks_code_is_refused_before_any_eval(self):
        rel = "hands/tests/moltbook/test_tags.py"
        text = self.base_text(rel)
        first = text.splitlines()[0]
        told = self.refused([{"path": rel, "old": first, "new": first + " "}]) if text.count(first) == 1 else \
            self.refused([{"path": "hands/tests/moltbook/test_new.py", "old": "", "new": "import unittest\n"}])
        self.assertIn("Moltbook's code", told)

    def test_a_moltbook_playbooks_words_may_still_change(self):
        entry, pub = self.gen([self.playbook_edit("Check first.")])  # moltbook-follow.md
        self.assertEqual(entry["verdict"], "opened", entry.get("findings"))

    def test_which_paths_are_moltbooks_code(self):
        for path in ("hands/moltbook", "hands/lib/teeny_hands/moltbook/owed.py", "hands/tests/moltbook/test_owed.py",
                     "bin/moltbook-prune"):
            self.assertTrue(rt.is_moltbook_code(path), path)
        for path in ("brain/playbooks/moltbook-daily.md", "brain/skills/moltbook/SKILL.md", "hands/x",
                     "brain/moltbook-peers.txt", "docs/moltbook.md"):
            self.assertFalse(rt.is_moltbook_code(path), path)


ANSWERED = {"kind": "reach", "surface": "x", "source": "reply-their-thread", "measure": "answered", "direction": "rise"}


class GrowthNeedsAThickStream(Box):
    settings = {"EVOLVE": 1, "EVOLVE_GENERATIONS_PER_DAY": 24, "EVOLVE_REVISE_ROUNDS": 1, "EVOLVE_PUBLISH_PROOF": 1}

    def setUp(self):
        super().setUp()
        self.ledger = XLedger(evolve.state_dir())
        self.page("# MEMORY.md\n\n## what i have decided\n\n")

    def replies(self, n, answered, since=T0 - 7 * DAY, until=T0):
        step = (until - since - 8 * HOUR) / max(n, 1)
        for k in range(n):
            at = since + HOUR + k * step
            m = self.ledger.mention(f"conv{at}", f"s{k}", at - 60)
            self.ledger.post(at, kind="reply", in_reply_to=m)
            if k < answered:
                self.ledger.mention(f"conv{at}", f"s{k}", at + 60)

    def finding(self):
        return next(f for f in evolve.read_findings(evolve.load_config())
                    if f.id == "f:reach_silent:x:reply-their-thread")

    def test_the_needed_n_is_computed_from_the_outcome_test_itself(self):
        n, (q, to) = er.need(20, 150, "rise", 0.1), er.effect(20, 150, "rise")
        self.assertAlmostEqual(to, 2 * q)
        self.assertGreaterEqual(er.detects(n, q, to, 0.1), er.POWER)
        self.assertLess(er.detects(n - 5, q, to, 0.1), er.POWER)
        # A wider share is easier, a rarer one harder; a fall is the rise of the rest.
        self.assertLess(er.need(50, 150, "rise", 0.1), n)
        self.assertGreater(er.need(1, 20, "rise", 0.1), 200)
        self.assertEqual(er.need(15, 20, "fall", 0.1), er.need(5, 20, "rise", 0.1))
        self.assertIsNone(er.need(20, 20, "rise", 0.1))  # nothing left to move
        self.assertGreater(er.need(0, 20, "rise", 0.1), 200)  # none yet: read as one

    def test_a_thin_baseline_waits_with_the_number_needed_and_takes_no_slot(self):
        self.replies(20, 2)
        f = self.finding()
        got = evolve.standing(self.entries(), f)
        self.assertEqual(got.state, "thin")
        self.assertFalse(f.power["powered"])
        self.assertIn("too thin", f.power["why"])
        self.assertIn(f"needs {f.power['need']}", f.power["why"])
        self.assertIn("20", f.power["why"])
        self.assertNotIn(f.id, [x.id for x in evolve.open_findings(self.entries(), [f])])
        row = next(r for r in evolve.status()["findings"]["current"] if r["id"] == f.id)
        self.assertEqual(row["state"], "thin")
        self.assertIn("too thin", row["power"]["why"])

    def test_a_thick_baseline_is_open_on_the_shortest_horizon_that_holds_it(self):
        self.replies(200, 30)
        f = self.finding()
        self.assertEqual(evolve.standing(self.entries(), f).state, "open")
        self.assertTrue(f.power["powered"])
        self.assertEqual(f.power["days"], 7)  # two days hold about 57 of the 94 or so it takes
        self.assertIn("7 days", ef.check_text(f.ref()))

    def test_a_stream_two_weeks_thick_runs_on_a_two_week_horizon(self):
        self.replies(120, 18, since=T0 - 14 * DAY)
        f = self.finding()
        self.assertEqual((f.power["powered"], f.power["days"]), (True, 14))

    def test_a_growth_candidate_is_not_opened_on_a_better_eval_alone(self):
        growth = {"lane": "growth", "id": "f:reach_silent:x:take", "section": "finding"}
        cand = {"learning": growth, "check": ANSWERED, "eval": {"verdict": "pass", "signal": "better"}}
        cfg = evolve.load_config()
        self.assertIn("too thin", evolve.unproven(cfg, {**cand, "check_power": {
            "powered": False, "why": "the baseline is too thin"}}))
        # A record from before the rule counted hits, not items: not proof.
        self.assertIsNotNone(evolve.unproven(cfg, {**cand, "check_power": {"powered": True, "before": 5, "need": 4}}))
        self.assertIsNone(evolve.unproven(cfg, {**cand, "check_power": {"powered": True, "items": 150, "need": 94}}))
        self.assertIsNotNone(evolve.unproven(cfg, {**cand, "check": {"kind": "judged"}, "check_power": {
            "powered": True, "items": 150, "need": 94}}))
        # Anything else is still proven by a better eval.
        self.assertIsNone(evolve.unproven(cfg, {**cand, "learning": {"id": "m1"}, "check_power": {"powered": False}}))

    def test_a_growth_change_is_scored_at_its_own_horizon_only_and_holds_its_finding_until_then(self):
        entry = {"id": "g1c1", "check": ANSWERED, "merged_at": evolve.iso(T0), "verdict": "opened",
                 "check_power": {"powered": True, "days": 14, "items": 120, "need": 90}}
        due = lambda days: [d for _, d in eo.due({"g1c1": entry}, (2, 7), T0 + days * DAY)]  # noqa: E731
        self.assertEqual((due(3), due(8), due(15)), ([], [], [14]))
        self.assertEqual(eo.outcome_of({**entry, "outcome_14d": {"verdict": "worked"}}, (2, 7)), "worked")
        self.assertEqual(eo.outcome_of(entry, (2, 7)), "pending")

    def test_old_archive_rows_still_parse_and_keep_their_horizons(self):
        old = {"id": "g25c3", "check": ANSWERED, "merged_at": evolve.iso(T0), "check_power": {"days": 2, "before": 5},
               "outcome_2d": {"verdict": "no-effect"}, "learning": {"id": "f:reach_silent:x:reply-their-thread",
                                                                    "section": "finding", "events": 39}}
        bare = {"id": "g9c4", "merged_at": evolve.iso(T0), "learning": {"id": "m45", "section": "s", "text": "t"}}
        self.assertEqual(eo.outcome_of(old, (2, 7)), "no-effect")
        self.assertEqual([d for e, d in eo.due({"a": old, "b": bare}, (2, 7), T0 + 30 * DAY)], [7])
        self.assertEqual(eo.outcome_of(bare, (2, 7)), "no-check")
        self.assertTrue(sel.lessons({"g25c3": old, "g9c4": bare}, (2, 7), lambda e: ["x"], 3))

    def test_a_verdict_on_a_thin_baseline_says_so(self):
        self.replies(20, 2, since=T0 - 2 * DAY)
        self.replies(60, 30, since=T0, until=T0 + 2 * DAY)
        entry = {"id": "g1c1", "check": ANSWERED, "merged_at": evolve.iso(T0)}
        got = eo.score(entry, 2, {}, self.ledger.sources(), T0 + 3 * DAY, min_events=4, p=0.1, ask=None, model=None)
        self.assertEqual(got["verdict"], "no-data")
        self.assertIn("baseline is too thin", got["why"])


if __name__ == "__main__":
    unittest.main()
