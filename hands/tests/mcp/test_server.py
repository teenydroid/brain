"""hands/mcp: the MCP handshake, a catalog that is exactly the four claim
and finish verbs, calls that run the hands with their rules, the tool names
the agents use, and the stdio server end to end."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import tomllib
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
LIB = HERE.parent.parent / "lib"
if str(LIB) not in sys.path:
    sys.path.insert(0, str(LIB))

from teeny_hands import hand  # noqa: E402
from teeny_hands.mcp import server  # noqa: E402

REPO = HERE.parents[2]
MCP = REPO / "hands" / "mcp"


class ServerTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name).resolve()
        (self.root / "secrets").mkdir()
        (self.root / "deploy").mkdir()
        shutil.copy(REPO / "deploy" / "browser-allow.txt", self.root / "deploy")
        self._env = dict(os.environ)
        os.environ["TEENY_ROOT"] = str(self.root)
        os.environ["TEENY_NOW"] = "1789369200"
        hand._ENV_CACHE = None

    def tearDown(self):
        os.environ.clear()
        os.environ.update(self._env)
        hand._ENV_CACHE = None
        self.tmp.cleanup()

    def rpc(self, method, params=None, msg_id=1):
        return server.handle({"jsonrpc": "2.0", "id": msg_id, "method": method, "params": params or {}})

    def call(self, name, arguments=None):
        result = self.rpc("tools/call", {"name": name, "arguments": arguments or {}})["result"]
        return result["isError"], json.loads(result["content"][0]["text"])

    def test_handshake(self):
        out = self.rpc("initialize", {"protocolVersion": "2025-03-26", "capabilities": {}})["result"]
        self.assertEqual(out["protocolVersion"], "2025-03-26")
        self.assertIn("tools", out["capabilities"])
        self.assertIsNone(server.handle({"jsonrpc": "2.0", "method": "notifications/initialized"}))
        self.assertEqual(self.rpc("ping")["result"], {})
        self.assertEqual(self.rpc("resources/list")["error"]["code"], -32601)

    def test_the_catalog_is_exactly_the_claim_and_finish_verbs(self):
        tools = self.rpc("tools/list")["result"]["tools"]
        self.assertEqual([t["name"] for t in tools], ["build_next", "build_done", "tasks_next", "tasks_done"])
        for tool in tools:
            self.assertEqual(tool["inputSchema"]["type"], "object")

    def test_nothing_that_reaches_outside_is_served(self):
        for name in ("build_ship", "ship", "tasks_add", "gh_push", "x_post"):
            self.assertEqual(self.rpc("tools/call", {"name": name})["error"]["code"], -32602)

    def test_a_build_round_trip(self):
        error, claimed = self.call("build_next")
        self.assertFalse(error)
        job = claimed["job"]
        self.assertEqual(job["kind"], "daily")
        (self.root / job["workdir"] / "main.py").write_text("print(1)\n")
        error, done = self.call("build_done", {"job_id": job["job_id"], "status": "ok", "summary": "a thing",
                                               "tests": "1 passed"})
        self.assertFalse(error)
        self.assertEqual(done["status"], "ok")

    def test_a_refusal_is_a_tool_error_with_the_hands_json(self):
        error, out = self.call("build_done", {"job_id": 42, "status": "ok", "summary": "s", "tests": "t"})
        self.assertTrue(error)
        self.assertEqual(out["refused"], "unknown job")
        error, out = self.call("tasks_done", {"task_id": 1, "outcome": "paid", "notes": "n"})
        self.assertTrue(error)
        self.assertIn("outcome", out["error"])

    def test_a_task_round_trip(self):
        from teeny_hands.tasks import queue

        queue.add({"goal": "g", "start_url": "https://github.com", "done_when": "d"})
        _, claimed = self.call("tasks_next")
        error, done = self.call("tasks_done", {"task_id": claimed["task"]["task_id"], "outcome": "needs_human",
                                               "notes": "payment checkpoint"})
        self.assertFalse(error)
        self.assertEqual(done["status"], "needs_human")

    def test_tool_names_follow_the_cores_scheme(self):
        # tinymcp `disambiguated_tool_name`: mcp_<server>_<tool>_<sha256(server\0tool)[:6]>.
        self.assertEqual(server.agent_tool_name("build_next"), "mcp_hands_build_next_1050fb")

    def test_the_agents_name_the_tools_the_core_registers(self):
        wanted = {"builder": ("build_next", "build_done"), "operator": ("tasks_next", "tasks_done")}
        for agent, tools in wanted.items():
            with open(REPO / "brain" / "agents" / f"{agent}.toml", "rb") as fh:
                named = tomllib.load(fh)["tools"]["named"]
            self.assertEqual({t for t in named if t.startswith("mcp_hands_")},
                             {server.agent_tool_name(t) for t in tools}, agent)

    def test_stdio_end_to_end(self):
        lines = [
            {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-06-18"}},
            {"jsonrpc": "2.0", "method": "notifications/initialized"},
            {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
            {"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {"name": "tasks_next", "arguments": {}}},
        ]
        done = subprocess.run([sys.executable, str(MCP)], input="".join(json.dumps(l) + "\n" for l in lines) + "not json\n",
                              capture_output=True, text=True, timeout=30)
        self.assertEqual(done.returncode, 0, done.stderr)
        answers = [json.loads(l) for l in done.stdout.splitlines()]
        self.assertEqual([a.get("id") for a in answers], [1, 2, 3, None])
        self.assertEqual(json.loads(answers[2]["result"]["content"][0]["text"])["task"], None)
        self.assertEqual(answers[3]["error"]["code"], -32700)
