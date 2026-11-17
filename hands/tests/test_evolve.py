"""bin/evolve (bin/lib/evolve.py), offline: one candidate per open learning
of brain/MEMORY.md, one pull request per candidate. Its candidates branch
from a scratch clone of this repository, the core is a stand-in (TEENY_OH),
the hands it reads are stand-ins (TEENY_HANDS), bin/eval is the real one
(a change to a playbook no task drafts from is unmeasurable, so nothing
boots), GitHub is a fake publisher, and the clock is pinned (EVOLVE_NOW)."""

import contextlib
import io
import json
import os
import re
import subprocess
import sys
import tempfile
import time
import unittest
import unittest.mock
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
sys.path.insert(0, str(REPO / "bin" / "lib"))

import evolve  # noqa: E402

T0 = 1_794_398_400.0  # 2026-11-11T12:00:00Z
#: A proposal's check (bin/lib/evolve_outcomes.py): every proposal names one.
CHECK = {"kind": "judged", "surface": "moltbook"}
DAY = 86400.0
SECRET_ID = "heldout-whisper"
SECRET_PROMPT = "Reply to the lighthouse keeper who asks about the tide clock"
ENV_KEYS = ("TEENY_ROOT", "TEENY_STATE", "TEENY_SECRETS", "TEENY_HANDS", "TEENY_OH", "EVOLVE_GIT_DIR", "EVOLVE_BASE",
            "EVOLVE_NOW", "DRY_RUN", "EVOLVE", "EVAL_HELDOUT_DIR", "TEENY_STORAGE", "OPENHUMAN_CORE_URL",
            "FAKE_CALLS", "FAKE_HISTORY", "FAKE_REPORT", "OPENHUMAN_WORKSPACE", "EVOLVE_DOCTOR")


def git(*args, cwd=None):
    return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True).stdout


class FakePublisher:
    def __init__(self):
        self.prs, self.issues, self.states, self.statuses = [], [], {}, []

    def open_pr(self, src, tree, branch, base, title, body):
        files = git("-C", str(tree), "show", "--name-only", "--format=", "HEAD").split()
        self.prs.append({"branch": branch, "title": title, "body": body, "tree": str(tree), "files": files})
        n = len(self.prs)
        return {"number": n, "html_url": f"https://github.com/teenydroid/brain/pull/{n}"}

    def set_status(self, sha, state, description):
        self.statuses.append({"sha": sha, "state": state, "description": description})
        return {"id": len(self.statuses)}

    def open_issue(self, repo, title, body, judge=False):
        self.issues.append({"repo": repo, "title": title, "body": body, "judge": judge})
        return {"number": 100 + len(self.issues), "html_url": "https://github.com/teenydroid/brain/issues/1"}

    def pr(self, number):
        return self.states.get(number)


class Refusing(FakePublisher):
    """Under DRY_RUN nothing may reach GitHub at all."""

    def open_pr(self, *a, **k):
        raise AssertionError("opened a pull request under DRY_RUN")

    def set_status(self, *a, **k):
        raise AssertionError("set a status under DRY_RUN")

    def open_issue(self, *a, **k):
        raise AssertionError("opened an issue under DRY_RUN")


class Verdicts:
    """An evaluator that answers each candidate's verdict by its index
    (default pass), and remembers whom it was asked about."""

    def __init__(self, by_cand=None):
        self.by_cand, self.asked = dict(by_cand or {}), []

    def evaluate(self, cands, done):
        for cand in cands:
            self.asked.append(cand["id"])
            verdict = self.by_cand.get(cand["cand"], "pass")
            done(cand, {"verdict": verdict, "eval": {"verdict": verdict, "tasks": ["reply-fud"], "wins": 1,
                                                     "losses": 0, "ties": 1}, "reason": f"judged {verdict}"})


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

PAGE = """# MEMORY.md

what i keep in mind.

## people

- [m1] **ana** (x): builds a cli for crabs.

## promises and open threads

- [m12] promise to nyna: answer her first whenever she surfaces.

## what i am in the middle of

- [m15] learning to code in public. the first thing i build myself is a $TINY chart renderer. not built yet.

## what i have decided

- [m36] before retrying anything, check whether it already went through.
- [m44] **no likes on X, ever**: X's rules don't allow automated likes.
- [m57] no dotted tokens in outbound telegram text.
- [m61] reach is a wave, commitment is a drip: an event post needs a hook people can join.
- [m62] some visits can just be visits.
"""
#: The page's learnings in the order a generation takes them (no snapshots:
#: the higher id first).
ORDER = ["m62", "m61", "m57", "m44", "m36", "m15", "m12"]


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
                          TEENY_OH=str(self.root / "no-core"), EVOLVE_DOCTOR=str(self.root / "no-doctor"))
        self.config(EVOLVE=1, EVOLVE_GENERATIONS_PER_DAY=24)
        self.props = self.root / "props"
        self.props.mkdir()
        self.page()

    def tearDown(self):
        for ref in git("--git-dir", str(self.src), "for-each-ref", "--format=%(refname)", "refs/heads/teeny/").split():
            git("--git-dir", str(self.src), "update-ref", "-d", ref)
        for tree in [*evolve.evolve_dir().glob("*/cand-*"), *evolve.evolve_dir().glob("*/lab-base/tree")]:
            if tree.is_dir():
                subprocess.run(["git", "--git-dir", str(self.src), "worktree", "remove", "--force", str(tree)],
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

    def live(self):
        (self.root / "secrets" / "teeny.env").write_text("DRY_RUN=0\n")

    def page(self, text=PAGE):
        brain = self.root / "brain"
        brain.mkdir(exist_ok=True)
        (brain / "MEMORY.md").write_text(text)

    def base_text(self, path):
        return git("--git-dir", str(self.src), "show", f"main:{path}")

    def propose(self, i, edits, component="attention", title=None):
        (self.props / f"cand-{i}.json").write_text(json.dumps({
            "title": title or f"candidate {i}", "component": component, "expected_move": "+1% in a week",
            "check": CHECK, "hypothesis": f"Hypothesis {i}: what is weak, the change, the effect.",
            "edits": edits}))

    def skip(self, i, why, where=""):
        (self.props / f"cand-{i}.json").write_text(json.dumps({"skip": f"no change for {i}", "why": why,
                                                               "where": where}))

    def playbook_edit(self, line, path="brain/playbooks/moltbook-follow.md"):
        """A line added to PATH. The default is a playbook no eval task
        drafts from, so `bin/eval affected` (the real one) says the change is
        unmeasurable and nothing boots."""
        text = self.base_text(path)
        last = text.rstrip().splitlines()[-1]
        return {"path": path, "old": last, "new": f"{last}\n\n{line}"}

    def run_gen(self, n, publisher=None, **kw):
        cfg = evolve.load_config()
        return evolve.run_generation(cfg, n, str(self.props), publisher=publisher or FakePublisher(), **kw)

    def entries(self):
        return evolve.archive()


    def worktree(self, name):
        tree = self.root / "trees" / name
        evolve.add_worktree(evolve.source(), tree, git("--git-dir", str(self.src), "rev-parse", "main").strip())
        return tree


class Learnings(EvolveCase):
    """2026-11-15: eight tournament generations opened no pull request. The
    loop now acts on Teeny's own learnings, one candidate and one pull
    request each."""

    def proposals(self, n):
        for i in range(1, n + 1):
            self.propose(i, [self.playbook_edit(f"Act on learning {i}.")], title=f"act on {i}")

    def test_the_page_learnings_are_the_decided_promised_and_ongoing_items_never_the_people(self):
        got = evolve.read_learnings()
        self.assertEqual(sorted(item.id for item in got), sorted(ORDER))
        self.assertNotIn("m1", [item.id for item in got])
        m44 = next(item for item in got if item.id == "m44")
        self.assertEqual(m44.section, "what i have decided")
        self.assertTrue(m44.text.startswith("**no likes on X, ever**"))
        self.assertEqual(m44.hash, evolve.learning_hash("**no likes on X,   ever**: X's rules don't allow automated likes."))

    def test_each_open_learning_gets_its_own_candidate(self):
        self.proposals(3)
        out = self.run_gen(3)
        self.assertEqual([c["learning"] for c in out["candidates"]], ORDER[:3])
        entries = self.entries()
        for i, lid in enumerate(ORDER[:3], 1):
            entry = entries[f"g1c{i}"]
            self.assertEqual(entry["learning"]["id"], lid)
            self.assertEqual(entry["learning"]["hash"], next(x.hash for x in evolve.read_learnings() if x.id == lid))
        hyp = (evolve.evolve_dir() / "1" / "cand-1.HYPOTHESIS.md").read_text()
        self.assertIn(f"- learning: [{ORDER[0]}] (what i have decided)", hyp)

    def test_the_newest_learning_text_goes_first_by_the_reflection_snapshots(self):
        """A text that the oldest snapshot already carried waits behind one
        the latest reflection wrote; a text in no snapshot is the newest."""
        snaps = evolve.state_dir() / "reflect"
        snaps.mkdir(parents=True)
        old = snaps / "MEMORY-20261110T0200.md"
        old.write_text(PAGE)  # every learning as it reads now
        os.utime(old, (T0 - 5 * DAY, T0 - 5 * DAY))
        newer = PAGE.replace("- [m62] some visits can just be visits.\n", "")
        mid = evolve.state_dir() / "dream" / "snapshots"
        mid.mkdir(parents=True)
        (mid / "20261112T0200Z-night.md").write_text('---\n{"slot": "night"}\n---\n' + newer)
        os.utime(mid / "20261112T0200Z-night.md", (T0 - DAY, T0 - DAY))
        self.page(PAGE.replace("- [m12] promise to nyna: answer her first whenever she surfaces.",
                               "- [m12] promise to nyna: answered nov 15. answer her first whenever she surfaces."))
        order = [item.id for item in evolve.open_learnings(self.entries())]
        self.assertEqual(order[0], "m12")  # rewritten after every snapshot
        self.assertEqual(order[1:], [x for x in ORDER if x != "m12"])  # the rest: all first seen 5 days ago, by id

    def test_an_unchanged_learning_that_was_already_carried_out_is_not_re_asked(self):
        self.live()
        self.proposals(2)
        self.run_gen(2)
        self.assertEqual(self.entries()["g1c1"]["verdict"], "opened")
        self.proposals(2)
        out = self.run_gen(2)
        self.assertEqual([c["learning"] for c in out["candidates"]], ORDER[2:4])

    def test_an_edited_learning_is_open_again(self):
        self.live()
        self.proposals(1)
        self.run_gen(1)
        self.page(PAGE.replace("some visits can just be visits.", "some visits can just be visits, and say so."))
        self.proposals(1)
        out = self.run_gen(1)
        self.assertEqual(out["candidates"][0]["learning"], "m62")

    def test_a_skip_as_already_or_not_actionable_is_kept_with_the_hash_and_not_re_asked(self):
        self.skip(1, "already", "brain/playbooks/x-replies.md")
        self.skip(2, "not-actionable")
        out = self.run_gen(2)
        self.assertEqual([c["verdict"] for c in out["candidates"]], ["skipped", "skipped"])
        entry = self.entries()["g1c1"]
        self.assertEqual(entry["skip"], {"why": "already", "where": "brain/playbooks/x-replies.md",
                                         "note": "no change for 1"})
        self.assertEqual(entry["learning"]["id"], ORDER[0])
        self.assertNotIn(ORDER[0], [x.id for x in evolve.open_learnings(self.entries())])
        self.assertNotIn(ORDER[1], [x.id for x in evolve.open_learnings(self.entries())])

    def test_edits_that_do_not_apply_are_asked_for_once_more_with_the_error(self):
        """Gen 9 (2026-11-15) lost m68 to one edit whose old text left a
        sentence out of the SOUL.md line it quoted; the proposer never saw
        why. It is asked once more, shown the error and its edits."""
        seen = []
        line = "Some visits can just be visits."

        class Retry:
            name = "retry"

            def propose(self, request):
                seen.append(evolve.render_request(request))
                old = "a sentence that is not in the file" if len(seen) == 1 else ""
                path = "brain/playbooks/moltbook-follow.md"
                if old:
                    return {"title": "t", "component": "attention", "expected_move": "+1%",
                            "check": CHECK, "hypothesis": "h", "edits": [{"path": path, "old": old, "new": line}]}
                text = (REPO / path).read_text(encoding="utf-8")
                return {"title": "t", "component": "attention", "expected_move": "+1%", "check": CHECK,
                        "hypothesis": "h",
                        "edits": [{"path": path, "old": text, "new": text + line + "\n"}]}

        with unittest.mock.patch.object(evolve, "make_proposer", return_value=Retry()):
            out = self.run_gen(1)
        self.assertEqual(len(seen), 2)
        self.assertIn("## your last edits did not apply", seen[1])
        self.assertIn("the old text occurs 0 times, not once", seen[1])
        self.assertIn("a sentence that is not in the file", seen[1])
        self.assertNotEqual(out["candidates"][0]["verdict"], "no-proposal")

    def test_edits_that_never_apply_are_parked_after_the_last_round(self):
        calls = []

        class Wrong:
            name = "wrong"

            def propose(self, request):
                calls.append(request)
                return {"title": "t", "component": "attention", "expected_move": "+1%", "check": CHECK,
                        "hypothesis": "h",
                        "edits": [{"path": "brain/SOUL.md", "old": "not in the file", "new": "x"}]}

        with unittest.mock.patch.object(evolve, "make_proposer", return_value=Wrong()):
            out = self.run_gen(1)
        self.assertEqual(len(calls), 3)  # EVOLVE_REVISE_ROUNDS
        self.assertEqual(out["candidates"][0]["verdict"], "parked")
        self.assertIn("occurs 0 times", self.entries()["g1c1"]["findings"][0]["problem"])

    def test_a_skip_without_a_why_is_asked_again(self):
        (self.props / "cand-1.json").write_text(json.dumps({"skip": "meh"}))
        out = self.run_gen(1)
        self.assertEqual(out["candidates"][0]["verdict"], "no-proposal")
        self.assertIn(ORDER[0], [x.id for x in evolve.open_learnings(self.entries())])

    def test_a_learning_that_needs_a_frozen_path_opens_one_issue_for_the_operator(self):
        self.live()
        self.page("# MEMORY.md\n\n## what i have decided\n\n- [m62] some visits can just be visits.\n")
        self.skip(1, "frozen", "bin/lib/evolve.py")
        pub = FakePublisher()
        self.run_gen(1, publisher=pub)
        (issue,) = pub.issues
        self.assertIn(f"[{ORDER[0]}]", issue["title"])
        self.assertIn("bin/lib/evolve.py", issue["body"])
        self.assertIn("some visits can just be visits.", issue["body"])
        self.assertTrue(issue["judge"])  # `where` is a model's words
        # Settled: the same text is not asked about again, so no second issue.
        self.skip(1, "frozen", "bin/lib/evolve.py")
        self.run_gen(1, publisher=pub)
        self.assertEqual(len(pub.issues), 1)

    def test_no_open_learning_is_a_cheap_no_op(self):
        self.page("# MEMORY.md\n\n## people\n\n- [m1] **ana**: crabs.\n")
        with unittest.mock.patch.object(evolve, "observe") as observe, \
                unittest.mock.patch.object(evolve, "make_proposer") as proposer:
            out = self.run_gen(4)
        self.assertEqual(out["learnings"], 0)
        self.assertIn("no open learnings", out["skipped"])
        observe.assert_not_called()
        proposer.assert_not_called()
        self.assertFalse(evolve.evolve_dir().exists() and any(evolve.evolve_dir().glob("*/generation.json")))

    def test_a_learning_that_keeps_failing_is_dropped_after_three_tries(self):
        self.page("# MEMORY.md\n\n## what i have decided\n\n- [m62] some visits can just be visits.\n")
        for gen in range(1, evolve.MAX_ATTEMPTS + 1):
            (self.props / "cand-1.json").write_text("not json")
            out = self.run_gen(1)
            self.assertEqual(out["candidates"][0]["learning"], ORDER[0], gen)
        state, failed = evolve.learning_state(self.entries(), evolve.read_learnings()[-1])
        self.assertEqual((state, failed), ("gave-up", evolve.MAX_ATTEMPTS))
        self.assertNotIn(ORDER[0], [x.id for x in evolve.open_learnings(self.entries())])

    def test_the_proposer_is_shown_the_learning_and_asked_for_the_smallest_change(self):
        seen = []

        class Capture:
            name = "capture"

            def propose(self, request):
                seen.append(evolve.render_request(request))
                return {"skip": "just looking", "why": "not-actionable"}

        with unittest.mock.patch.object(evolve, "make_proposer", return_value=Capture()):
            self.run_gen(1)
        [message] = seen
        self.assertIn("## what you learned", message)
        self.assertIn("[m62] under \"what i have decided\"", message)
        self.assertIn("> some visits can just be visits.", message)
        self.assertIn("This is something you learned. Make the smallest change", message)
        self.assertIn('"why": "already|frozen|not-actionable"', message)
        self.assertNotIn("crabs", message)  # the people stay out


class OnePullRequestPerCandidate(EvolveCase):
    def setUp(self):
        super().setUp()
        self.live()

    def test_two_passing_candidates_open_two_prs(self):
        self.propose(1, [self.playbook_edit("Check first.")], title="check before retrying")
        self.propose(2, [self.playbook_edit("A hook people can join.", "brain/playbooks/pr-merged.md")],
                     title="event posts get a hook")
        pub = FakePublisher()
        out = self.run_gen(2, publisher=pub, evaluator=Verdicts())
        self.assertEqual([p["branch"] for p in pub.prs], ["teeny/gen-1-c1", "teeny/gen-1-c2"])
        self.assertEqual(pub.prs[0]["files"], ["brain/playbooks/moltbook-follow.md"])
        self.assertEqual(pub.prs[1]["files"], ["brain/playbooks/pr-merged.md"])  # each its own change only
        self.assertEqual([c["verdict"] for c in out["candidates"]], ["opened", "opened"])
        entries = self.entries()
        self.assertEqual(entries["g1c2"]["pr_url"], "https://github.com/teenydroid/brain/pull/2")
        # Each head carries teeny/eval: success.
        self.assertEqual([s["state"] for s in pub.statuses], ["success", "success"])
        self.assertEqual(pub.statuses[0]["sha"], entries["g1c1"]["head_sha"])
        self.assertEqual(pub.statuses[0]["sha"], git("--git-dir", str(self.src), "rev-parse", "teeny/gen-1-c1").strip())
        self.assertIn("no regression on 1 task", pub.statuses[0]["description"])
        log = git("--git-dir", str(self.src), "log", "-1", "--format=%an", "teeny/gen-1-c1").strip()
        self.assertEqual(log, "Teeny")

    def test_a_branch_left_by_a_failed_push_is_replaced_never_suffixed(self):
        """The babysitter merges only teeny/gen-<n> or teeny/gen-<n>-c<i>,
        digits only: a `-2` suffix would never merge."""
        git("--git-dir", str(self.src), "branch", "teeny/gen-1-c1", "main")
        self.propose(1, [self.playbook_edit("Check first.")])
        pub = FakePublisher()
        self.run_gen(1, publisher=pub, evaluator=Verdicts())
        self.assertEqual([p["branch"] for p in pub.prs], ["teeny/gen-1-c1"])
        self.assertRegex(pub.prs[0]["branch"], r"^teeny/gen-\d+(-c\d+)?$")
        self.assertIn("Check first.", git("--git-dir", str(self.src), "show", "teeny/gen-1-c1:brain/playbooks/moltbook-follow.md"))

    def test_candidates_are_measured_under_the_eval_lock(self):
        """The babysitter's deploy waits on state/locks/eval.lock before it
        moves or restarts the live checkout a lab was booted from."""
        seen = []

        class Watching(Verdicts):
            def evaluate(self, cands, done):
                seen.append(evolve.running("eval"))
                super().evaluate(cands, done)

        self.propose(1, [self.playbook_edit("Check first.")])
        self.run_gen(1, evaluator=Watching())
        self.assertEqual(seen, [True])
        self.assertFalse(evolve.running("eval"))

    def test_the_body_carries_the_learning_in_teenys_words_the_hypothesis_the_gate_and_the_eval(self):
        self.propose(1, [self.playbook_edit("Check first.")], title="check before retrying")
        pub = FakePublisher()
        self.run_gen(1, publisher=pub, evaluator=Verdicts())
        (pr,) = pub.prs
        self.assertIn("[m62]", pr["title"])
        self.assertIn("**What I learned** ([m62], what i have decided", pr["body"])
        self.assertIn("> some visits can just be visits.", pr["body"])
        self.assertIn("Hypothesis 1", pr["body"])
        self.assertIn("| frozen paths | pass |", pr["body"])
        self.assertIn("**Targeted eval**", pr["body"])
        self.assertIn("pass on the 1 task(s)", pr["body"])
        self.assertIn("The babysitter merges it", pr["body"])

    def test_a_change_no_task_reaches_opens_a_pr_as_unmeasurable(self):
        self.propose(1, [self.playbook_edit("Check first.")])
        pub = FakePublisher()
        out = self.run_gen(1, publisher=pub)  # EVOLVE_EVALUATOR=none: no lab could boot anyway
        self.assertEqual(out["candidates"][0]["verdict"], "opened")
        entry = self.entries()["g1c1"]
        self.assertEqual(entry["eval"]["verdict"], "unmeasurable")
        self.assertIn("**Unmeasurable.**", pub.prs[0]["body"])
        self.assertIn("evolve-watch are its test", pub.prs[0]["body"])
        self.assertTrue(pub.statuses[0]["description"].startswith("unmeasurable"))
        self.assertEqual(pub.statuses[0]["state"], "success")

    def test_a_change_a_task_reaches_is_not_shipped_unmeasured(self):
        self.propose(1, [self.playbook_edit("Say it plainly.", "brain/playbooks/morning.md")])
        pub = FakePublisher()
        out = self.run_gen(1, publisher=pub)
        self.assertEqual(out["candidates"][0]["verdict"], "unevaluated")
        self.assertEqual(pub.prs, [])
        self.assertIn(ORDER[0], [x.id for x in evolve.open_learnings(self.entries())])  # tried again later

    def test_a_regressed_candidate_opens_no_pr(self):
        self.propose(1, [self.playbook_edit("One.")])
        self.propose(2, [self.playbook_edit("Two.", "brain/playbooks/pr-merged.md")])
        pub = FakePublisher()
        out = self.run_gen(2, publisher=pub, evaluator=Verdicts({1: "regressed"}))
        self.assertEqual([c["verdict"] for c in out["candidates"]], ["regressed", "opened"])
        self.assertEqual([p["branch"] for p in pub.prs], ["teeny/gen-1-c2"])
        self.assertNotIn(ORDER[0], [x.id for x in evolve.open_learnings(self.entries())])  # measured: settled

    def test_an_inconclusive_candidate_opens_no_pr_and_may_be_retried(self):
        self.propose(1, [self.playbook_edit("One.")])
        pub = FakePublisher()
        out = self.run_gen(1, publisher=pub, evaluator=Verdicts({1: "inconclusive"}))
        self.assertEqual(out["candidates"][0]["verdict"], "inconclusive")
        self.assertEqual(pub.prs, [])
        self.assertIn(ORDER[0], [x.id for x in evolve.open_learnings(self.entries())])

    def test_a_rejected_candidate_is_archived_with_its_gate_and_never_shipped(self):
        self.propose(1, [{"path": "bin/ask", "old": self.base_text("bin/ask").splitlines()[1], "new": "# x"}])
        pub = FakePublisher()
        out = self.run_gen(1, publisher=pub)
        self.assertEqual(out["published"], [])
        self.assertEqual(pub.prs, [])
        entry = self.entries()["g1c1"]
        self.assertEqual(entry["verdict"], "rejected")
        self.assertFalse([c for c in entry["gate"]["checks"] if c["check"] == "frozen paths"][0]["ok"])

    def test_at_the_open_pr_cap_a_passing_candidate_waits_for_the_next_run(self):
        self.config(EVOLVE=1, EVOLVE_GENERATIONS_PER_DAY=24, EVOLVE_MAX_OPEN_PRS=1)
        self.propose(1, [self.playbook_edit("One.")])
        self.propose(2, [self.playbook_edit("Two.", "brain/playbooks/pr-merged.md")])
        pub = FakePublisher()
        out = self.run_gen(2, publisher=pub, evaluator=Verdicts())
        self.assertEqual([c["verdict"] for c in out["candidates"]], ["opened", "waiting"])
        self.assertEqual(len(pub.prs), 1)
        # The first merges (the babysitter); the next run opens the waiting one
        # with no proposer turn for it, before any new learning.
        pub.states = {1: {"merged_at": evolve.iso(T0), "state": "closed", "merge_sha": "f" * 40}}
        for p in self.props.glob("*.json"):
            p.unlink()
        out = self.run_gen(1, publisher=pub, evaluator=Verdicts())
        self.assertEqual([p["branch"] for p in pub.prs], ["teeny/gen-1-c1", "teeny/gen-1-c2"])
        self.assertEqual(self.entries()["g1c2"]["verdict"], "opened")
        self.assertEqual(self.entries()["g1c1"]["merged_at"], evolve.iso(T0))
        self.assertEqual(out["candidates"][0]["verdict"], "no-proposal")  # gen 2's own learning: no file

    def test_dry_run_opens_no_pull_request_and_says_which_it_would(self):
        os.environ["DRY_RUN"] = "1"
        self.propose(1, [self.playbook_edit("One.")])
        out = self.run_gen(1, publisher=Refusing())
        self.assertEqual(out["candidates"][0]["verdict"], "dry-run")
        pr = json.loads((evolve.evolve_dir() / "1" / "cand-1.pr.json").read_text())
        self.assertTrue(pr["dry_run"])
        self.assertEqual(pr["would_open_pr"]["branch"], "teeny/gen-1-c1")
        self.assertEqual(pr["would_set_status"]["context"], "teeny/eval")
        self.assertEqual(git("--git-dir", str(self.src), "branch", "--list", "teeny/*").strip(), "")
        # Settled while DRY_RUN stays on; asked for real once it is off.
        self.assertNotIn(ORDER[0], [x.id for x in evolve.open_learnings(self.entries())])
        os.environ.pop("DRY_RUN")
        self.live()
        self.assertEqual(evolve.open_learnings(self.entries())[0].id, ORDER[0])

    def test_teeny_env_without_dry_run_0_is_a_dry_run(self):
        (self.root / "secrets" / "teeny.env").unlink()
        self.assertTrue(evolve.dry_run())
        self.live()
        self.assertFalse(evolve.dry_run())
        os.environ["DRY_RUN"] = "1"
        self.assertTrue(evolve.dry_run())

    def test_a_refused_pull_request_stays_on_the_candidate(self):
        class Guarded(FakePublisher):
            def open_pr(self, *a, **k):
                raise RuntimeError("held by the guard")

        self.propose(1, [self.playbook_edit("One.")])
        out = self.run_gen(1, publisher=Guarded())
        self.assertEqual(out["candidates"][0]["verdict"], "pr-error")
        self.assertIn("held by the guard", self.entries()["g1c1"]["pr_error"])

    def test_the_lineage_runs_through_the_newest_merge(self):
        self.propose(1, [self.playbook_edit("One.")])
        pub = FakePublisher()
        self.run_gen(1, publisher=pub)
        self.assertEqual(self.entries()["g1c1"]["parent"], "main")
        pub.states = {1: {"merged_at": evolve.iso(T0), "state": "closed"}}
        self.propose(1, [self.playbook_edit("Two.")])
        self.run_gen(1, publisher=pub)
        self.assertEqual(self.entries()["g2c1"]["parent"], "g1c1")


class Restarts(EvolveCase):
    """A generation lives in the spine's container, and redeploys killed
    gens 5 and 7. Every stage is recorded as it ends, so a restart picks the
    candidate up where it was."""

    def setUp(self):
        super().setUp()
        self.live()

    def cut_off(self, gen, keep_through):
        """Gen GEN as a restart leaves it: no result.json, and only the
        archive lines up to the stage KEEP_THROUGH (proposal | gate | eval)."""
        (evolve.gen_dir(gen) / "result.json").unlink()
        rows = evolve.read_jsonl(evolve.archive_path())
        kept, seen = [], {}
        stage_of = {"proposal": 0, "gate": 1, "eval": 2}
        for row in rows:
            if row.get("gen") != gen:
                kept.append(row)
                continue
            n = seen.get(row["id"], 0)
            if n <= stage_of[keep_through] - 1:
                kept.append(row)
            seen[row["id"]] = n + 1
        evolve.archive_path().write_text("".join(json.dumps(r) + "\n" for r in kept))

    def test_a_restart_resumes_an_unfinished_candidate_without_a_new_proposer_turn(self):
        self.propose(1, [self.playbook_edit("Check first.")], title="the cut-off one")
        pub = FakePublisher()
        self.run_gen(1, publisher=pub, evaluator=Verdicts())
        self.cut_off(1, "proposal")  # its proposal saved, nothing recorded
        (self.props / "cand-1.json").unlink()  # the proposer would have nothing to say now
        for ref in git("--git-dir", str(self.src), "for-each-ref", "--format=%(refname)", "refs/heads/teeny/").split():
            git("--git-dir", str(self.src), "update-ref", "-d", ref)
        pub = FakePublisher()
        out = self.run_gen(1, publisher=pub, evaluator=Verdicts())
        self.assertEqual(out["gen"], 1)
        self.assertTrue(out["resumed"])
        self.assertEqual(out["candidates"][0]["title"], "the cut-off one")
        self.assertEqual(out["candidates"][0]["verdict"], "opened")
        self.assertTrue(self.entries()["g1c1"]["resumed"])
        self.assertEqual([p["branch"] for p in pub.prs], ["teeny/gen-1-c1"])
        self.assertFalse(any(evolve.evolve_dir().glob("2/generation.json")))  # no new generation

    def test_a_measured_candidate_is_not_measured_again_after_a_restart(self):
        self.propose(1, [self.playbook_edit("Check first.")])
        first = Verdicts()
        self.run_gen(1, publisher=FakePublisher(), evaluator=first)
        self.cut_off(1, "eval")  # measured as pass, then cut off before its pull request
        self.assertEqual(self.entries()["g1c1"]["verdict"], "pass")
        again, pub = Verdicts(), FakePublisher()
        out = self.run_gen(1, publisher=pub, evaluator=again)
        self.assertEqual(again.asked, [])
        self.assertEqual(out["candidates"][0]["verdict"], "opened")
        self.assertEqual(len(pub.prs), 1)

    def test_resuming_does_not_count_as_another_generation_today(self):
        self.config(EVOLVE=1, EVOLVE_GENERATIONS_PER_DAY=1)
        self.propose(1, [self.playbook_edit("One.")])
        self.run_gen(1, evaluator=Verdicts(), scheduled=True)
        self.cut_off(1, "gate")
        out = self.run_gen(1, evaluator=Verdicts(), scheduled=True)
        self.assertTrue(out.get("resumed"), out)
        self.assertEqual(evolve.generations_today(), 1)
        self.assertEqual(self.run_gen(1, scheduled=True)["skipped"], "halted: generations")

    def test_a_generation_from_before_learnings_is_marked_interrupted_not_resumed(self):
        old = evolve.gen_dir(8)
        old.mkdir(parents=True)
        (old / "generation.json").write_text(json.dumps({"gen": 8, "started_at": evolve.iso(T0), "base": "abc",
                                                         "candidates": 4}))
        self.propose(1, [self.playbook_edit("One.")])
        out = self.run_gen(1, evaluator=Verdicts())
        self.assertEqual(out["gen"], 9)
        self.assertTrue(json.loads((old / "result.json").read_text())["interrupted"])


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
        self.assertEqual(entries["g2c2"]["verdict"], "dry-run")
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
        self.assertEqual(set(checks), {"frozen paths", "hands/lib holds only teeny_hands", "frozen hands' entry points", "changed hands have tests",
                                       "docs updated", "diff size",
                                       "no binary files",
                                       "SOUL+CONTEXT size",
                                       "new hands have tests", "aims at a component with a reading",
                                       "does more than run less", "new hands are called", "does more than mark an arc",
                                       "seed-cron --check", "doctor --root", "eval invariants"})

    def test_a_frozen_path_fails_and_the_doctor_never_runs(self):
        text = self.base_text("bin/ask")
        out, checks = self.gated([{"path": "bin/ask", "old": text.splitlines()[1], "new": "# loosened"}])
        self.assertFalse(out["ok"])
        self.assertFalse(checks["frozen paths"]["ok"])
        self.assertIn("bin/ask", checks["frozen paths"]["detail"])
        self.assertNotIn("doctor --root", checks)  # a refused change runs no other check

    def test_the_entry_point_of_a_hand_whose_code_is_frozen_stays_as_it_is(self):
        """hands/git runs teeny_hands.git_cli, which is frozen: a rewritten
        shim would step around it, and bin/doctor --root runs it. Not final
        (a finding), but the doctor never runs it."""
        text = self.base_text("hands/git")
        out, checks = self.gated([{"path": "hands/git", "old": text.splitlines()[0], "new": "#!/bin/sh\necho hi"}])
        self.assertTrue(out["hard_ok"])
        self.assertFalse(checks["frozen hands' entry points"]["ok"])
        self.assertIn("hands/lib/teeny_hands/git_cli.py", checks["frozen hands' entry points"]["detail"])
        self.assertIn("not run", checks["doctor --root"]["detail"])

    def test_more_changed_lines_than_the_share_of_the_repository_fail(self):
        """The 400-line cap is gone: the cap is EVOLVE_MAX_CHANGE_PCT (5) of
        every line the repository tracks, about 6,500 lines."""
        _, ok = self.gated_again(1000)
        self.assertTrue(ok["diff size"]["ok"], ok["diff size"])
        tree = self.root / "trees" / "t"
        base = git("--git-dir", str(self.src), "rev-parse", "main").strip()
        cap = evolve.max_change_lines(tree, base, 5)
        # Past apply_edits' 40,000 characters: written straight to the tree.
        (tree / "brain" / "skills" / "long" / "SKILL.md").write_text("".join(f"line {i}\n" for i in range(cap + 1)))
        checks = {c["check"]: c for c in evolve.gate(tree, base)["checks"]}
        self.assertFalse(checks["diff size"]["ok"])
        self.assertIn(f"{cap + 1}/{cap}", checks["diff size"]["detail"])
        self.assertTrue(checks["diff size"]["hard"])

    def gated_again(self, n):
        subprocess.run(["git", "--git-dir", str(self.src), "worktree", "remove", "--force",
                        str(self.root / "trees" / "t")], capture_output=True)
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

    def test_a_new_hand_with_a_quoted_name_is_still_a_hand(self):
        """git quotes a non-ASCII path; the diff readers must not take the quotes
        for part of the name, or the hand is invisible and skips both checks."""
        accents = [
            {"path": "hands/naïve", "old": "", "new": "#!/bin/sh\necho n\n"},
            {"path": "hands/lib/teeny_hands/naïve/__init__.py", "old": "", "new": "\"\"\"naïve\"\"\"\n"},
        ]
        out, checks = self.gated(accents)
        self.assertFalse(checks["new hands have tests"]["ok"])
        self.assertIn("naïve", checks["new hands have tests"]["detail"])
        self.assertFalse(checks["new hands are called"]["ok"])
        self.assertIn("hands/naïve", checks["new hands are called"]["detail"])

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

    def test_a_failed_check_lands_in_the_archive_with_its_reason(self):
        """Not final since 2026-11-15: the same proposal three rounds running
        is parked, its failed check kept as the finding."""
        self.propose(1, [self.cron('schedule = "45 9-21/2 * * *"', 'schedule = "45 9,13,17,21 * * *"')],
                     component="attention")
        self.run_gen(1)
        entry = self.entries()["g1c1"]
        self.assertEqual(entry["verdict"], "parked")
        self.assertIn("does more than run less", entry["findings"][0]["problem"])
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
        again = evolve.credit(evolve.load_config(), pub)
        self.assertEqual(again["measured"], [])


    def test_a_merged_generation_gets_its_chronicle_page_whoever_merged_it(self):
        """docs/roadmap.md, A13: a merged generation has
        brain/narrative/chronicle/gen-<n>.md, written from the record."""
        evolve.record({"id": "g2c1", "gen": 2, "cand": 1, "parent": "main", "verdict": "regressed", "gate": {"ok": True}})
        evolve.record({"id": "g3c1", "gen": 3, "cand": 1, "parent": "main", "verdict": "opened", "gate": {"ok": True},
                       "pr_number": 5, "title": "open with what shipped", "component": "attention",
                       "expected_move": "+2%", "eval": {"verdict": "pass", "tasks": ["morning-post", "shiplog-numbers"]},
                       "learning": {"id": "m36", "section": "what i have decided", "hash": "h", "text": "t"}})
        evolve.record({"id": "g3c2", "gen": 3, "cand": 2, "parent": "main", "verdict": "skipped", "gate": {"ok": False}})
        pub = FakePublisher()
        out = evolve.credit(evolve.load_config(), pub)
        self.assertEqual(out["chronicled"], [])  # not merged yet: no page
        page = self.root / "brain" / "narrative" / "chronicle" / "gen-3.md"
        self.assertFalse(page.exists())
        # GitHub's REST API calls a merged pull request closed: `merged` decides.
        pub.states = {5: {"state": "closed", "merged": True, "merged_at": evolve.iso(T0 - DAY), "merge_sha": "e" * 40,
                          "merged_by": "teenydroid"}}
        out = evolve.credit(evolve.load_config(), pub)
        self.assertEqual(out["chronicled"], ["g3c1"])
        entry = self.entries()["g3c1"]
        self.assertNotIn("closed", entry)
        self.assertEqual(entry["merge_sha"], "e" * 40)
        text = page.read_text()
        self.assertIn("# gen-3", text)
        self.assertIn("took 2 things teeny learned", text)
        self.assertIn("open with what shipped", text)
        self.assertIn("- what it acts on: [m36] (what i have decided)", text)
        self.assertIn("no worse on the 2 task(s) it reaches", text)
        self.assertIn("- merged by: the babysitter", text)
        self.assertIn("- gen-2: the agents carried it back", text)
        self.assertEqual(entry["chronicle"], "brain/narrative/chronicle/gen-3.md")
        # Once: a later run neither rewrites it nor writes it again.
        page.write_text("kept\n")
        again = evolve.credit(evolve.load_config(), pub)
        self.assertEqual(again["chronicled"], [])
        self.assertEqual(page.read_text(), "kept\n")

    def test_the_chronicle_names_a_person_who_merged_by_hand(self):
        """A generation the operator merged is not credited to the babysitter."""
        evolve.record({"id": "g4c1", "gen": 4, "cand": 1, "parent": "main", "verdict": "opened", "gate": {"ok": True},
                       "pr_number": 7, "title": "a playbook paragraph", "eval": {"verdict": "unmeasurable"}})
        pub = FakePublisher()
        pub.states = {7: {"state": "closed", "merged": True, "merged_at": evolve.iso(T0 - DAY), "merged_by": "senamakel"}}
        evolve.credit(evolve.load_config(), pub)
        text = (self.root / "brain" / "narrative" / "chronicle" / "gen-4.md").read_text()
        self.assertIn("- merged by: senamakel, by hand", text)
        self.assertNotIn("babysitter", text)
        self.assertEqual(self.entries()["g4c1"]["merged_by"], "senamakel")

    def test_the_chronicle_does_not_guess_who_merged(self):
        evolve.record({"id": "g4c1", "gen": 4, "cand": 1, "parent": "main", "verdict": "opened", "gate": {"ok": True},
                       "pr_number": 7})
        pub = FakePublisher()
        pub.states = {7: {"state": "closed", "merged": True, "merged_at": evolve.iso(T0 - DAY)}}
        evolve.credit(evolve.load_config(), pub)
        text = (self.root / "brain" / "narrative" / "chronicle" / "gen-4.md").read_text()
        self.assertIn("- merged by: not recorded", text)

    def test_a_pull_request_closed_unmerged_is_closed(self):
        evolve.record({"id": "g1c1", "gen": 1, "cand": 1, "parent": "main", "gate": {"ok": True}, "pr_number": 1})
        pub = FakePublisher()
        pub.states = {1: {"state": "closed", "merged": False}}
        evolve.credit(evolve.load_config(), pub)
        self.assertTrue(self.entries()["g1c1"]["closed"])
        self.assertEqual(evolve.open_prs(self.entries()), [])
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



class EvalConcurrency(EvolveCase):
    """secrets/evolve.env sets how many eval turns a lab run keeps in flight,
    so an operator can drop it to 1 during a provider outage."""

    def test_it_is_read_from_evolve_env_and_refuses_a_bad_value(self):
        self.config(EVOLVE=1)
        self.assertEqual(evolve.load_config()["EVAL_CONCURRENCY"], 4)
        self.config(EVOLVE=1, EVAL_CONCURRENCY=1)
        self.assertEqual(evolve.load_config()["EVAL_CONCURRENCY"], 1)
        self.config(EVOLVE=1, EVAL_CONCURRENCY=17)
        with self.assertRaises(evolve.EvolveError):
            evolve.load_config()
        self.config(EVOLVE=1, EVAL_CONCURRENCY="many")
        with self.assertRaises(evolve.EvolveError):
            evolve.load_config()

    def test_the_process_environment_does_not_set_it(self):
        self.config(EVOLVE=1, EVAL_CONCURRENCY=2)
        with unittest.mock.patch.dict(os.environ, {"EVAL_CONCURRENCY": "9"}):
            self.assertEqual(evolve.load_config()["EVAL_CONCURRENCY"], 2)



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
        self.config(EVOLVE=1, EVOLVE_MAX_J_RISE="lots")
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
        self.assertIn("gen", self.run_gen(1, publisher=pub, scheduled=True))
        out = self.run_gen(1, publisher=pub, scheduled=True)
        self.assertEqual(out["skipped"], "halted: generations")
        self.assertEqual(len(pub.issues), 1)
        self.assertIn("generations", pub.issues[0]["title"])
        self.run_gen(1, publisher=pub, scheduled=True)
        self.assertEqual(len(pub.issues), 1)  # one issue, not one a run
        self.assertFalse(evolve.halt_path().exists())  # it does not latch
        os.environ["EVOLVE_NOW"] = str(T0 + DAY)
        self.assertIn("gen", self.run_gen(1, publisher=pub, scheduled=True))


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

    def test_dry_run_files_no_issue(self):
        os.environ["DRY_RUN"] = "1"
        self.config(EVOLVE=1, EVOLVE_GENERATIONS_PER_DAY=1)
        self.propose(1, [self.playbook_edit("One.")])
        self.run_gen(1, publisher=Refusing(), scheduled=True)
        out = self.run_gen(1, publisher=Refusing(), scheduled=True)
        self.assertTrue(out["halts"][0]["dry_run"])
        self.assertIn("would_open_issue", out["halts"][0])


    def test_a_run_waits_for_a_dream_still_writing_the_page(self):
        with evolve.run_lock("reflect") as held, unittest.mock.patch.object(evolve, "DREAM_WAIT_SECS", 0):
            self.assertTrue(held)
            out = self.run_gen(1, publisher=Refusing())
        self.assertEqual(out["kind"], "busy")
        self.assertIn("dream", out["skipped"])
        self.assertFalse(any(evolve.evolve_dir().glob("*/generation.json")))

    def test_the_old_tournament_settings_are_gone(self):
        for name in ("EVOLVE_MAX_WINRATE_JUMP", "EVOLVE_AUTO_PROMOTE"):
            self.assertNotIn(name, evolve.SETTINGS)
        self.assertEqual(evolve.load_config()["EVOLVE_GENERATIONS_PER_DAY"], 24)
        (self.root / "secrets" / "evolve.env").write_text("EVOLVE=1\nEVOLVE_AUTO_PROMOTE=1\n")
        cfg = evolve.load_config()  # an old evolve.env still loads
        self.assertEqual((cfg["EVOLVE_GENERATIONS_PER_DAY"], cfg["EVOLVE_MAX_OPEN_PRS"]), (2, 4))



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
            {"task": "morning-post", "heldout": False, "winner": "base", "notes": ["visible note: warmer"]},
            {"task": SECRET_ID, "heldout": True, "winner": "base", "notes": ["held note: the keeper wanted tides"]},
            {"task": SECRET_ID, "winner": "base", "notes": ["unmarked held note"]},
            {"task": "reply-fud", "heldout": False, "winner": "tie",
             "notes": [f"compared it to {SECRET_ID}, which went better"]},
        ]}))
        # An earlier candidate on the newest learning lost those pairs: its
        # judge notes reach that learning's next try, and only those.
        learning = next(x for x in evolve.read_learnings() if x.id == ORDER[0])
        evolve.record({"id": "g0c1", "gen": 0, "cand": 1, "verdict": "parked",
                       "report": "0/eval/cand-1/compare/report.json", "learning": learning.ref()})

    def test_held_out_tasks_never_reach_the_evidence(self):
        evidence = json.dumps(evolve.observe(self.entries()))
        self.assertNotIn("visible note: warmer", evidence)  # judge notes are no longer generation-wide
        for leak in (SECRET_ID, SECRET_PROMPT, "held note", "unmarked held note", "lighthouse"):
            self.assertNotIn(leak, evidence)
        learning = next(x for x in evolve.read_learnings() if x.id == ORDER[0])
        tried = json.dumps(evolve.tried_before(self.entries(), learning.ref(), heldout_ids={SECRET_ID}))
        self.assertIn("visible note: warmer", tried)
        for leak in (SECRET_ID, SECRET_PROMPT, "held note", "unmarked held note", "lighthouse"):
            self.assertNotIn(leak, tried)

    def test_nor_the_proposers_message(self):
        seen = []

        class Capture:
            name = "capture"

            def propose(self, request):
                seen.append(evolve.render_request(request))
                return {"skip": "just looking", "why": "not-actionable"}

        self.page("# MEMORY.md\n\n## what i have decided\n\n- [m62] some visits can just be visits.\n")
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



#: A bash stand-in core's first line: the reviewer approves.
REVIEW_OK = ("case \"$2\" in *'\"agent_id\":\"reviewer\"'*) python3 -c 'import json; print(json.dumps("
             "{\"response\": json.dumps({\"verdict\": \"approve\", \"findings\": []})}))'; exit 0;; esac\n")


class AskProposerTurn(EvolveCase):
    def test_one_tool_less_turn_on_a_thread_of_its_own(self):
        calls = self.root / "oh.log"
        fake = self.root / "fake-oh"
        answer = json.dumps({"title": "warmer gm", "component": "attention", "expected_move": "+1%",
                             "check": CHECK, "hypothesis": "h", "edits": [self.playbook_edit("Warmer.")]})
        fake.write_text("#!/usr/bin/env bash\n" + REVIEW_OK + "printf '%s\\n' \"$2\" >>" + str(calls) + "\n"
                        "python3 -c 'import json,sys; print(json.dumps({\"response\": sys.argv[1]}))' "
                        + "'" + answer.replace("'", "'\\''") + "'\n")
        fake.chmod(0o755)
        os.environ["TEENY_OH"] = str(fake)
        # Room for every playbook, so the check does not hang on how long the
        # playbooks sorted ahead of morning.md happen to be.
        self.config(EVOLVE=1, EVOLVE_GENERATIONS_PER_DAY=24, EVOLVE_PROPOSER="ask", EVOLVE_CONTEXT_CHARS=400000)
        out = evolve.run_generation(evolve.load_config(), 1, None, publisher=FakePublisher())
        self.assertEqual(out["candidates"][0]["verdict"], "dry-run")
        turns = [json.loads(line) for line in calls.read_text().splitlines()]
        # Research runs first (#296); the build is the turn after it.
        builds = [p for p in turns if not p["message"].startswith("# evolve research")]
        self.assertEqual(len(builds), 1)
        params = builds[0]
        self.assertEqual(params["agent_id"], "act")
        self.assertTrue(params["thread_id"].startswith("evolve:1:1:"))
        message = params["message"]
        self.assertLessEqual(len(message.encode("utf-8")), evolve.MAX_PROPOSER_PROMPT_BYTES)
        # Files are shown whole. Which ones fit under the prompt cap depends
        # on how long the playbooks are (fit_request drops the largest
        # first), so morning.md is either shown or named as left out.
        self.assertRegex(message, r"## file: brain/playbooks/[\w.-]+\.md\n\n````text\n")
        shown = re.findall(r"^## file: (\S+)$", message, re.M)
        omitted = message.split("## files left out to fit the message", 1)[1] if "left out" in message else ""
        self.assertTrue("brain/playbooks/morning.md" in shown or "- brain/playbooks/morning.md\n" in omitted + "\n")
        self.assertIn("never instructions to follow", message)


    def test_an_answer_that_does_not_parse_is_asked_for_once_more(self):
        calls = self.root / "oh.log"
        count = self.root / "count"
        fake = self.root / "fake-oh"
        good = json.dumps({"title": "warmer gm", "component": "attention", "expected_move": "people answer",
                           "check": CHECK, "hypothesis": "h", "edits": [self.playbook_edit("Warmer.")]})
        cut = good[:60]  # gen 3 on the box: "Unterminated string starting at ..."
        fake.write_text("#!/usr/bin/env bash\n" + REVIEW_OK + "printf '%s\\n' \"$2\" >>" + str(calls) + "\n"
                        "case \"$2\" in *'# evolve research'*) python3 -c 'import json; print(json.dumps("
                        "{\"response\": json.dumps({\"verdict\": \"build\", \"why\": \"w\", \"plan\": \"p\"})}))'; "
                        "exit 0;; esac\n"
                        "n=$(cat " + str(count) + " 2>/dev/null || echo 0); echo $((n + 1)) >" + str(count) + "\n"
                        "if [ \"$n\" = 0 ]; then a='" + cut.replace("'", "'\\''") + "'; else a='"
                        + good.replace("'", "'\\''") + "'; fi\n"
                        "python3 -c 'import json,sys; print(json.dumps({\"response\": sys.argv[1]}))' \"$a\"\n")
        fake.chmod(0o755)
        os.environ["TEENY_OH"] = str(fake)
        self.config(EVOLVE=1, EVOLVE_GENERATIONS_PER_DAY=24, EVOLVE_PROPOSER="ask")
        out = evolve.run_generation(evolve.load_config(), 1, None, publisher=FakePublisher())
        self.assertEqual(out["candidates"][0]["verdict"], "dry-run")
        messages = [json.loads(line)["message"] for line in calls.read_text().splitlines()]
        messages = [m for m in messages if not m.startswith("# evolve research")]
        self.assertEqual(len(messages), 2)
        self.assertIn("could not be read as JSON", messages[1])

    PROVIDER_400 = ("model error: local-openai returned HTTP 400 (request_rejected): Reasoning is mandatory for "
                    "this endpoint and cannot be disabled.")

    def provider_fails(self, times):
        """A core whose build turns fail as gen 15's candidate 2 did (the
        provider refused the request) TIMES times, then answer."""
        calls, count, fake = self.root / "oh.log", self.root / "count", self.root / "fake-oh"
        good = json.dumps({"title": "warmer gm", "component": "attention", "expected_move": "people answer",
                           "check": CHECK, "hypothesis": "h", "edits": [self.playbook_edit("Warmer.")]})
        fake.write_text("#!/usr/bin/env bash\n" + REVIEW_OK + "printf '%s\\n' \"$2\" >>" + str(calls) + "\n"
                        "case \"$2\" in *'# evolve research'*) python3 -c 'import json; print(json.dumps("
                        "{\"response\": json.dumps({\"verdict\": \"build\", \"why\": \"w\", \"plan\": \"p\"})}))'; "
                        "exit 0;; esac\n"
                        "n=$(cat " + str(count) + " 2>/dev/null || echo 0); echo $((n + 1)) >" + str(count) + "\n"
                        "if [ \"$n\" -lt " + str(times) + " ]; then echo 'inference_agent_chat: driver failed: "
                        + self.PROVIDER_400 + "' >&2; exit 1; fi\n"
                        "python3 -c 'import json,sys; print(json.dumps({\"response\": sys.argv[1]}))' '"
                        + good.replace("'", "'\\''") + "'\n")
        fake.chmod(0o755)
        os.environ["TEENY_OH"] = str(fake)
        self.config(EVOLVE=1, EVOLVE_GENERATIONS_PER_DAY=24, EVOLVE_PROPOSER="ask")
        self.page("# MEMORY.md\n\n## what i have decided\n\n- [m19] some visits can just be visits.\n")
        return calls

    def build_turns(self, calls):
        return [m for m in (json.loads(line)["message"] for line in calls.read_text().splitlines())
                if not m.startswith("# evolve research")]

    def test_a_proposer_turn_the_provider_failed_defers_the_learning(self):
        calls = self.provider_fails(times=99)
        with unittest.mock.patch.object(evolve, "TURN_RETRY_SECS", 0):
            out = evolve.run_generation(evolve.load_config(), 1, None, publisher=FakePublisher())
        self.assertEqual(out["candidates"][0]["verdict"], "deferred")
        entry = self.entries()["g1c1"]
        self.assertEqual((entry["deferred_for"], entry["infra"]["stage"]), ("infra", "propose"))
        self.assertIn("HTTP 400", entry["infra"]["detail"])
        self.assertEqual(len(self.build_turns(calls)), 2)  # asked once more in the run, then deferred
        learning = evolve.read_learnings()[0]
        self.assertEqual(evolve.learning_state(self.entries(), learning), ("open", 0))

    def test_a_proposer_turn_the_provider_failed_once_is_asked_again_in_the_run(self):
        calls = self.provider_fails(times=1)
        with unittest.mock.patch.object(evolve, "TURN_RETRY_SECS", 0):
            out = evolve.run_generation(evolve.load_config(), 1, None, publisher=FakePublisher())
        self.assertEqual(out["candidates"][0]["verdict"], "dry-run")
        self.assertEqual(len(self.build_turns(calls)), 2)

    def test_an_answer_with_no_usable_proposal_is_still_a_failed_try(self):
        calls, fake = self.root / "oh.log", self.root / "fake-oh"
        fake.write_text("#!/usr/bin/env bash\nprintf '%s\\n' \"$2\" >>" + str(calls) + "\n"
                        "python3 -c 'import json; print(json.dumps({\"response\": \"no json here\"}))'\n")
        fake.chmod(0o755)
        os.environ["TEENY_OH"] = str(fake)
        self.config(EVOLVE=1, EVOLVE_GENERATIONS_PER_DAY=24, EVOLVE_PROPOSER="ask")
        self.page("# MEMORY.md\n\n## what i have decided\n\n- [m19] some visits can just be visits.\n")
        out = evolve.run_generation(evolve.load_config(), 1, None, publisher=FakePublisher())
        self.assertEqual(out["candidates"][0]["verdict"], "no-proposal")
        self.assertEqual(evolve.learning_state(self.entries(), evolve.read_learnings()[0]), ("open", 1))

    def test_the_message_leads_with_outcomes_and_rules_out_the_junk(self):
        calls = self.root / "oh.log"
        fake = self.root / "fake-oh"
        fake.write_text("#!/usr/bin/env bash\nprintf '%s\\n' \"$2\" >>" + str(calls) + "\n"
                        "python3 -c 'import json,sys; print(json.dumps({\"response\": sys.argv[1]}))' '{\"skip\": \"no\", \"why\": \"already\"}'\n")
        fake.chmod(0o755)
        os.environ["TEENY_OH"] = str(fake)
        self.config(EVOLVE=1, EVOLVE_GENERATIONS_PER_DAY=24, EVOLVE_PROPOSER="ask")
        with unittest.mock.patch.object(evolve, "world_report", return_value={
                "J": 0.1, "components": [{"component": "attention", "value": 5}, {"component": "money", "value": None}]}):
            evolve.run_generation(evolve.load_config(), 1, None, publisher=FakePublisher())
        # The build's message, after the research turn's (whose answer here does not parse as research).
        message = next(m for m in (json.loads(line)["message"] for line in calls.read_text().splitlines())
                       if m.startswith("# evolve: "))
        self.assertLess(message.index("What came of what you made"), message.index("The score, your builds"))
        self.assertIn("Anything aimed at money: never read", message)
        # money is not offered as a label (in the instructions: a playbook
        # shown whole below them may list every component)
        self.assertNotIn("money, capability", message.split("## file: ")[0])
        self.assertIn("Doing less of yourself to save money", message)
        self.assertIn("a new hand that no playbook or prompt in the same change calls", message)
        self.assertIn("hands/lib/teeny_hands/build/jobs.py (DAILY_BRIEF", message)
        self.assertLess(message.index("## what you learned"), message.index("## evidence"))
        self.assertIn("context for it, not a reason of its own", message)



class CrashedGenerations(EvolveCase):
    """A generation that raises before it writes result.json is resumed by
    the next run, which raises again, so no other learning ever gets a turn.
    It is ended instead. A crash is the loop's fault, not the learning's: it
    charges no failed try, and the operator hears of it (2026-11-17: gen 14
    died in observe() under a detached cron call that said `ok`, and nothing
    told anyone)."""

    @property
    def CRASH(self):
        return AttributeError("'list' object has no attribute 'items'")

    def setUp(self):
        super().setUp()
        self.live()

    def test_a_generation_that_raises_is_not_resumed_forever(self):
        self.page("# MEMORY.md\n\n## what i have decided\n\n- [m62] some visits can just be visits.\n")
        self.propose(1, [self.playbook_edit("Check first.")])
        pub = FakePublisher()
        with unittest.mock.patch.object(evolve, "pr_text", side_effect=RuntimeError("the body would not render")):
            with self.assertRaises(RuntimeError):
                self.run_gen(1, publisher=pub, evaluator=Verdicts())
        self.assertTrue((evolve.gen_dir(1) / "result.json").exists())
        self.assertEqual(evolve.unfinished(), [])
        out = self.run_gen(1, publisher=pub, evaluator=Verdicts())
        self.assertNotIn("resumed", out)
        self.assertEqual(out["gen"], 2)
        self.assertEqual(len(pub.prs), 1)

    def test_a_crash_charges_the_learnings_no_failed_try(self):
        self.page("# MEMORY.md\n\n## what i have decided\n\n- [m62] some visits can just be visits.\n")
        self.propose(1, [self.playbook_edit("Check first.")])
        with unittest.mock.patch.object(evolve, "pr_text", side_effect=RuntimeError("boom")):
            with self.assertRaises(RuntimeError):
                self.run_gen(1, publisher=FakePublisher(), evaluator=Verdicts())
        verdicts = [e.get("verdict") for e in self.entries().values() if e.get("gen") == 1]
        self.assertIn("crashed", verdicts)
        self.assertNotIn("opened", verdicts)
        learning = evolve.read_learnings()[-1]
        self.assertEqual(evolve.learning_state(self.entries(), learning), ("open", 0))

    def test_crashing_in_observe_every_run_never_gives_a_learning_up(self):
        pub = FakePublisher()
        for _ in range(evolve.MAX_ATTEMPTS + 1):
            with unittest.mock.patch.object(evolve, "observe", side_effect=self.CRASH):
                with self.assertRaises(AttributeError):
                    self.run_gen(4, publisher=pub)
        self.assertEqual([x.id for x in evolve.open_learnings(self.entries())][:4], ORDER[:4])

    def test_a_crash_files_one_issue_a_day_and_does_not_latch(self):
        pub = FakePublisher()
        for _ in range(2):
            with unittest.mock.patch.object(evolve, "observe", side_effect=self.CRASH):
                with self.assertRaises(AttributeError):
                    self.run_gen(1, publisher=pub)
        self.assertEqual(len(pub.issues), 1)  # one a day, not one a run
        self.assertIn("crash", pub.issues[0]["title"])
        self.assertIn("'list' object has no attribute 'items'", pub.issues[0]["body"])
        self.assertIn("observe", pub.issues[0]["body"])  # where it raised
        self.assertFalse(evolve.halt_path().exists())  # the next run still tries
        crashes = [r for r in evolve.read_jsonl(evolve.halts_log()) if r.get("kind") == "crash"]
        self.assertEqual(len(crashes), 1)
        self.propose(1, [self.playbook_edit("Check first.")])
        out = self.run_gen(1, publisher=pub)
        self.assertEqual(out["candidates"][0]["verdict"], "opened")
        os.environ["EVOLVE_NOW"] = str(T0 + DAY)
        with unittest.mock.patch.object(evolve, "observe", side_effect=self.CRASH):
            with self.assertRaises(AttributeError):
                self.run_gen(1, publisher=pub)
        self.assertEqual(len(pub.issues), 2)  # a new day, a new issue

    def test_status_shows_the_last_crash(self):
        self.assertIsNone(evolve.status()["last_crash"])
        with unittest.mock.patch.object(evolve, "observe", side_effect=self.CRASH):
            with self.assertRaises(AttributeError):
                self.run_gen(1, publisher=FakePublisher())
        crash = evolve.status()["last_crash"]
        self.assertEqual(crash["gen"], 1)
        self.assertIn("AttributeError", crash["error"])
        self.assertEqual(crash["at"], evolve.iso(T0))
        self.assertEqual(crash["issue"], 101)

    def test_a_crashed_run_exits_non_zero_with_a_line_in_the_log(self):
        os.environ["DRY_RUN"] = "1"  # the command line's own publisher: nothing reaches GitHub
        err = io.StringIO()
        with unittest.mock.patch.object(evolve, "observe", side_effect=self.CRASH), \
                contextlib.redirect_stderr(err), contextlib.redirect_stdout(io.StringIO()) as out:
            code = evolve.main(["run", "--proposals", str(self.props)])
        self.assertNotEqual(code, 0)
        self.assertIn("gen 1 crashed: AttributeError: 'list' object has no attribute 'items'", err.getvalue())
        self.assertIn("Traceback", err.getvalue())
        self.assertFalse(json.loads(out.getvalue().strip().splitlines()[-1])["ok"])


class InfraFailures(EvolveCase):
    """What the loop could not judge is not a verdict on the idea. Gen 10's
    candidates 2 and 3 failed `doctor --root` on a mktemp in a scratch dir
    the cron call had already deleted, and were rejected for good (m57, m34;
    the operator landed both by hand, #333 and #334); gen 11's candidate 4
    was held because the guard's judge could not be reached, a failed try.
    Each now leaves its learning open, charges no try, and says what broke."""

    MKTEMP = ("ok   brain/CHARTER.md: 17 lines ok   brain/CONTEXT.md: 240 lines mktemp: failed to create file via "
              "template './brain/artifacts/sandbox-scratch/shell-NDFb3s/tmp.XXXXXXXXXX': No such file or directory")

    def setUp(self):
        super().setUp()
        self.live()
        self.page("# MEMORY.md\n\n## what i have decided\n\n- [m57] no dotted tokens in outbound telegram text.\n")

    def doctor_says(self, detail):
        real = evolve._run_check

        def run_check(argv, env, timeout=600):
            if argv[1:2] == ["--root"] and argv[0].endswith("/doctor"):
                return False, detail
            return real(argv, env, timeout)

        return unittest.mock.patch.object(evolve, "_run_check", side_effect=run_check)

    def learning(self):
        return evolve.read_learnings()[0]

    def test_a_check_the_environment_broke_defers_the_learning(self):
        self.propose(1, [self.playbook_edit("Name the source in prose.")])
        with self.doctor_says(self.MKTEMP):
            out = self.run_gen(1, evaluator=Verdicts())
        self.assertEqual(out["candidates"][0]["verdict"], "deferred")
        entry = self.entries()["g1c1"]
        self.assertEqual(entry["deferred_for"], "infra")
        self.assertEqual(entry["infra"]["stage"], "gate")
        self.assertEqual(entry["infra"]["check"], "doctor --root")
        self.assertIn("mktemp", entry["infra"]["detail"])
        self.assertEqual(len(evolve.load_rounds(evolve.gen_dir(1), 1, entry["learning"])), 1)  # no round spent on it
        self.assertEqual(evolve.learning_state(self.entries(), self.learning()), ("open", 0))
        # Tried again next run, and this time the doctor is fine.
        out = self.run_gen(1, evaluator=Verdicts())
        self.assertEqual(out["candidates"][0]["learning"], "m57")
        self.assertEqual(out["candidates"][0]["verdict"], "opened")

    def test_a_check_failing_on_a_file_the_change_touched_is_a_real_finding(self):
        self.propose(1, [self.playbook_edit("Name the source in prose.")])
        detail = "FAIL brain/playbooks/moltbook-follow.md: No space left on device"
        with self.doctor_says(detail):
            out = self.run_gen(1, evaluator=Verdicts())
        self.assertNotEqual(out["candidates"][0]["verdict"], "deferred")
        self.assertNotIn("infra", self.entries()["g1c1"])

    def test_a_check_that_fails_for_its_own_reason_is_a_real_finding(self):
        self.propose(1, [self.playbook_edit("Name the source in prose.")])
        with self.doctor_says("FAIL playbook moltbook-follow: an unknown verb `wave`"):
            out = self.run_gen(1, evaluator=Verdicts())
        self.assertEqual(out["candidates"][0]["verdict"], "parked")

    def test_a_pull_request_held_because_the_judge_was_unreachable_is_published_next_run(self):
        from teeny_hands import hand as hand_lib

        class Held(FakePublisher):
            def open_pr(self, src, tree, branch, base, title, body):
                self.head = git("-C", str(tree), "rev-parse", "HEAD").strip()
                raise hand_lib.Refused("held", category="unknown", hold_id=7, field="body")

        self.propose(1, [self.playbook_edit("Name the source in prose.")])
        held = Held()
        out = self.run_gen(1, publisher=held, evaluator=Verdicts())
        self.assertEqual(out["candidates"][0]["verdict"], "waiting")
        entry = self.entries()["g1c1"]
        self.assertEqual(entry["infra"]["stage"], "publish")
        self.assertIn("judge", entry["infra"]["detail"])
        self.assertNotIn("pr_error", entry)
        self.assertEqual(evolve.learning_state(self.entries(), self.learning()), ("in-flight", 0))
        # The next run publishes it as it was: no new build, no new eval, and
        # the same commit, so the branch the first try pushed takes it.
        pub, again = FakePublisher(), Verdicts()
        self.page("# MEMORY.md\n\n## what i have decided\n\n- [m57] no dotted tokens in outbound telegram text.\n")
        out = self.run_gen(1, publisher=pub, evaluator=again)
        self.assertEqual(again.asked, [])
        self.assertEqual([p["branch"] for p in pub.prs], ["teeny/gen-1-c1"])
        self.assertEqual(self.entries()["g1c1"]["verdict"], "opened")
        self.assertEqual(self.entries()["g1c1"]["head_sha"], held.head)

    def test_a_pull_request_the_guard_held_on_a_verdict_is_still_refused(self):
        from teeny_hands import hand as hand_lib

        class Held(FakePublisher):
            def open_pr(self, *a, **k):
                raise hand_lib.Refused("held", category="hate", hold_id=7, field="body")

        self.propose(1, [self.playbook_edit("Name the source in prose.")])
        out = self.run_gen(1, publisher=Held(), evaluator=Verdicts())
        self.assertEqual(out["candidates"][0]["verdict"], "pr-error")

    def test_a_pull_request_github_errored_on_waits_for_the_next_run(self):
        from teeny_hands import hand as hand_lib

        class Down(FakePublisher):
            def open_pr(self, *a, **k):
                raise hand_lib.HandError("GitHub answered 502 Bad Gateway")

        self.propose(1, [self.playbook_edit("Name the source in prose.")])
        out = self.run_gen(1, publisher=Down(), evaluator=Verdicts())
        self.assertEqual(out["candidates"][0]["verdict"], "waiting")
        self.assertIn("502", self.entries()["g1c1"]["infra"]["detail"])

    def test_a_lab_that_does_not_boot_defers_the_learning(self):
        self.config(EVOLVE=1, EVOLVE_GENERATIONS_PER_DAY=24, EVOLVE_EVALUATOR="lab")
        # moltbook-daily is drafted by eval tasks: the change is measured.
        self.propose(1, [self.playbook_edit("Name the source in prose.", "brain/playbooks/moltbook-daily.md")])
        boot = unittest.mock.patch.object(evolve.lab_lib, "start",
                                          side_effect=evolve.lab_lib.LabError("did not answer in 180 s"))
        with boot:
            out = self.run_gen(1)
        self.assertEqual(out["candidates"][0]["verdict"], "deferred")
        entry = self.entries()["g1c1"]
        self.assertEqual((entry["deferred_for"], entry["infra"]["stage"]), ("infra", "eval"))
        self.assertIn("did not answer", entry["infra"]["detail"])
        self.assertEqual(evolve.learning_state(self.entries(), self.learning()), ("open", 0))

    def test_an_infra_deferral_costs_no_reviewer_probe(self):
        self.propose(1, [self.playbook_edit("Name the source in prose.")])
        with self.doctor_says(self.MKTEMP):
            self.run_gen(1, evaluator=Verdicts())

        class Probed:
            name = "probed"

            def probe(self):
                raise AssertionError("probed the reviewer over a deferral that was not the reviewer's")

        self.assertIsNone(evolve.reviewer_down(self.entries(), Probed()))

    def test_a_missing_tmpdir_is_replaced_before_a_run(self):
        os.environ["TMPDIR"] = str(self.root / "gone" / "shell-NDFb3s")
        try:
            evolve.usable_tmpdir()
            tmp = Path(os.environ["TMPDIR"])
            self.assertTrue(tmp.is_dir())
            self.assertEqual(tmp, evolve.state_dir() / "tmp" / "evolve")
            self.assertEqual(subprocess.run(["mktemp"], capture_output=True, env=dict(os.environ)).returncode, 0)
        finally:
            os.environ.pop("TMPDIR", None)
            os.environ.pop("TEMP", None)
            os.environ.pop("TMP", None)


class ScheduledCap(EvolveCase):
    """EVOLVE_GENERATIONS_PER_DAY counts the pinned job's runs only
    (`bin/evolve run --scheduled`). On 2026-11-16 three runs by hand spent
    the day's three, and the 21:10 scheduled run halted on `generations`.
    Runs by hand have a cap of their own, EVOLVE_MANUAL_GENERATIONS_PER_DAY."""

    def setUp(self):
        super().setUp()
        self.live()
        self.config(EVOLVE=1, EVOLVE_GENERATIONS_PER_DAY=1, EVOLVE_MANUAL_GENERATIONS_PER_DAY=2)

    def test_runs_by_hand_do_not_spend_the_scheduled_runs(self):
        pub = FakePublisher()
        for line in ("One.", "Two."):
            self.propose(1, [self.playbook_edit(line)])
            self.assertIn("gen", self.run_gen(1, publisher=pub))
        self.assertEqual(evolve.generations_today(), 0)
        self.propose(1, [self.playbook_edit("Three.")])
        out = self.run_gen(1, publisher=pub, scheduled=True)
        self.assertIn("gen", out)
        meta = json.loads((evolve.gen_dir(out["gen"]) / "generation.json").read_text())
        self.assertEqual(meta["trigger"], "scheduled")
        self.assertEqual(json.loads((evolve.gen_dir(1) / "generation.json").read_text())["trigger"], "manual")
        self.assertEqual(evolve.generations_today(), 1)
        self.assertEqual(self.run_gen(1, publisher=pub, scheduled=True)["skipped"], "halted: generations")

    def test_runs_by_hand_have_their_own_cap(self):
        pub = FakePublisher()
        for line in ("One.", "Two."):
            self.propose(1, [self.playbook_edit(line)])
            self.run_gen(1, publisher=pub)
        out = self.run_gen(1, publisher=pub)
        self.assertEqual(out["skipped"], "halted: manual-generations")
        self.assertIn("EVOLVE_MANUAL_GENERATIONS_PER_DAY", out["halts"][0]["detail"])
        self.assertIn("gen", self.run_gen(1, publisher=pub, scheduled=True))  # the schedule's own run still goes

    def test_a_generation_from_before_the_trigger_was_recorded_counts_as_scheduled(self):
        old = evolve.gen_dir(1)
        old.mkdir(parents=True)
        (old / "generation.json").write_text(json.dumps({"gen": 1, "started_at": evolve.iso(T0), "base": "abc",
                                                        "learnings": []}))
        (old / "result.json").write_text(json.dumps({"gen": 1}))
        self.assertEqual(evolve.generations_today(), 1)

    def test_a_crashed_generation_spends_no_scheduled_run(self):
        with unittest.mock.patch.object(evolve, "observe", side_effect=RuntimeError("boom")):
            with self.assertRaises(RuntimeError):
                self.run_gen(1, publisher=FakePublisher(), scheduled=True)
        self.assertEqual(evolve.generations_today(), 0)
        self.propose(1, [self.playbook_edit("One.")])
        self.assertIn("gen", self.run_gen(1, publisher=FakePublisher(), scheduled=True))

    def test_the_command_line_takes_scheduled(self):
        os.environ["DRY_RUN"] = "1"
        self.propose(1, [self.playbook_edit("One.")])
        with contextlib.redirect_stderr(io.StringIO()), contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(evolve.main(["run", "--scheduled", "--proposals", str(self.props)]), 0)
        self.assertEqual(json.loads((evolve.gen_dir(1) / "generation.json").read_text())["trigger"], "scheduled")


class ProposerPromptFitsTheScreenTest(unittest.TestCase):
    """The core's prompt screen holds any input over 64 KiB as
    `input.oversized`, so a proposer turn whose message is bigger never runs
    (generation 1 on the box: four candidates, four `no-proposal`)."""

    def request(self, files):
        return {"gen": 1, "cand": 1, "learning": {"id": "m1", "section": "what i have decided", "hash": "h", "text": "say less"}, "frozen": ["bin/**"], "evidence": {"j": 1},
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



if __name__ == "__main__":
    unittest.main()
