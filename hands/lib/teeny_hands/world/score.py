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
- **A new version of a component starts a new series.** When what a
  component counts changes (attention became people-weighted reach in
  version 2), the step from the last old row to the first new one counts
  zero (`rebased`), so a change of definition is never read as growth.
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
        "attention", 1.0, 50.0, 0.10, "weighted accounts",
        "people-weighted reach (version 2): followers on X, Moltbook and GitHub and Moltbook karma, and the distinct "
        "accounts that mentioned, replied to, commented, mailed or messaged Teeny in the 7 days before the snapshot, "
        "each weighed by whether it is a human, an agent or unknown, and an account that reached Teeny first "
        "counting double",
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

#: --- attention, version 2: people-weighted reach (docs/roadmap.md, P2) ----------
#: Rows written before version 2 have no `version` in their detail and count
#: as version 1 (an unweighted sum). `credit` never counts the step between two
#: versions.
ATTENTION_VERSION = 2
REACH_CLASSES = ("human", "agent", "unknown")
#: A follower or a point of karma: a stock, weighed by who holds it.
FOLLOWER_WEIGHTS = {"human": 1.0, "unknown": 0.5, "agent": 0.1}
#: An account that engaged in the 7-day window: a flow, worth more than a
#: follower because it did something.
ENGAGED_WEIGHTS = {"human": 5.0, "unknown": 2.5, "agent": 0.5}
#: An engaged account whose first contact came before Teeny ever addressed
#: it (it was not answering Teeny's outreach) counts this many times over.
FIRST_CONTACT_FACTOR = 2.0
#: Who holds each attention stat. No source says who an X or GitHub follower
#: is, so they are unknown; Moltbook admits only AI agents.
STAT_CLASSES = {
    "x.followers": "unknown",
    "github.followers": "unknown",
    "moltbook.followers": "agent",
    "moltbook.karma": "agent",
}
#: Why each surface's accounts get the class they do (`collect.classify`).
CLASS_RULES = {
    "moltbook": "agent: Moltbook is a network for AI agents only",
    "telegram": "human: Telegram marks every bot account (is_bot), and bots never reach the inbox",
    "gh": "agent when the login ends in [bot] (a GitHub App), unknown otherwise",
    "x": "unknown: nothing X returns says whether an account is automated",
    "mail": "unknown: automated senders never reach the inbox, but nothing says a person wrote the rest",
}


def engaged_weight(kind: str, first_contact_theirs: bool) -> float:
    """What one engaged account adds to attention (version 2)."""
    return ENGAGED_WEIGHTS[kind] * (FIRST_CONTACT_FACTOR if first_contact_theirs else 1.0)


#: The penalty component: a count of side effects, not a level.
PENALTY = "penalty"
PENALTY_WEIGHT = 0.02
PENALTY_COUNTS = (
    "guard holds on Teeny's own outbound text (not ones the operator approved or the re-screen cleared), "
    "and wallet payments that ended in doubt, on the UTC day before the snapshot"
)


def curve(value: float, offset: float) -> float:
    """log(W + c) for W ≥ 0, mirrored below zero (see the module doc)."""
    if value >= 0:
        return math.log(value + offset)
    return 2 * math.log(offset) - math.log(offset - value)


def _day(text: str) -> _date:
    return _date.fromisoformat(text)


def credit(series: Iterable[tuple], component: Component) -> dict[str, Any]:
    """A component's growth over a run of (date, value) or (date, value,
    version) rows, oldest first.

    `delta_log` is the plain change, curve(last) − curve(first). `credit` is
    what J counts: the sum of each step between consecutive rows, each
    clipped to ±daily_cap times the days it spans. `capped_days` says how
    many steps the cap cut. A step between rows of different versions counts
    zero, and `rebased` says how many there were; `delta_log` then runs from
    the first row of the newest version.
    """
    rows = sorted((r[0], float(r[1]), int(r[2]) if len(r) > 2 else 1) for r in series)
    if len(rows) < 2:
        return {"delta_log": 0.0, "credit": 0.0, "capped_days": 0, "steps": 0, "rebased": 0}
    total = 0.0
    capped = 0
    rebased = 0
    start = rows[0]
    for (d1, v1, k1), (d2, v2, k2) in zip(rows, rows[1:]):
        if k1 != k2:
            rebased += 1
            start = (d2, v2, k2)
            continue
        gap = max(1, (_day(d2) - _day(d1)).days)
        step = curve(v2, component.offset) - curve(v1, component.offset)
        limit = component.daily_cap * gap
        if abs(step) > limit:
            capped += 1
            step = math.copysign(limit, step)
        total += step
    delta = curve(rows[-1][1], component.offset) - curve(start[1], component.offset)
    return {"delta_log": delta, "credit": total, "capped_days": capped, "steps": len(rows) - 1, "rebased": rebased}


def j_score(credits: dict[str, float], penalty_events: float) -> float:
    """Σ wᵢ · creditᵢ − PENALTY_WEIGHT · penalty events."""
    return sum(BY_NAME[name].weight * value for name, value in credits.items()) - PENALTY_WEIGHT * penalty_events
