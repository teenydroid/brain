"""The math over the ledger: near-duplicates, learning progress, the
learnability band, the ranking score, ANNECS and the niche archive
(docs/research/teeny-application.md §4, Phase D).

  score(g) = LP(family) · [0 < p̂ < 1] · (1 − p̂) · interest(g) · E[ΔJ | g] · first(g)

- LP(family): |rate(recent) − rate(older)|, the family's success rate over
  the last RECENT_DAYS against the RECENT_DAYS..OLDER_DAYS before, each rate
  with a Beta(1, 1) prior. A family with fewer than LP_MIN_RESOLVED goals
  resolved has no history to compare, so it gets LP_UNKNOWN (explore it);
  any other family at least LP_FLOOR, so a stalled family ranks low but
  still ranks.
- p̂: (solved + 1) / (resolved + 2) over every resolved goal of the family.
  The prior keeps p̂ strictly inside (0, 1), so the band indicator is read
  from the counts: a family resolved BAND_MIN times or more that always
  succeeded or never did is out of the band (0); every other family is in (1).
- interest(g): 1 + 1 / (1 + goals the family has had). The judge-based
  interestingness check (OMNI) is future work; until then the near-duplicate
  filter at proposal and this family novelty stand in for it.
- E[ΔJ | g]: the weight of the goal's W component in J (§2): attention
  highest, $TINY high, money, capability and shipped medium, world low.
- first(g): FIRST_WEIGHT for a goal marked `first` while no solved goal in
  the ledger has the same check (kind and its non-threshold parameters), so
  a checkable first counts extra only until it has happened.
"""

from __future__ import annotations

import json
import re
from typing import Any, Iterable, Mapping

from . import checks

Json = dict[str, Any]
DAY = 86400.0
RECENT_DAYS = 14
OLDER_DAYS = 56
LP_MIN_RESOLVED = 2
LP_UNKNOWN = 0.25
LP_FLOOR = 0.02
BAND_MIN = 3
FIRST_WEIGHT = 2.0
#: E[ΔJ | g]: the component's weight in J (§2 of the proposal).
WEIGHTS = {"attention": 3.0, "tiny": 2.0, "money": 1.0, "capability": 1.0, "shipped": 1.0, "world": 0.5}
#: Word overlap (Jaccard) at which two instructions say the same thing.
DUPLICATE_JACCARD = 0.6
#: Two checks of one kind and the same subject are the same goal when their
#: thresholds are within this ratio of each other.
DUPLICATE_THRESHOLD_RATIO = 1.5

_STOP = frozenset(
    "a an the and or of to in on for at by with from into my me i it its is be get gain reach make have has this that"
    " least more than over within new one".split()
)


def words(text: str) -> frozenset[str]:
    out = set()
    for word in re.findall(r"[a-z0-9$]+", text.lower()):
        if word in _STOP:
            continue
        if len(word) > 3 and word.endswith("s") and not word.endswith("ss"):
            word = word[:-1]
        out.add(word)
    return frozenset(out)


def jaccard(a: Iterable[str], b: Iterable[str]) -> float:
    a, b = set(a), set(b)
    if not a and not b:
        return 1.0
    return len(a & b) / len(a | b)


def subject(kind: str, params: Mapping[str, Any]) -> str:
    """The check without its threshold: what it measures."""
    spec = checks.KINDS[kind]
    rest = {k: (v.lower() if isinstance(v, str) else v) for k, v in params.items() if k != spec.threshold}
    return kind + json.dumps(rest, sort_keys=True)


def same_check(a: Mapping[str, Any], b: Mapping[str, Any]) -> bool:
    if subject(a["check_kind"], a["params"]) != subject(b["check_kind"], b["params"]):
        return False
    key = checks.KINDS[a["check_kind"]].threshold
    if key is None:
        return True
    x, y = float(a["params"][key]), float(b["params"][key])
    return max(x, y) / min(x, y) <= DUPLICATE_THRESHOLD_RATIO


def near_duplicate(goal: Mapping[str, Any], other: Mapping[str, Any]) -> bool:
    """The same goal in other words, or the same check at about the same bar."""
    if same_check(goal, other) and goal.get("channel") == other.get("channel"):
        return True
    return jaccard(words(goal["instruction"]), words(other["instruction"])) >= DUPLICATE_JACCARD


# --- families ------------------------------------------------------------------------------------


def _rate(solved: int, resolved: int) -> float:
    return (solved + 1) / (resolved + 2)


def family_stats(goals: list[Mapping[str, Any]], now: float) -> dict[str, Json]:
    """Per family: counts, p̂, LP, whether it is in the band, and its novelty."""
    out: dict[str, Json] = {}
    for g in goals:
        f = out.setdefault(g["family"], {"family": g["family"], "goals": 0, "open": 0, "solved": 0, "failed": 0,
                                         "_recent": [0, 0], "_older": [0, 0]})
        f["goals"] += 1
        if g["status"] == "open":
            f["open"] += 1
            continue
        won = g["status"] == "solved"
        f["solved" if won else "failed"] += 1
        age = now - float(g.get("resolved_at") or now)
        bucket = "_recent" if age <= RECENT_DAYS * DAY else "_older" if age <= OLDER_DAYS * DAY else None
        if bucket:
            f[bucket][0] += int(won)
            f[bucket][1] += 1
    for f in out.values():
        recent, older = f.pop("_recent"), f.pop("_older")
        resolved = f["solved"] + f["failed"]
        f["resolved"] = resolved
        f["p_hat"] = round(_rate(f["solved"], resolved), 4)
        if resolved < LP_MIN_RESOLVED:
            lp = LP_UNKNOWN
        else:
            lp = max(LP_FLOOR, abs(_rate(*recent) - _rate(*older)))
        f["lp"] = round(lp, 4)
        f["in_band"] = not (resolved >= BAND_MIN and f["solved"] in (0, resolved))
        f["interest"] = round(1 + 1 / (1 + f["goals"]), 4)
        f["score"] = round(f["lp"] * int(f["in_band"]) * (1 - f["p_hat"]) * f["interest"], 6)
    return out


def _blank_family(name: str) -> Json:
    return {"family": name, "goals": 0, "open": 0, "solved": 0, "failed": 0, "resolved": 0, "p_hat": 0.5,
            "lp": LP_UNKNOWN, "in_band": True, "interest": 2.0, "score": LP_UNKNOWN * 0.5 * 2.0}


def first_counts(goal: Mapping[str, Any], goals: list[Mapping[str, Any]]) -> bool:
    if not goal.get("first"):
        return False
    key = subject(goal["check_kind"], goal["params"])
    return not any(g["status"] == "solved" and g["id"] != goal.get("id")
                   and subject(g["check_kind"], g["params"]) == key for g in goals)


def score(goal: Mapping[str, Any], family: Mapping[str, Any], goals: list[Mapping[str, Any]]) -> Json:
    expected = WEIGHTS[goal["component"]]
    first = FIRST_WEIGHT if first_counts(goal, goals) else 1.0
    band = 1 if family["in_band"] else 0
    value = family["lp"] * band * (1 - family["p_hat"]) * family["interest"] * expected * first
    return {"score": round(value, 6), "lp": family["lp"], "band": band, "p_hat": family["p_hat"],
            "interest": family["interest"], "expected_dj": expected, "first": first}


def rank(goals: list[Mapping[str, Any]], now: float) -> Json:
    families = family_stats(goals, now)
    ranked = []
    for g in goals:
        if g["status"] != "open":
            continue
        ranked.append({"id": g["id"], "instruction": g["instruction"], "family": g["family"],
                       "component": g["component"], "channel": g["channel"], "deadline_at": g["deadline_at"],
                       **score(g, families[g["family"]], goals)})
    ranked.sort(key=lambda r: (-r["score"], r["deadline_at"], r["id"]))
    fams = sorted(families.values(), key=lambda f: (-f["score"], f["family"]))
    return {"goals": ranked, "families": fams, "new_family": _blank_family("<new>"),
            "formula": "LP(family) * [0 < p_hat < 1] * (1 - p_hat) * interest * E[dJ] * first"}


# --- ANNECS and the archive -----------------------------------------------------------------------


def quality(goal: Mapping[str, Any]) -> float:
    """How much a solved goal says: harder families (lower p̂ when it was
    proposed), heavier components, and firsts count more."""
    p_hat = float(goal.get("p_hat") if goal.get("p_hat") is not None else 0.5)
    return round((1 - p_hat) * WEIGHTS[goal["component"]] * (FIRST_WEIGHT if goal.get("first") else 1.0), 6)


def annecs(goals: list[Mapping[str, Any]], channels: tuple[str, ...]) -> Json:
    """ANNECS: solved goals that were new when proposed (`novel`) and are not
    a near-duplicate of a goal solved before them; and the MAP-Elites archive
    over (W component x channel), the best solved goal in each niche."""
    solved = sorted((g for g in goals if g["status"] == "solved"), key=lambda g: (g["resolved_at"], g["id"]))
    counted: list[Mapping[str, Any]] = []
    for g in solved:
        if g.get("novel") and not any(near_duplicate(g, c) for c in counted):
            counted.append(g)
    niches: dict[tuple[str, str], Json] = {}
    for g in solved:
        cell = niches.setdefault((g["component"], g["channel"]), {"component": g["component"], "channel": g["channel"],
                                                                 "solved": 0, "best": None})
        cell["solved"] += 1
        q = quality(g)
        if cell["best"] is None or q > cell["best"]["quality"]:
            cell["best"] = {"id": g["id"], "instruction": g["instruction"], "family": g["family"], "quality": q,
                            "check": g["check_kind"], "solved_at": g["resolved_at"]}
    empty = [f"{c} x {ch}" for c in checks.COMPONENTS for ch in channels if (c, ch) not in niches]
    return {
        "annecs": len(counted),
        "counted": [g["id"] for g in counted],
        "solved": len(solved),
        "archive": {"cells": len(checks.COMPONENTS) * len(channels), "filled": len(niches),
                    "niches": sorted(niches.values(), key=lambda n: (n["component"], n["channel"])), "empty": empty},
    }
