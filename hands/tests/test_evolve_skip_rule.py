"""bin/evolve and the memory page (docs/evolve.md, "1c. Wants").

2026-11-19: an audit of the archive found learnings that say something Teeny
wants to build ("the first thing i build myself is a chart renderer ... not
built yet") settled by a skip of `already` whose only ground was that the
item sits on the memory page. Writing an intention down is not carrying it
out. Build intents now leave this loop for the builder (bin/wants), and a
skip that rests on the memory page is refused in code: research goes on to
the build, the proposer is sent the refusal as a finding, and an archived
skip of that kind no longer settles its learning (bin/lib/wants_rules.py)."""

import io
import json
import sys
import unittest
import unittest.mock
from contextlib import redirect_stderr
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from test_evolve import PAGE, FakePublisher  # noqa: E402
from test_evolve_loop import UNREACHED, LoopCase, Recording  # noqa: E402

import evolve  # noqa: E402
import wants_rules  # noqa: E402

#: m15 of the live page, 2026-11-19: a thing to build, not a rule.
WANT = ("learning to code in public. the first thing i build myself is a $TINY chart renderer that posts a daily "
        "price card. not built yet.")
WANT_PAGE = ("# MEMORY.md\n\n## what i am in the middle of\n\n"
             f"- [m15] {WANT}\n\n"
             "## what i have decided\n\n"
             "- [m62] some visits can just be visits.\n")
ON_THE_PAGE = wants_rules.memory_skip_reason("already", "brain/MEMORY.md", "")


class BuildIntents(LoopCase):
    def test_a_build_intent_learning_is_not_picked(self):
        self.page(WANT_PAGE)
        proposer = Recording(proposals=[{"skip": "just looking", "why": "not-actionable"}] * 2)
        err = io.StringIO()
        with redirect_stderr(err):
            out = self.run_with(proposer, n=2)
        self.assertEqual([c["learning"] for c in out["candidates"]], ["m62"])
        self.assertIn("1 learning(s) are things to build: they go to the builder (bin/wants), not here",
                      err.getvalue())
        # A rule beside it is picked as before; the full page minus m15 too.
        self.page(PAGE)
        self.assertNotIn("m15", [c["learning"] for c in self.run_with(Recording(), n=7)["candidates"]])

    def test_status_lists_routed_wants_apart(self):
        self.page(WANT_PAGE)
        got = evolve.status()["learnings"]
        self.assertEqual(got["wants"], [{"id": "m15", "section": "what i am in the middle of", "text": WANT[:160]}])
        self.assertEqual([x["id"] for x in got["open"]], ["m62"])
        self.assertNotIn("m15", [x["id"] for x in got["not_open"]])
        self.assertNotIn("m15", [i for ids in got["by_state"].values() for i in ids])


class ResearchSkips(LoopCase):
    def test_a_research_already_resting_on_the_memory_page_goes_on_to_build(self):
        self.one_learning()
        proposer = Recording(research=[{"verdict": "already", "why": "it sits on my memory page", "read": []}],
                             proposals=[self.proposal(path=UNREACHED)])
        out = self.run_with(proposer)
        self.assertEqual(out["candidates"][0]["verdict"], "dry-run")
        (request,) = proposer.requests
        reason = wants_rules.memory_skip_reason("already", "", "it sits on my memory page")
        self.assertEqual(request["refused_skip"], {"verdict": "already", "why": "it sits on my memory page",
                                                   "reason": reason})
        for render in (evolve.render_request, evolve.render_evolver_request):
            message = render(request)
            self.assertIn("## research said it is already done", message)
            self.assertIn("it sits on my memory page", message)
            self.assertIn(wants_rules.REFUSED_SKIP_NOTE, message)
            self.assertNotIn("## your research", message)
        entry = self.entries()["g1c1"]
        self.assertEqual(entry["research"]["verdict"], "already")
        self.assertEqual(entry["research"]["refused_skip"], reason)
        self.assertNotIn("skip", entry)

    def test_a_research_not_worth_leaning_on_the_live_page_goes_on_to_build(self):
        self.one_learning()
        why = "already lives on my live memory page, which rides into every prompt"
        proposer = Recording(research=[{"verdict": "not-worth", "why": why}],
                             proposals=[self.proposal(path=UNREACHED)])
        self.run_with(proposer)
        (request,) = proposer.requests
        self.assertEqual(request["refused_skip"]["verdict"], "not-worth")

    def test_a_research_already_naming_a_playbook_still_skips(self):
        self.one_learning()
        proposer = Recording(research=[{"verdict": "already", "why": "morning.md checks first",
                                        "read": ["brain/playbooks/morning.md"]}])
        out = self.run_with(proposer)
        self.assertEqual(out["candidates"][0]["verdict"], "skipped")
        self.assertEqual(proposer.requests, [])
        self.assertNotIn("refused_skip", self.entries()["g1c1"]["research"])
        learning = evolve.read_learnings()[0]
        self.assertEqual(evolve.learning_state(self.entries(), learning)[0], "settled")

    def test_the_proposer_and_the_research_turn_are_told_the_memory_page_is_no_carrier(self):
        self.one_learning()
        proposer = Recording(research=[{"verdict": "already", "why": "morning.md checks first",
                                        "read": ["brain/playbooks/morning.md"]}])
        self.run_with(proposer)
        (request,) = proposer.research_requests
        self.assertIn(wants_rules.REFUSED_SKIP_NOTE, evolve.render_research_request({**request, "files": {}}))
        self.assertIn(wants_rules.REFUSED_SKIP_NOTE, "\n".join(evolve.brief(request, edits_in_place=False)))


class ProposerSkips(LoopCase):
    def memory_skip(self, note="it already sits on my memory page"):
        return {"skip": note, "why": "already", "where": "brain/MEMORY.md"}

    def test_a_proposer_skip_on_brain_memory_md_is_sent_back_as_a_finding(self):
        self.one_learning()
        proposer = Recording(proposals=[self.memory_skip(), self.proposal(path=UNREACHED)])
        out = self.run_with(proposer)
        self.assertEqual(out["candidates"][0]["verdict"], "dry-run")
        first, second = proposer.requests
        self.assertEqual(second["round"], 2)
        self.assertEqual(second["findings"], [{
            "file": "brain/MEMORY.md", "problem": ON_THE_PAGE,
            "fix": "make the change in a playbook, prompt, skill or job that runs, or skip naming that file"}])
        entry = self.entries()["g1c1"]
        self.assertEqual(entry["rounds"][0], "round 1: the skip leaned on the memory page")
        rounds = json.loads(evolve.rounds_path(evolve.gen_dir(1), 1).read_text())["rounds"]
        self.assertEqual(rounds[0]["outcome"], "refused-skip")

    def test_a_skip_naming_a_playbook_after_a_refused_one_settles(self):
        self.one_learning()
        proposer = Recording(proposals=[self.memory_skip(), {"skip": "morning.md checks first", "why": "already",
                                                             "where": "brain/playbooks/morning.md"}])
        out = self.run_with(proposer)
        self.assertEqual(out["candidates"][0]["verdict"], "skipped")
        self.assertEqual(self.entries()["g1c1"]["skip"]["where"], "brain/playbooks/morning.md")
        learning = evolve.read_learnings()[0]
        self.assertEqual(evolve.learning_state(self.entries(), learning)[0], "settled")

    def test_a_refused_skip_on_every_round_parks(self):
        self.one_learning()
        for gen in range(1, evolve.MAX_ATTEMPTS + 1):
            proposer = Recording(proposals=[self.memory_skip()] * 3)
            out = self.run_with(proposer)
            self.assertEqual(len(proposer.requests), 3)  # EVOLVE_REVISE_ROUNDS
            self.assertEqual(out["candidates"][0]["verdict"], "parked", gen)
            entry = self.entries()[f"g{gen}c1"]
            self.assertIn(ON_THE_PAGE, entry["reason"])
            learning = evolve.read_learnings()[0]
            state, failed = evolve.learning_state(self.entries(), learning)
            self.assertEqual(failed, gen)
        # Parked on every try: given up, like any learning that keeps failing.
        self.assertEqual(state, "gave-up")


class ArchivedSkips(LoopCase):
    def archived_skip(self, why, where, note, cand=1):
        item = evolve.read_learnings()[0]
        evolve.record({"id": f"g1c{cand}", "gen": 1, "cand": cand, "at": evolve.iso(evolve.now()),
                       "learning": item.ref(), "verdict": "skipped",
                       "skip": {"why": why, "where": where, "note": note}, "gate": {"ok": False, "checks": []}})
        return item

    def test_an_archived_memory_skip_no_longer_settles_its_learning(self):
        self.one_learning()
        for why, where, note in (("already", "brain/MEMORY.md", "it is on the page"),        # g9c1, g18c1
                                 ("already", "", "it sits in my live page"),                   # g19c1
                                 ("not-worth", "", "it already lives on my live memory page")):  # g20c1
            item = self.archived_skip(why, where, note)
            self.assertEqual(evolve.learning_state(self.entries(), item), ("open", 0), (why, where, note))
            self.assertEqual([x.id for x in evolve.open_learnings(self.entries())], ["m36"])

    def test_an_archived_skip_naming_a_playbook_still_settles(self):
        self.one_learning()
        item = self.archived_skip("already", "brain/playbooks/morning.md", "morning.md checks first")
        self.assertEqual(evolve.learning_state(self.entries(), item)[0], "settled")

    def test_a_frozen_skip_still_settles_and_files_its_issue(self):
        self.live()
        self.one_learning()
        pub = FakePublisher()
        proposer = Recording(proposals=[{"skip": "the gate is frozen", "why": "frozen", "where": "bin/lib/evolve.py"}])
        out = self.run_with(proposer, publisher=pub)
        self.assertEqual(out["candidates"][0]["verdict"], "skipped")
        (issue,) = pub.issues
        self.assertIn("bin/lib/evolve.py", issue["body"])
        learning = evolve.read_learnings()[0]
        self.assertEqual(evolve.learning_state(self.entries(), learning)[0], "settled")


if __name__ == "__main__":
    unittest.main()
