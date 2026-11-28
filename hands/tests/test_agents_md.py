"""AGENTS.md at the root is the map an agent reads before changing me, my own
mind included (brain/agents/mind.md). It names every broker verb, every
intent, every tool of the mind and every hand. These tests read each list
from its source and fail when the map leaves one out, so a new verb, intent
or hand cannot land without a line in AGENTS.md.

Everything is read from source with ast and regexes, never imported: broker.py
and tick.py read settings and state at import.
"""
from __future__ import annotations

import ast
import os
import re
import tomllib
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
AGENTS = (REPO / "AGENTS.md").read_text(encoding="utf-8")
NOT_HANDS = {"README.md", "lib", "tests"}


def _module(path: Path) -> ast.Module:
    return ast.parse(path.read_text(encoding="utf-8"))


def _dict_keys(module: ast.Module, name: str) -> list[str]:
    for node in module.body:
        target = node.target if isinstance(node, ast.AnnAssign) else (node.targets[0] if isinstance(node, ast.Assign) else None)
        if isinstance(target, ast.Name) and target.id == name and isinstance(node.value, ast.Dict):
            return [k.value for k in node.value.keys if isinstance(k, ast.Constant)]
    raise AssertionError(f"no dict {name}")


def _constant(module: ast.Module, name: str) -> str:
    for node in module.body:
        if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == name for t in node.targets):
            return node.value.value
    raise AssertionError(f"no constant {name}")


def _routes() -> dict[str, str]:
    """tick.ROUTES as intent -> the hand its route function runs (the first
    element of the tuple it returns)."""
    module = _module(REPO / "bin" / "lib" / "tick.py")
    functions = {n.name: n for n in module.body if isinstance(n, ast.FunctionDef)}
    routes: dict[str, str] = {}
    for node in module.body:
        if isinstance(node, ast.AnnAssign) and getattr(node.target, "id", None) == "ROUTES":
            for key, value in zip(node.value.keys, node.value.values):
                hands = {
                    ret.value.elts[0].value
                    for ret in ast.walk(functions[value.id])
                    if isinstance(ret, ast.Return) and isinstance(ret.value, ast.Tuple) and ret.value.elts
                    and isinstance(ret.value.elts[0], ast.Constant) and isinstance(ret.value.elts[0].value, str)
                }
                assert len(hands) == 1, (key.value, hands)
                routes[key.value] = hands.pop()
    assert routes, "no ROUTES in tick.py"
    return routes


def _section(title: str) -> str:
    start = AGENTS.index(title)
    nxt = re.search(r"^#{2,3} ", AGENTS[start + len(title):], re.M)
    return AGENTS[start: start + len(title) + (nxt.start() if nxt else len(AGENTS))]


def _hand_rows() -> dict[str, str]:
    """Each hand named in the first cell of the hands table -> its row."""
    rows: dict[str, str] = {}
    for line in _section("### Every hand").splitlines():
        cells = line.split("|")
        if len(cells) > 4 and "`" in cells[1]:
            for name in re.findall(r"`([a-z0-9-]+)`", cells[1]):
                rows[name] = line
    return rows


def _launchers() -> list[str]:
    hands = REPO / "hands"
    return sorted(p.name for p in hands.iterdir()
                  if p.name not in NOT_HANDS and p.is_file() and os.access(p, os.X_OK))


def _slug(heading: str) -> str:
    """GitHub's anchor for a heading."""
    text = heading.strip().lower()
    text = re.sub(r"[^\w\- ]", "", text)
    return text.replace(" ", "-")


class AgentsMdTest(unittest.TestCase):
    def test_every_broker_verb_is_named(self):
        broker = _module(REPO / "bin" / "lib" / "broker.py")
        for path in _dict_keys(broker, "PATHS"):
            self.assertIn(f"teeny {path.lstrip('/')}", AGENTS, f"AGENTS.md does not name the broker verb {path}")

    def test_every_gh_verb_is_named(self):
        broker = _module(REPO / "bin" / "lib" / "broker.py")
        section = _section("### `bin/teeny`")
        for verb in [*_dict_keys(broker, "GH_READS"), *_dict_keys(broker, "GH_WRITES")]:
            self.assertIn(f"`{verb}`", section, f"AGENTS.md does not name the gh verb {verb}")

    def test_every_intent_is_in_the_intents_table(self):
        section = _section("### Intents for `teeny do`")
        for intent in _routes():
            self.assertTrue(f"`{intent}`" in section, f"AGENTS.md's intents table leaves out {intent}")
        dispatch = _constant(_module(REPO / "bin" / "lib" / "broker.py"), "DISPATCH_VERB")
        self.assertIn(f"`{dispatch}`", section)

    def test_every_intent_is_in_its_hands_row(self):
        rows = _hand_rows()
        for intent, hand in _routes().items():
            self.assertTrue(hand in rows, f"no row for hands/{hand}, which {intent} runs")
            self.assertTrue(f"`{intent}`" in rows[hand], f"{intent} runs hands/{hand} but its row does not name it")

    def test_every_hand_has_a_row(self):
        rows = _hand_rows()
        for name in _launchers():
            self.assertTrue(name in rows, f"hands/{name} has no row in AGENTS.md's hands table")
        for name in rows:
            self.assertTrue(name in _launchers(), f"AGENTS.md has a row for hands/{name}, which does not exist")

    def test_every_tool_of_the_mind_is_named(self):
        section = _section("### My mind's own tools")
        for agent in ("mind", "worker"):
            spec = tomllib.loads((REPO / "brain" / "agents" / f"{agent}.toml").read_text(encoding="utf-8"))
            for tool in spec["tools"]["named"]:
                self.assertIn(f"`{tool}`", section, f"AGENTS.md does not name the {agent}'s tool {tool}")

    def test_every_call_service_is_named(self):
        services = tomllib.loads((REPO / "evals" / "services.toml").read_text(encoding="utf-8"))["services"]
        for name in services:
            self.assertIn(f"`{name}`", _section("### `bin/teeny`"), f"AGENTS.md does not name the service {name}")

    def test_links_resolve(self):
        anchors = {_slug(m) for m in re.findall(r"^#{2,4} (.+)$", (REPO / "docs" / "hands.md").read_text(encoding="utf-8"), re.M)}
        for target in re.findall(r"\]\(([^)\s]+)\)", AGENTS):
            if target.startswith(("http://", "https://", "mailto:")):
                continue
            path, _, anchor = target.partition("#")
            self.assertTrue((REPO / path).exists(), f"AGENTS.md links to {path}, which does not exist")
            if path == "docs/hands.md" and anchor:
                self.assertIn(anchor, anchors, f"AGENTS.md links to docs/hands.md#{anchor}, which is no heading")

    def test_claude_md_loads_it(self):
        self.assertEqual((REPO / "CLAUDE.md").read_text(encoding="utf-8").strip(), "@AGENTS.md")

    def test_the_mind_is_told_to_read_it(self):
        self.assertIn("repo/AGENTS.md", (REPO / "brain" / "agents" / "mind.md").read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
