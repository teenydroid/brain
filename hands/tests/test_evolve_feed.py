"""Which learnings bin/evolve picks (bin/lib/evolve.py, "learnings"; docs/evolve.md,
"1. Learnings"), on test_evolve's harness. Generations 9-15 (2026-11-15 to 17)
spent a third of their 28 candidates on learnings with nothing left to do: the
dream rewrites an item's wording twice a day, the text hash changed, and a
learning already carried out or skipped was picked again. A learning now
follows its id across rewrites that add little, and reopens on a material one."""

import datetime as dt
import json
import os
import sys
import unittest
import unittest.mock
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from test_evolve import DAY, T0, EvolveCase  # noqa: E402
from test_evolve_loop import BUILD, LoopCase, Recording  # noqa: E402

import evolve  # noqa: E402


def at(t):
    return dt.datetime.fromtimestamp(t, dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def learning(lid, text, section="what i have decided"):
    return evolve.Learning(lid, section, text, evolve.learning_hash(text), 0)


def ref(lid, text, section="what i have decided"):
    return learning(lid, text, section).ref()


#: Trimmed from the box's state/evolve/archive.jsonl (generations 9-15), the
#: texts as each candidate saw them.
M68_G9 = ("drips build (steve's order): dripsplan accepted nov 15, ships as a PR for steve; v1 plans and signs nothing "
          "(signing needs operator sign-off). it wants eth on mainnet; my surplus is usdc on base. the refusal-ledger "
          "README roadmap (my theses + fromthebot's line, credited) queues behind it, one slot a day.")
M68_G10 = ("**build queue**: refusal-ledger README roadmap (my theses + fromthebot's credited line) queues one slot a day. "
           "hedgediff was asked by bookkeeper/diviner/lightningzero (transcript vs compaction, report every hedge): "
           "noted, no promises.")
M68_G11 = ("**build queue**: refusal-ledger README roadmap (my theses + fromthebot's credited line) queues one slot a "
           "day — read the diff before any claim, all credits uncut. hedgediff shipped nov 16 to "
           "bookkeeper/diviner/lightningzero's ask.")
M68_G14 = ("**build queue**: refusal-ledger README roadmap queues one slot a day — read the diff before any claim, all "
           "credits uncut; greg2718's review notes queue a brain propose_change brief.")
M53_G12 = ("x402 rail (USDC on Base): funded nov 14; paid reads live under the daily cap. run-4, my own scheduled bridge "
           "job, keeps the surplus topped without anyone asking.")
M53_G14 = ("x402 rail (USDC on Base): funded nov 14; paid reads live under the daily cap. my own scheduled bridge job "
           "keeps the surplus topped without anyone asking.")
M36_G12 = ("before retrying anything, check whether it already went through. wait longer when a retry is costly. when "
           "i don't know why something failed, say \"unknown\", don't guess. a lucky save is luck. platform state beats "
           "my lists: judge a hand by its first outcome, and point a follow-up ref at the thing, never an address. "
           "franassistant's correction: an 'already answered' refusal is the platform's record, not delivery proof — "
           "records diverge, audit against outcomes.")
M36_G14 = ("before retrying anything, check whether it already went through. wait longer when a retry is costly. when "
           "i don't know why something failed, say \"unknown\", don't guess, and never build content theories from "
           "hold patterns — a hold's cause is only what its own record states. a lucky save is luck. platform state "
           "beats my lists: judge a hand by its first outcome, and point a follow-up ref at the thing, never an "
           "address. records diverge — audit against outcomes.")
M45_G9 = ("**how moltbook works**: one top-level comment per post, ever; after that only replies to an existing "
          "comment. never guess an id: use full ids from a fresh listing right before firing — one that read fine "
          "yesterday can 404 today. replies past depth 5 never render; leave them, nod with an upvote.")
M45_G15 = ("**how moltbook works**: one top-level comment per post, ever; after that only replies to an existing "
           "comment, and parent_id is the comment you answer, never its parent. never guess an id: use full ids from a "
           "fresh listing right before firing — one that read fine yesterday can 404 today. replies past depth 5 never "
           "render; leave them, nod with an upvote.")
M19_G10 = ("moltbook pacing: refusals are free and name their cause; the 50-comment cap is a rolling 24h tide, one slot "
           "per aged write — you can't know a slot is free without firing. held = unknown; holds can self-deliver, so "
           "a manual re-send is the duplicate.")
M19_G15 = ("moltbook pacing: refusals are free and name their cause; the 50-comment cap is a rolling 24h tide — one "
           "fresh probe per beat, volleys die. cap-refused comments die (only pacing holds self-retry late, never "
           "re-send by hand); an unreadable challenge is a void, never re-fired; a nag re-offer means unanswered; nag "
           "clearing is display, not delivery.")
MIDDLE = "what i am in the middle of"

#: (id, gen, cand, verdict, at, learning id, section, text, extra)
ARCHIVE = [
    ("g9c2", 9, 2, "no-proposal", "2026-11-15T02:30:00Z", "m68", MIDDLE, M68_G9, {}),
    ("g9c4", 9, 4, "opened", "2026-11-15T02:30:00Z", "m45", "what i have decided", M45_G9,
     {"title": "curate owns the nod: ids only from this run's listing", "pr_number": 288,
      "pr_url": "https://github.com/teenydroid/brain/pull/288", "merged_at": "2026-11-15T10:00:00Z"}),
    ("g10c1", 10, 1, "skipped", "2026-11-15T14:30:00Z", "m68", MIDDLE, M68_G10,
     {"skip": {"why": "already", "where": "brain/playbooks/build-idea.md", "note": "the queue is already working"}}),
    ("g10c4", 10, 4, "skipped", "2026-11-15T14:30:00Z", "m19", "what i have decided", M19_G10,
     {"skip": {"why": "already", "where": "brain/CONTEXT.md", "note": "already carried"}}),
    ("g11c2", 11, 2, "skipped", "2026-11-16T02:30:00Z", "m68", MIDDLE, M68_G11,
     {"skip": {"why": "already", "where": "brain/playbooks/build-idea.md", "note": "the queue already runs itself"}}),
    ("g12c2", 12, 2, "opened", "2026-11-16T11:48:00Z", "m36", "what i have decided", M36_G12,
     {"title": "giveaway-post reads the thread before firing a beat", "pr_number": 320,
      "pr_url": "https://github.com/teenydroid/brain/pull/320", "merged_at": "2026-11-16T16:00:00Z"}),
    ("g12c3", 12, 3, "skipped", "2026-11-16T11:48:00Z", "m53", MIDDLE, M53_G12,
     {"skip": {"why": "already", "where": "brain/cron.toml (run-4, the bridge job)", "note": "already enacted"}}),
]
#: When generation 14 and 15 picked.
GEN14 = dt.datetime(2026, 11, 17, 2, 30, tzinfo=dt.timezone.utc).timestamp()
GEN15 = dt.datetime(2026, 11, 17, 14, 30, tzinfo=dt.timezone.utc).timestamp()


def replay_entries(upto_gen):
    out = {}
    for eid, gen, cand, verdict, when, lid, section, text, extra in ARCHIVE:
        if gen < upto_gen:
            out[eid] = {"id": eid, "gen": gen, "cand": cand, "verdict": verdict, "at": when, "recorded_at": when,
                        "learning": ref(lid, text, section), **extra}
    return out


class Materiality(unittest.TestCase):
    """The rule: a rewrite is material when at least half of the new text's
    content words (three letters or more, no stopword) are not in the old."""

    def test_rewording_and_reordering_add_nothing(self):
        self.assertEqual(evolve.novelty("check first, then retry once.", "Retry once; check FIRST."), 0.0)
        self.assertFalse(evolve.materially_differs(M53_G12, M53_G14))

    def test_a_text_mostly_new_is_material(self):
        self.assertTrue(evolve.materially_differs("some visits can just be visits.",
                                                  "some visits can just be visits, unless a promise is owed there."))
        self.assertGreaterEqual(evolve.novelty(M19_G10, M19_G15), evolve.MATERIAL_SHARE)

    def test_an_empty_text_is_never_material(self):
        self.assertFalse(evolve.materially_differs("anything at all", "the and of"))


class Replay(EvolveCase):
    """Generations 9-15 replayed on the new rule: each case is what the old
    rule did wrong, or the borderline one."""

    def state(self, lid, text, upto_gen, when, section="what i have decided"):
        os.environ["EVOLVE_NOW"] = str(when)
        return evolve.learning_state(replay_entries(upto_gen), learning(lid, text, section))

    def test_m68_build_queue_rewrites_never_reopen_it(self):
        # gen 11 picked it again after gen 10's `already`, gen 14 after gen 11's.
        self.assertEqual(self.state("m68", M68_G11, 11, GEN14 - DAY, MIDDLE)[0], "settled")
        self.assertEqual(self.state("m68", M68_G14, 14, GEN14, MIDDLE)[0], "settled")

    def test_m53_x402_status_note_stays_skipped(self):
        self.assertEqual(self.state("m53", M53_G14, 14, GEN14, MIDDLE)[0], "settled")

    def test_m36_one_clause_merging_an_already_merged_rule_does_not_reopen_it(self):
        self.assertLess(evolve.novelty(M36_G12, M36_G14), evolve.MATERIAL_SHARE)
        self.assertEqual(self.state("m36", M36_G14, 14, GEN14)[0], "settled")

    def test_m45_borderline_a_two_word_clause_stays_settled(self):
        """Gen 15 picked m45 again and got a useful second change (#351),
        but the rewrite added two content words of 33: no lexical rule that
        keeps m68 and m36 settled reopens it. A bigger rewrite would."""
        self.assertLess(evolve.novelty(M45_G9, M45_G15), 0.1)
        self.assertEqual(self.state("m45", M45_G15, 15, GEN15)[0], "settled")

    def test_m19_rewritten_into_new_rules_reopens(self):
        self.assertEqual(self.state("m19", M19_G15, 15, GEN15), ("open", 0))


class ArchiveCase(EvolveCase):
    def entry(self, eid, verdict, lid, text, when=T0, **extra):
        gen, cand = (int(x) for x in eid[1:].split("c"))
        evolve.record({"id": eid, "gen": gen, "cand": cand, "verdict": verdict, "at": at(when),
                       "learning": ref(lid, text), **extra})


class FollowTheId(ArchiveCase):
    def test_failed_tries_follow_the_id_across_a_rewording(self):
        self.entry("g1c1", "no-proposal", "m62", "some visits can just be visits.")
        self.entry("g2c1", "parked", "m62", "Some visits can just be visits!")
        self.assertEqual(evolve.learning_state(self.entries(), learning("m62", "visits: some can just be visits.")),
                         ("open", 2))
        self.entry("g3c1", "inconclusive", "m62", "some visits are just visits.")
        self.assertEqual(evolve.learning_state(self.entries(), learning("m62", "some visits can just be visits.")),
                         ("gave-up", 3))

    def test_a_material_rewrite_starts_the_count_again(self):
        for n in (1, 2, 3):
            self.entry(f"g{n}c1", "no-proposal", "m62", "some visits can just be visits.")
        now = learning("m62", "some visits can just be visits, unless a promise is owed there: then keep it.")
        self.assertEqual(evolve.learning_state(self.entries(), now), ("open", 0))

    def test_a_merged_learning_never_cools_off(self):
        self.entry("g1c1", "opened", "m62", "some visits can just be visits.", merged_at=at(T0))
        os.environ["EVOLVE_NOW"] = str(T0 + 90 * DAY)
        self.assertEqual(evolve.learning_state(self.entries(), learning("m62", "Some visits: just visits."))[0],
                         "settled")

    def test_the_hash_is_still_recorded(self):
        item = learning("m62", "some visits can just be visits.")
        self.assertEqual(item.ref()["hash"], evolve.learning_hash("some  visits can just be visits."))


class SkipCoolOff(ArchiveCase):
    """A skip as `already` or `not-worth` holds for EVOLVE_SKIP_COOLOFF_DAYS
    (doubling with each skip of the same learning) while the text has not
    materially changed; a material change reopens it at once."""

    TEXT = "x402 rail: funded nov 14; paid reads live under the daily cap."

    def skip(self, eid, why="already", when=T0, text=TEXT):
        self.entry(eid, "skipped", "m53", text, when, skip={"why": why, "where": "hands/x402", "note": "it runs"})

    def state(self, when, text=TEXT):
        os.environ["EVOLVE_NOW"] = str(when)
        return evolve.learning_state(self.entries(), learning("m53", text))[0]

    def test_a_skip_holds_through_a_rewording_for_the_cool_off_then_is_asked_again(self):
        self.skip("g1c1", "not-worth")
        reworded = "x402 rail funded nov 14: paid reads live, under the daily cap."
        self.assertEqual(self.state(T0 + 6 * DAY, reworded), "settled")
        self.assertEqual(self.state(T0 + 8 * DAY, reworded), "open")

    def test_a_material_change_reopens_a_skip_at_once(self):
        self.skip("g1c1")
        self.assertEqual(self.state(T0 + DAY, "x402 rail: spend it on paid search for research turns, capped, "
                                              "and post the receipts weekly."), "open")

    def test_each_skip_doubles_the_cool_off(self):
        self.skip("g1c1")
        self.skip("g2c1", when=T0 + 8 * DAY)
        self.assertEqual(self.state(T0 + 8 * DAY + 10 * DAY), "settled")
        self.assertEqual(self.state(T0 + 8 * DAY + 15 * DAY), "open")

    def test_the_cool_off_is_a_setting(self):
        self.config(EVOLVE=1, EVOLVE_SKIP_COOLOFF_DAYS=2)
        self.assertEqual(evolve.load_config()["EVOLVE_SKIP_COOLOFF_DAYS"], 2)
        self.skip("g1c1")
        os.environ["EVOLVE_NOW"] = str(T0 + 3 * DAY)
        self.assertEqual(evolve.learning_state(self.entries(), learning("m53", self.TEXT), cooloff_days=2)[0], "open")

    def test_a_frozen_skip_does_not_cool_off(self):
        self.skip("g1c1", "frozen")
        self.assertEqual(self.state(T0 + 60 * DAY), "settled")


class PickOrder(ArchiveCase):
    PAGE = ("# MEMORY.md\n\n## what i am in the middle of\n\n- [m70] drawing dated self-portraits.\n\n"
            "## what i have decided\n\n- [m62] some visits can just be visits.\n- [m61] answer direct questions first.\n"
            "- [m60] no numbers from memory.\n")

    def test_a_learning_the_box_keeps_failing_drifts_back_without_being_given_up(self):
        self.page(self.PAGE)
        self.entry("g1c1", "deferred", "m62", "some visits can just be visits.", deferred_for="infra")
        self.entry("g2c1", "crashed", "m62", "some visits can just be visits.")
        self.entry("g2c2", "deferred", "m61", "answer direct questions first.", deferred_for="infra")
        order = [x.id for x in evolve.open_learnings(self.entries())]
        self.assertEqual(order[:3], ["m60", "m61", "m62"])
        self.assertEqual(evolve.learning_state(self.entries(), evolve.read_learnings()[1]), ("open", 0))

    def test_status_notes_come_after_every_rule_and_promise(self):
        """'what i am in the middle of' holds status: picked only when no
        rule or promise is open to fill the slot."""
        self.page(self.PAGE)
        self.assertEqual([x.id for x in evolve.open_learnings(self.entries())], ["m62", "m61", "m60", "m70"])

    def test_a_rewording_keeps_its_age_by_the_snapshots(self):
        snaps = evolve.state_dir() / "reflect"
        snaps.mkdir(parents=True)
        old = snaps / "MEMORY-20261110T0200.md"
        old.write_text(self.PAGE)
        os.utime(old, (T0 - 5 * DAY, T0 - 5 * DAY))
        # m60 reworded today, m61 rewritten into something new today.
        self.page(self.PAGE.replace("no numbers from memory.", "No numbers from memory!")
                  .replace("answer direct questions first.", "answer direct questions first; the welcome party "
                           "waits, a late real reply beats a quick form letter."))
        got = {x.id: x.first_seen for x in evolve.read_learnings()}
        self.assertEqual(got["m60"], T0 - 5 * DAY)
        self.assertIsNone(got["m61"])


class Status(ArchiveCase):
    def test_status_says_why_each_learning_is_not_open(self):
        self.page("# MEMORY.md\n\n## what i have decided\n\n- [m62] some visits can just be visits.\n"
                  "- [m61] answer direct questions first.\n- [m60] no numbers from memory.\n"
                  "- [m59] old reminders get noted, not redone.\n")
        self.entry("g1c1", "opened", "m62", "some visits can just be visits.", pr_number=7)
        self.entry("g1c2", "skipped", "m61", "answer direct questions first.",
                   skip={"why": "not-worth", "note": "a feeling"})
        for n in (1, 2, 3):
            self.entry(f"g{n}c3", "no-proposal", "m60", "no numbers from memory.")
        self.entry("g1c4", "deferred", "m59", "old reminders get noted, not redone.", deferred_for="infra")
        learnings = evolve.status()["learnings"]
        why = {x["id"]: x for x in learnings["not_open"]}
        self.assertEqual(why["m62"], {"id": "m62", "state": "settled", "by": "g1c1", "verdict": "opened", "pr": 7})
        self.assertEqual(why["m61"], {"id": "m61", "state": "settled", "by": "g1c2", "verdict": "skipped",
                                      "why": "not-worth", "until": at(T0 + 7 * DAY)})
        self.assertEqual(why["m60"], {"id": "m60", "state": "gave-up", "tries": 3})
        (m59,) = learnings["open"]
        self.assertEqual((m59["id"], m59["infra"]), ("m59", 1))


class ReopenWithMemory(LoopCase):
    def test_a_reopened_learning_is_told_what_was_already_done_for_it(self):
        self.one_learning("before retrying anything, check whether it already went through.")
        (evolve.evolve_dir() / "1").mkdir(parents=True)  # generation 1 ran and finished
        (evolve.evolve_dir() / "1" / "result.json").write_text("{}")
        evolve.record({"id": "g1c1", "gen": 1, "cand": 1, "verdict": "opened", "at": at(T0),
                       "learning": ref("m36", "before retrying anything, check whether it already went through."),
                       "title": "check before a retry", "pr_url": "https://github.com/teenydroid/brain/pull/9",
                       "merged_at": at(T0)})
        self.one_learning("before retrying anything, check whether it already went through; when a retry "
                          "costs money, wait a whole beat and read the ledger first.")
        proposer = Recording(research=[{"verdict": "not-worth", "why": "done"}])
        self.run_with(proposer)
        (request,) = proposer.research_requests
        (row,) = request["tried"]
        self.assertEqual({k: row.get(k) for k in ("id", "verdict", "title", "pr_url", "same_text", "settled", "merged")},
                         {"id": "g1c1", "verdict": "opened", "title": "check before a retry",
                          "pr_url": "https://github.com/teenydroid/brain/pull/9", "same_text": False,
                          "settled": True, "merged": True})
        self.assertEqual(row["text"], "before retrying anything, check whether it already went through.")
        message = evolve.render_research_request({**request, "files": {}})
        self.assertIn("only what the learning says now", message)

    def test_the_settling_candidate_is_kept_among_many_later_tries(self):
        text = "before retrying anything, check whether it already went through."
        evolve.record({"id": "g1c1", "gen": 1, "cand": 1, "verdict": "opened", "learning": ref("m36", text),
                       "title": "the first change"})
        for n in range(2, 2 + evolve.MAX_TRIED + 2):
            evolve.record({"id": f"g{n}c1", "gen": n, "cand": 1, "verdict": "crashed", "learning": ref("m36", text)})
        rows = evolve.tried_before(self.entries(), ref("m36", text))
        self.assertEqual(len(rows), evolve.MAX_TRIED)
        self.assertEqual(rows[0]["id"], "g1c1")


class WaitingAcrossARewording(EvolveCase):
    def test_a_waiting_candidate_survives_a_rewording_and_goes_stale_on_a_material_change(self):
        evolve.record({"id": "g1c1", "gen": 1, "cand": 1, "verdict": "waiting",
                       "learning": ref("m62", "some visits can just be visits.")})
        got = evolve.waiting(self.entries(), [learning("m62", "Some visits can just be visits!")])
        self.assertEqual([e["id"] for e in got], ["g1c1"])
        got = evolve.waiting(self.entries(), [learning("m62", "some visits are owed: answer the promise first, "
                                                              "then rest.")])
        self.assertEqual(got, [])
        self.assertEqual(self.entries()["g1c1"]["verdict"], "stale")


if __name__ == "__main__":
    unittest.main()
