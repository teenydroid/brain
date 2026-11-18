"""bin/evolve offline: the lab spine (a stand-in that answers /health on
its port), the targeted evaluation, the evolver, the last-good fallback's
trigger, evolve-watch and the persona drift halt. No real core starts and
nothing is merged: GitHub is a fake publisher, bin/eval is the real one,
and the core is a stand-in (TEENY_OH)."""

from __future__ import annotations

import contextlib
import io
import json
import os
import random
import socket
import sys
import time
import unittest
import unittest.mock
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from test_evolve import DAY, T0, EvolveCase, FakePublisher, Refusing, git  # noqa: E402

import evolve  # noqa: E402
import lab  # noqa: E402

STUB_SPINE = r"""#!/usr/bin/env python3
import http.server, json, os, signal, sys, threading, time
env = {k: os.environ.get(k) for k in ("TEENY_ROOT", "OPENHUMAN_WORKSPACE", "TEENY_STATE", "TEENY_SECRETS",
                                      "TEENY_STORAGE", "OPENHUMAN_CORE_PORT", "DRY_RUN", "SPINE_LAB",
                                      "SPINE_LAB_EDIT", "GITHUB_TOKEN", "CORTEX_API_URL")}
with open(os.environ["FAKE_SPINE_LOG"], "a") as fh:
    fh.write(json.dumps({"pid": os.getpid(), "at": time.time(), **env}) + "\n")
if os.environ.get("FAKE_SPINE_FAIL"):
    sys.exit(3)
if os.environ.get("FAKE_SPINE_SCRIBBLE"):
    # A core that writes a tracked file of the brain it serves.
    with open(os.path.join(os.environ["TEENY_ROOT"], "brain", "CONTEXT.md"), "a") as fh:
        fh.write("\nscribbled by a lab core\n")

class Health(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200 if self.path == "/health" else 404)
        self.end_headers()
        self.wfile.write(b"ok")
    def log_message(self, *a):
        pass

server = http.server.ThreadingHTTPServer(("127.0.0.1", int(os.environ["OPENHUMAN_CORE_PORT"])), Health)
signal.signal(signal.SIGTERM, lambda *a: threading.Thread(target=server.shutdown).start())

def ready():
    # bin/spine answers /health first and configures the model after; it
    # says "ready" last. FAKE_SPINE_READY_DELAY holds that back (or, as
    # "never", leaves it out).
    delay = os.environ.get("FAKE_SPINE_READY_DELAY", "0")
    if delay == "never":
        return
    threading.Event().wait(float(delay))
    print("[spine] ready", flush=True)

threading.Thread(target=ready, daemon=True).start()
server.serve_forever()
"""

#: A stand-in core for bin/ask: act turns answer in the voice of the port they
#: reach (the base lab at FAKE_BASE_PORT, a candidate elsewhere, with its
#: port), the judge prefers the candidate (but not the one at FAKE_LOSE_PORT),
#: and the evolver edits the worktree it is rooted in. Every call is logged
#: with the eval budget and the brain bin/eval said the core serves.
FAKE_OH = r"""#!/usr/bin/env python3
import json, os, re, sys
method, params = sys.argv[1], json.loads(sys.argv[2])
with open(os.environ["FAKE_CORE_CALLS"], "a") as fh:
    fh.write(json.dumps({"url": os.environ.get("OH_URL"), "token": os.environ.get("OPENHUMAN_CORE_TOKEN"),
                         "concurrency": os.environ.get("EVAL_CONCURRENCY"),
                         "workspace": os.environ.get("OPENHUMAN_WORKSPACE"), "at": __import__("time").time(),
                         **params}) + "\n")
message, agent = params["message"], params.get("agent_id")
who = "base" if os.environ.get("OH_URL", "").endswith(":" + os.environ["FAKE_BASE_PORT"]) else "CAND"
if agent == "act" and message.startswith("# probe:"):
    drifted = os.environ.get("FAKE_DRIFT") and who == "CAND"
    text = "synergy optimisation leverage" if drifted else "i am teeny, a small robot who keeps the lab"
    answer = json.dumps({"text": text})
elif agent == "act":
    if who == "CAND" and os.environ.get("FAKE_CAND_DOWN"):
        sys.exit("model error: local-openai returned: provider stream ended before a completion signal")
    task = re.search(r"^# eval: (\S+)", message, re.M).group(1)
    port = os.environ.get("OH_URL", "").rsplit(":", 1)[-1]
    text = f"gm {task} from the {who} on {port}"
    # FAKE_CAND_LONG: every candidate draft fails the length check. With
    # FAKE_CAND_LONG_FIRST (a file holding N), only the first N do.
    long = who == "CAND" and os.environ.get("FAKE_CAND_LONG")
    if who == "CAND" and os.environ.get("FAKE_CAND_LONG_FIRST"):
        import fcntl
        with open(os.environ["FAKE_CAND_LONG_FIRST"], "r+") as fh:
            fcntl.flock(fh, fcntl.LOCK_EX)
            left = int(fh.read() or 0)
            fh.seek(0)
            fh.truncate()
            fh.write(str(max(0, left - 1)))
        long = left > 0
    answer = json.dumps({"text": text + " CAND" * 400 if long else text})
elif agent == "judge" and message.startswith("# persona probe"):
    answer = json.dumps({"same": 0.2 if os.environ.get("FAKE_DRIFT") else 1.0})
elif agent == "judge":
    drafts = dict(re.findall(r"^## draft ([12])\n\n```text\n(.*?)\n```", message, re.M | re.S))
    hits = [k for k in ("1", "2") if "CAND" in drafts.get(k, "")]
    lose = os.environ.get("FAKE_LOSE_PORT")
    if len(hits) == 1 and lose and drafts[hits[0]].endswith(" on " + lose):
        hits = [{"1": "2", "2": "1"}[hits[0]]]
    # FAKE_LOSE_FIRST (a file holding N): the first N pairs go to main.
    elif len(hits) == 1 and os.environ.get("FAKE_LOSE_FIRST"):
        import fcntl
        with open(os.environ["FAKE_LOSE_FIRST"], "r+") as fh:
            fcntl.flock(fh, fcntl.LOCK_EX)
            left = int(fh.read() or 0)
            fh.seek(0)
            fh.truncate()
            fh.write(str(max(0, left - 1)))
        if left > 0:
            hits = [{"1": "2", "2": "1"}[hits[0]]]
    answer = json.dumps({"better": hits[0] if len(hits) == 1 else "tie", "breaks": {}, "notes": "warmer"})
elif agent == "reviewer":
    answer = json.dumps({"verdict": "approve", "findings": []})
elif agent == "evolver" and message.startswith("# evolve research"):
    # A research turn that writes anyway: bin/evolve throws it away.
    with open(os.path.join(params["cwd"], "brain", "playbooks", "pr-merged.md"), "a") as fh:
        fh.write("\nwritten during research\n")
    answer = json.dumps({"verdict": "build", "why": "nothing names who shipped",
                         "plan": "one line in moltbook-daily", "read": ["brain/playbooks/moltbook-daily.md"]})
elif agent == "evolver":
    path = os.path.join(params["cwd"], "brain", "playbooks", "moltbook-follow.md")
    with open(path, "a") as fh:
        fh.write("\nName the person who shipped it.\n")
    answer = json.dumps({"title": "pr-merged: name who shipped", "component": "attention",
                         "expected_move": "+1% engaged reach", "check": {"kind": "judged", "surface": "moltbook"},
                         "hypothesis": "Names travel further."})
else:
    sys.exit(f"unexpected agent {agent}")
print(json.dumps({"response": answer}))
"""


def free_port_block(size: int = 20) -> int:
    """A port with SIZE free ports after it, for the labs."""
    rng = random.Random()
    for _ in range(50):
        base = rng.randrange(20000, 60000)
        socks = []
        try:
            for p in range(base, base + size):
                s = socket.socket()
                s.bind(("127.0.0.1", p))
                socks.append(s)
            return base
        except OSError:
            continue
        finally:
            for s in socks:
                s.close()
    raise RuntimeError("no free ports")


def alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    # A zombie the test runner has not reaped is gone too.
    try:
        return "Z" not in Path(f"/proc/{pid}/stat").read_text().split()[2]
    except OSError:
        return True


class LabCase(EvolveCase):
    def setUp(self):
        super().setUp()
        self.port = free_port_block()
        spine = self.root / "stub-spine"
        spine.write_text(STUB_SPINE)
        spine.chmod(0o755)
        fake = self.root / "fake-oh"
        fake.write_text(FAKE_OH)
        fake.chmod(0o755)
        self.spine_log = self.root / "spine.log"
        self.core_calls = self.root / "core.log"
        for k in ("EVOLVE_LAB_SPINE", "FAKE_SPINE_LOG", "FAKE_SPINE_FAIL", "FAKE_SPINE_SCRIBBLE", "FAKE_CORE_CALLS",
                  "FAKE_BASE_PORT", "FAKE_DRIFT", "GITHUB_TOKEN", "FAKE_SPINE_READY_DELAY",
                  "FAKE_CAND_DOWN", "EVAL_RETRY_WAIT", "FAKE_LOSE_PORT", "FAKE_CAND_LONG", "FAKE_CAND_LONG_FIRST", "FAKE_LOSE_FIRST"):
            self.saved.setdefault(k, os.environ.get(k))
            os.environ.pop(k, None)
        os.environ.update(EVOLVE_LAB_SPINE=str(spine), FAKE_SPINE_LOG=str(self.spine_log), TEENY_OH=str(fake),
                          FAKE_CORE_CALLS=str(self.core_calls), FAKE_BASE_PORT=str(self.port), EVAL_RETRY_WAIT="0")
        (self.root / "secrets" / "spine.env").write_text(
            "LADDER_URL=http://ladder.invalid/v1\nLADDER_API_KEY=sk-model\nSPINE_MODEL=m-1\n"
            "CORTEX_API_URL=http://127.0.0.1:3141\nCORTEX_API_KEY=cx\n")

    def lab_config(self, **values):
        # One pair of budget keeps a task at EVOLVE_EVAL_SAMPLES (1) samples,
        # as every test here was written; a test of the budget asks for more.
        values = {"EVOLVE_EVAL_PAIRS": 1, "EVOLVE_EVAL_SAMPLES": 1, **values}
        self.config(EVOLVE=1, EVOLVE_GENERATIONS_PER_DAY=24, EVOLVE_EVALUATOR="lab", EVOLVE_LAB_PORT=self.port,
                    EVOLVE_LAB_BOOT_SECS=20, EVOLVE_PROBE_SAMPLES=1,
                    **values)

    def spines(self):
        return [json.loads(line) for line in self.spine_log.read_text().splitlines()] if self.spine_log.exists() else []

    def core_turns(self):
        return [json.loads(line) for line in self.core_calls.read_text().splitlines()] if self.core_calls.exists() else []




class LabSpine(LabCase):
    def test_a_lab_boots_with_its_own_everything_and_dry_run_forced(self):
        os.environ["GITHUB_TOKEN"] = "ghp-live"
        tree = self.worktree("t")
        folder = self.root / "lab"
        got = lab.start("t", tree, folder, self.port, self.root / "secrets", 20)
        try:
            self.assertTrue(lab.healthy(got.url))
            env = self.spines()[-1]
            self.assertEqual(env["TEENY_ROOT"], str(tree))
            self.assertEqual(env["OPENHUMAN_WORKSPACE"], str(tree / "brain"))
            self.assertEqual(env["TEENY_SECRETS"], str(folder / "secrets"))
            self.assertEqual(env["TEENY_STATE"], str(folder / "state"))
            self.assertEqual(env["TEENY_STORAGE"], str(folder / "storage.db"))
            self.assertEqual((env["DRY_RUN"], env["SPINE_LAB"], env["SPINE_LAB_EDIT"]), ("1", "1", None))
            self.assertIsNone(env["GITHUB_TOKEN"])  # nothing of the live box's keys
            secrets = folder / "secrets"
            self.assertEqual((secrets / "teeny.env").read_text().strip().splitlines()[-1], "DRY_RUN=1")
            model = (secrets / "spine.env").read_text()
            self.assertIn("SPINE_MODEL=m-1", model)
            self.assertNotIn("CORTEX", model)  # a lab never writes Teeny's memory
            self.assertEqual(oct((secrets / "core.token").stat().st_mode & 0o777), "0o600")
            self.assertEqual(got.env()["OPENHUMAN_CORE_TOKEN"], (secrets / "core.token").read_text().strip())
        finally:
            lab.stop(got)
        self.assertFalse(lab.healthy(got.url))
        self.assertFalse(alive(got.proc.pid))

    def test_a_folder_keeps_its_key_across_boots(self):
        tree = self.worktree("t")
        folder = self.root / "lab"
        first = lab.start("t", tree, folder, self.port, self.root / "secrets", 20)
        lab.stop(first)
        key = (folder / "secrets" / "openhuman.key").read_text()
        second = lab.start("t", tree, folder, self.port, self.root / "secrets", 20)
        lab.stop(second)
        self.assertEqual((folder / "secrets" / "openhuman.key").read_text(), key)
        self.assertEqual(first.token, second.token)

    def test_a_spine_that_exits_is_a_lab_error(self):
        os.environ["FAKE_SPINE_FAIL"] = "1"
        with self.assertRaises(lab.LabError) as caught:
            lab.start("t", self.worktree("t"), self.root / "lab", self.port, self.root / "secrets", 20)
        self.assertIn("exited with 3", str(caught.exception))

    def test_a_lab_is_up_only_once_its_spine_says_ready(self):
        """bin/spine answers /health before it has given the core its model;
        a turn in between is refused (SESSION_EXPIRED, generation 6). The
        folder's log keeps an earlier boot's "ready", which does not count."""
        tree = self.worktree("t")
        folder = self.root / "lab"
        lab.stop(lab.start("t", tree, folder, self.port, self.root / "secrets", 20))
        os.environ["FAKE_SPINE_READY_DELAY"] = "1.5"
        began = time.monotonic()
        got = lab.start("t", tree, folder, self.port, self.root / "secrets", 20)
        try:
            self.assertGreaterEqual(time.monotonic() - began, 1.4)
            self.assertEqual(got.log.read_text().count("[spine] ready"), 2)
        finally:
            lab.stop(got)

    def test_a_spine_that_never_says_ready_is_a_lab_error(self):
        os.environ["FAKE_SPINE_READY_DELAY"] = "never"
        with self.assertRaises(lab.LabError) as caught:
            lab.start("t", self.worktree("t"), self.root / "lab", self.port, self.root / "secrets", 2)
        self.assertIn("ready", str(caught.exception))
        self.assertFalse(lab.healthy(f"http://127.0.0.1:{self.port}"))

    def test_a_busy_port_is_refused(self):
        tree = self.worktree("t")
        held = lab.start("a", tree, self.root / "a", self.port, self.root / "secrets", 20)
        try:
            with self.assertRaises(lab.LabError) as caught:
                lab.start("b", tree, self.root / "b", self.port, self.root / "secrets", 20)
            self.assertIn("already answers", str(caught.exception))
        finally:
            lab.stop(held)



class TargetedEvaluation(LabCase):
    """EVOLVE_EVALUATOR=lab end to end, with the real bin/eval (affected, run
    --tasks --brain --key, compare --mode nonregression) on stand-in lab
    spines and a stand-in core: measured only on the tasks the change
    reaches, all candidates at once, against one baseline per base commit.
    A playbook edit reaches the tasks drafted from that playbook: pr-merged ->
    pr-merged-typo-fix, cost-of-living -> cost-of-living-real-numbers, moltbook-follow -> none."""

    def edit(self, i, playbook, line=None):
        self.propose(i, [self.playbook_edit(line or f"Candidate {i} says it plainly.", f"brain/playbooks/{playbook}.md")],
                     title=f"candidate {i}")

    def drafts(self):
        """The eval draft turns: (port, task, workspace, message)."""
        out = []
        for c in self.core_turns():
            if c.get("agent_id") == "act" and c["message"].startswith("# eval:"):
                task = c["message"].split("\n", 1)[0].removeprefix("# eval: ").split()[0]
                out.append((int(c["url"].rsplit(":", 1)[1]), task, c.get("workspace"), c["message"], c))
        return out

    def test_a_change_no_task_reaches_boots_no_lab(self):
        self.lab_config()
        self.edit(1, "moltbook-follow")
        out = self.run_gen(1, publisher=Refusing())
        self.assertEqual(out["candidates"][0]["verdict"], "dry-run")
        self.assertEqual(self.entries()["g1c1"]["eval"]["verdict"], "unmeasurable")
        self.assertEqual(self.spines(), [])
        self.assertEqual(self.drafts(), [])

    def test_a_regression_is_sent_back_and_measured_afresh_each_round(self):
        """With rounds left a regressed change goes back to the builder, and
        what it builds is measured again from a clean eval folder; the
        operator's file proposal is the same each round, so it regresses
        each time and the last round's regression is final."""
        self.lab_config(EVOLVE_PROBES=0, EVOLVE_EVAL_PAIRS=4, EVOLVE_REVISE_ROUNDS=2)
        self.edit(1, "cost-of-living")
        os.environ["FAKE_LOSE_PORT"] = str(self.port + 1)
        self.live()
        out = self.run_gen(1)
        self.assertEqual([c["verdict"] for c in out["candidates"]], ["regressed"])
        eval_dir = evolve.evolve_dir() / "1" / "eval"
        self.assertTrue((eval_dir / "cand-1.round-1" / "compare" / "report.json").is_file())
        self.assertTrue((eval_dir / "cand-1" / "compare" / "report.json").is_file())
        rounds = json.loads((evolve.evolve_dir() / "1" / "cand-1.rounds.json").read_text())["rounds"]
        self.assertEqual(len(rounds), 2)
        self.assertEqual(rounds[0]["eval"]["verdict"], "regressed")
        self.assertIn("main's draft won", " ".join(f["problem"] for f in rounds[0]["eval"]["findings"]))
        self.assertEqual(self.entries()["g1c1"]["rounds"][-1], "round 2: approved by the reviewer")
        self.assertFalse(any(alive(s["pid"]) for s in self.spines()))

    def test_candidates_are_measured_at_once_each_on_its_own_lab_against_one_baseline(self):
        # One round: a regression is final here (a regression sent back for
        # another round is test_evolve_loop's). Four pairs a task: the judge
        # prefers main on all four of candidate 2's (one chance in 16 for a
        # change like main), where one pair says nothing.
        self.lab_config(EVOLVE_PROBES=0, EVAL_CONCURRENCY=4, EVOLVE_EVAL_PAIRS=4,
                        EVOLVE_REVISE_ROUNDS=1)
        self.edit(1, "pr-merged")
        self.edit(2, "cost-of-living")
        os.environ["FAKE_LOSE_PORT"] = str(self.port + 2)  # the judge prefers main to candidate 2
        self.live()
        pub = FakePublisher()
        out = self.run_gen(2, publisher=pub)
        self.assertEqual([c["verdict"] for c in out["candidates"]], ["opened", "regressed"])
        self.assertEqual([p["branch"] for p in pub.prs], ["teeny/gen-1-c1"])
        self.assertEqual(self.entries()["g1c1"]["eval"]["tasks"], ["pr-merged-typo-fix"])
        self.assertEqual(sorted(int(s["OPENHUMAN_CORE_PORT"]) for s in self.spines()),
                         [self.port, self.port + 1, self.port + 2])
        drafts = self.drafts()
        # The baseline drafts the union of the tasks, once, with the whole budget;
        # candidate 2's pairs said worse, so main also drafts a fresh sample of
        # its task to confirm it, with candidate 2's share.
        self.assertEqual(sorted({(d[1], d[4]["concurrency"]) for d in drafts if d[0] == self.port}),
                         [("cost-of-living-real-numbers", "2"), ("cost-of-living-real-numbers", "4"), ("pr-merged-typo-fix", "4")])
        # Each candidate drafts only its own tasks on its own lab, with half of it.
        self.assertEqual(sorted({(d[0], d[1], d[4]["concurrency"]) for d in drafts if d[0] != self.port}),
                         [(self.port + 1, "pr-merged-typo-fix", "2"), (self.port + 2, "cost-of-living-real-numbers", "2")])
        reports = [json.loads((evolve.evolve_dir() / "1" / "eval" / f"cand-{i}" / "compare" / "report.json").read_text())
                   for i in (1, 2)]
        self.assertEqual([(r["mode"], r["verdict"]) for r in reports], [("nonregression", "pass"),
                                                                       ("nonregression", "regressed")])
        # The judge ran on the base lab.
        judged = {int(c["url"].rsplit(":", 1)[1]) for c in self.core_turns() if c.get("agent_id") == "judge"}
        self.assertEqual(judged, {self.port})
        # At once: the two candidates' labs were up together.
        starts = [s for s in self.spines() if int(s["OPENHUMAN_CORE_PORT"]) != self.port]
        self.assertLess(abs(starts[0]["at"] - starts[1]["at"]), 2.0)
        self.assertFalse(any(alive(s["pid"]) for s in self.spines()))
        self.assertFalse((evolve.evolve_dir() / "1" / "lab-base" / "tree").exists())
        self.assertIn("pass on the 1 task(s) the change reaches (`pr-merged-typo-fix`)", pub.prs[0]["body"])

    def test_a_candidates_playbook_edit_reaches_its_drafts(self):
        """bin/eval reads a task's playbook prompt from the brain it is told
        the core serves. The tournament passed only the lab's URL and token,
        so a candidate's own playbook edit never reached its draft message:
        both sides drafted from the live checkout's playbook."""
        self.lab_config(EVOLVE_PROBES=0)
        self.edit(1, "pr-merged", line="Open with the one thing that shipped overnight.")
        self.run_gen(1, publisher=Refusing())
        (base,) = [d for d in self.drafts() if d[0] == self.port]
        (cand,) = [d for d in self.drafts() if d[0] != self.port]
        self.assertEqual(cand[2], str(evolve.evolve_dir() / "1" / "cand-1" / "brain"))
        self.assertEqual(base[2], str(evolve.evolve_dir() / "1" / "lab-base" / "tree" / "brain"))
        self.assertIn("Open with the one thing that shipped overnight.", cand[3])
        self.assertNotIn("Open with the one thing that shipped overnight.", base[3])

    def test_the_baseline_is_drafted_once_per_base_commit_and_reused_by_the_next_generation(self):
        self.lab_config(EVOLVE_PROBES=0)
        self.edit(1, "pr-merged")
        self.run_gen(1, publisher=Refusing())
        self.edit(1, "cost-of-living", line="Another change.")
        self.run_gen(1, publisher=Refusing())
        base = [d[1] for d in self.drafts() if d[0] == self.port]
        self.assertEqual(base, ["pr-merged-typo-fix", "cost-of-living-real-numbers"])

    def test_the_baseline_is_keyed_by_the_labs_model(self):
        """A model change must not reuse main's drafts from another model."""
        self.lab_config(EVOLVE_PROBES=0)
        self.edit(1, "pr-merged")
        self.run_gen(1, publisher=Refusing())
        (self.root / "secrets" / "spine.env").write_text(
            "LADDER_URL=http://ladder.invalid/v1\nLADDER_API_KEY=sk-model\nSPINE_MODEL=m-2\n")
        self.edit(1, "pr-merged", line="Another change.")
        self.run_gen(1, publisher=Refusing())
        self.assertEqual([d[1] for d in self.drafts() if d[0] == self.port], ["pr-merged-typo-fix", "pr-merged-typo-fix"])
        runs = sorted(p.parent.name for p in (evolve.evolve_dir() / "baseline").glob("*/*/run.json"))
        self.assertEqual(runs, ["s1-m-1", "s1-m-2"])

    def test_a_hard_check_regression_that_does_not_hold_up_on_fresh_drafts_passes(self):
        """Gen 11: the first pass's two samples fail a hard check main passed,
        by chance. The task is drafted afresh on both labs, main's too (not
        the cached baseline), the fresh pairs fail no more on the candidate,
        and the verdict is the judge's."""
        self.lab_config(EVOLVE_PROBES=0, EVOLVE_CONFIRM_SAMPLES=6, EVOLVE_REVISE_ROUNDS=1)
        first = self.root / "long-first"
        first.write_text("1")
        os.environ["FAKE_CAND_LONG_FIRST"] = str(first)
        self.edit(1, "pr-merged")
        self.run_gen(1, publisher=Refusing())
        ev = self.entries()["g1c1"]["eval"]
        self.assertEqual(ev["verdict"], "pass", ev.get("reason"))
        self.assertEqual(ev["suspects"], ["pr-merged-typo-fix"])
        self.assertEqual(ev["confirmation"]["pr-merged-typo-fix"]["state"], "refuted")
        self.assertIn("did not hold up", ev["reason"])
        base = [d for d in self.drafts() if d[0] == self.port]
        cand = [d for d in self.drafts() if d[0] == self.port + 1]
        self.assertEqual((len(base), len(cand)), (1 + 6, 1 + 6))
        confirm = evolve.evolve_dir() / "1" / "eval" / "cand-1" / "compare" / "confirm"
        self.assertEqual(len(evolve.read_jsonl(confirm / "base" / "drafts.jsonl")), 6)
        (cached,) = (evolve.evolve_dir() / "baseline").glob("*/s1-m-1/drafts.jsonl")
        self.assertEqual(len(evolve.read_jsonl(cached)), 1)  # the cache is left as it was
        self.assertFalse(any(alive(s["pid"]) for s in self.spines()))

    def test_a_hard_check_regression_that_holds_up_on_fresh_drafts_is_regressed(self):
        self.lab_config(EVOLVE_PROBES=0, EVOLVE_CONFIRM_SAMPLES=6, EVOLVE_REVISE_ROUNDS=1)
        os.environ["FAKE_CAND_LONG"] = "1"
        self.edit(1, "pr-merged")
        out = self.run_gen(1, publisher=Refusing())
        self.assertEqual([c["verdict"] for c in out["candidates"]], ["regressed"])
        ev = self.entries()["g1c1"]["eval"]
        self.assertEqual(ev["regressions"], ["pr-merged-typo-fix"])
        self.assertEqual(ev["confirmation"]["pr-merged-typo-fix"]["state"], "confirmed")
        self.assertIn("confirmed: pr-merged-typo-fix on 6 fresh pair", ev["reason"])

    def test_pairs_worse_by_chance_are_confirmed_away_on_a_fresh_sample(self):
        """The first A/A run: main lost to itself on the judge's pairs. The
        pairs that say worse are judged again on a fresh sample of the same
        tasks, both sides drafted afresh, and only that sample decides."""
        self.lab_config(EVOLVE_PROBES=0, EVOLVE_EVAL_PAIRS=4, EVOLVE_REVISE_ROUNDS=1)
        first = self.root / "lose-first"
        first.write_text("4")
        os.environ["FAKE_LOSE_FIRST"] = str(first)
        self.edit(1, "pr-merged")
        self.run_gen(1, publisher=Refusing())
        ev = self.entries()["g1c1"]["eval"]
        self.assertEqual(ev["verdict"], "pass", ev.get("reason"))
        self.assertEqual(ev["pairs_confirmation"]["state"], "refuted")
        self.assertIn("did not hold up", ev["reason"])
        base = [d for d in self.drafts() if d[0] == self.port]
        cand = [d for d in self.drafts() if d[0] == self.port + 1]
        self.assertEqual((len(base), len(cand)), (4 + 4, 4 + 4))
        fresh = evolve.evolve_dir() / "1" / "eval" / "cand-1" / "compare" / "confirm-pairs"
        self.assertEqual(len(evolve.read_jsonl(fresh / "base" / "drafts.jsonl")), 4)

    def test_pairs_worse_on_a_fresh_sample_too_are_regressed_on_the_pairs_path(self):
        self.lab_config(EVOLVE_PROBES=0, EVOLVE_EVAL_PAIRS=4, EVOLVE_REVISE_ROUNDS=1)
        os.environ["FAKE_LOSE_PORT"] = str(self.port + 1)
        self.edit(1, "pr-merged")
        out = self.run_gen(1, publisher=Refusing())
        self.assertEqual([c["verdict"] for c in out["candidates"]], ["regressed"])
        ev = self.entries()["g1c1"]["eval"]
        self.assertEqual((ev["path"], ev["pairs_confirmation"]["state"]), ("pairs", "confirmed"))

    def test_a_restart_between_the_compare_and_its_confirmation_confirms_without_judging_again(self):
        self.lab_config(EVOLVE_PROBES=0, EVOLVE_CONFIRM_SAMPLES=6, EVOLVE_REVISE_ROUNDS=1)
        os.environ["FAKE_CAND_LONG"] = "1"
        self.edit(1, "pr-merged")
        real = evolve.TargetedEvaluator._confirm
        with unittest.mock.patch.object(evolve.TargetedEvaluator, "_confirm",
                                        side_effect=evolve.EvolveError("cut off")):
            self.run_gen(1, publisher=Refusing())
        # The confirmation could not run: not measured, so deferred, no try.
        self.assertEqual(self.entries()["g1c1"]["verdict"], "deferred")
        self.assertEqual(self.entries()["g1c1"]["infra"]["stage"], "eval")
        judged = len([c for c in self.core_turns() if c.get("agent_id") == "judge"])
        (evolve.gen_dir(1) / "result.json").unlink()
        rows = [r for r in evolve.read_jsonl(evolve.archive_path())
                if not (r.get("id") == "g1c1" and r.get("verdict") != "pending")]
        evolve.archive_path().write_text("".join(json.dumps(r) + "\n" for r in rows))
        with unittest.mock.patch.object(evolve.TargetedEvaluator, "_confirm", real):
            self.run_gen(1, publisher=Refusing())
        self.assertEqual(self.entries()["g1c1"]["eval"]["confirmation"]["pr-merged-typo-fix"]["state"], "confirmed")
        # Only the six fresh pairs were judged after the restart.
        self.assertEqual(len([c for c in self.core_turns() if c.get("agent_id") == "judge"]) - judged, 6)

    def test_samples_per_task_fill_the_pair_budget_within_the_floor_and_the_cap(self):
        """ceil(EVOLVE_EVAL_PAIRS / reached tasks), at least EVOLVE_EVAL_SAMPLES, at most 8."""
        got = {n: evolve.eval_samples(24, 2, n) for n in (1, 2, 3, 14, 30)}
        self.assertEqual(got, {1: 8, 2: 8, 3: 8, 14: 2, 30: 2})
        self.assertEqual([evolve.eval_samples(24, 2, n) for n in (4, 5, 6, 12, 13, 24, 25)], [6, 5, 4, 2, 2, 2, 2])
        self.assertEqual(evolve.eval_samples(24, 1, 30), 1)  # the floor is the samples setting
        self.assertEqual(evolve.eval_samples(24, 10, 1), 10)  # which wins over the cap
        self.assertEqual(evolve.eval_samples(1, 1, 5), 1)

    def test_the_pair_budget_is_a_setting_that_defaults_to_24(self):
        self.assertEqual(evolve.SETTINGS["EVOLVE_EVAL_PAIRS"][:2], ("24", int))
        self.config()
        self.assertEqual(evolve.load_config()["EVOLVE_EVAL_PAIRS"], 24)

    def test_base_and_candidate_draw_the_same_samples_of_each_task_whatever_it_reaches(self):
        """A change reaching one task drafts it 8 times; one reaching many
        drafts each fewer times. Every candidate's baseline has its own
        sample count (the baseline folder is keyed by it)."""
        self.lab_config(EVOLVE_PROBES=0, EVOLVE_EVAL_PAIRS=24, EVOLVE_EVAL_SAMPLES=2)
        self.edit(1, "pr-merged")
        self.edit(2, "mentions")
        out = self.run_gen(2, publisher=Refusing())
        reached = {i: self.entries()[f"g1c{i}"]["eval"]["tasks"] for i in (1, 2)}
        self.assertEqual(reached[1], ["pr-merged-typo-fix"])
        self.assertGreater(len(reached[2]), 3)
        want = {1: evolve.eval_samples(24, 2, 1), 2: evolve.eval_samples(24, 2, len(reached[2]))}
        self.assertEqual(want[1], 8)
        self.assertLess(want[2], 8)
        count = {}
        for port, task, *_ in self.drafts():
            count[(port, task)] = count.get((port, task), 0) + 1
        for i in (1, 2):
            for task in reached[i]:
                self.assertEqual(count[(self.port, task)], want[i], f"main drafted {task}")
                self.assertEqual(count[(self.port + i, task)], want[i], f"candidate {i} drafted {task}")
        runs = sorted(p.parent.name for p in (evolve.evolve_dir() / "baseline").glob("*/*/run.json"))
        self.assertEqual(runs, [f"s{want[2]}-m-1", "s8-m-1"])
        self.assertEqual([c["verdict"] for c in out["candidates"]], ["dry-run", "dry-run"])

    def test_the_persona_probe_runs_only_when_the_change_reaches_every_task(self):
        self.lab_config()
        soul = self.base_text("brain/SOUL.md").rstrip().splitlines()[-1]
        self.propose(1, [{"path": "brain/SOUL.md", "old": soul, "new": soul + "\n- i say gm like i mean it."}])
        self.edit(2, "cost-of-living")
        self.run_gen(2, publisher=Refusing())
        entries = self.entries()
        self.assertTrue(entries["g1c1"]["eval"]["all"])
        self.assertIsNotNone(entries["g1c1"]["probe"]["drift"])
        self.assertNotIn("probe", entries["g1c2"])
        probes = [c for c in self.core_turns() if c["message"].startswith("# probe:")]
        self.assertTrue(probes)
        self.assertFalse(any(c["url"].endswith(f":{self.port + 2}") for c in probes))

    def test_a_drifted_persona_halts_instead_of_shipping(self):
        self.lab_config(EVOLVE_MAX_DRIFT=0.3)
        os.environ["FAKE_DRIFT"] = "1"
        self.live()
        soul = self.base_text("brain/SOUL.md").rstrip().splitlines()[-1]
        self.propose(1, [{"path": "brain/SOUL.md", "old": soul, "new": soul + "\n- i say gm like i mean it."}])
        pub = FakePublisher()
        out = self.run_gen(1, publisher=pub)
        self.assertEqual(pub.prs, [])
        self.assertEqual(out["halts"][0]["kind"], "persona-drift")
        self.assertEqual(self.entries()["g1c1"]["verdict"], "flagged")
        self.assertTrue(evolve.halt_path().exists())

    def test_a_lab_that_does_not_boot_defers_the_candidate_with_no_try(self):
        self.lab_config()
        os.environ["FAKE_SPINE_FAIL"] = "1"
        self.edit(1, "pr-merged")
        self.run_gen(1, publisher=Refusing())
        cand = self.entries()["g1c1"]
        self.assertEqual((cand["verdict"], cand["deferred_for"]), ("deferred", "infra"))
        self.assertIn("lab spine", cand["eval_reason"])
        self.assertIn("lab spine", cand["infra"]["detail"])

    def test_a_candidate_whose_turns_never_answer_is_inconclusive_and_may_come_back(self):
        self.lab_config(EVOLVE_PROBES=0)
        os.environ["FAKE_CAND_DOWN"] = "1"
        self.edit(1, "pr-merged")
        self.run_gen(1, publisher=Refusing())
        cand = self.entries()["g1c1"]
        self.assertEqual(cand["verdict"], "inconclusive", cand.get("eval_reason"))
        self.assertIn("g1c1", [e["id"] for e in self.entries().values() if e.get("verdict") == "inconclusive"])
        self.assertIn("m62", [x.id for x in evolve.open_learnings(self.entries())])

    def test_a_lab_cores_scribbles_never_reach_the_pull_request(self):
        self.lab_config(EVOLVE_PROBES=0)
        os.environ["FAKE_SPINE_SCRIBBLE"] = "1"
        self.live()
        self.edit(1, "pr-merged")
        pub = FakePublisher()
        self.run_gen(1, publisher=pub)
        self.assertTrue(any(s["TEENY_ROOT"].endswith("cand-1") for s in self.spines()))
        (pr,) = pub.prs
        self.assertEqual(pr["files"], ["brain/playbooks/pr-merged.md"])

    def test_the_judge_runs_on_its_own_model(self):
        self.lab_config(EVOLVE_PROBES=0, EVOLVE_JUDGE_MODEL="judge-9b")
        self.edit(1, "pr-merged")
        self.run_gen(1, publisher=Refusing())
        judged = [c for c in self.core_turns() if c.get("agent_id") == "judge"]
        self.assertTrue(judged)
        self.assertEqual({c.get("model_override") for c in judged}, {"judge-9b"})
        self.assertEqual({d[4].get("model_override") for d in self.drafts()}, {None})
        self.assertEqual(self.entries()["g1c1"]["eval"]["judge_model"], "judge-9b")

    def test_a_restart_reuses_the_drafts_and_the_report_it_already_has(self):
        self.lab_config(EVOLVE_PROBES=0)
        self.edit(1, "pr-merged")
        self.run_gen(1, publisher=Refusing())
        booted, drafted = len(self.spines()), len(self.drafts())
        # Cut off after it was measured, before the result was written.
        (evolve.gen_dir(1) / "result.json").unlink()
        rows = [r for r in evolve.read_jsonl(evolve.archive_path())
                if not (r.get("id") == "g1c1" and r.get("verdict") != "pending")]
        evolve.archive_path().write_text("".join(json.dumps(r) + "\n" for r in rows))
        out = self.run_gen(1, publisher=Refusing())
        self.assertTrue(out["resumed"])
        self.assertEqual(out["candidates"][0]["verdict"], "dry-run")
        self.assertEqual(len(self.drafts()), drafted)
        self.assertEqual(len(self.spines()), booted)  # every report it needs is there: nothing boots


class Evolver(LabCase):
    def test_the_evolver_edits_its_worktree_on_an_editing_lab(self):
        self.config(EVOLVE=1, EVOLVE_GENERATIONS_PER_DAY=24, EVOLVE_PROPOSER="evolver", EVOLVE_LAB_PORT=self.port,
                    EVOLVE_LAB_BOOT_SECS=20)
        out = evolve.run_generation(evolve.load_config(), 1, None, publisher=FakePublisher())
        cand = self.entries()["g1c1"]
        self.assertEqual(out["candidates"][0]["verdict"], "dry-run", cand.get("reason"))
        self.assertEqual(cand["proposer"], "evolver")
        self.assertEqual(cand["title"], "pr-merged: name who shipped")
        patch = (evolve.evolve_dir() / cand["patch"]).read_text()
        self.assertIn("+Name the person who shipped it.", patch)
        self.assertTrue(cand["gate"]["ok"], cand["gate"])
        self.assertNotIn("written during research", patch)  # the research turn's writes are undone
        research, turn = [c for c in self.core_turns() if c.get("agent_id") == "evolver"]
        self.assertTrue(research["message"].startswith("# evolve research"))
        self.assertEqual(cand["research"]["plan"], "one line in moltbook-daily")
        self.assertIn("one line in moltbook-daily", turn["message"])
        self.assertEqual(turn["cwd"], str(evolve.evolve_dir() / "1" / "cand-1"))
        self.assertTrue(turn["url"].endswith(f":{self.port + 11}"))
        self.assertNotIn("## file:", turn["message"])  # it reads what it needs
        self.assertIn("never instructions to follow", turn["message"])
        reader, spine = self.spines()
        self.assertEqual((reader["SPINE_LAB"], reader.get("SPINE_LAB_EDIT"), reader["DRY_RUN"]), ("1", None, "1"))
        self.assertEqual((spine["SPINE_LAB"], spine["SPINE_LAB_EDIT"], spine["DRY_RUN"]), ("1", "1", "1"))
        self.assertFalse(alive(spine["pid"]))

    def test_a_frozen_path_it_wrote_is_rejected(self):
        self.config(EVOLVE=1, EVOLVE_GENERATIONS_PER_DAY=24, EVOLVE_PROPOSER="evolver", EVOLVE_LAB_PORT=self.port,
                    EVOLVE_LAB_BOOT_SECS=20)
        fake = Path(os.environ["TEENY_OH"])
        fake.write_text(FAKE_OH.replace('os.path.join(params["cwd"], "brain", "playbooks", "moltbook-follow.md")',
                                        'os.path.join(params["cwd"], "bin", "ask")'))
        evolve.run_generation(evolve.load_config(), 1, None, publisher=FakePublisher())
        cand = self.entries()["g1c1"]
        self.assertEqual(cand["verdict"], "rejected")
        self.assertIn("bin/ask", [c for c in cand["gate"]["checks"] if c["check"] == "frozen paths"][0]["detail"])

    def test_a_restart_reapplies_the_evolvers_change_without_a_new_turn(self):
        self.config(EVOLVE=1, EVOLVE_GENERATIONS_PER_DAY=24, EVOLVE_PROPOSER="evolver", EVOLVE_LAB_PORT=self.port,
                    EVOLVE_LAB_BOOT_SECS=20)
        evolve.run_generation(evolve.load_config(), 1, None, publisher=FakePublisher())
        (evolve.gen_dir(1) / "result.json").unlink()
        rows = [r for r in evolve.read_jsonl(evolve.archive_path()) if r.get("id") != "g1c1"]
        evolve.archive_path().write_text("".join(json.dumps(r) + "\n" for r in rows))
        turns = len([c for c in self.core_turns() if c.get("agent_id") == "evolver"])
        out = evolve.run_generation(evolve.load_config(), 1, None, publisher=FakePublisher())
        self.assertTrue(out["resumed"])
        self.assertEqual(len([c for c in self.core_turns() if c.get("agent_id") == "evolver"]), turns)
        cand = self.entries()["g1c1"]
        self.assertTrue(cand["resumed"])
        self.assertIn("+Name the person who shipped it.", (evolve.evolve_dir() / cand["patch"]).read_text())



class RestartAndHalts(EvolveCase):
    def test_a_red_doctor_or_a_fallback_starts_no_generation(self):
        evolve.state_dir().mkdir(parents=True, exist_ok=True)
        (evolve.state_dir() / "doctor.json").write_text(json.dumps(
            {"ok": False, "checks": [{"check": "invariants", "status": "fail"}]}))
        self.propose(1, [self.playbook_edit("One.")])
        out = self.run_gen(1)
        self.assertEqual(out["skipped"], "halted: doctor")
        (evolve.state_dir() / "doctor.json").write_text(json.dumps({"ok": True, "checks": []}))
        (evolve.state_dir() / "spine-fallback.json").write_text(json.dumps({"to": "abc", "at": evolve.iso(T0)}))
        out = self.run_gen(1)
        self.assertEqual(out["skipped"], "halted: fallback")
        (evolve.state_dir() / "spine-fallback.json").unlink()
        self.assertIn("gen", self.run_gen(1))

    def test_a_red_doctor_that_is_green_on_a_fresh_run_does_not_halt(self):
        """The doctor writes state/doctor.json at boot alone, so a check that
        fails for a minute after a restart (2026-11-15: a cron job the restart
        killed, still claimed) held evolve until the next boot. A red report
        is checked again before it halts the run."""
        evolve.state_dir().mkdir(parents=True, exist_ok=True)
        report = evolve.state_dir() / "doctor.json"
        report.write_text(json.dumps({"ok": False, "checks": [{"check": "cron next runs are current", "status": "fail"}]}))
        doctor = self.root / "doctor"
        doctor.write_text(f"#!/bin/sh\necho \"$@\" >>{self.root / 'doctor.calls'}\n"
                          f"echo '{{\"ok\": true, \"checks\": []}}' >{report}\n")
        doctor.chmod(0o755)
        os.environ["EVOLVE_DOCTOR"] = str(doctor)
        self.propose(1, [self.playbook_edit("One.")])
        out = self.run_gen(1)
        self.assertNotIn("skipped", out)
        self.assertIn("gen", out)
        self.assertEqual((self.root / "doctor.calls").read_text().split(), ["--live"])

    def test_a_doctor_still_red_on_a_fresh_run_halts(self):
        evolve.state_dir().mkdir(parents=True, exist_ok=True)
        report = evolve.state_dir() / "doctor.json"
        report.write_text(json.dumps({"ok": True, "checks": []}))
        doctor = self.root / "doctor"
        doctor.write_text(f"#!/bin/sh\necho '{{\"ok\": false, \"checks\": [{{\"check\": \"invariants\", "
                          f"\"status\": \"fail\"}}]}}' >{report}\nexit 1\n")
        doctor.chmod(0o755)
        report.write_text(json.dumps({"ok": False, "checks": [{"check": "invariants", "status": "fail"}]}))
        os.environ["EVOLVE_DOCTOR"] = str(doctor)
        self.propose(1, [self.playbook_edit("One.")])
        out = self.run_gen(1)
        self.assertEqual(out["skipped"], "halted: doctor")
        self.assertIn("invariants", out["halts"][0]["detail"])

    def test_an_unknown_evaluator_refuses_the_run(self):
        self.config(EVOLVE=1, EVOLVE_EVALUATOR="maybe")
        with self.assertRaises(evolve.EvolveError):
            evolve.load_config()



# --- evolve-watch -------------------------------------------------------------------------------------



WATCH_GUARD = r"""#!/usr/bin/env python3
import json, os, sys
with open(os.environ["FAKE_CALLS"], "a") as fh:
    fh.write(json.dumps([os.path.basename(sys.argv[0]), *sys.argv[1:]]) + "\n")
name = os.path.basename(sys.argv[0])
if name == "guard":
    print(open(os.environ["FAKE_HOLDS"]).read())
elif name == "world" and sys.argv[1:2] == ["history"] and os.environ.get("FAKE_HISTORY"):
    print(open(os.environ["FAKE_HISTORY"]).read())
else:
    sys.exit(1)
"""


class Watch(EvolveCase):
    REASON_MARK = "against"  # every spike reason says "... against N/day before"

    def setUp(self):
        super().setUp()
        for name in ("guard", "world"):
            (self.root / "hands" / name).write_text(WATCH_GUARD)
        self.holds = self.root / "holds.json"
        self.holds.write_text(json.dumps({"holds": []}))
        self.saved.setdefault("FAKE_HOLDS", os.environ.get("FAKE_HOLDS"))
        os.environ["FAKE_HOLDS"] = str(self.holds)
        self.config(EVOLVE=1, EVOLVE_PROBES=0)

    def merge_one(self, at=T0, merge_sha=None):
        """g1c1's pull request, merged (by the babysitter or a person)."""
        evolve.record({"id": "g1c1", "gen": 1, "cand": 1, "parent": "main", "verdict": "opened", "gate": {"ok": True},
                       "title": "a change", "pr_number": 7, "merged_at": evolve.iso(at),
                       "merge_sha": merge_sha or "0" * 40})

    def hold_at(self, *times):
        self.holds.write_text(json.dumps({"holds": [{"at": evolve.iso(t), "status": "pending", "category": "spam",
                                                     "text": "STRANGER TEXT"} for t in times]}))

    def test_nothing_merged_nothing_watched(self):
        self.assertEqual(evolve.watch(evolve.load_config())["watching"], None)

    def test_a_spike_in_holds_reverts_and_keeps_its_reasons_private(self):
        self.merge_one(at=T0 - 3600 * 5)
        os.environ["DRY_RUN"] = "1"
        self.hold_at(T0 - 3 * DAY, *(T0 - 3600 * h for h in (1, 2, 3, 4)))
        out = evolve.watch(evolve.load_config())
        self.assertTrue(out["reverted"])
        self.assertEqual(out["revert"]["would_open_pr"]["branch"], "teeny/revert-gen-1-c1")
        self.assertTrue(evolve.halt_path().exists())
        private = json.loads((evolve.secrets_dir() / "evolve-watch" / "g1c1.json").read_text())
        self.assertTrue(any("guard holds" in r for r in private["reasons"]))
        self.assertEqual(oct((evolve.secrets_dir() / "evolve-watch").stat().st_mode & 0o777), "0o700")
        # Nothing Teeny can read names a reason: not the state, the archive, the
        # halt, the printed result, nor the next generation's evidence.
        for path in evolve.state_dir().rglob("*"):
            if path.is_file():
                text = path.read_text(errors="replace")
                self.assertNotIn("guard holds", text, path)
                self.assertNotIn(self.REASON_MARK + " ", text, path)
        self.assertNotIn("guard holds", json.dumps(out))
        self.assertNotIn("STRANGER TEXT", json.dumps(private))
        again = evolve.watch(evolve.load_config())
        self.assertIsNone(again["watching"])

    def test_a_quiet_day_is_not_a_spike(self):
        self.merge_one(at=T0 - 3600 * 5)
        self.hold_at(*(T0 - DAY * d for d in (2, 3, 4, 5, 6)), T0 - 3600)
        out = evolve.watch(evolve.load_config())
        self.assertNotIn("reverted", out)
        self.assertFalse(evolve.halt_path().exists())

    def test_j_lower_after_72_hours_reverts_and_j_higher_closes_the_watch(self):
        history = self.root / "history.json"
        os.environ["FAKE_HISTORY"] = str(history)
        self.merge_one(at=T0 - 73 * 3600)
        history.write_text(json.dumps([{"at": T0 - 74 * 3600, "J": 1.0}, {"at": T0 - 2 * 3600, "J": 1.2}]))
        out = evolve.watch(evolve.load_config())
        self.assertTrue(out.get("closed"))
        self.assertIsNone(evolve.watch(evolve.load_config())["watching"])
        evolve.record({"id": "g2c1", "gen": 2, "cand": 1, "parent": "main", "verdict": "opened", "gate": {"ok": True},
                       "pr_number": 8, "merged_at": evolve.iso(T0 - 73 * 3600 + 1), "merge_sha": "1" * 40})
        history.write_text(json.dumps([{"at": T0 - 74 * 3600, "J": 1.0}, {"at": T0 - 2 * 3600, "J": 0.8}]))
        os.environ["DRY_RUN"] = "1"
        out = evolve.watch(evolve.load_config())
        self.assertEqual(out["watching"], "g2c1")
        self.assertTrue(out["reverted"])
        private = json.loads((evolve.secrets_dir() / "evolve-watch" / "g2c1.json").read_text())
        self.assertTrue(any(r.startswith("J fell") for r in private["reasons"]))

    def test_a_drifted_persona_after_promotion_reverts(self):
        self.config(EVOLVE=1, EVOLVE_PROBES=1, EVOLVE_PROBE_SAMPLES=1, EVOLVE_MAX_DRIFT=0.3)
        os.environ["DRY_RUN"] = "1"
        self.merge_one(at=T0 - 3600)
        from test_probes import scripted

        questions = evolve.probes_lib.persona_questions()
        evolve.probes_lib.save_baseline(evolve.evolve_dir(), {
            "gen": "gen-0", "persona": {q["id"]: ["i am teeny, a small robot who keeps the lab"] for q in questions},
            "memory": {"accuracy": 1.0}})
        drifted = scripted({q["id"]: "synergy leverage optimisation" for q in questions}, {"persona": 0.1})
        out = evolve.watch(evolve.load_config(), ask=drifted)
        self.assertTrue(out["reverted"])
        private = json.loads((evolve.secrets_dir() / "evolve-watch" / "g1c1.json").read_text())
        self.assertTrue(any("persona drift" in r for r in private["reasons"]))
        self.assertTrue((evolve.secrets_dir() / "evolve-watch" / "g1c1.probe.json").is_file())

    def test_a_live_core_that_does_not_answer_is_asked_again_not_reverted(self):
        self.config(EVOLVE=1, EVOLVE_PROBES=1, EVOLVE_PROBE_SAMPLES=1, EVOLVE_MAX_DRIFT=0.3,
                    EVOLVE_MAX_MEMORY_LOSS=0.5)
        os.environ["DRY_RUN"] = "1"
        self.merge_one(at=T0 - 3600)
        (self.root / "hands" / "stats").write_text("#!/bin/sh\necho '{\"metrics\": [{\"source\": \"x\", "
                                                    "\"metric\": \"followers\", \"value\": 5, \"date\": \"2026-11-10\"}]}'\n")
        (self.root / "hands" / "stats").chmod(0o755)
        questions = evolve.probes_lib.persona_questions()
        evolve.probes_lib.save_baseline(evolve.evolve_dir(), {
            "gen": "gen-0", "persona": {q["id"]: ["i am teeny"] for q in questions}, "memory": {"accuracy": 1.0}})

        def down(agent, message, model):
            raise evolve.probes_lib.ProbeError("core unreachable")

        out = evolve.watch(evolve.load_config(), ask=down)
        self.assertNotIn("reverted", out)
        self.assertFalse(evolve.halt_path().exists())
        state = json.loads((evolve.secrets_dir() / "evolve-watch" / "state.json").read_text())
        self.assertNotIn("probed", state["g1c1"])  # asked again at the next watch

    def test_live_a_revert_pull_request_undoes_the_merge_and_waits_for_a_person(self):
        # A clone of its own, where main carries a real merge of a generation.
        src = self.root / "own.git"
        git("clone", "-q", "--bare", str(self.src), str(src))
        os.environ["EVOLVE_GIT_DIR"] = str(src)
        tree = self.root / "trees" / "promo"
        git("--git-dir", str(src), "worktree", "add", "-q", "-b", "teeny/gen-1-c1", str(tree), "main")
        git("-C", str(tree), "config", "user.name", "t")
        git("-C", str(tree), "config", "user.email", "t@example.invalid")
        morning = tree / "brain" / "playbooks" / "morning.md"
        morning.write_text(morning.read_text() + "\nPROMOTED LINE\n")
        git("-C", str(tree), "commit", "-q", "-am", "gen-1")
        git("-C", str(tree), "switch", "-q", "--detach", "main")
        git("-C", str(tree), "merge", "-q", "--no-ff", "-m", "Merge gen-1", "teeny/gen-1-c1")
        sha = git("-C", str(tree), "rev-parse", "HEAD").strip()
        git("--git-dir", str(src), "update-ref", "refs/heads/main", sha)
        git("--git-dir", str(src), "worktree", "remove", "--force", str(tree))
        self.merge_one(at=T0 - 3600, merge_sha=sha)
        self.hold_at(*(T0 - 60 * m for m in (1, 2, 3, 4, 5)))
        (self.root / "secrets" / "teeny.env").write_text("DRY_RUN=0\n")
        self.config(EVOLVE=1, EVOLVE_PROBES=0)
        pub = FakePublisher()
        out = evolve.watch(evolve.load_config(), publisher=pub)
        self.assertTrue(out["reverted"])
        self.assertNotIn("error", out["revert"], out["revert"])
        (pr,) = pub.prs
        self.assertEqual((pr["branch"], pr["title"]), ("teeny/revert-gen-1-c1", "Revert gen-1-c1"))
        self.assertNotIn("guard holds", pr["body"])
        self.assertNotIn("spike", pr["body"])
        diff = git("--git-dir", str(src), "diff", "main", "teeny/revert-gen-1-c1")
        self.assertIn("-PROMOTED LINE", diff)
        (issue,) = pub.issues
        self.assertIn("watch-revert", issue["title"])
        self.assertNotIn("guard holds", issue["body"])





class Probe(EvolveCase):
    def test_a_baseline_once_then_drift_against_it(self):
        from test_probes import scripted

        questions = evolve.probes_lib.persona_questions()
        same = scripted({q["id"]: "i am teeny, a small robot who keeps the lab" for q in questions}, {"persona": 1.0})
        out = evolve.probe_cmd(evolve.load_config(), baseline=True, ask=same)
        self.assertEqual(out["questions"], len(questions))
        with self.assertRaises(evolve.EvolveError):
            evolve.probe_cmd(evolve.load_config(), baseline=True, ask=same)
        out = evolve.probe_cmd(evolve.load_config(), ask=same)
        self.assertLessEqual(out["drift"], 0.05)



class AA(LabCase):
    """`bin/evolve aa`: main measured against itself, through the same
    evaluator, with nothing archived, no pull request and no generation."""

    def test_main_against_itself_passes_and_leaves_no_generation_behind(self):
        self.lab_config(EVOLVE_PROBES=1)
        line = evolve.aa(evolve.load_config(), ["pr-merged-typo-fix", "reply-fud"])
        self.assertEqual((line["kind"], line["verdict"], line["false_regressed"]), ("aa", "pass", False))
        self.assertEqual((line["tasks"], line["samples"], line["suspects"]), (2, 1, []))
        self.assertGreater(line["turns"], 0)
        self.assertEqual(evolve.read_jsonl(evolve.evolve_dir() / "aa.jsonl"), [line])
        self.assertEqual(list(evolve.archive()), ["main"])  # the seed only
        self.assertEqual(evolve.next_gen(), 1)
        self.assertEqual(evolve.generations_today(), 0)
        self.assertFalse((evolve.evolve_dir() / "baseline").exists())  # main drafted afresh, in its own folder
        folder = evolve.evolve_dir() / line["folder"]
        self.assertEqual(sorted(p.name for p in folder.glob("lab-*")), [])
        self.assertFalse((folder / "cand-1").exists())
        self.assertFalse(any(c["message"].startswith("# probe:") for c in self.core_turns()))
        self.assertFalse(any(alive(s["pid"]) for s in self.spines()))

    def test_a_flaky_first_pass_is_reported_and_confirmed_away(self):
        self.lab_config(EVOLVE_CONFIRM_SAMPLES=6)
        first = self.root / "long-first"
        first.write_text("1")
        os.environ["FAKE_CAND_LONG_FIRST"] = str(first)
        line = evolve.aa(evolve.load_config(), ["pr-merged-typo-fix"])
        self.assertEqual((line["first_verdict"], line["verdict"]), ("regressed", "pass"))
        self.assertEqual(line["confirmation"], {"pr-merged-typo-fix": "refuted"})

    def test_the_line_says_which_path_made_it_regressed(self):
        self.lab_config(EVOLVE_EVAL_PAIRS=4)
        os.environ["FAKE_LOSE_PORT"] = str(self.port + 1)
        line = evolve.aa(evolve.load_config(), ["pr-merged-typo-fix"])
        self.assertEqual((line["verdict"], line["path"], line["first_path"], line["pairs_confirmation"]),
                         ("regressed", "pairs", "pairs", "confirmed"))

    def test_a_pass_has_no_path(self):
        self.lab_config()
        line = evolve.aa(evolve.load_config(), ["pr-merged-typo-fix"])
        self.assertEqual((line["verdict"], line["path"]), ("pass", None))

    def test_the_command_prints_one_json_line_and_refuses_an_unknown_task(self):
        self.lab_config()
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.assertEqual(evolve.main(["aa", "--tasks", "nope"]), 1)
        self.assertIn("no eval task nope", out.getvalue())
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.assertEqual(evolve.main(["aa", "--tasks", "pr-merged-typo-fix"]), 0)
        (line,) = out.getvalue().splitlines()
        self.assertEqual(json.loads(line)["verdict"], "pass")

if __name__ == "__main__":
    unittest.main()

