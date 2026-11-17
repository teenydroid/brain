"""bin/lib/evolve.py defines each top-level name once. A second `def` of the
same name silently replaces the first, and every caller of the first gets
the second's shape: `_cron_failures` did that (a dict per job, then a list
of runs) and crashed every evolve generation in observe()."""
from __future__ import annotations

import ast
import collections
import unittest
from pathlib import Path

LIB = Path(__file__).resolve().parents[2] / "bin" / "lib"


def duplicate_defs(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    names = collections.Counter(node.name for node in tree.body
                                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)))
    return sorted(name for name, n in names.items() if n > 1)


class NoShadowing(unittest.TestCase):
    def test_evolve_and_its_evidence_define_each_function_once(self):
        for name in ("evolve.py", "evolve_evidence.py"):
            with self.subTest(name):
                self.assertEqual(duplicate_defs(LIB / name), [])


if __name__ == "__main__":
    unittest.main()
