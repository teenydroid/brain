"""The RENT_* keys of hands/rent, read and checked in one place, so the hand
and bin/doctor cannot disagree. A value that is unreadable, negative or over
its ceiling is an error, never a default: a typo must not lift a limit.

  RENTAHUMAN_API_KEY        the box's own `rah_` key (rentahuman.ai/account/api-keys);
                            every verb but `status`'s caps needs it
  RENTAHUMAN_API_BASE       where RentAHuman answers (default https://rentahuman.ai;
                            https only, or loopback for the tests)
  RENT_MAX_PER_BOUNTY_USD   the most one bounty may cost, all in: the worker's
                            pay plus RentAHuman's service fee (default 25,
                            ceiling 500)
  RENT_MAX_PER_DAY_USD      the most the bounties posted in one UTC day may
                            cost together (default 50, ceiling 1000); 0 is
                            the off switch: nothing is posted
  RENT_OPEN_MAX             bounties open at once (default 3, ceiling 20)
  RENT_AUTO_ACCEPT_HOURS    hours a posted bounty waits for Teeny to pick an
                            applicant before `poll` accepts RentAHuman's best
                            match (default 6, ceiling 168; 0 never does)

The money is in the RentAHuman wallet the operator tops up by card; Teeny's
own wallet and its caps are not involved. RentAHuman's own wallet controls
(`spendingCapPer24h`) are a second fence the operator sets to the same day
cap.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Mapping

CENTS = 100

#: key -> (default, ceiling, may be zero)
AMOUNTS = {
    "RENT_MAX_PER_BOUNTY_USD": ("25", 500, False),
    "RENT_MAX_PER_DAY_USD": ("50", 1000, True),
}
#: key -> (default, ceiling, may be zero)
COUNTS = {
    "RENT_OPEN_MAX": (3, 20, False),
    "RENT_AUTO_ACCEPT_HOURS": (6, 168, True),
}
#: The keys this file reads (and bin/doctor checks).
KEYS = (*AMOUNTS, *COUNTS)


class SettingsError(Exception):
    """A key is wrong. Says which, never a secret (none of these is one)."""


@dataclass(frozen=True)
class Rent:
    per_bounty_cents: int
    per_day_cents: int
    open_max: int
    auto_accept_hours: int

    @property
    def on(self) -> bool:
        return self.per_day_cents > 0


def _raw(env: Mapping[str, str], key: str) -> str:
    return str(env.get(key) or "").strip()


def _amount(env: Mapping[str, str], key: str) -> int:
    default, ceiling, zero = AMOUNTS[key]
    raw = _raw(env, key) or default
    try:
        value = Decimal(raw)
    except InvalidOperation:
        raise SettingsError(f"{key} is not a number") from None
    if not value.is_finite() or value < 0 or (value == 0 and not zero):
        raise SettingsError(f"{key} must be a number above zero" + (" (or 0, off)" if zero else ""))
    if value > ceiling:
        raise SettingsError(f"{key} is above its ceiling of {ceiling}")
    cents = value * CENTS
    if cents != cents.to_integral_value():
        raise SettingsError(f"{key} has more than two decimals")
    return int(cents)


def _count(env: Mapping[str, str], key: str) -> int:
    default, ceiling, zero = COUNTS[key]
    raw = _raw(env, key)
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError:
        raise SettingsError(f"{key} is a whole number") from None
    if value < 0 or (value == 0 and not zero):
        raise SettingsError(f"{key} must be above zero" + (" (or 0, never)" if zero else ""))
    if value > ceiling:
        raise SettingsError(f"{key} is above its ceiling of {ceiling}")
    return value


def load(env: Mapping[str, str]) -> Rent:
    """The settings, or SettingsError naming the first wrong key."""
    found = Rent(
        _amount(env, "RENT_MAX_PER_BOUNTY_USD"),
        _amount(env, "RENT_MAX_PER_DAY_USD"),
        _count(env, "RENT_OPEN_MAX"),
        _count(env, "RENT_AUTO_ACCEPT_HOURS"),
    )
    if found.on and found.per_bounty_cents > found.per_day_cents:
        raise SettingsError("RENT_MAX_PER_BOUNTY_USD is above RENT_MAX_PER_DAY_USD: no bounty could ever be posted")
    return found


def check(env: Mapping[str, str]) -> str:
    """bin/doctor: one line saying what is on, or SettingsError."""
    found = load(env)
    if not found.on:
        return "rent off (RENT_MAX_PER_DAY_USD=0)"
    if not _raw(env, "RENTAHUMAN_API_KEY"):
        return "rent has no RENTAHUMAN_API_KEY: nothing is posted until the operator adds the box's key"
    accept = f"auto-accept after {found.auto_accept_hours}h" if found.auto_accept_hours else "no auto-accept"
    return (f"rent on: ${found.per_bounty_cents / CENTS:g} a bounty, ${found.per_day_cents / CENTS:g} a day,"
            f" {found.open_max} open at once, {accept}")
