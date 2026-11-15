"""The SURPLUS_* keys of hands/surplus, read and checked in one place, so the
hand and bin/doctor cannot disagree. A value that is unreadable, zero where a
number above zero is needed, or over its ceiling is an error, never a
default: a typo must not lift a limit.

  SURPLUS_SWEEP            1 turns the sweep on (off by default)
  SURPLUS_ADDRESS          the surplus wallet, an EVM address that takes USDC
                           on Base (required once on). It must also be an
                           allowlisted vendor on base: `hands/wallet vendors
                           add surplus ADDRESS --chain base`
  SURPLUS_KEEP_SOL         SOL left on Solana for fees and the x402 rail's
                           own top-ups (default 0.1, ceiling 10)
  SURPLUS_KEEP_BASE_USD    USDC left on Base for the x402 rail (default 10,
                           ceiling 200); at least X402_FUND_TARGET_USD while
                           X402_FUND=1, or the two jobs would bridge in and
                           pay out the same dollars in a loop
  SURPLUS_MIN_BRIDGE_SOL   no bridge smaller than this (default 0.05, ceiling
                           10): a small Solana bridge loses its rent and fees
                           (the loss check refuses one under about 0.07)
  SURPLUS_MIN_PAYOUT_USD   no payout smaller than this (default 1, ceiling 100)
  SURPLUS_MIN_GAS_ETH      ETH on Base the payout needs first, for gas
                           (default 0.00002, ceiling 1); the bridge, which
                           pays the surplus wallet directly, needs none

Every dollar moved is still held under the wallet's own caps
(WALLET_MAX_PER_TX_USD, WALLET_MAX_PER_DAY_USD,
WALLET_MAX_PER_RECIPIENT_WEEK_USD, WALLET_MAX_CONVERT_PER_DAY_USD): these
keys only ever narrow them.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Mapping

MICRO = 1_000_000
LAMPORTS = 10**9
WEI = 10**18

#: (scale, default, ceiling) per amount key.
AMOUNTS = {
    "SURPLUS_KEEP_SOL": (LAMPORTS, "0.1", 10),
    "SURPLUS_KEEP_BASE_USD": (MICRO, "10", 200),
    "SURPLUS_MIN_BRIDGE_SOL": (LAMPORTS, "0.05", 10),
    "SURPLUS_MIN_PAYOUT_USD": (MICRO, "1", 100),
    "SURPLUS_MIN_GAS_ETH": (WEI, "0.00002", 1),
}
#: The keys this file reads (and bin/doctor checks).
KEYS = ("SURPLUS_SWEEP", "SURPLUS_ADDRESS", *AMOUNTS)

_EVM = re.compile(r"^0x[0-9a-fA-F]{40}$")


class SettingsError(Exception):
    """A key is wrong. Says which, never a secret (none of these is one)."""


@dataclass(frozen=True)
class Sweep:
    address: str
    keep_sol_raw: int
    keep_base_micro: int
    min_bridge_raw: int
    min_payout_micro: int
    min_gas_wei: int


def _raw(env: Mapping[str, str], key: str) -> str:
    return str(env.get(key) or "").strip()


def on(env: Mapping[str, str]) -> bool:
    value = _raw(env, "SURPLUS_SWEEP").lower()
    if value in ("", "0", "false", "no", "off"):
        return False
    if value in ("1", "true", "yes", "on"):
        return True
    raise SettingsError(f"SURPLUS_SWEEP is 1 or 0, not {value!r}")


def _amount(env: Mapping[str, str], key: str) -> int:
    scale, default, ceiling = AMOUNTS[key]
    raw = _raw(env, key) or default
    try:
        value = Decimal(raw)
    except InvalidOperation:
        raise SettingsError(f"{key} is not a number") from None
    if not value.is_finite() or value <= 0:
        raise SettingsError(f"{key} must be a number above zero")
    if value > ceiling:
        raise SettingsError(f"{key} is above its ceiling of {ceiling}")
    units = value * scale
    if units != units.to_integral_value():
        raise SettingsError(f"{key} has too many decimals")
    return int(units)


def _address(env: Mapping[str, str]) -> str:
    address = _raw(env, "SURPLUS_ADDRESS")
    if not address:
        raise SettingsError("SURPLUS_ADDRESS is not set")
    if not _EVM.match(address):
        raise SettingsError("SURPLUS_ADDRESS is an EVM address, 0x and 40 hex digits")
    return address


def _rail_target(env: Mapping[str, str]) -> int | None:
    """X402_FUND_TARGET_USD in micro-dollars while the x402 top-up is on."""
    from ..x402 import settings as x402

    try:
        if not x402.fund_on(env):
            return None
        return x402.fund(env).target_micro
    except x402.SettingsError:
        return None  # bin/doctor's x402 check names that one


def load(env: Mapping[str, str]) -> Sweep:
    """The sweep's settings, or SettingsError naming the first wrong key."""
    found = Sweep(
        _address(env),
        _amount(env, "SURPLUS_KEEP_SOL"),
        _amount(env, "SURPLUS_KEEP_BASE_USD"),
        _amount(env, "SURPLUS_MIN_BRIDGE_SOL"),
        _amount(env, "SURPLUS_MIN_PAYOUT_USD"),
        _amount(env, "SURPLUS_MIN_GAS_ETH"),
    )
    target = _rail_target(env)
    if target is not None and found.keep_base_micro < target:
        raise SettingsError(
            "SURPLUS_KEEP_BASE_USD must be at least X402_FUND_TARGET_USD while X402_FUND=1, or the top-up and the"
            " sweep would move the same dollars back and forth")
    return found


def check(env: Mapping[str, str]) -> str:
    """bin/doctor: one line saying what is on, or SettingsError. Off, the keys
    that are set are still checked, so turning it on cannot surprise."""
    if not on(env):
        for key in AMOUNTS:
            _amount(env, key)
        if _raw(env, "SURPLUS_ADDRESS"):
            _address(env)
        return "surplus sweep off"
    found = load(env)
    return (f"surplus sweep on: SOL above {found.keep_sol_raw / LAMPORTS:g} bridged to Base, USDC on Base above"
            f" ${found.keep_base_micro / MICRO:g} paid to {found.address}")
