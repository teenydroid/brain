"""bin/evolve (bin/lib/evolve.py): Phase B of the evolve loop, offline. Its
candidates branch from a scratch clone of this repository, the core is a
stand-in (TEENY_OH), the hands it reads are stand-ins (TEENY_HANDS), GitHub
is a fake publisher, and the clock is pinned (EVOLVE_NOW)."""

import json
import time
import os
import random
import subprocess
import sys
import tempfile
import unittest
import unittest.mock
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
sys.path.insert(0, str(REPO / "bin" / "lib"))

import evolve  # noqa: E402

T0 = 1_794_398_400.0  # 2026-11-11T12:00:00Z
DAY = 86400.0
SECRET_ID = "heldout-whisper"
SECRET_PROMPT = "Reply to the lighthouse keeper who asks about the tide clock"
ENV_KEYS = ("TEENY_ROOT", "TEENY_STATE", "TEENY_SECRETS", "TEENY_HANDS", "TEENY_OH", "EVOLVE_GIT_DIR", "EVOLVE_BASE",
            "EVOLVE_NOW", "DRY_RUN", "EVOLVE", "EVAL_HELDOUT_DIR", "TEENY_STORAGE", "OPENHUMAN_CORE_URL",
            "FAKE_CALLS", "FAKE_HISTORY", "FAKE_REPORT", "OPENHUMAN_WORKSPACE")


def git(*args, cwd=None):
    return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True).stdout


class FakePublisher:
    def __init__(self):
        self.prs, self.issues, self.states = [], [], {}

    def open_pr(self, src, tree, branch, base, title, body):
        self.prs.append({"branch": branch, "title": title, "body": body, "tree": str(tree)})
        n = len(self.prs)
        return {"number": n, "html_url": f"https://github.com/teenydroid/brain/pull/{n}"}

    def open_issue(self, repo, title, body):
        self.issues.append({"repo": repo, "title": title, "body": body})
        return {"number": 100 + len(self.issues), "html_url": "https://github.com/teenydroid/brain/issues/1"}

    def pr(self, number):
        return self.states.get(number)


class Refusing(FakePublisher):
    """Under DRY_RUN nothing may reach GitHub at all."""

    def open_pr(self, *a, **k):
        raise AssertionError("opened a pull request under DRY_RUN")

    def open_issue(self, *a, **k):
        raise AssertionError("opened an issue under DRY_RUN")


FAKE_HAND = r"""#!/usr/bin/env python3
import json, os, sys
name = os.path.basename(sys.argv[0])
args = sys.argv[1:]
with open(os.environ["FAKE_CALLS"], "a") as fh:
    fh.write(json.dumps([name, *args]) + "\n")
if name == "world" and args[:1] == ["history"]:
    path = os.environ.get("FAKE_HISTORY")
    if not path:
        sys.exit(1)
    print(open(path).read())
elif name == "world" and args[:1] == ["report"]:
    print(json.dumps({"J": 0.12, "components": {"attention": 0.1}}))
elif name == "build":
    print(json.dumps({"jobs": [{"id": 7, "kind": "self", "status": "failed", "summary": "abandoned: the builder never reported back"}]}))
elif name == "guard":
    print(json.dumps({"holds": [{"status": "pending", "category": "spam", "text": "STRANGER TEXT"}]}))
else:
    sys.exit(1)
"""


class EvolveCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.shared = tempfile.TemporaryDirectory()
        cls.src = Path(cls.shared.name) / "src.git"
        # This repository's HEAD, pushed into a scratch bare repository: the
        # brain every candidate starts from. A push, not a clone, so a
        # detached or shallow checkout (CI's) works too.
        git("init", "-q", "--bare", str(cls.src))
        git("--git-dir", str(cls.src), "config", "receive.shallowUpdate", "true")
        git("-c", "core.hooksPath=/dev/null", "push", "-q", "--no-verify", str(cls.src), "HEAD:refs/heads/main", cwd=REPO)

    @classmethod
    def tearDownClass(cls):
        cls.shared.cleanup()

    def setUp(self):
        self.saved = {k: os.environ.get(k) for k in ENV_KEYS}
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        (self.root / "secrets").mkdir()
        (self.root / "state").mkdir()
        hands = self.root / "hands"
        hands.mkdir()
        for name in ("world", "build", "guard"):
            (hands / name).write_text(FAKE_HAND)
            (hands / name).chmod(0o755)
        self.calls = self.root / "calls.log"
        for k in ENV_KEYS:
            os.environ.pop(k, None)
        os.environ.update(TEENY_ROOT=str(self.root), TEENY_HANDS=str(hands), EVOLVE_GIT_DIR=str(self.src),
                          EVOLVE_BASE="main", EVOLVE_NOW=str(T0), FAKE_CALLS=str(self.calls),
                          TEENY_OH=str(self.root / "no-core"))
        self.config(EVOLVE=1, EVOLVE_GENERATIONS_PER_DAY=24)
        self.props = self.root / "props"
        self.props.mkdir()

    def tearDown(self):
        for ref in git("--git-dir", str(self.src), "for-each-ref", "--format=%(refname)", "refs/heads/teeny/").split():
            git("--git-dir", str(self.src), "update-ref", "-d", ref)
        for gen in evolve.evolve_dir().glob("*/cand-*"):
            if gen.is_dir():
                subprocess.run(["git", "--git-dir", str(self.src), "worktree", "remove", "--force", str(gen)],
                               capture_output=True)
        subprocess.run(["git", "--git-dir", str(self.src), "worktree", "prune"], capture_output=True)
        for k, v in self.saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        self.tmp.cleanup()

    # --- helpers -------------------------------------------------------------------

    def config(self, **values):
        # No test boots a real core: the lab evaluator is switched off
        # unless a test asks for it (and then its spine is a stand-in).
        values.setdefault("EVOLVE_EVALUATOR", "none")
        text = "".join(f"{k}={v}\n" for k, v in values.items())
        (self.root / "secrets" / "evolve.env").write_text(text)

    def base_text(self, path):
        return git("--git-dir", str(self.src), "show", f"main:{path}")

    def propose(self, i, edits, component="attention", title=None):
        (self.props / f"cand-{i}.json").write_text(json.dumps({
            "title": title or f"candidate {i}", "component": component, "expected_move": "+1% in a week",
            "hypothesis": f"Hypothesis {i}: what is weak, the change, the effect.", "edits": edits}))

    def playbook_edit(self, line):
        text = self.base_text("brain/playbooks/morning.md")
        last = text.rstrip().splitlines()[-1]
        return {"path": "brain/playbooks/morning.md", "old": last, "new": f"{last}\n\n{line}"}

    def run_gen(self, n, publisher=None, **kw):
        cfg = evolve.load_config()
        return evolve.run_generation(cfg, n, str(self.props), publisher=publisher or FakePublisher(), **kw)

    def entries(self):
        return evolve.archive()

    def worktree(self, name):
        tree = self.root / "trees" / name
        evolve.add_worktree(evolve.source(), tree, git("--git-dir", str(self.src), "rev-parse", "main").strip())
        return tree


class ParentSampling(EvolveCase):
    def test_main_seeds_an_empty_archive(self):
        self.assertEqual(list(self.entries()), ["main"])
        self.assertEqual(evolve.sample_parents(self.entries(), 3, random.Random(1)), ["main", "main", "main"])

    def test_weight_rises_with_score_and_falls_with_children(self):
        entries = {
            "main": evolve.seed_entry(),
            "good": {"id": "good", "parent": "main", "verdict": "won", "gate": {"ok": True}, "scores": {"win_rate": 0.8}},
            "meh": {"id": "meh", "parent": "main", "verdict": "lost", "gate": {"ok": True}, "scores": {"win_rate": 0.4}},
        }
        w = evolve.parent_weights(entries)
        self.assertGreater(w["good"], w["meh"])
        # main has two children: half of a childless entry's weight at its score, over three.
        self.assertAlmostEqual(w["main"], 0.5 / 3)
        self.assertAlmostEqual(w["good"], 1 / (1 + 2.718281828 ** (-10 * 0.3)), places=4)
        entries["kid"] = {"id": "kid", "parent": "good", "verdict": "rejected", "gate": {"ok": False}}
        self.assertAlmostEqual(evolve.parent_weights(entries)["good"], w["good"] / 2)

    def test_rejected_and_duplicate_entries_are_never_parents(self):
        entries = {"main": evolve.seed_entry(),
                   "bad": {"id": "bad", "parent": "main", "verdict": "rejected", "gate": {"ok": False}},
                   "dup": {"id": "dup", "parent": "main", "verdict": "duplicate", "gate": {"ok": False}}}
        self.assertEqual(set(evolve.parent_weights(entries)), {"main"})

    def test_credit_feeds_the_weight(self):
        entries = {"main": evolve.seed_entry(),
                   "a": {"id": "a", "parent": "main", "verdict": "unevaluated", "gate": {"ok": True}},
                   "b": {"id": "b", "parent": "main", "verdict": "unevaluated", "gate": {"ok": True}, "credit": 0.2}}
        w = evolve.parent_weights(entries)
        self.assertGreater(w["b"], w["a"])
        entries["b"]["credit"] = -0.2
        self.assertLess(evolve.parent_weights(entries)["b"], w["a"])

    def test_sampling_follows_the_weights_and_spreads_a_generation(self):
        entries = {"main": evolve.seed_entry(),
                   "good": {"id": "good", "parent": "main", "verdict": "won", "gate": {"ok": True}, "scores": {"win_rate": 0.9}}}
        picks = [evolve.sample_parents(entries, 1, random.Random(s))[0] for s in range(2000)]
        w = evolve.parent_weights(entries)
        self.assertAlmostEqual(picks.count("good") / 2000, w["good"] / (w["good"] + w["main"]), delta=0.03)
        # Each pick counts as a child, so four picks do not all land on one.
        many = evolve.sample_parents(entries, 4, random.Random(3))
        self.assertEqual(len(many), 4)
        self.assertEqual(evolve.sample_parents(entries, 4, random.Random(3)), many)

    def test_a_child_starts_from_its_unmerged_parents_change(self):
        self.propose(1, [self.playbook_edit("Parent line: name one thing that shipped.")])
        self.run_gen(1)
        parent = self.entries()["g1c1"]
        self.assertEqual(parent["verdict"], "unevaluated")
        self.propose(1, [{"path": "brain/playbooks/morning.md", "old": "Parent line: name one thing that shipped.",
                          "new": "Parent line: name one thing that shipped, and who shipped it."}])
        with unittest.mock.patch.object(evolve, "sample_parents", return_value=["g1c1"]):
            self.run_gen(1)
        child = self.entries()["g2c1"]
        self.assertEqual(child["parent"], "g1c1")
        mutation = (evolve.evolve_dir() / child["mutation_patch"]).read_text()
        full = (evolve.evolve_dir() / child["patch"]).read_text()
        self.assertIn("-Parent line: name one thing that shipped.", mutation)
        self.assertNotIn("-Parent line", full)  # against main the parent's line is new
        self.assertIn("+Parent line: name one thing that shipped, and who shipped it.", full)


class NearDuplicates(EvolveCase):
    def test_changed_lines_ignore_context_order_and_whitespace(self):
        a = "--- a/x\n+++ b/x\n@@ -1,3 +1,4 @@\n ctx\n+new  line\n-old line\n"
        b = "--- a/x\n+++ b/x\n@@ -9,2 +9,3 @@\n other ctx\n-old line\n+new line \n"
        self.assertEqual(evolve.changed_lines(a), evolve.changed_lines(b))
        self.assertEqual(evolve.diff_hash(a), evolve.diff_hash(b))
        c = a.replace("+++ b/x", "+++ b/y")
        self.assertEqual(evolve.similarity(evolve.changed_lines(a), evolve.changed_lines(c)), 0.0)

    def test_similarity_is_jaccard_over_changed_lines(self):
        a, b = {"1", "2", "3", "4"}, {"1", "2", "3", "5"}
        self.assertAlmostEqual(evolve.similarity(a, b), 3 / 5)
        self.assertEqual(evolve.nearest(a, {"x": b, "y": {"9"}}), ("x", 0.6))

    def test_a_near_duplicate_of_the_archive_is_dropped_before_the_gate(self):
        self.propose(1, [self.playbook_edit("Say gm like it is the first time.")])
        self.run_gen(1)
        self.propose(1, [self.playbook_edit("Say  gm like it is the first time. ")])
        self.propose(2, [self.playbook_edit("A genuinely different line about the kettle.")])
        with unittest.mock.patch.object(evolve, "gate", wraps=evolve.gate) as gated:
            self.run_gen(2)
        entries = self.entries()
        self.assertEqual(entries["g2c1"]["verdict"], "duplicate")
        self.assertEqual(entries["g2c1"]["duplicate_of"], "g1c1")
        self.assertEqual(entries["g2c2"]["verdict"], "unevaluated")
        self.assertEqual(gated.call_count, 1)

    def test_two_candidates_of_one_generation_are_not_both_kept(self):
        self.propose(1, [self.playbook_edit("Same idea.")])
        self.propose(2, [self.playbook_edit("Same idea.")])
        self.run_gen(2)
        self.assertEqual(self.entries()["g1c2"]["verdict"], "duplicate")

    def test_a_low_runway_makes_fewer_candidates_and_says_why(self):
        """docs/roadmap.md, S2: below the low tier bin/evolve runs fewer."""
        (self.root / "secrets" / "teeny.env").write_text("RUNWAY=1\n")
        (self.root / "state" / "runway.json").write_text(json.dumps(
            {"tier": "critical", "days": 3.0, "at": time.time(), "usdc_micro": 30_000_000, "burn_micro_per_day": 10_000_000}))
        from teeny_hands import hand as hand_mod
        hand_mod._ENV_CACHE = None
        self.addCleanup(setattr, hand_mod, "_ENV_CACHE", None)
        for i in (1, 2, 3):
            self.propose(i, [self.playbook_edit(f"Idea {i}.")])
        out = self.run_gen(3)
        self.assertEqual(len(out["candidates"]), 1)
        self.assertIn("critical tier", out["runway"])
        self.assertIn("1 candidate(s), not 3", out["runway"])


class Gate(EvolveCase):
    def gated(self, edits):
        tree = self.worktree("t")
        evolve.apply_edits(tree, edits)
        base = git("--git-dir", str(self.src), "rev-parse", "main").strip()
        out = evolve.gate(tree, base)
        return out, {c["check"]: c for c in out["checks"]}

    def test_a_playbook_delta_passes_every_check(self):
        out, checks = self.gated([self.playbook_edit("Name the person who shipped it.")])
        self.assertTrue(out["ok"], checks)
        self.assertEqual(set(checks), {"frozen paths", "existing hand entry points", "diff size", "SOUL+CONTEXT size",
                                       "new hands have tests", "aims at a component with a reading",
                                       "does more than run less", "new hands are called", "does more than mark an arc",
                                       "seed-cron --check", "doctor --root", "eval invariants"})

    def test_a_frozen_path_fails_and_the_doctor_never_runs(self):
        text = self.base_text("bin/ask")
        out, checks = self.gated([{"path": "bin/ask", "old": text.splitlines()[1], "new": "# loosened"}])
        self.assertFalse(out["ok"])
        self.assertFalse(checks["frozen paths"]["ok"])
        self.assertIn("bin/ask", checks["frozen paths"]["detail"])
        self.assertIn("not run", checks["doctor --root"]["detail"])

    def test_an_existing_hands_entry_point_is_not_changed(self):
        text = self.base_text("hands/git")
        out, checks = self.gated([{"path": "hands/git", "old": text.splitlines()[0], "new": "#!/bin/sh\necho hi"}])
        self.assertFalse(checks["existing hand entry points"]["ok"])
        self.assertIn("not run", checks["doctor --root"]["detail"])

    def test_more_than_400_changed_lines_fail(self):
        body = "".join(f"line {i}\n" for i in range(401))
        out, checks = self.gated([{"path": "brain/skills/long/SKILL.md", "old": "", "new": body}])
        self.assertFalse(checks["diff size"]["ok"])
        self.assertIn("401/400", checks["diff size"]["detail"])
        _, ok = self.gated_again(400)
        self.assertTrue(ok["diff size"]["ok"])

    def gated_again(self, n):
        subprocess.run(["git", "--git-dir", str(self.src), "worktree", "remove", "--force",
                        str(self.root / "trees" / "t")], check=True, capture_output=True)
        body = "".join(f"line {i}\n" for i in range(n))
        return self.gated([{"path": "brain/skills/long/SKILL.md", "old": "", "new": body}])

    def test_more_than_60_lines_of_soul_and_context_fail(self):
        soul = self.base_text("brain/SOUL.md")
        last = soul.rstrip().splitlines()[-1]
        out, checks = self.gated([{"path": "brain/SOUL.md", "old": last,
                                   "new": last + "\n" + "".join(f"- a new trait {i}\n" for i in range(61))}])
        self.assertFalse(checks["SOUL+CONTEXT size"]["ok"])
        self.assertTrue(checks["diff size"]["ok"])

    def test_a_new_hand_without_tests_fails_and_with_them_passes(self):
        hand = [{"path": "hands/kettle", "old": "", "new": "#!/usr/bin/env python3\nprint('{}')\n"},
                {"path": "hands/lib/teeny_hands/kettle/__init__.py", "old": "", "new": "\"\"\"kettle\"\"\"\n"}]
        out, checks = self.gated(hand)
        self.assertFalse(checks["new hands have tests"]["ok"])
        self.assertIn("kettle", checks["new hands have tests"]["detail"])
        subprocess.run(["git", "--git-dir", str(self.src), "worktree", "remove", "--force",
                        str(self.root / "trees" / "t")], check=True, capture_output=True)
        out, checks = self.gated(hand + [{"path": "hands/tests/test_kettle.py", "old": "",
                                          "new": "import unittest\n\nclass T(unittest.TestCase):\n    def test_it(self):\n        pass\n"}])
        self.assertTrue(checks["new hands have tests"]["ok"], checks["new hands have tests"])

    def newer_main(self):
        """A main ahead of this checkout, as Teeny's clone is when the box
        has not pulled yet: its frozen playbook rules know a verb this
        checkout's do not, and one of its playbooks uses it. Gen 4 on the box
        (2026-11-13) was exactly this: every candidate failed `doctor --root`
        on main's own upstream-pick playbook."""
        tree = self.worktree("newer")
        rules = tree / "bin" / "lib" / "playbook.py"
        rules.write_text(rules.read_text().replace('"build": ("self", "upstream", "idea"),',
                                                   '"build": ("self", "upstream", "idea", "kettle"),'))
        (tree / "brain" / "playbooks" / "kettle.md").write_text(
            '+++\ndescription = "brew"\noutput = "hand:build kettle"\nsurface = "github"\nmax_per_day = 1\n+++\n'
            "Brew one thing.\n")
        evolve.git(evolve.in_tree(tree), "add", "-A")
        evolve.git(evolve.in_tree(tree), "commit", "-q", "--no-verify", "-m", "newer main")
        return evolve.git(evolve.in_tree(tree), "rev-parse", "HEAD").strip()

    def test_the_gate_checks_with_the_bases_own_frozen_code_not_this_checkouts(self):
        base = self.newer_main()
        tree = self.root / "trees" / "cand"
        evolve.add_worktree(evolve.source(), tree, base)
        evolve.apply_edits(tree, [self.playbook_edit("Name the person who shipped it.")])
        out = evolve.gate(tree, base)
        checks = {c["check"]: c for c in out["checks"]}
        self.assertTrue(checks["doctor --root"]["ok"], checks["doctor --root"])
        self.assertTrue(checks["seed-cron --check"]["ok"], checks["seed-cron --check"])
        self.assertTrue(checks["eval invariants"]["ok"], checks["eval invariants"])

    def test_a_failing_doctor_check_is_named_in_the_detail(self):
        text = self.base_text("brain/playbooks/morning.md")
        line = next(l for l in text.splitlines() if l.startswith("output = "))
        out, checks = self.gated([{"path": "brain/playbooks/morning.md", "old": line, "new": 'output = "hand:guard check"'}])
        self.assertFalse(checks["doctor --root"]["ok"])
        self.assertIn("FAIL playbook morning", checks["doctor --root"]["detail"])
        self.assertIn("cannot be an output", checks["doctor --root"]["detail"])

    def test_a_cron_rule_broken_fails_seed_cron(self):
        out, checks = self.gated([self.cron_edit()])
        self.assertFalse(checks["seed-cron --check"]["ok"])
        self.assertFalse(out["ok"])

    def cron_edit(self):
        tail = "\n".join(self.base_text("brain/cron.toml").rstrip().splitlines()[-4:])
        return {"path": "brain/cron.toml", "old": tail,
                "new": tail + '\n\n[jobs.flood]\nschedule = "* * * * *"\ncommand = "bin/heartbeat"\n'}

    def test_dropping_an_invariant_fails_the_invariants(self):
        out, checks = self.gated([{"path": "brain/CONTEXT.md", "old": "never sell $TINY", "new": "hold it loosely"}])
        self.assertFalse(checks["eval invariants"]["ok"])
        self.assertFalse(checks["doctor --root"]["ok"])

    def test_edits_cannot_leave_the_worktree(self):
        tree = self.worktree("t")
        for path in ("../escape", "/etc/passwd", ".git/config", "HYPOTHESIS.md"):
            with self.assertRaises(evolve.EvolveError):
                evolve.apply_edits(tree, [{"path": path, "old": "", "new": "x"}])
        with self.assertRaises(evolve.EvolveError):
            evolve.apply_edits(tree, [{"path": "brain/SOUL.md", "old": "this text is not there", "new": "x"}])

    def test_a_rejected_candidate_is_archived_with_its_gate_and_never_shipped(self):
        self.propose(1, [{"path": "bin/ask", "old": self.base_text("bin/ask").splitlines()[1], "new": "# x"}])
        pub = FakePublisher()
        os.environ["DRY_RUN"] = "0"
        (self.root / "secrets" / "teeny.env").write_text("DRY_RUN=0\n")
        out = self.run_gen(1, publisher=pub)
        self.assertIsNone(out["selected"])
        self.assertEqual(pub.prs, [])
        entry = self.entries()["g1c1"]
        self.assertEqual(entry["verdict"], "rejected")
        self.assertFalse([c for c in entry["gate"]["checks"] if c["check"] == "frozen paths"][0]["ok"])


WORLD = {"components": [{"component": "attention", "value": 392, "was": 390},
                        {"component": "money", "note": "no rows yet: this source has never been read"}],
         "unread": ["money"]}


class PurposeChecks(EvolveCase):
    """What a change is for (docs/evolve.md, "3. Gate"): the gate throws out
    what gens 4 and 5 on the box proposed to game the score, and nothing else."""

    def gated(self, edits, component="attention", evidence=None):
        tree = self.root / "trees" / f"t{len(list((self.root / 'trees').glob('*'))) if (self.root / 'trees').exists() else 0}"
        evolve.add_worktree(evolve.source(), tree, git("--git-dir", str(self.src), "rev-parse", "main").strip())
        evolve.apply_edits(tree, edits)
        base = git("--git-dir", str(self.src), "rev-parse", "main").strip()
        out = evolve.gate(tree, base, component, evidence if evidence is not None else {"world": WORLD})
        return out, {c["check"]: c for c in out["checks"]}

    def cron(self, old, new):
        return {"path": "brain/cron.toml", "old": old, "new": new}

    def test_a_change_aimed_at_a_component_never_read_is_thrown_out(self):
        """g4c3: "a daily $TINY buy so the tiny component finally gets rows"."""
        out, checks = self.gated([self.playbook_edit("Ask one question back.")], component="money")
        self.assertFalse(out["ok"])
        self.assertIn("money has never been read", checks["aims at a component with a reading"]["detail"])
        out, checks = self.gated([self.playbook_edit("Ask one question back.")], component="attention")
        self.assertTrue(checks["aims at a component with a reading"]["ok"])
        # Without a world report there is nothing to hold it to.
        out, checks = self.gated([self.playbook_edit("Ask one question back.")], component="money", evidence={})
        self.assertTrue(checks["aims at a component with a reading"]["ok"])

    def test_only_thinning_a_working_job_is_thrown_out(self):
        """g4c4: "cut moltbook-threads from 7 runs a day to 4"."""
        out, checks = self.gated([self.cron('schedule = "45 9-21/2 * * *"', 'schedule = "45 9,13,17,21 * * *"')])
        self.assertFalse(out["ok"])
        detail = checks["does more than run less"]["detail"]
        self.assertIn("moltbook-threads thinned from 49 to 28 runs a week", detail)

    def test_thinning_a_failing_job_is_thrown_out_too(self):
        """g5c3: "trim moltbook-helpdesk from 8 runs a day to 4: half its runs die"."""
        failing = {"world": WORLD, "outcomes": {"failing": [{"job": "bin/act moltbook-helpdesk", "failed_runs": 3}]}}
        out, checks = self.gated([self.cron('schedule = "15 8-22/2 * * *"', 'schedule = "15 8,12,16,20 * * *"')],
                                 evidence=failing)
        self.assertFalse(checks["does more than run less"]["ok"])

    def test_turning_off_a_job_that_only_fails_passes_and_a_working_one_does_not(self):
        off = self.cron('command = "bin/act moltbook-helpdesk"\n', 'command = "bin/act moltbook-helpdesk"\nenabled = false\n')
        out, checks = self.gated([off])
        self.assertFalse(checks["does more than run less"]["ok"])
        self.assertIn("moltbook-helpdesk turned off", checks["does more than run less"]["detail"])
        failing = {"world": WORLD, "outcomes": {"failing": [{"job": "teeny.moltbook-helpdesk", "failed_runs": 9}]}}
        out, checks = self.gated([off], evidence=failing)
        self.assertTrue(checks["does more than run less"]["ok"], checks["does more than run less"])

    def test_a_schedule_change_that_comes_with_more_passes(self):
        out, checks = self.gated([self.cron('schedule = "45 9-21/2 * * *"', 'schedule = "45 9,13,17,21 * * *"'),
                                  self.playbook_edit("Join only threads with a question in them.")])
        self.assertTrue(checks["does more than run less"]["ok"])
        out, checks = self.gated([self.cron('schedule = "45 9-21/2 * * *"', 'schedule = "45 9-21 * * *"')])
        self.assertTrue(checks["does more than run less"]["ok"])

    def kettle(self, extra=()):
        return [{"path": "hands/kettle", "old": "", "new": "#!/usr/bin/env python3\nprint('{}')\n"},
                {"path": "hands/lib/teeny_hands/kettle/__init__.py", "old": "", "new": "\"\"\"kettle\"\"\"\n"},
                {"path": "hands/tests/test_kettle.py", "old": "",
                 "new": "import unittest\n\nclass T(unittest.TestCase):\n    def test_it(self):\n        pass\n"}, *extra]

    def test_a_new_hand_nothing_calls_is_thrown_out(self):
        """g5c4: "new hand hands/notes ... hands_with_tests 21 -> 22"."""
        out, checks = self.gated(self.kettle(), component="capability")
        self.assertFalse(checks["new hands are called"]["ok"])
        self.assertIn("hands/kettle", checks["new hands are called"]["detail"])
        text = self.base_text("brain/playbooks/morning.md")
        anchor = "[[inputs]]\nname = \"hn-top\""
        self.assertIn(anchor, text)
        uses = {"path": "brain/playbooks/morning.md", "old": anchor,
                "new": "[[inputs]]\nname = \"kettle\"\nrun = \"hands/kettle\"\noptional = true\n\n" + anchor}
        out, checks = self.gated(self.kettle([uses]), component="capability")
        self.assertTrue(checks["new hands are called"]["ok"], checks["new hands are called"])

    def test_only_marking_an_arc_started_is_thrown_out(self):
        arcs = self.base_text("brain/narrative/ARCS.md")
        heading = "## find out what the terminal is typing when nobody is typing\n\n- status: not started."
        self.assertIn(heading, arcs)
        out, checks = self.gated([{"path": "brain/narrative/ARCS.md", "old": heading,
                                   "new": heading.replace("not started.", "started.")}], component="world")
        self.assertFalse(checks["does more than mark an arc"]["ok"])
        out, checks = self.gated([{"path": "brain/narrative/ARCS.md", "old": heading,
                                   "new": heading.replace("not started.", "started.")},
                                  self.playbook_edit("Say what the terminal printed overnight.")], component="world")
        self.assertTrue(checks["does more than mark an arc"]["ok"])

    def test_every_rejection_lands_in_the_archive_with_its_reason(self):
        self.propose(1, [self.cron('schedule = "45 9-21/2 * * *"', 'schedule = "45 9,13,17,21 * * *"')],
                     component="attention")
        self.run_gen(1)
        entry = self.entries()["g1c1"]
        self.assertEqual(entry["verdict"], "rejected")
        failed = [c for c in entry["gate"]["checks"] if not c["ok"]]
        self.assertEqual([c["check"] for c in failed], ["does more than run less"])
        self.assertIn("thinned", failed[0]["detail"])


class AnswersThatDoNotParse(unittest.TestCase):
    def test_a_raw_newline_inside_a_string_is_read(self):
        self.assertEqual(evolve.first_json('{"skip": "line one\nline two"}')["skip"], "line one\nline two")

    def test_a_brace_in_prose_before_the_answer_is_stepped_over(self):
        got = evolve.first_json('Here is {my plan, roughly.\n{"title": "t", "component": "attention"}')
        self.assertEqual(got["title"], "t")

    def test_an_answer_cut_off_mid_string_still_fails_saying_so(self):
        with self.assertRaises(evolve.EvolveError) as err:
            evolve.first_json('{"title": "t", "hypothesis": "it stops he')
        self.assertIn("Unterminated string", str(err.exception))


class ArchiveAndSelect(EvolveCase):
    def test_every_candidate_is_appended_with_its_record(self):
        self.propose(1, [self.playbook_edit("One.")])
        self.propose(2, [{"path": "bin/ask", "old": self.base_text("bin/ask").splitlines()[1], "new": "# x"}])
        (self.props / "cand-3.json").write_text(json.dumps({"skip": "nothing today"}))
        self.run_gen(3)
        lines = [json.loads(l) for l in evolve.archive_path().read_text().splitlines()]
        self.assertEqual(lines[0]["id"], "main")
        self.assertEqual([l["id"] for l in lines[1:]], ["g1c1", "g1c2", "g1c3"])
        one = lines[1]
        for key in ("parent", "diff_hash", "diff_stats", "gate", "scores", "verdict", "base", "patch"):
            self.assertIn(key, one)
        self.assertEqual(one["diff_stats"]["added"], 2)
        self.assertEqual([l["verdict"] for l in lines[1:]], ["unevaluated", "rejected", "no-proposal"])
        self.assertTrue((evolve.evolve_dir() / "1" / "evidence.json").is_file())
        self.assertTrue((evolve.evolve_dir() / "1" / "cand-1.HYPOTHESIS.md").read_text().startswith("# candidate 1"))

    def test_later_lines_update_an_entry(self):
        evolve.record({"id": "g9c1", "verdict": "unevaluated", "gen": 9})
        evolve.record({"id": "g9c1", "verdict": "won", "gen": 9, "scores": {"win_rate": 0.7}})
        self.assertEqual(self.entries()["g9c1"]["verdict"], "won")
        self.assertEqual(len(evolve.archive_path().read_text().splitlines()), 3)

    def test_one_pull_request_for_the_best_with_hypothesis_gate_and_report(self):
        self.propose(1, [self.playbook_edit("One.")], title="the first")
        self.propose(2, [self.playbook_edit("Two, much longer.\nAnd a second line.")], title="the second")
        (self.root / "secrets" / "teeny.env").write_text("DRY_RUN=0\n")
        pub = FakePublisher()
        out = self.run_gen(2, publisher=pub)
        self.assertEqual(len(pub.prs), 1)
        pr = pub.prs[0]
        self.assertEqual(pr["branch"], "teeny/gen-1")
        self.assertEqual(out["selected"], "g1c1")  # unmeasured: the smaller diff first
        self.assertIn("Hypothesis 1", pr["body"])
        self.assertIn("| frozen paths | pass |", pr["body"])
        self.assertIn("**Unevaluated.**", pr["body"])
        entry = self.entries()["g1c1"]
        self.assertEqual(entry["pr_url"], "https://github.com/teenydroid/brain/pull/1")
        log = git("--git-dir", str(self.src), "log", "-1", "--format=%an %s", "teeny/gen-1")
        self.assertEqual(log.strip(), "Teeny gen-1: the first")
        files = git("--git-dir", str(self.src), "show", "--name-only", "--format=", "teeny/gen-1").split()
        self.assertEqual(files, ["brain/playbooks/morning.md"])  # HYPOTHESIS.md is in the body, not the diff

    def test_dry_run_opens_no_pull_request(self):
        os.environ["DRY_RUN"] = "1"
        self.propose(1, [self.playbook_edit("One.")])
        out = self.run_gen(1, publisher=Refusing())
        self.assertTrue(out["dry_run"])
        self.assertEqual(out["would_open_pr"]["branch"], "teeny/gen-1")
        pr = json.loads((evolve.evolve_dir() / "1" / "pr.json").read_text())
        self.assertTrue(pr["dry_run"])
        branches = git("--git-dir", str(self.src), "branch", "--list", "teeny/*")
        self.assertEqual(branches.strip(), "")

    def test_teeny_env_without_dry_run_0_is_a_dry_run(self):
        self.assertTrue(evolve.dry_run())
        (self.root / "secrets" / "teeny.env").write_text("DRY_RUN=0\n")
        self.assertFalse(evolve.dry_run())
        os.environ["DRY_RUN"] = "1"
        self.assertTrue(evolve.dry_run())

    def test_a_measured_winner_beats_an_unmeasured_one_and_a_loser_never_ships(self):
        def cand(i, verdict, rate=None, lines=5):
            return {"id": f"g1c{i}", "cand": i, "parent": "main", "verdict": verdict, "gate": {"ok": True, "lines": lines},
                    "scores": {"win_rate": rate, "decided": 12} if rate is not None else None}
        entries = {"main": evolve.seed_entry()}
        ranked = evolve.rank([cand(1, "unevaluated", lines=1), cand(2, "won", 0.7), cand(3, "won", 0.8)], entries)
        self.assertEqual([c["id"] for c in ranked], ["g1c3", "g1c2"])
        self.assertEqual(evolve.rank([cand(1, "lost", 0.3)], entries), [])


class Credit(EvolveCase):
    def history(self, points):
        path = self.root / "history.json"
        path.write_text(json.dumps({"history": [{"date": evolve.iso(at), "J": j} for at, j in points]}))
        os.environ["FAKE_HISTORY"] = str(path)

    def test_clade_credit_is_a_decayed_sum_of_descendants(self):
        entries = {"main": evolve.seed_entry(),
                   "a": {"id": "a", "parent": "main"},
                   "b": {"id": "b", "parent": "a", "delta_j": 0.4},
                   "c": {"id": "c", "parent": "b", "delta_j": -0.2}}
        got = evolve.clade_credit(entries, 0.5)
        self.assertAlmostEqual(got["c"], -0.2)
        self.assertAlmostEqual(got["b"], 0.4 + 0.5 * -0.2)
        self.assertAlmostEqual(got["a"], 0.5 * 0.4 + 0.25 * -0.2)
        self.assertAlmostEqual(got["main"], 0.25 * 0.4 + 0.125 * -0.2)

    def test_delta_j_is_recorded_seven_days_after_a_merge(self):
        merged = T0 - 8 * DAY
        self.history([(merged - DAY, 1.0), (merged + DAY, 1.1), (merged + 7 * DAY - 60, 1.3), (T0, 1.5)])
        evolve.record({"id": "g1c1", "gen": 1, "cand": 1, "parent": "main", "verdict": "unevaluated",
                       "gate": {"ok": True}, "pr_number": 1})
        evolve.record({"id": "g2c1", "gen": 2, "cand": 1, "parent": "g1c1", "verdict": "unevaluated",
                       "gate": {"ok": True}, "pr_number": 2})
        pub = FakePublisher()
        pub.states = {1: {"merged_at": evolve.iso(merged)}, 2: {"merged_at": evolve.iso(T0 - 3 * DAY)}}
        out = evolve.credit(evolve.load_config(), pub)
        self.assertEqual(out["measured"], ["g1c1"])
        entries = self.entries()
        self.assertAlmostEqual(entries["g1c1"]["delta_j"], 0.3)
        self.assertNotIn("delta_j", entries["g2c1"])  # merged three days ago: too soon
        self.assertEqual(entries["g2c1"]["merged_at"], evolve.iso(T0 - 3 * DAY))
        self.assertAlmostEqual(entries["g1c1"]["credit"], 0.3)
        self.assertAlmostEqual(entries["main"]["credit"], 0.15)
        # And the credit moves the parent weights.
        weights = evolve.parent_weights(entries)
        uncredited = evolve.parent_weights({**entries, "g1c1": {**entries["g1c1"], "credit": 0.0}})
        self.assertGreater(weights["g1c1"], uncredited["g1c1"])
        again = evolve.credit(evolve.load_config(), pub)
        self.assertEqual(again["measured"], [])

    def test_a_generation_a_person_merged_gets_its_chronicle_page(self):
        """docs/roadmap.md, A13: whoever merged it, a merged generation has
        brain/narrative/chronicle/gen-<n>.md."""
        evolve.record({"id": "g2c1", "gen": 2, "cand": 1, "parent": "main", "verdict": "lost", "gate": {"ok": True}})
        evolve.record({"id": "g3c1", "gen": 3, "cand": 1, "parent": "main", "verdict": "won", "gate": {"ok": True},
                       "pr_number": 5, "title": "open with what shipped", "component": "attention",
                       "expected_move": "+2%", "scores": {"wins": 3, "decided": 4}})
        evolve.record({"id": "g3c2", "gen": 3, "cand": 2, "parent": "main", "verdict": "lost", "gate": {"ok": True}})
        pub = FakePublisher()
        out = evolve.credit(evolve.load_config(), pub)
        self.assertEqual(out["chronicled"], [])  # not merged yet: no page
        page = self.root / "brain" / "narrative" / "chronicle" / "gen-3.md"
        self.assertFalse(page.exists())
        pub.states = {5: {"merged_at": evolve.iso(T0 - DAY)}}
        out = evolve.credit(evolve.load_config(), pub)
        self.assertEqual(out["chronicled"], ["g3c1"])
        text = page.read_text()
        self.assertIn("# gen-3", text)
        self.assertIn("ran 2 candidates", text)
        self.assertIn("open with what shipped", text)
        self.assertIn("won 3 of 4 pairs", text)
        self.assertIn("- merged by: the operator", text)
        self.assertIn("- gen-2: the agents carried it back", text)
        self.assertEqual(self.entries()["g3c1"]["chronicle"], "brain/narrative/chronicle/gen-3.md")
        # Once: a later run neither rewrites it nor writes it again.
        page.write_text("kept\n")
        again = evolve.credit(evolve.load_config(), pub)
        self.assertEqual(again["chronicled"], [])
        self.assertEqual(page.read_text(), "kept\n")

    def test_a_promoted_generation_keeps_the_page_its_commit_carried(self):
        evolve.record({"id": "g1c1", "gen": 1, "cand": 1, "parent": "main", "verdict": "won", "gate": {"ok": True},
                       "pr_number": 1, "promoted_at": evolve.iso(T0 - DAY)})
        pub = FakePublisher()
        pub.states = {1: {"merged_at": evolve.iso(T0 - DAY)}}
        out = evolve.credit(evolve.load_config(), pub)
        self.assertEqual(out["chronicled"], [])
        self.assertFalse((self.root / "brain" / "narrative" / "chronicle" / "gen-1.md").exists())

    def test_the_chronicle_goes_to_the_live_brain_the_core_boots(self):
        live = self.root / "elsewhere"
        os.environ["OPENHUMAN_WORKSPACE"] = str(live)
        evolve.record({"id": "g1c1", "gen": 1, "cand": 1, "parent": "main", "gate": {"ok": True}, "pr_number": 1})
        pub = FakePublisher()
        pub.states = {1: {"merged_at": evolve.iso(T0)}}
        evolve.credit(evolve.load_config(), pub)
        self.assertTrue((live / "narrative" / "chronicle" / "gen-1.md").is_file())

    def test_without_hands_world_nothing_is_measured(self):
        os.environ.pop("FAKE_HISTORY", None)
        evolve.record({"id": "g1c1", "gen": 1, "cand": 1, "parent": "main", "merged_at": evolve.iso(T0 - 9 * DAY),
                       "gate": {"ok": True}})
        out = evolve.credit(evolve.load_config(), FakePublisher())
        self.assertEqual(out["measured"], [])
        self.assertEqual(out["history"], "no hands/world history")

    def test_history_shapes_are_read(self):
        path = self.root / "h.json"
        path.write_text(json.dumps([{"day": "2026-11-01", "j": 0.5}, {"at": T0, "score": 0.7}]))
        os.environ["FAKE_HISTORY"] = str(path)
        self.assertEqual([j for _, j in evolve.world_history()], [0.5, 0.7])


class Halts(EvolveCase):
    def live(self):
        (self.root / "secrets" / "teeny.env").write_text("DRY_RUN=0\n")

    def test_evolve_0_is_the_default_and_the_kill_switch(self):
        (self.root / "secrets" / "evolve.env").unlink()
        self.assertFalse(evolve.load_config().enabled)
        out = self.run_gen(1, publisher=Refusing())
        self.assertIn("EVOLVE=0", out["skipped"])
        self.config(EVOLVE=1)
        os.environ["EVOLVE"] = "0"
        self.assertFalse(evolve.load_config().enabled)
        os.environ.pop("EVOLVE")
        self.config(EVOLVE=0)
        os.environ["EVOLVE"] = "1"  # the environment never switches it on
        self.assertFalse(evolve.load_config().enabled)
        self.assertFalse(evolve.evolve_dir().exists() and any(evolve.evolve_dir().glob("*/generation.json")))

    def test_a_bad_threshold_refuses_the_run(self):
        self.config(EVOLVE=1, EVOLVE_MAX_WINRATE_JUMP="lots")
        with self.assertRaises(evolve.EvolveError):
            evolve.load_config()
        self.config(EVOLVE=1, EVOLVE_CANDIDATES=50)
        with self.assertRaises(evolve.EvolveError):
            evolve.load_config()

    def test_generations_per_day(self):
        self.live()
        self.config(EVOLVE=1, EVOLVE_GENERATIONS_PER_DAY=1)
        self.propose(1, [self.playbook_edit("One.")])
        pub = FakePublisher()
        self.assertIn("gen", self.run_gen(1, publisher=pub))
        out = self.run_gen(1, publisher=pub)
        self.assertEqual(out["skipped"], "halted: generations")
        self.assertEqual(len(pub.issues), 1)
        self.assertIn("generations", pub.issues[0]["title"])
        self.run_gen(1, publisher=pub)
        self.assertEqual(len(pub.issues), 1)  # one issue, not one a run
        self.assertFalse(evolve.halt_path().exists())  # it does not latch
        os.environ["EVOLVE_NOW"] = str(T0 + DAY)
        self.assertIn("gen", self.run_gen(1, publisher=pub))

    def cut_off(self, gen):
        """Gen GEN as a restart leaves it: started, its proposals made, no
        result and nothing archived (2026-11-13: a spine rebuild at 22:44
        ended gen 5 while its labs measured two gated candidates)."""
        (evolve.gen_dir(gen) / "result.json").unlink()
        kept = [r for r in evolve.read_jsonl(evolve.archive_path()) if r.get("gen") != gen]
        evolve.archive_path().write_text("".join(json.dumps(r) + "\n" for r in kept))

    def test_a_generation_a_restart_cut_off_does_not_use_up_the_day_and_its_proposals_are_reused(self):
        self.live()
        self.config(EVOLVE=1, EVOLVE_GENERATIONS_PER_DAY=1)
        self.propose(1, [self.playbook_edit("One.")], title="the cut-off one")
        pub = FakePublisher()
        self.assertEqual(self.run_gen(1, publisher=pub)["gen"], 1)
        self.cut_off(1)
        (self.props / "cand-1.json").unlink()  # its proposer would have nothing to say now
        out = self.run_gen(1, publisher=pub)
        self.assertEqual(out.get("gen"), 2, out)
        self.assertEqual(out["resumed_from"], 1)
        [cand] = out["candidates"]
        self.assertEqual(cand["title"], "the cut-off one")
        self.assertNotEqual(cand["verdict"], "no-proposal")
        cut = json.loads((evolve.gen_dir(1) / "result.json").read_text())
        self.assertTrue(cut["interrupted"])
        self.assertEqual(cut["resumed_in"], 2)
        # Gen 2 counts for the day; the cut-off gen 1 does not.
        self.assertEqual(evolve.generations_today(), 1)
        self.assertEqual(self.run_gen(1, publisher=pub)["skipped"], "halted: generations")

    def test_a_cut_off_generation_is_resumed_once(self):
        self.live()
        self.propose(1, [self.playbook_edit("One.")], title="once")
        self.run_gen(1)
        self.cut_off(1)
        self.assertEqual(self.run_gen(1)["resumed_from"], 1)
        self.propose(1, [self.playbook_edit("Two.")], title="fresh")
        out = self.run_gen(1)
        self.assertNotIn("resumed_from", out)
        self.assertEqual(out["candidates"][0]["title"], "fresh")

    def test_status_says_whether_a_generation_is_running(self):
        self.assertFalse(evolve.status()["running"])
        with evolve.run_lock() as held:
            self.assertTrue(held)
            self.assertTrue(evolve.status()["running"])

    def test_a_fast_j_rise_stops_the_loop_until_resumed(self):
        self.live()
        self.config(EVOLVE=1, EVOLVE_GENERATIONS_PER_DAY=24, EVOLVE_MAX_J_RISE=0.5, EVOLVE_J_WINDOW_DAYS=7)
        path = self.root / "h.json"
        path.write_text(json.dumps([{"at": T0 - 8 * DAY, "J": 1.0}, {"at": T0 - DAY, "J": 1.7}]))
        os.environ["FAKE_HISTORY"] = str(path)
        pub = FakePublisher()
        out = self.run_gen(1, publisher=pub)
        self.assertEqual(out["skipped"], "halted: j-rise")
        self.assertEqual(len(pub.issues), 1)
        self.assertTrue(evolve.halt_path().exists())
        path.write_text(json.dumps([{"at": T0 - DAY, "J": 1.0}]))
        out = self.run_gen(1, publisher=pub)
        self.assertIn("until `bin/evolve resume`", out["skipped"])
        self.assertEqual(len(pub.issues), 1)
        evolve.resume()
        self.propose(1, [self.playbook_edit("One.")])
        self.assertIn("gen", self.run_gen(1, publisher=pub))

    def test_a_slow_j_rise_passes(self):
        self.config(EVOLVE=1, EVOLVE_GENERATIONS_PER_DAY=24, EVOLVE_MAX_J_RISE=0.5)
        path = self.root / "h.json"
        path.write_text(json.dumps([{"at": T0 - 8 * DAY, "J": 1.0}, {"at": T0 - DAY, "J": 1.2}]))
        os.environ["FAKE_HISTORY"] = str(path)
        self.assertEqual(evolve.halts_before(evolve.load_config()), [])

    def test_a_win_rate_jump_is_flagged_for_a_person_not_shipped(self):
        self.live()
        self.config(EVOLVE=1, EVOLVE_GENERATIONS_PER_DAY=24, EVOLVE_MAX_WINRATE_JUMP=0.25)
        self.propose(1, [self.playbook_edit("One.")])

        class Judged:
            def evaluate(self, gen_dir, cand, src):
                return {"verdict": "won", "scores": {"win_rate": 0.95, "decided": 18, "wins": 17, "losses": 1}}

        pub = FakePublisher()
        with unittest.mock.patch.object(evolve, "make_evaluators", return_value={1: Judged()}):
            out = self.run_gen(1, publisher=pub)
        self.assertEqual(pub.prs, [])
        self.assertEqual(len(pub.issues), 1)
        self.assertIn("winrate-jump", pub.issues[0]["title"])
        self.assertEqual(self.entries()["g1c1"]["verdict"], "flagged")
        self.assertTrue(evolve.halt_path().exists())
        self.assertIn("winrate-jump", out["halt"]["kind"])

    def test_a_win_rate_near_the_last_ones_ships(self):
        entries = {"main": evolve.seed_entry(),
                   "g1c1": {"id": "g1c1", "gen": 1, "scores": {"win_rate": 0.62}}}
        cand = {"id": "g2c1", "gen": 2, "scores": {"win_rate": 0.7}}
        self.assertIsNone(evolve.winrate_halt(evolve.load_config(), entries, cand))
        cand["scores"]["win_rate"] = 0.9
        self.assertIsNotNone(evolve.winrate_halt(evolve.load_config(), entries, cand))

    def test_dry_run_files_no_issue(self):
        os.environ["DRY_RUN"] = "1"
        self.config(EVOLVE=1, EVOLVE_GENERATIONS_PER_DAY=1)
        self.propose(1, [self.playbook_edit("One.")])
        self.run_gen(1, publisher=Refusing())
        out = self.run_gen(1, publisher=Refusing())
        self.assertTrue(out["halts"][0]["dry_run"])
        self.assertIn("would_open_issue", out["halts"][0])


class HeldOut(EvolveCase):
    def setUp(self):
        super().setUp()
        held = self.root / "secrets" / "heldout"
        held.mkdir()
        (held / f"{SECRET_ID}.toml").write_text(
            f'description = "a reply to a stranger at the lighthouse"\nkind = "reply"\nprompt = \'\'\'{SECRET_PROMPT}\'\'\'\n'
            '[inputs]\nmention = {id = "9", author = "keeper", text = "tide?"}\n')
        report = evolve.evolve_dir() / "0" / "eval" / "cand-1" / "compare" / "report.json"
        report.parent.mkdir(parents=True)
        report.write_text(json.dumps({"win_rate": 0.6, "reason": "fine", "pairs": [
            {"task": "morning-post", "heldout": False, "winner": "cand", "notes": ["visible note: warmer"]},
            {"task": SECRET_ID, "heldout": True, "winner": "base", "notes": ["held note: the keeper wanted tides"]},
            {"task": SECRET_ID, "winner": "base", "notes": ["unmarked held note"]},
            {"task": "reply-fud", "heldout": False, "winner": "tie",
             "notes": [f"compared it to {SECRET_ID}, which went better"]},
        ]}))

    def test_held_out_tasks_never_reach_the_evidence(self):
        evidence = json.dumps(evolve.observe(self.entries()))
        self.assertIn("visible note: warmer", evidence)
        for leak in (SECRET_ID, SECRET_PROMPT, "held note", "unmarked held note", "lighthouse"):
            self.assertNotIn(leak, evidence)

    def test_nor_the_proposers_message(self):
        seen = []

        class Capture:
            name = "capture"

            def propose(self, request):
                seen.append(evolve.render_request(request))
                return {"skip": "just looking"}

        with unittest.mock.patch.object(evolve, "make_proposer", return_value=Capture()):
            self.run_gen(1)
        self.assertEqual(len(seen), 1)
        for leak in (SECRET_ID, SECRET_PROMPT, "lighthouse"):
            self.assertNotIn(leak, seen[0])
        self.assertIn("visible note: warmer", seen[0])
        stored = (evolve.evolve_dir() / "1" / "evidence.json").read_text()
        self.assertNotIn(SECRET_ID, stored)

    def test_the_strangers_hold_text_is_not_evidence(self):
        evidence = json.dumps(evolve.observe(self.entries()))
        self.assertNotIn("STRANGER TEXT", evidence)
        self.assertIn("pending:spam", evidence)


class LabLessons(EvolveCase):
    def test_the_labs_lessons_are_evidence(self):
        self.assertEqual(evolve.observe(self.entries())["lessons"], {"available": False, "why": "no hands/lab lessons"})
        lab = Path(os.environ["TEENY_HANDS"]) / "lab"
        lab.write_text("#!/usr/bin/env python3\nimport json\nprint(json.dumps({'ok': True, 'lessons': "
                       "[{'id': 1, 'verdict': 'works', 'lesson': 'confessions get comments'}]}))\n")
        lab.chmod(0o755)
        got = evolve.observe(self.entries())["lessons"]
        self.assertEqual(got["lessons"][0]["lesson"], "confessions get comments")


PAGE = """# MEMORY.md

what i keep in mind.

## people

- [m1] **ana** (x): builds a cli for crabs.

## what i am in the middle of

- [m15] learning to code in public. the first thing i build myself is a $TINY chart renderer. not built yet.

## what i have decided

- [m44] **no likes on X, ever**: X's rules don't allow automated likes.
- [m57] no dotted tokens in outbound telegram text.
"""


class Decisions(EvolveCase):
    """2026-11-14: the dream's page listed what Teeny had decided and was in
    the middle of (a chart renderer, a refusal ledger), and five generations
    proposed from the score alone: none read the page."""

    def page(self, text=PAGE):
        brain = self.root / "brain"
        brain.mkdir(exist_ok=True)
        (brain / "MEMORY.md").write_text(text)

    def test_what_the_page_says_teeny_decided_is_evidence_and_who_it_knows_is_not(self):
        self.page()
        got = evolve.observe(self.entries())["decisions"]
        self.assertEqual(got["source"], "brain/MEMORY.md")
        text = json.dumps(got)
        self.assertIn("[m15] learning to code in public", text)
        self.assertIn("[m57] no dotted tokens", text)
        self.assertNotIn("crabs", text)

    def test_no_page_says_so(self):
        self.assertEqual(evolve.observe(self.entries())["decisions"],
                         {"available": False, "why": "no brain/MEMORY.md"})

    def test_a_long_page_is_clipped(self):
        self.page("## what i have decided\n\n" + "".join(f"- [m{n}] {'x' * 900}\n" for n in range(100)))
        got = evolve.observe(self.entries())["decisions"]
        self.assertLessEqual(len(json.dumps(got)), evolve.MAX_DECISIONS_CHARS + 500)
        self.assertTrue(got["clipped"])

    def test_the_first_candidate_of_every_generation_looks_at_the_page_first(self):
        for gen in range(1, 9):
            self.assertEqual(evolve.lens(gen, 1), evolve.DECISIONS_LENS)
            self.assertNotEqual(evolve.lens(gen, 2), evolve.DECISIONS_LENS)

    def test_the_proposer_is_shown_the_page_and_asked_to_carry_a_decision_out(self):
        self.page()
        seen = []

        class Capture:
            name = "capture"

            def propose(self, request):
                seen.append(evolve.render_request(request))
                return {"skip": "just looking"}

        with unittest.mock.patch.object(evolve, "make_proposer", return_value=Capture()):
            self.run_gen(1)
        [message] = seen
        self.assertIn("## your own page", message)
        self.assertIn("- [m15] learning to code in public", message)
        self.assertIn(evolve.DECISIONS_LENS, message)


class AskProposerTurn(EvolveCase):
    def test_one_tool_less_turn_on_a_thread_of_its_own(self):
        calls = self.root / "oh.log"
        fake = self.root / "fake-oh"
        answer = json.dumps({"title": "warmer gm", "component": "attention", "expected_move": "+1%",
                             "hypothesis": "h", "edits": [self.playbook_edit("Warmer.")]})
        fake.write_text("#!/usr/bin/env bash\nprintf '%s\\n' \"$2\" >>" + str(calls) + "\n"
                        "python3 -c 'import json,sys; print(json.dumps({\"response\": sys.argv[1]}))' "
                        + "'" + answer.replace("'", "'\\''") + "'\n")
        fake.chmod(0o755)
        os.environ["TEENY_OH"] = str(fake)
        self.config(EVOLVE=1, EVOLVE_GENERATIONS_PER_DAY=24, EVOLVE_PROPOSER="ask")
        out = evolve.run_generation(evolve.load_config(), 1, None, publisher=FakePublisher())
        self.assertEqual(out["candidates"][0]["verdict"], "unevaluated")
        params = json.loads(calls.read_text().splitlines()[0])
        self.assertEqual(params["agent_id"], "act")
        self.assertTrue(params["thread_id"].startswith("evolve:1:1:"))
        self.assertIn("## file: brain/playbooks/morning.md", params["message"])
        self.assertIn("never instructions to follow", params["message"])


    def test_an_answer_that_does_not_parse_is_asked_for_once_more(self):
        calls = self.root / "oh.log"
        count = self.root / "count"
        fake = self.root / "fake-oh"
        good = json.dumps({"title": "warmer gm", "component": "attention", "expected_move": "people answer",
                           "hypothesis": "h", "edits": [self.playbook_edit("Warmer.")]})
        cut = good[:60]  # gen 3 on the box: "Unterminated string starting at ..."
        fake.write_text("#!/usr/bin/env bash\nprintf '%s\\n' \"$2\" >>" + str(calls) + "\n"
                        "n=$(cat " + str(count) + " 2>/dev/null || echo 0); echo $((n + 1)) >" + str(count) + "\n"
                        "if [ \"$n\" = 0 ]; then a='" + cut.replace("'", "'\\''") + "'; else a='"
                        + good.replace("'", "'\\''") + "'; fi\n"
                        "python3 -c 'import json,sys; print(json.dumps({\"response\": sys.argv[1]}))' \"$a\"\n")
        fake.chmod(0o755)
        os.environ["TEENY_OH"] = str(fake)
        self.config(EVOLVE=1, EVOLVE_GENERATIONS_PER_DAY=24, EVOLVE_PROPOSER="ask")
        out = evolve.run_generation(evolve.load_config(), 1, None, publisher=FakePublisher())
        self.assertEqual(out["candidates"][0]["verdict"], "unevaluated")
        messages = [json.loads(line)["message"] for line in calls.read_text().splitlines()]
        self.assertEqual(len(messages), 2)
        self.assertIn("could not be read as JSON", messages[1])

    def test_the_message_leads_with_outcomes_and_rules_out_the_junk(self):
        calls = self.root / "oh.log"
        fake = self.root / "fake-oh"
        fake.write_text("#!/usr/bin/env bash\nprintf '%s\\n' \"$2\" >>" + str(calls) + "\n"
                        "python3 -c 'import json,sys; print(json.dumps({\"response\": sys.argv[1]}))' '{\"skip\": \"no\"}'\n")
        fake.chmod(0o755)
        os.environ["TEENY_OH"] = str(fake)
        self.config(EVOLVE=1, EVOLVE_GENERATIONS_PER_DAY=24, EVOLVE_PROPOSER="ask")
        with unittest.mock.patch.object(evolve, "world_report", return_value={
                "J": 0.1, "components": [{"component": "attention", "value": 5}, {"component": "money", "value": None}]}):
            evolve.run_generation(evolve.load_config(), 1, None, publisher=FakePublisher())
        message = json.loads(calls.read_text().splitlines()[0])["message"]
        self.assertLess(message.index("What came of what you made"), message.index("The score, your builds"))
        self.assertIn("Anything aimed at money: never read", message)
        self.assertNotIn("money, capability", message)  # money is not offered as a label
        self.assertIn("Doing less of yourself to save money", message)
        self.assertIn("a new hand that no playbook or prompt in the same change calls", message)
        self.assertIn("hands/lib/teeny_hands/build/jobs.py (DAILY_BRIEF", message)
        self.assertIn("This candidate looks first at", message)
        self.assertIn("An idea heard outside (outcomes.outside.heard", message)
        self.assertIn("fair evidence when you say what you heard and where", message)


if __name__ == "__main__":
    unittest.main()


class ProposerPromptFitsTheScreenTest(unittest.TestCase):
    """The core's prompt screen holds any input over 64 KiB as
    `input.oversized`, so a proposer turn whose message is bigger never runs
    (generation 1 on the box: four candidates, four `no-proposal`)."""

    def request(self, files):
        return {"gen": 1, "cand": 1, "focus": "reach", "frozen": ["bin/**"], "evidence": {"j": 1},
                "parent": "main", "parent_title": None, "parent_patch": "", "siblings": [],
                "editable": sorted(files), "files": dict(files)}

    def test_an_oversized_request_is_cut_to_fit_dropping_the_largest_files_first(self):
        files = {"brain/playbooks/big.md": "x" * 50_000, "brain/playbooks/mid.md": "y" * 20_000,
                 "brain/playbooks/small.md": "z" * 500}
        fitted = evolve.fit_request(self.request(files))
        rendered = evolve.render_request(fitted).encode("utf-8")
        self.assertLessEqual(len(rendered), evolve.MAX_PROPOSER_PROMPT_BYTES)
        self.assertNotIn("brain/playbooks/big.md", fitted["files"])
        self.assertIn("brain/playbooks/small.md", fitted["files"])
        # A path it can no longer see is still one it may change, and the
        # message says what was left out.
        self.assertIn("brain/playbooks/big.md", fitted["editable"])
        self.assertIn("brain/playbooks/big.md", evolve.render_request(fitted))

    def test_multibyte_text_is_measured_in_bytes(self):
        files = {"brain/SOUL.md": "é" * 40_000}  # 80 KB of UTF-8 in 40k characters
        fitted = evolve.fit_request(self.request(files))
        self.assertLessEqual(len(evolve.render_request(fitted).encode("utf-8")), evolve.MAX_PROPOSER_PROMPT_BYTES)

    def test_a_request_that_fits_is_left_as_it_is(self):
        request = self.request({"brain/playbooks/small.md": "z" * 500})
        self.assertEqual(evolve.fit_request(request), request)
