"""bin/evolve, Phase C, offline: the lab spine (a stand-in that answers
/health on its port), the evolver, promotion, the restart, the last-good
fallback's trigger, evolve-watch and the persona drift halt. No real core
starts, nothing is merged and nothing restarts: GitHub is a fake publisher
and promoter, the restart a fake restarter (or a scratch checkout and a
`sleep` that gets the TERM), and the core a stand-in (TEENY_OH)."""

from __future__ import annotations

import json
import os
import random
import signal
import socket
import subprocess
import sys
import unittest
import unittest.mock
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from test_evolve import DAY, T0, EvolveCase, FakePublisher, Refusing, git  # noqa: E402

import evolve  # noqa: E402
import lab  # noqa: E402

STUB_SPINE = r"""#!/usr/bin/env python3
import http.server, json, os, signal, sys, threading
env = {k: os.environ.get(k) for k in ("TEENY_ROOT", "OPENHUMAN_WORKSPACE", "TEENY_STATE", "TEENY_SECRETS",
                                      "TEENY_STORAGE", "OPENHUMAN_CORE_PORT", "DRY_RUN", "SPINE_LAB",
                                      "SPINE_LAB_EDIT", "GITHUB_TOKEN", "CORTEX_API_URL")}
with open(os.environ["FAKE_SPINE_LOG"], "a") as fh:
    fh.write(json.dumps({"pid": os.getpid(), **env}) + "\n")
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
server.serve_forever()
"""

#: A stand-in core for bin/ask: act turns answer in the voice of the port they
#: reach (the base lab at FAKE_BASE_PORT, a candidate elsewhere), the judge
#: prefers the candidate, and the evolver edits the worktree it is rooted in.
FAKE_OH = r"""#!/usr/bin/env python3
import json, os, re, sys
method, params = sys.argv[1], json.loads(sys.argv[2])
with open(os.environ["FAKE_CORE_CALLS"], "a") as fh:
    fh.write(json.dumps({"url": os.environ.get("OH_URL"), "token": os.environ.get("OPENHUMAN_CORE_TOKEN"),
                         **params}) + "\n")
message, agent = params["message"], params.get("agent_id")
who = "base" if os.environ.get("OH_URL", "").endswith(":" + os.environ["FAKE_BASE_PORT"]) else "CAND"
if agent == "act" and message.startswith("# probe:"):
    drifted = os.environ.get("FAKE_DRIFT") and who == "CAND"
    text = "synergy optimisation leverage" if drifted else "i am teeny, a small robot who keeps the lab"
    answer = json.dumps({"text": text})
elif agent == "act":
    task = re.search(r"^# eval: (\S+)", message, re.M).group(1)
    answer = json.dumps({"text": f"gm {task} from the {who}"})
elif agent == "judge" and message.startswith("# persona probe"):
    answer = json.dumps({"same": 0.2 if os.environ.get("FAKE_DRIFT") else 1.0})
elif agent == "judge":
    drafts = dict(re.findall(r"^## draft ([12])\n\n```text\n(.*?)\n```", message, re.M | re.S))
    hits = [k for k in ("1", "2") if "CAND" in drafts.get(k, "")]
    answer = json.dumps({"better": hits[0] if len(hits) == 1 else "tie", "breaks": {}, "notes": "warmer"})
elif agent == "evolver":
    path = os.path.join(params["cwd"], "brain", "playbooks", "morning.md")
    with open(path, "a") as fh:
        fh.write("\nName the person who shipped it.\n")
    answer = json.dumps({"title": "morning: name who shipped", "component": "attention",
                         "expected_move": "+1% engaged reach", "hypothesis": "Names travel further."})
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
                  "FAKE_BASE_PORT", "FAKE_DRIFT", "GITHUB_TOKEN"):
            self.saved.setdefault(k, os.environ.get(k))
            os.environ.pop(k, None)
        os.environ.update(EVOLVE_LAB_SPINE=str(spine), FAKE_SPINE_LOG=str(self.spine_log), TEENY_OH=str(fake),
                          FAKE_CORE_CALLS=str(self.core_calls), FAKE_BASE_PORT=str(self.port))
        (self.root / "secrets" / "spine.env").write_text(
            "LADDER_URL=http://ladder.invalid/v1\nLADDER_API_KEY=sk-model\nSPINE_MODEL=m-1\n"
            "CORTEX_API_URL=http://127.0.0.1:3141\nCORTEX_API_KEY=cx\n")

    def lab_config(self, **values):
        self.config(EVOLVE=1, EVOLVE_GENERATIONS_PER_DAY=24, EVOLVE_EVALUATOR="lab", EVOLVE_LAB_PORT=self.port,
                    EVOLVE_LAB_BOOT_SECS=20, EVOLVE_EVAL_SAMPLES=1, EVOLVE_PROBE_SAMPLES=1,
                    EVOLVE_MAX_WINRATE_JUMP=0.6, **values)

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

    def test_a_busy_port_is_refused(self):
        tree = self.worktree("t")
        held = lab.start("a", tree, self.root / "a", self.port, self.root / "secrets", 20)
        try:
            with self.assertRaises(lab.LabError) as caught:
                lab.start("b", tree, self.root / "b", self.port, self.root / "secrets", 20)
            self.assertIn("already answers", str(caught.exception))
        finally:
            lab.stop(held)


class LabEvaluation(LabCase):
    def test_every_candidate_is_measured_on_a_lab_of_its_own(self):
        self.lab_config()
        self.propose(1, [self.playbook_edit("Open with what shipped.")])
        os.environ["DRY_RUN"] = "1"
        out = self.run_gen(1, publisher=Refusing())
        cand = self.entries()["g1c1"]
        self.assertEqual(cand["verdict"], "won", cand.get("eval_reason"))
        self.assertEqual(cand["scores"]["win_rate"], 1.0)
        # One lab over main for the baseline, one over the candidate's worktree.
        spines = self.spines()
        ports = sorted(int(s["OPENHUMAN_CORE_PORT"]) for s in spines)
        self.assertEqual(ports, [self.port, self.port + 1])
        roots = {s["TEENY_ROOT"] for s in spines}
        self.assertIn(str(evolve.evolve_dir() / "1" / "cand-1"), roots)
        self.assertIn(str(evolve.evolve_dir() / "1" / "lab-base" / "tree"), roots)
        self.assertTrue(all(s["DRY_RUN"] == "1" and s["SPINE_LAB"] == "1" for s in spines))
        # Each side drafted on its own lab, with that lab's token.
        drafts = [c for c in self.core_turns() if c.get("agent_id") == "act" and c["message"].startswith("# eval:")]
        cand_token = (evolve.evolve_dir() / "1" / "lab-1" / "secrets" / "core.token").read_text().strip()
        self.assertTrue(any(c["url"].endswith(f":{self.port + 1}") and c["token"] == cand_token for c in drafts))
        # The persona probe asked it, and main's answers became gen-0.
        self.assertIsNotNone(cand["probe"]["drift"])
        self.assertLessEqual(cand["probe"]["drift"], 0.1)
        self.assertTrue((evolve.evolve_dir() / "probes" / "gen-0.json").is_file())
        # Every lab is gone, and so is the base lab's worktree.
        self.assertFalse(any(alive(s["pid"]) for s in spines))
        self.assertFalse((evolve.evolve_dir() / "1" / "lab-base" / "tree").exists())
        self.assertTrue(out["dry_run"])

    def test_the_restored_tree_keeps_exactly_the_candidates_change(self):
        self.lab_config(EVOLVE_PROBES=0)
        os.environ["FAKE_SPINE_SCRIBBLE"] = "1"
        self.propose(1, [self.playbook_edit("Open with what shipped.")])
        out = self.run_gen(1, hold=True)
        self.assertEqual(out["candidates"][0]["verdict"], "won")
        self.assertTrue(any(s["TEENY_ROOT"].endswith("cand-1") for s in self.spines()))
        tree = evolve.evolve_dir() / "1" / "cand-1"
        self.assertNotIn("scribbled", (tree / "brain" / "CONTEXT.md").read_text())
        self.assertIn("Open with what shipped.", (tree / "brain" / "playbooks" / "morning.md").read_text())
        self.assertTrue((tree / "HYPOTHESIS.md").is_file())

    def test_a_lab_that_does_not_boot_leaves_the_candidate_unevaluated(self):
        self.lab_config()
        os.environ["FAKE_SPINE_FAIL"] = "1"
        self.propose(1, [self.playbook_edit("Open with what shipped.")])
        self.run_gen(1)
        cand = self.entries()["g1c1"]
        self.assertEqual(cand["verdict"], "unevaluated")
        self.assertIn("lab spine", cand["eval_reason"])

    def test_a_drifted_persona_halts_instead_of_shipping(self):
        self.lab_config(EVOLVE_MAX_DRIFT=0.3)
        os.environ["FAKE_DRIFT"] = "1"
        (self.root / "secrets" / "teeny.env").write_text("DRY_RUN=0\n")
        self.propose(1, [self.playbook_edit("Open with what shipped.")])
        pub = FakePublisher()
        out = self.run_gen(1, publisher=pub)
        self.assertEqual(pub.prs, [])
        self.assertEqual(out["halt"]["kind"], "persona-drift")
        self.assertEqual(self.entries()["g1c1"]["verdict"], "flagged")
        self.assertGreater(self.entries()["g1c1"]["probe"]["drift"], 0.3)
        self.assertTrue(evolve.halt_path().exists())

    def test_the_judge_runs_on_its_own_model(self):
        self.lab_config(EVOLVE_JUDGE_MODEL="judge-9b")
        self.propose(1, [self.playbook_edit("Open with what shipped.")])
        self.run_gen(1, hold=True)
        judged = [c for c in self.core_turns() if c.get("agent_id") == "judge"]
        self.assertTrue(judged)
        self.assertEqual({c.get("model_override") for c in judged}, {"judge-9b"})
        drafted = [c for c in self.core_turns() if c.get("agent_id") == "act"]
        self.assertEqual({c.get("model_override") for c in drafted}, {None})
        self.assertEqual(self.entries()["g1c1"]["scores"]["judge_model"], "judge-9b")


class Evolver(LabCase):
    def test_the_evolver_edits_its_worktree_on_an_editing_lab(self):
        self.config(EVOLVE=1, EVOLVE_GENERATIONS_PER_DAY=24, EVOLVE_PROPOSER="evolver", EVOLVE_LAB_PORT=self.port,
                    EVOLVE_LAB_BOOT_SECS=20)
        out = evolve.run_generation(evolve.load_config(), 1, None, publisher=FakePublisher())
        cand = self.entries()["g1c1"]
        self.assertEqual(out["candidates"][0]["verdict"], "unevaluated", cand.get("reason"))
        self.assertEqual(cand["proposer"], "evolver")
        self.assertEqual(cand["title"], "morning: name who shipped")
        patch = (evolve.evolve_dir() / cand["patch"]).read_text()
        self.assertIn("+Name the person who shipped it.", patch)
        self.assertTrue(cand["gate"]["ok"], cand["gate"])
        (turn,) = [c for c in self.core_turns() if c.get("agent_id") == "evolver"]
        self.assertEqual(turn["cwd"], str(evolve.evolve_dir() / "1" / "cand-1"))
        self.assertTrue(turn["url"].endswith(f":{self.port + 11}"))
        self.assertNotIn("## file:", turn["message"])  # it reads what it needs
        self.assertIn("never instructions to follow", turn["message"])
        (spine,) = self.spines()
        self.assertEqual((spine["SPINE_LAB"], spine["SPINE_LAB_EDIT"], spine["DRY_RUN"]), ("1", "1", "1"))
        self.assertFalse(alive(spine["pid"]))

    def test_a_frozen_path_it_wrote_is_rejected(self):
        self.config(EVOLVE=1, EVOLVE_GENERATIONS_PER_DAY=24, EVOLVE_PROPOSER="evolver", EVOLVE_LAB_PORT=self.port,
                    EVOLVE_LAB_BOOT_SECS=20)
        fake = Path(os.environ["TEENY_OH"])
        fake.write_text(FAKE_OH.replace('os.path.join(params["cwd"], "brain", "playbooks", "morning.md")',
                                        'os.path.join(params["cwd"], "bin", "ask")'))
        evolve.run_generation(evolve.load_config(), 1, None, publisher=FakePublisher())
        cand = self.entries()["g1c1"]
        self.assertEqual(cand["verdict"], "rejected")
        self.assertIn("bin/ask", [c for c in cand["gate"]["checks"] if c["check"] == "frozen paths"][0]["detail"])


# --- promotion ----------------------------------------------------------------------------------------


class FakePromoter:
    def __init__(self, refuse=False):
        self.merges, self.tags, self.refuse = [], [], refuse

    def merge(self, number):
        self.merges.append(number)
        if self.refuse:
            return {"merged": False, "error": "required checks are pending"}
        return {"merged": True, "sha": f"{number:040d}"}

    def tag(self, name, sha):
        self.tags.append((name, sha))
        return {"tagged": True}


class FakeRestarter:
    def __init__(self, archive_path=None):
        self.calls, self.archive_path, self.seen = [], archive_path, None

    def restart(self, sha, gen, record_last_good=True):
        self.calls.append((sha, gen, record_last_good))
        if self.archive_path:
            self.seen = self.archive_path.read_text()
        return {"restarted": True, "to": sha}


def judged(heldout_won=True, drift=0.05, rate=0.75):
    class Judged:
        def evaluate(self, gen_dir, cand, src):
            return {"verdict": "won", "scores": {"promote": True, "win_rate": rate, "decided": 20, "wins": 15,
                                                 "losses": 5, "heldout": {"won": heldout_won, "wins": 3, "losses": 0,
                                                                          "decided": 3}},
                    "probe": {"drift": drift}}

    return {1: Judged()}


class Promotion(EvolveCase):
    def setUp(self):
        super().setUp()
        (self.root / "secrets" / "teeny.env").write_text("DRY_RUN=0\n")
        self.propose(1, [self.playbook_edit("Open with what shipped.")], title="open with what shipped")

    def gen(self, evaluators, **cfg):
        self.config(EVOLVE=1, EVOLVE_GENERATIONS_PER_DAY=24, **cfg)
        self.pub, self.promoter = FakePublisher(), FakePromoter()
        self.restarter = FakeRestarter(evolve.archive_path())
        with unittest.mock.patch.object(evolve, "make_evaluators", return_value=evaluators):
            return evolve.run_generation(evolve.load_config(), 1, str(self.props), publisher=self.pub,
                                         promoter=self.promoter, restarter=self.restarter)

    def test_off_by_default_the_operator_merges(self):
        out = self.gen(judged())
        self.assertEqual(len(self.pub.prs), 1)
        self.assertEqual(self.promoter.merges, [])
        self.assertEqual(self.restarter.calls, [])
        self.assertNotIn("promoted_at", self.entries()["g1c1"])
        self.assertNotIn("promotion", out)

    def test_a_double_win_merges_tags_chronicles_and_restarts_last(self):
        out = self.gen(judged(), EVOLVE_AUTO_PROMOTE=1)
        self.assertEqual(self.promoter.merges, [1])
        sha = f"{1:040d}"
        self.assertEqual(self.promoter.tags, [("gen-1", sha)])
        files = git("--git-dir", str(self.src), "show", "--name-only", "--format=", "teeny/gen-1").split()
        self.assertEqual(sorted(files), ["brain/narrative/chronicle/gen-1.md", "brain/playbooks/morning.md"])
        chronicle = git("--git-dir", str(self.src), "show", "teeny/gen-1:brain/narrative/chronicle/gen-1.md")
        self.assertIn("the night shift ran 1 candidate", chronicle)
        self.assertIn("open with what shipped", chronicle)
        self.assertIn("Promoted by the loop", self.pub.prs[0]["body"])
        entry = self.entries()["g1c1"]
        self.assertEqual((entry["merge_sha"], entry["promotion"]["state"]), (sha, "merged"))
        self.assertTrue(entry["promoted_at"] and entry["merged_at"])
        # The restart comes last, once the archive already says it merged.
        self.assertEqual(self.restarter.calls, [(sha, 1, True)])
        self.assertIn('"promoted_at"', self.restarter.seen)
        self.assertTrue(out["restart"]["restarted"])

    def test_no_held_out_win_no_merge(self):
        out = self.gen(judged(heldout_won=False), EVOLVE_AUTO_PROMOTE=1)
        self.assertEqual(len(self.pub.prs), 1)  # still a pull request for the operator
        self.assertEqual(self.promoter.merges, [])
        self.assertIn("held-out", out["promotion"]["why"])
        self.assertEqual(self.restarter.calls, [])
        files = git("--git-dir", str(self.src), "show", "--name-only", "--format=", "teeny/gen-1").split()
        self.assertEqual(files, ["brain/playbooks/morning.md"])

    def test_an_unmeasured_candidate_is_never_promoted(self):
        out = self.gen({}, EVOLVE_AUTO_PROMOTE=1)
        self.assertEqual(self.promoter.merges, [])
        self.assertIn("visible", out["promotion"]["why"])

    def test_dry_run_merges_nothing_and_says_it_would(self):
        (self.root / "secrets" / "teeny.env").write_text("DRY_RUN=1\n")
        self.config(EVOLVE=1, EVOLVE_GENERATIONS_PER_DAY=24, EVOLVE_AUTO_PROMOTE=1)
        promoter, restarter = FakePromoter(), FakeRestarter()
        with unittest.mock.patch.object(evolve, "make_evaluators", return_value=judged()):
            out = evolve.run_generation(evolve.load_config(), 1, str(self.props), publisher=Refusing(),
                                        promoter=promoter, restarter=restarter)
        self.assertTrue(out["would_promote"])
        self.assertEqual((promoter.merges, restarter.calls), ([], []))
        self.assertEqual(git("--git-dir", str(self.src), "branch", "--list", "teeny/*").strip(), "")

    def test_the_environment_can_switch_promotion_off_but_never_on(self):
        self.config(EVOLVE=1, EVOLVE_AUTO_PROMOTE=1)
        os.environ["EVOLVE_AUTO_PROMOTE"] = "0"
        try:
            self.assertFalse(evolve.load_config().auto_promote)
            self.config(EVOLVE=1)
            os.environ["EVOLVE_AUTO_PROMOTE"] = "1"
            self.assertFalse(evolve.load_config().auto_promote)
        finally:
            os.environ.pop("EVOLVE_AUTO_PROMOTE")

    def test_a_refused_merge_waits_and_promote_retries_it(self):
        self.config(EVOLVE=1, EVOLVE_GENERATIONS_PER_DAY=24, EVOLVE_AUTO_PROMOTE=1)
        refusing, restarter = FakePromoter(refuse=True), FakeRestarter()
        with unittest.mock.patch.object(evolve, "make_evaluators", return_value=judged()):
            out = evolve.run_generation(evolve.load_config(), 1, str(self.props), publisher=FakePublisher(),
                                        promoter=refusing, restarter=restarter)
        self.assertEqual(out["promotion"]["state"], "pending")
        self.assertEqual(restarter.calls, [])
        self.assertEqual(self.entries()["g1c1"]["promotion"]["state"], "pending")
        promoter = FakePromoter()
        got = evolve.promote_cmd(evolve.load_config(), 1, promoter, restarter)
        self.assertTrue(got["promoted"])
        self.assertEqual(restarter.calls, [(f"{1:040d}", 1, True)])
        again = evolve.promote_cmd(evolve.load_config(), 1, promoter, restarter)
        self.assertIn("already promoted", again["skipped"])

    def test_the_chronicle_counts_the_generations_carried_back(self):
        entries = {"main": evolve.seed_entry(),
                   "g1c1": {"id": "g1c1", "gen": 1, "promoted_at": "2026-11-01T00:00:00Z"},
                   "g2c1": {"id": "g2c1", "gen": 2, "verdict": "lost"},
                   "g3c1": {"id": "g3c1", "gen": 3, "verdict": "rejected"}}
        text = evolve.chronicle_text(4, {"id": "g4c1", "parent": "main", "title": "t", "component": "world"},
                                     entries, 2)
        self.assertIn("- gen-2: the agents carried it back", text)
        self.assertIn("- gen-3: the agents carried it back", text)
        self.assertNotIn("gen-1:", text)


class RestartAndHalts(EvolveCase):
    def checkout(self):
        root = self.root / "live"
        root.mkdir()
        run = lambda *a: subprocess.run(["git", "-C", str(root), *a], check=True, capture_output=True,  # noqa: E731
                                        text=True).stdout.strip()
        run("init", "-q", "-b", "main")
        run("config", "user.name", "t")
        run("config", "user.email", "t@example.invalid")
        (root / "f").write_text("a\n")
        run("add", "f")
        run("commit", "-q", "-m", "a")
        old = run("rev-parse", "HEAD")
        run("switch", "-q", "-c", "next")
        (root / "f").write_text("b\n")
        run("commit", "-q", "-am", "b")
        new = run("rev-parse", "HEAD")
        run("switch", "-q", "main")
        return root, run, old, new

    def test_the_live_restart_records_last_good_fast_forwards_and_terms_the_spine(self):
        root, run, old, new = self.checkout()
        sleeper = subprocess.Popen(["sleep", "60"])
        (evolve.state_dir()).mkdir(parents=True, exist_ok=True)
        (evolve.state_dir() / "spine.pid").write_text(f"{sleeper.pid}\n")
        try:
            got = evolve.LiveRestarter(root).restart(new, 3)
            self.assertTrue(got["restarted"], got)
            self.assertEqual(run("rev-parse", "HEAD"), new)
            self.assertEqual(json.loads((evolve.state_dir() / "last-good").read_text())["commit"], old)
            self.assertEqual(sleeper.wait(timeout=10), -signal.SIGTERM)
        finally:
            if sleeper.poll() is None:
                sleeper.kill()

    def test_a_checkout_with_edits_is_not_restarted(self):
        root, run, old, new = self.checkout()
        (root / "f").write_text("an operator's edit\n")
        got = evolve.LiveRestarter(root).restart(new, 3)
        self.assertFalse(got["restarted"])
        self.assertIn("tracked edits", got["error"])
        self.assertEqual(run("rev-parse", "HEAD"), old)
        self.assertFalse((evolve.state_dir() / "last-good").exists())

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

    def promote_one(self, at=T0, merge_sha=None):
        evolve.record({"id": "g1c1", "gen": 1, "cand": 1, "parent": "main", "verdict": "won", "gate": {"ok": True},
                       "title": "a change", "pr_number": 7, "promoted_at": evolve.iso(at), "merged_at": evolve.iso(at),
                       "merge_sha": merge_sha or "0" * 40})

    def hold_at(self, *times):
        self.holds.write_text(json.dumps({"holds": [{"at": evolve.iso(t), "status": "pending", "category": "spam",
                                                     "text": "STRANGER TEXT"} for t in times]}))

    def test_nothing_promoted_nothing_watched(self):
        self.assertEqual(evolve.watch(evolve.load_config())["watching"], None)

    def test_a_spike_in_holds_reverts_and_keeps_its_reasons_private(self):
        self.promote_one(at=T0 - 3600 * 5)
        os.environ["DRY_RUN"] = "1"
        self.hold_at(T0 - 3 * DAY, *(T0 - 3600 * h for h in (1, 2, 3, 4)))
        out = evolve.watch(evolve.load_config())
        self.assertTrue(out["reverted"])
        self.assertEqual(out["revert"]["would_open_pr"]["branch"], "teeny/revert-gen-1")
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
        self.promote_one(at=T0 - 3600 * 5)
        self.hold_at(*(T0 - DAY * d for d in (2, 3, 4, 5, 6)), T0 - 3600)
        out = evolve.watch(evolve.load_config())
        self.assertNotIn("reverted", out)
        self.assertFalse(evolve.halt_path().exists())

    def test_j_lower_after_72_hours_reverts_and_j_higher_closes_the_watch(self):
        history = self.root / "history.json"
        os.environ["FAKE_HISTORY"] = str(history)
        self.promote_one(at=T0 - 73 * 3600)
        history.write_text(json.dumps([{"at": T0 - 74 * 3600, "J": 1.0}, {"at": T0 - 2 * 3600, "J": 1.2}]))
        out = evolve.watch(evolve.load_config())
        self.assertTrue(out.get("closed"))
        self.assertIsNone(evolve.watch(evolve.load_config())["watching"])
        evolve.record({"id": "g2c1", "gen": 2, "cand": 1, "parent": "main", "verdict": "won", "gate": {"ok": True},
                       "pr_number": 8, "promoted_at": evolve.iso(T0 - 73 * 3600 + 1), "merge_sha": "1" * 40})
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
        self.promote_one(at=T0 - 3600)
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
        self.promote_one(at=T0 - 3600)
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

    def test_live_a_revert_pull_request_undoes_the_merge(self):
        # A clone of its own, where main carries a real merge of a generation.
        src = self.root / "own.git"
        git("clone", "-q", "--bare", str(self.src), str(src))
        os.environ["EVOLVE_GIT_DIR"] = str(src)
        tree = self.root / "trees" / "promo"
        git("--git-dir", str(src), "worktree", "add", "-q", "-b", "teeny/gen-1", str(tree), "main")
        git("-C", str(tree), "config", "user.name", "t")
        git("-C", str(tree), "config", "user.email", "t@example.invalid")
        morning = tree / "brain" / "playbooks" / "morning.md"
        morning.write_text(morning.read_text() + "\nPROMOTED LINE\n")
        git("-C", str(tree), "commit", "-q", "-am", "gen-1")
        git("-C", str(tree), "switch", "-q", "--detach", "main")
        git("-C", str(tree), "merge", "-q", "--no-ff", "-m", "Merge gen-1", "teeny/gen-1")
        sha = git("-C", str(tree), "rev-parse", "HEAD").strip()
        git("--git-dir", str(src), "update-ref", "refs/heads/main", sha)
        git("--git-dir", str(src), "worktree", "remove", "--force", str(tree))
        self.promote_one(at=T0 - 3600, merge_sha=sha)
        self.hold_at(*(T0 - 60 * m for m in (1, 2, 3, 4, 5)))
        (self.root / "secrets" / "teeny.env").write_text("DRY_RUN=0\n")
        self.config(EVOLVE=1, EVOLVE_PROBES=0, EVOLVE_AUTO_PROMOTE=1)
        pub, promoter, restarter = FakePublisher(), FakePromoter(), FakeRestarter()
        out = evolve.watch(evolve.load_config(), publisher=pub, promoter=promoter, restarter=restarter)
        self.assertTrue(out["reverted"])
        self.assertNotIn("error", out["revert"], out["revert"])
        (pr,) = pub.prs
        self.assertEqual((pr["branch"], pr["title"]), ("teeny/revert-gen-1", "Revert gen-1"))
        self.assertNotIn("guard holds", pr["body"])
        self.assertNotIn("spike", pr["body"])
        diff = git("--git-dir", str(src), "diff", "main", "teeny/revert-gen-1")
        self.assertIn("-PROMOTED LINE", diff)
        self.assertEqual(promoter.merges, [1])
        # Back onto the reverted main, keeping the last-good from before the promotion.
        self.assertEqual(restarter.calls, [(f"{1:040d}", 1, False)])
        (issue,) = pub.issues
        self.assertIn("watch-revert", issue["title"])
        self.assertNotIn("guard holds", issue["body"])

    def test_without_auto_promote_the_revert_waits_for_the_operator(self):
        self.promote_one(at=T0 - 3600)
        self.hold_at(*(T0 - 60 * m for m in (1, 2, 3, 4, 5)))
        (self.root / "secrets" / "teeny.env").write_text("DRY_RUN=0\n")
        pub, promoter, restarter = FakePublisher(), FakePromoter(), FakeRestarter()
        with unittest.mock.patch.object(evolve, "revert", wraps=evolve.revert) as rev:
            out = evolve.watch(evolve.load_config(), publisher=pub, promoter=promoter, restarter=restarter)
        self.assertTrue(out["reverted"])
        self.assertEqual(rev.call_count, 1)
        self.assertEqual((promoter.merges, restarter.calls), ([], []))


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


if __name__ == "__main__":
    unittest.main()
