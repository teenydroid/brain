#!/usr/bin/env python3
"""The hands' unit tests, one shard of them: CI runs the suite as SHARDS
jobs side by side (.github/workflows/check.yml, `hands`), since one runner
took over half an hour for all of it.

    python3 bin/test/hands_shard.py --shards 4 --shard 1
    python3 bin/test/hands_shard.py --shards 4 --list     # which shard runs what

It discovers exactly what `python3 -m unittest discover -s hands/tests -t
hands/tests` does, then deals out whole test classes (a class's tests stay
together and in discovery order, so setUpClass runs as it always did) to the
shard that has the least work so far, heaviest first. A module that defines
setUpModule or tearDownModule is dealt whole (none does yet). Every test lands
in exactly one shard, so the shards together run each test once, as plain
discovery does. A module that fails to import is a failing test in its shard,
as it is under plain discovery. Standard library only.
"""

from __future__ import annotations

import argparse
import sys
import unittest
from pathlib import Path

TESTS = Path(__file__).resolve().parents[2] / "hands" / "tests"

#: Seconds per test on the runner for the modules far slower than the rest,
#: measured in CI on 2026-11-16 (the rest average about 0.05 s): the evolve
#: modules build candidates in scratch git worktrees and run bin/doctor on
#: them. Only the balance between shards rests on these; a module missing here
#: still runs, weighted as DEFAULT_COST a test.
COST = {
    "test_evolve": 3.5,
    "test_evolve_loop": 4.3,
    "test_evolve_lab": 6.7,
    "test_evolve_generation": 10.0,
    "test_tick": 0.5,
    "babysit.test_deploy": 2.2,
    "test_reflect": 0.25,
}
DEFAULT_COST = 0.05


def flatten(suite: unittest.TestSuite):
    for item in suite:
        if isinstance(item, unittest.TestSuite):
            yield from flatten(item)
        else:
            yield item


def module_of(group: str) -> str:
    return group.split(":", 1)[0]


def groups_of(start: Path) -> dict[str, list[unittest.TestCase]]:
    """Every discovered test, by `module:Class` (by `module` alone for a
    module with module fixtures), in discovery order."""
    suite = unittest.TestLoader().discover(str(start), top_level_dir=str(start))
    out: dict[str, list[unittest.TestCase]] = {}
    for test in flatten(suite):
        name = test.__class__.__module__
        if name.startswith("unittest."):
            # A module that does not import is a _FailedTest named after it.
            key = test.id()
        else:
            module = sys.modules.get(name)
            whole = any(hasattr(module, f) for f in ("setUpModule", "tearDownModule"))
            key = name if whole else f"{name}:{test.__class__.__qualname__}"
        out.setdefault(key, []).append(test)
    return out


def deal(groups: dict[str, list[unittest.TestCase]], shards: int) -> list[list[str]]:
    """Group names per shard: heaviest first, each to the lightest shard."""
    load = [0.0] * shards
    out: list[list[str]] = [[] for _ in range(shards)]
    weight = {name: len(tests) * COST.get(module_of(name), DEFAULT_COST) for name, tests in groups.items()}
    for name in sorted(groups, key=lambda n: (-weight[n], n)):
        i = min(range(shards), key=lambda k: (load[k], k))
        out[i].append(name)
        load[i] += weight[name]
    return out


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--shards", type=int, required=True)
    parser.add_argument("--shard", type=int, help="which one to run, from 1")
    parser.add_argument("--list", action="store_true", help="print each shard's modules and test counts")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args(argv)
    if args.shards < 1 or (not args.list and not (args.shard and 1 <= args.shard <= args.shards)):
        parser.error("--shard must be between 1 and --shards")
    # As `-m unittest discover -t hands/tests` does: the tests import their
    # siblings (and `support`) from there.
    sys.path.insert(0, str(TESTS))
    groups = groups_of(TESTS)
    dealt = deal(groups, args.shards)
    if args.list:
        for i, names in enumerate(dealt, 1):
            count = sum(len(groups[n]) for n in names)
            cost = sum(len(groups[n]) * COST.get(module_of(n), DEFAULT_COST) for n in names)
            print(f"shard {i}: {count} tests, about {cost:.0f} s: {' '.join(sorted(names))}")
        return 0
    names = set(dealt[args.shard - 1])
    # Discovery order, so the run is the plain run with other modules left out.
    suite = unittest.TestSuite(t for name, tests in groups.items() if name in names for t in tests)
    total = sum(len(t) for t in groups.values())
    print(f"shard {args.shard}/{args.shards}: {suite.countTestCases()} of {total} tests, "
          f"{len(names)} of {len(groups)} classes", file=sys.stderr)
    result = unittest.TextTestRunner(verbosity=2 if args.verbose else 1).run(suite)
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
