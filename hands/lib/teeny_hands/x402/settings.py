"""The X402_* keys of hands/x402, read and checked in one place, so the hand
and bin/doctor cannot disagree. A value that is unreadable, zero where a
number above zero is needed, or over its ceiling is an error, never a
default: a typo must not lift a limit.

Funding the rail (fund.py), off unless X402_FUND=1:

  X402_FUND                  1 turns the top-up on
  X402_FUND_FLOOR_USD        below this much USDC on Base, top up (required)
  X402_FUND_TARGET_USD       top up to this (required, above the floor)
  X402_FUND_MAX_PER_DAY_USD  the most topped up in any 24 hours (required)
  X402_FUND_COOLDOWN_MIN     a conversion toward Base USDC this recent is
                             still in flight: wait (default 60, 5..1440)
  X402_FUND_KEEP_ETH_USD     ETH left on a network for gas, in dollars at
                             WALLET_ETH_USD (default 2)
  X402_FUND_KEEP_SOL         SOL left for fees (default 0.05)

Paid search (search.py):

  X402_EXA_MAX_USD           the most one Exa request may pay (default 0.02)
  X402_FIRECRAWL             1 turns on `crawl` (Firecrawl's x402 search)
  X402_FIRECRAWL_MAX_USD     the most one Firecrawl request may pay (default 0.02)
  X402_TEXT_CHARS            text kept per result (default 4000, 200..10000)
  X402_CACHE                 0 turns the hour-long read cache off

Every dollar limit here is also held under the wallet's own caps
(WALLET_MAX_X402_*, WALLET_MAX_CONVERT_PER_DAY_USD, WALLET_MAX_PER_TX_USD):
these only ever narrow them.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Mapping

MICRO = 1_000_000
LAMPORTS = 10**9

#: Ceilings, in dollars (SOL for KEEP_SOL).
CEILINGS = {
    "X402_FUND_FLOOR_USD": 100,
    "X402_FUND_TARGET_USD": 200,
    "X402_FUND_MAX_PER_DAY_USD": 200,
    "X402_FUND_KEEP_ETH_USD": 100,
    "X402_FUND_KEEP_SOL": 10,
    "X402_EXA_MAX_USD": 1,
    "X402_FIRECRAWL_MAX_USD": 1,
}
DEFAULTS = {
    "X402_FUND_KEEP_ETH_USD": "2",
    "X402_FUND_KEEP_SOL": "0.05",
    "X402_EXA_MAX_USD": "0.02",
    "X402_FIRECRAWL_MAX_USD": "0.02",
}
FUND_REQUIRED = ("X402_FUND_FLOOR_USD", "X402_FUND_TARGET_USD", "X402_FUND_MAX_PER_DAY_USD")
COOLDOWN = ("X402_FUND_COOLDOWN_MIN", 60, 5, 1440)
TEXT_CHARS = ("X402_TEXT_CHARS", 4000, 200, 10000)
#: The keys this file reads (and bin/doctor checks). The paid endpoint's
#: X402_ENDPOINT, X402_FACILITATOR_URL and X402_FEE_PAYER are hands/wallet's.
KEYS = ("X402_FUND", *FUND_REQUIRED, COOLDOWN[0], "X402_FUND_KEEP_ETH_USD", "X402_FUND_KEEP_SOL",
        "X402_EXA_MAX_USD", "X402_FIRECRAWL", "X402_FIRECRAWL_MAX_USD", TEXT_CHARS[0], "X402_CACHE")


class SettingsError(Exception):
    """A key is wrong. Says which, never a secret (none of these is one)."""


@dataclass(frozen=True)
class Fund:
    floor_micro: int
    target_micro: int
    day_micro: int
    cooldown_secs: float
    keep_eth_micro: int
    keep_sol_raw: int


@dataclass(frozen=True)
class Search:
    exa_micro: int
    firecrawl_on: bool
    firecrawl_micro: int
    text_chars: int


def _raw(env: Mapping[str, str], key: str) -> str:
    return str(env.get(key) or "").strip()


def _on(env: Mapping[str, str], key: str) -> bool:
    value = _raw(env, key).lower()
    if value in ("", "0", "false", "no", "off"):
        return False
    if value in ("1", "true", "yes", "on"):
        return True
    raise SettingsError(f"{key} is 1 or 0, not {value!r}")


def _amount(env: Mapping[str, str], key: str, scale: int, required: bool = False) -> int | None:
    raw = _raw(env, key) or DEFAULTS.get(key, "")
    if not raw:
        if required:
            raise SettingsError(f"{key} is not set")
        return None
    try:
        value = Decimal(raw)
    except InvalidOperation:
        raise SettingsError(f"{key} is not a number") from None
    if not value.is_finite() or value <= 0:
        raise SettingsError(f"{key} must be a number above zero")
    if value > CEILINGS[key]:
        raise SettingsError(f"{key} is above its ceiling of {CEILINGS[key]}")
    units = value * scale
    if units != units.to_integral_value():
        raise SettingsError(f"{key} has too many decimals")
    return int(units)


def _whole(env: Mapping[str, str], spec: tuple[str, int, int, int]) -> int:
    key, default, low, high = spec
    raw = _raw(env, key)
    if not raw:
        return default
    if not raw.isdigit() or not low <= int(raw) <= high:
        raise SettingsError(f"{key} is a whole number, {low}..{high}")
    return int(raw)


def fund_on(env: Mapping[str, str]) -> bool:
    return _on(env, "X402_FUND")


def fund(env: Mapping[str, str]) -> Fund:
    """The top-up's settings, or SettingsError naming the first wrong key."""
    floor = _amount(env, "X402_FUND_FLOOR_USD", MICRO, required=True)
    target = _amount(env, "X402_FUND_TARGET_USD", MICRO, required=True)
    day = _amount(env, "X402_FUND_MAX_PER_DAY_USD", MICRO, required=True)
    assert floor is not None and target is not None and day is not None
    if floor >= target:
        raise SettingsError("X402_FUND_FLOOR_USD must be below X402_FUND_TARGET_USD")
    return Fund(
        floor, target, day, _whole(env, COOLDOWN) * 60.0,
        _amount(env, "X402_FUND_KEEP_ETH_USD", MICRO) or 0,
        _amount(env, "X402_FUND_KEEP_SOL", LAMPORTS) or 0,
    )


def search(env: Mapping[str, str]) -> Search:
    exa = _amount(env, "X402_EXA_MAX_USD", MICRO)
    firecrawl = _amount(env, "X402_FIRECRAWL_MAX_USD", MICRO)
    assert exa is not None and firecrawl is not None
    return Search(exa, _on(env, "X402_FIRECRAWL"), firecrawl, _whole(env, TEXT_CHARS))


def check(env: Mapping[str, str]) -> str:
    """bin/doctor: one line saying what is on, or SettingsError. Unset keys
    are fine (each has a default or keeps its feature off); the fund's
    required keys are required only once X402_FUND=1, but checked whenever
    they are set."""
    found = search(env)
    _on(env, "X402_CACHE")
    on = fund_on(env)
    parts = [f"search ${found.exa_micro / MICRO:g} an Exa request",
             f"Firecrawl {'on' if found.firecrawl_on else 'off'}"]
    if on or any(_raw(env, key) for key in (*FUND_REQUIRED, COOLDOWN[0], "X402_FUND_KEEP_ETH_USD", "X402_FUND_KEEP_SOL")):
        if not on:
            # Set but switched off: still sane, so turning it on cannot surprise.
            for key in FUND_REQUIRED:
                if _raw(env, key):
                    _amount(env, key, MICRO)
            _whole(env, COOLDOWN)
            _amount(env, "X402_FUND_KEEP_ETH_USD", MICRO)
            _amount(env, "X402_FUND_KEEP_SOL", LAMPORTS)
        else:
            f = fund(env)
            parts.insert(0, f"top-up on: below ${f.floor_micro / MICRO:g} on Base, up to ${f.target_micro / MICRO:g},"
                            f" at most ${f.day_micro / MICRO:g} a day")
    if not on:
        parts.insert(0, "top-up off")
    return "; ".join(parts)
