"""The charter (docs/governance.md; docs/roadmap.md, G1).

Every fence the charter names says where it lives in code and which test
pins it. This reads the fence table and fails when a fence's code, or the
test that pins it, stops existing: a fence the page names but nothing
checks is a fence on paper only.

A cited test is `path::Class.test_name` (a Python test: the method must be
defined in that class) or `path::label` (a check in a shell test: the label
must appear quoted in that file).
"""

from __future__ import annotations

import ast
import re
import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
sys.path.insert(0, str(REPO / "bin" / "lib"))

import frozen  # noqa: E402

PAGE = REPO / "docs" / "governance.md"
#: The fences DIRECTIVE.md lists ("The fences that never move"), by id. Each
#: must have a row; the page may name more.
DIRECTIVE_FENCES = {"guard", "x-rules", "wallet-caps", "no-model-pays", "never-sell", "frozen-paths", "watchers",
                    "browser-deny", "payment-page", "in-doubt", "tool-less", "only-operator-widens"}

_CODE = re.compile(r"`([^`]+)`")


def fences() -> dict[str, dict[str, object]]:
    """The fence table: id -> {fence, lives, pinned}."""
    text = PAGE.read_text(encoding="utf-8")
    section = text.split("\n## The fences\n", 1)[1].split("\n## ", 1)[0]
    rows: dict[str, dict[str, object]] = {}
    for line in section.splitlines():
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if len(cells) != 4 or cells[0] in ("ID", "") or set(cells[0]) <= {"-"}:
            continue
        rows[cells[0]] = {"fence": cells[1], "lives": _CODE.findall(cells[2]), "pinned": _CODE.findall(cells[3])}
    return rows


def resolves(ref: str) -> str | None:
    """None when the cited test exists, else why not."""
    path, _, name = ref.partition("::")
    file = REPO / path
    if not name:
        return f"{ref}: no test named after ::"
    if not file.is_file():
        return f"{ref}: {path} does not exist"
    source = file.read_text(encoding="utf-8")
    if path.endswith(".py"):
        cls, _, method = name.partition(".")
        for node in ast.walk(ast.parse(source)):
            if isinstance(node, ast.ClassDef) and node.name == cls:
                if any(isinstance(n, ast.FunctionDef) and n.name == method for n in node.body):
                    return None
        return f"{ref}: no {cls}.{method} in {path}"
    if f'"{name}"' in source:
        return None
    return f"{ref}: no check labelled {name!r} in {path}"


class CharterTest(unittest.TestCase):
    def test_the_page_names_every_fence_in_the_directive(self):
        self.assertEqual(DIRECTIVE_FENCES - set(fences()), set())

    def test_every_fence_lives_in_code_that_exists(self):
        for fid, row in fences().items():
            self.assertTrue(row["lives"], f"{fid}: names no code")
            for path in row["lives"]:
                if "*" in path:
                    self.assertTrue(list(REPO.glob(path)), f"{fid}: {path} matches nothing")
                else:
                    self.assertTrue((REPO / path).exists(), f"{fid}: {path} does not exist")

    def test_every_fence_is_pinned_by_a_test_that_exists(self):
        for fid, row in fences().items():
            self.assertTrue(row["pinned"], f"{fid}: no test pins it")
            for ref in row["pinned"]:
                self.assertIsNone(resolves(ref), fid)

    def test_a_missing_test_is_caught(self):
        self.assertIsNotNone(resolves("hands/tests/test_charter.py::CharterTest.test_that_was_never_written"))
        self.assertIsNotNone(resolves("bin/test/doctor_test.sh::a check nobody wrote"))
        self.assertIsNotNone(resolves("hands/tests/nowhere.py::A.b"))
        self.assertIsNone(resolves("hands/tests/test_charter.py::CharterTest.test_a_missing_test_is_caught"))

    def test_the_charter_and_its_short_version_are_frozen(self):
        patterns = frozen.parse_patterns((REPO / "evals" / "frozen.txt").read_text())
        for path in ("docs/governance.md", "brain/CHARTER.md"):
            self.assertIsNotNone(frozen.frozen_pattern(path, patterns), path)

    def test_the_short_version_is_anchored_in_the_invariants(self):
        invariants = (REPO / "evals" / "invariants.md").read_text()
        self.assertRegex(invariants, r"(?m)^- charter \| CHARTER\.md \| ")
        self.assertIn("**charter**", invariants)


if __name__ == "__main__":
    unittest.main()
