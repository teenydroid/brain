"""Runway as a sense (docs/roadmap.md, S2): how many days the wallet's USDC
lasts at the current burn, and the tier that cuts optional spend when it is
low. Frozen (evals/frozen.txt), and the tiers are frozen config
(evals/runway.toml, read from this checkout, never from the brain).

  measure   USDC held (Solana, and every EVM network on mainnet) over the
            last 7 days' cost a day (`hands/wallet costs --days 7`)
  daily     measures, writes state/runway.json, and files one
            `health · runway` stimulus a UTC day: the days, the tier, and
            what the tier cuts. `bin/health runway`, the pinned `runway` job
  limits    what the tier in force allows: `media` (pictures and clips) and
            `evolve_candidates`. Read by hands/image and hands/video
            (media/assets.py), bin/evolve and the mind's beat header

Off unless RUNWAY=1 in secrets/teeny.env: `daily` is refused (`off`) and
`limits` cuts nothing. On, a runway not measured for STALE_DAYS (a balance
or a cost that could not be read) counts as the `low` tier: an unread
balance is not money, and spending less is the safe side to err on.

Only ever less: a tier lowers what Teeny may spend on optional things. It
never lifts a cap, and nothing here spends or signs.
"""

from __future__ import annotations

import datetime as _dt
import json
import tomllib
from pathlib import Path
from typing import Any

from . import hand, stimuli

Json = dict[str, Any]

TIERS_FILE = Path(__file__).resolve().parents[3] / "evals" / "runway.toml"
STALE_DAYS = 3
BURN_DAYS = 7
#: What a tier may set, and what "no tier" means for each.
UNCUT: Json = {"media": True, "evolve_candidates": None}
DAY = 86400.0


def tiers(path: Path | None = None) -> list[Json]:
    """The tiers, lowest `below_days` first. A file that does not parse is an
    error, not an empty list: frozen config never fails open."""
    data = tomllib.loads((path or TIERS_FILE).read_text())
    out = []
    for name, tier in (data.get("tiers") or {}).items():
        below = tier.get("below_days")
        if isinstance(below, bool) or not isinstance(below, (int, float)) or below <= 0:
            raise hand.HandError(f"evals/runway.toml: tier {name} needs below_days above zero")
        media = tier.get("media", True)
        cands = tier.get("evolve_candidates")
        if not isinstance(media, bool) or (cands is not None and (isinstance(cands, bool) or not isinstance(cands, int) or cands < 1)):
            raise hand.HandError(f"evals/runway.toml: tier {name} has a bad media or evolve_candidates")
        out.append({"name": name, "below_days": float(below), "media": media, "evolve_candidates": cands})
    return sorted(out, key=lambda t: t["below_days"])


def tier_for(days: float | None, table: list[Json]) -> Json | None:
    """The lowest tier the runway is under; None above them all."""
    if days is None:
        return None
    for tier in table:
        if days < tier["below_days"]:
            return tier
    return None


def _path() -> Path:
    return hand.state_dir() / "runway.json"


def measure() -> Json:
    """USDC held and the burn a day. Raises HandError when either cannot be
    read (an incomplete cost report counts as unread)."""
    from .wallet import reads

    balance = reads.balance()
    micro = int(balance["usdc"]["raw"])
    for network in ((balance.get("evm") or {}).get("networks") or {}).values():
        if isinstance(network.get("usdc"), dict):
            micro += int(network["usdc"]["raw"])
    parts = reads.cost_parts(BURN_DAYS)
    if not parts["complete"]:
        raise hand.HandError("a cost source could not be read")
    spent = parts["models"]["micro"] + parts["x402"]["micro"] + sum(b["share_micro"] for b in parts["bills"])
    burn = spent / BURN_DAYS
    days = None if burn <= 0 else micro / burn
    return {"usdc_micro": micro, "burn_micro_per_day": round(burn), "days": None if days is None else round(days, 1)}


def _cuts(tier: Json | None) -> str:
    if tier is None:
        return "nothing is cut"
    cut = []
    if not tier["media"]:
        cut.append("no pictures or clips")
    if tier["evolve_candidates"]:
        cut.append(f"at most {tier['evolve_candidates']} evolve candidate(s) a generation")
    return ", ".join(cut) or "nothing is cut"


def daily() -> Json:
    """Measures, saves, and files one `health · runway` stimulus a UTC day."""
    if not hand.flag("RUNWAY", False):
        raise hand.Refused("the runway sense is off: RUNWAY=1 in secrets/teeny.env", rule="off")
    table = tiers()
    now = hand.now()
    day = _dt.datetime.fromtimestamp(now, _dt.timezone.utc).strftime("%Y-%m-%d")
    try:
        got = measure()
    except hand.HandError as exc:
        state = load()
        out = {"date": day, "measured": False, "error": str(exc)[:200]}
        lim = limits()
        text = (f"Your runway could not be measured today ({out['error']}). "
                f"Until it is, you spend as the {lim['tier']} tier: {_cuts(lim)}." if lim["tier"] else
                f"Your runway could not be measured today ({out['error']}).")
        out["filed"] = stimuli.add("health", "runway", day, text, payload={"measured": False, "tier": lim["tier"]})
        out["last"] = state
        return out
    tier = tier_for(got["days"], table)
    state = {**got, "tier": tier["name"] if tier else None, "at": now}
    path = _path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, sort_keys=True))
    tmp.replace(path)
    usdc = got["usdc_micro"] / 1e6
    burn = got["burn_micro_per_day"] / 1e6
    if got["days"] is None:
        text = f"Runway: you hold {usdc:.2f} USDC and nothing cost money this week, so it does not run down."
    else:
        text = (f"Runway: you hold {usdc:.2f} USDC and running you cost {burn:.2f} a day this week: "
                f"{got['days']:g} days at this rate.")
    text += f" Tier: {state['tier'] or 'none'}; {_cuts(tier)}."
    out = {"date": day, "measured": True, **state}
    out["filed"] = stimuli.add("health", "runway", day, text, payload={k: v for k, v in state.items() if k != "at"})
    return out


def load() -> Json | None:
    try:
        state = json.loads(_path().read_text())
    except (OSError, ValueError):
        return None
    return state if isinstance(state, dict) else None


def limits() -> Json:
    """What the tier in force allows. Off: nothing is cut. On and stale or
    never measured: the `low` tier (else the highest tier there is)."""
    if not hand.flag("RUNWAY", False):
        return {**UNCUT, "tier": None, "days": None, "why": None}
    table = tiers()
    state = load()
    fresh = state is not None and float(state.get("at") or 0) >= hand.now() - STALE_DAYS * DAY
    if fresh:
        tier = next((t for t in table if t["name"] == state.get("tier")), None)
        days = state.get("days")
    else:
        tier = next((t for t in table if t["name"] == "low"), table[-1] if table else None)
        days = None
    if tier is None:
        return {**UNCUT, "tier": None, "days": days, "why": None}
    why = (f"runway {days:g} days, under {tier['below_days']:g}" if fresh and days is not None
           else "runway not measured in the last {} days".format(STALE_DAYS))
    return {"media": tier["media"], "evolve_candidates": tier["evolve_candidates"], "tier": tier["name"], "days": days, "why": why}


def cap_candidates(n: int) -> tuple[int, str | None]:
    """`n` candidates, cut to the tier's most; and why, when cut."""
    lim = limits()
    most = lim["evolve_candidates"]
    if most is not None and n > most:
        return most, f"{lim['tier']} tier ({lim['why']}): {most} candidate(s), not {n}"
    return n, None


def header_line() -> str | None:
    """The line in the mind's beat header, or None when the sense is off."""
    if not hand.flag("RUNWAY", False):
        return None
    try:
        lim = limits()
    except (hand.HandError, OSError, ValueError):
        return None
    if lim["tier"] is None:
        days = lim["days"]
        return None if days is None else f"Runway: {days:g} days at the current burn."
    return f"Runway: {lim['why']}. You are on the {lim['tier']} tier: {_cuts(lim)}."
