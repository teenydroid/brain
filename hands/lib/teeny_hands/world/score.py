"""The scoring rules: what each component weighs, its offset, its daily cap,
and the arithmetic of J (docs/research/teeny-application.md, §2).

    J = Σᵢ wᵢ · Δlog(Wᵢ + cᵢ)  −  penalty weight · penalty events

over a window of N days (7). Every constant is here, in code, and this file
is frozen (evals/frozen.txt): Teeny can change how it grows, never how the
growth is counted.

- **Log growth.** Each component counts as log(W + c), so a component's
  growth is relative: 100 followers on 1,000 counts as $10 on $100. The
  offset c keeps a component near zero from turning one unit into a huge
  jump (log(1) to log(2) would be a doubling).
- **Signed where it has to be.** Money can be negative (costs above what
  came in). Below zero the curve is mirrored, 2·log(c) − log(c − W): the
  same value (log c) and slope (1/c) at zero, so it is smooth, and a loss
  counts as much as the equal gain. For W ≥ 0 it is exactly log(W + c).
- **A daily cap per component, both ways.** Each day's step is clipped to
  ±cap (±cap·k across a gap of k days), so one spike can't carry J and one
  glitch in a source can't sink it.
- **No credit for side effects.** The penalty is a count of events (guard
  holds on Teeny's own text, payments in doubt), each one subtracting
  PENALTY_WEIGHT, with no cap.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date as _date
from typing import Any, Iterable


@dataclass(frozen=True)
class Component:
    name: str
    weight: float
    offset: float  # c
    daily_cap: float  # the most one day's Δlog may add or take away
    unit: str
    counts: str


#: Weights: attention highest, then $TINY, money, capability, shipped, and
#: the world (lore) lowest, because Teeny writes ARCS.md itself.
COMPONENTS: tuple[Component, ...] = (
    Component(
        "attention", 1.0, 50.0, 0.10, "accounts",
        "followers on X, Moltbook and GitHub, Moltbook karma, and distinct accounts that mentioned, "
        "replied to, mailed or messaged Teeny in the 7 days before the snapshot",
    ),
    Component("tiny", 0.8, 10_000.0, 0.20, "tokens", "$TINY held in Teeny's wallet, in tokens, never in dollars"),
    Component(
        "money", 0.6, 100.0, 0.20, "USD",
        "the wallet's USD value (USDC, and SOL at WALLET_SOL_USD) minus what the team put in (WORLD_TEAM_USD) "
        "minus the last 30 days of running costs",
    ),
    Component("capability", 0.5, 5.0, 0.10, "count", "hands that have tests, plus channels Teeny can act on"),
    Component("shipped", 0.4, 5.0, 0.15, "count", "build jobs shipped, plus GitHub stars, forks and issues on its repositories"),
    Component("world", 0.2, 2.0, 0.10, "points", "lore arcs in ARCS.md: 1 point started, 2 points done"),
)

BY_NAME = {c.name: c for c in COMPONENTS}

#: The penalty component: a count of side effects, not a level.
PENALTY = "penalty"
PENALTY_WEIGHT = 0.02
PENALTY_COUNTS = (
    "guard holds on Teeny's own outbound text (not ones the operator approved or the re-screen cleared), "
    "and wallet payments that ended in doubt or failed, on the UTC day before the snapshot"
)


def curve(value: float, offset: float) -> float:
    """log(W + c) for W ≥ 0, mirrored below zero (see the module doc)."""
    if value >= 0:
        return math.log(value + offset)
    return 2 * math.log(offset) - math.log(offset - value)


def _day(text: str) -> _date:
    return _date.fromisoformat(text)


def credit(series: Iterable[tuple[str, float]], component: Component) -> dict[str, Any]:
    """A component's growth over a run of (date, value) rows, oldest first.

    `delta_log` is the plain change, curve(last) − curve(first). `credit` is
    what J counts: the sum of each step between consecutive rows, each
    clipped to ±daily_cap times the days it spans. `capped_days` says how
    many steps the cap cut.
    """
    rows = sorted(series)
    if len(rows) < 2:
        return {"delta_log": 0.0, "credit": 0.0, "capped_days": 0, "steps": 0}
    total = 0.0
    capped = 0
    for (d1, v1), (d2, v2) in zip(rows, rows[1:]):
        gap = max(1, (_day(d2) - _day(d1)).days)
        step = curve(v2, component.offset) - curve(v1, component.offset)
        limit = component.daily_cap * gap
        if abs(step) > limit:
            capped += 1
            step = math.copysign(limit, step)
        total += step
    delta = curve(rows[-1][1], component.offset) - curve(rows[0][1], component.offset)
    return {"delta_log": delta, "credit": total, "capped_days": capped, "steps": len(rows) - 1}


def j_score(credits: dict[str, float], penalty_events: float) -> float:
    """Σ wᵢ · creditᵢ − PENALTY_WEIGHT · penalty events."""
    return sum(BY_NAME[name].weight * value for name, value in credits.items()) - PENALTY_WEIGHT * penalty_events
