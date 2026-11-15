"""The giveaway pool's caps and switch (docs/governance.md, `giveaway-pool`;
deploy/teeny.env.example, "Tiny Drops").

Read from secrets/teeny.env, never the environment:

  GIVEAWAYS                      the switch: off unless truthy (L3 under `spend`
                                 in evals/autonomy.toml). Off, no round opens, is
                                 judged, drawn or settled; a clawback, which only
                                 returns tokens to the pool, still runs
  GIVEAWAY_POOL_MAX_TINY         the most one round's pool may hold
  GIVEAWAY_SEASON_MAX_TINY       the most opened in any rolling 30 days
  GIVEAWAY_ROUNDS_PER_WEEK       rounds opened in any rolling 7 days
  GIVEAWAY_MAX_PER_WINNER_TINY   the most one winner may get from one round
  GIVEAWAY_CLAWBACK_DAYS         days a prize may be claimed before the pool
                                 takes back what is left
  GIVEAWAY_X_LOOKUPS_PER_ROUND   paid `hands/x users` calls one draw may make
  GIVEAWAY_CLUSTER               devnet or mainnet. The core signs and sends on
                                 its own cluster (WALLET_CLUSTER), so the two
                                 must agree or nothing is signed
  GIVEAWAY_DEVNET_MINT           devnet only, and required there: a Token-2022
                                 test mint with 6 decimals standing in for
                                 $TINY, which exists on mainnet only

Each one is required. A cap that is missing, zero, unreadable or negative is
an error (rule `caps`), never a default: a typo must not lift a cap, as in
hands/wallet's caps. $TINY amounts are whole tokens, held here as atomic units
(10^-6, TINY_DECIMALS), never a float.

`policy_account` is the core's own copy of these caps: bin/lib/wallet_config.py
writes it as `[wallet_policy.accounts.solana_1]`, the only thing that lets the
giveaway wallet (Solana index 1) sign at all. It lists the Rewards program
alone (every transaction the hand builds has that one instruction; the program
moves the tokens itself), and one mint, the round's, at GIVEAWAY_POOL_MAX_TINY
a transaction and GIVEAWAY_SEASON_MAX_TINY in any 24 hours (no day may spend
more than a season; anything tighter would refuse a full pool). With the
switch off, or the caps not sane, there is no section and the core refuses
every transaction the giveaway wallet is asked to sign.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Mapping

from .. import hand
from ..wallet import caps as wallet_caps
from ..wallet import solana

SWITCH = "GIVEAWAYS"
POOL_MAX = "GIVEAWAY_POOL_MAX_TINY"
SEASON_MAX = "GIVEAWAY_SEASON_MAX_TINY"
ROUNDS_PER_WEEK = "GIVEAWAY_ROUNDS_PER_WEEK"
MAX_PER_WINNER = "GIVEAWAY_MAX_PER_WINNER_TINY"
CLAWBACK_DAYS = "GIVEAWAY_CLAWBACK_DAYS"
X_LOOKUPS = "GIVEAWAY_X_LOOKUPS_PER_ROUND"
CLUSTER = "GIVEAWAY_CLUSTER"
DEVNET_MINT = "GIVEAWAY_DEVNET_MINT"
CAP_KEYS = (POOL_MAX, SEASON_MAX, ROUNDS_PER_WEEK, MAX_PER_WINNER, CLAWBACK_DAYS, X_LOOKUPS, CLUSTER)

#: $TINY: Token-2022, 6 decimals (docs/drops-schema.md, "The $TINY mint").
TINY_MINT = wallet_caps.TINY_MINT
TINY_DECIMALS = 6
ATOMIC = 10**TINY_DECIMALS
TOKEN_2022 = "TokenzQdBNbLqP5VEhdkAS6EPFLC1PHnBqCXEpPxuEb"
TOKEN_PROGRAM = "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA"
#: The Solana Foundation Rewards program every round is paid through.
REWARDS_PROGRAM = "REWArDioXgQJ2fZKkfu9LCLjQfRwYWVVfsvcsR5hoXi"
CLUSTERS = ("devnet", "mainnet")
#: Ceilings no setting lifts: a pool of a billion $TINY, a year of clawback.
MAX_TINY = Decimal(10**9)
MAX_ROUNDS_PER_WEEK = 14
MAX_CLAWBACK_DAYS = 365
MAX_X_LOOKUPS = 20
#: The giveaway wallet: Solana account index 1 (m/44'/501'/1'/0') in the core,
#: under its own policy section.
ACCOUNT_INDEX = 1
POLICY_ACCOUNT = "solana_1"


@dataclass(frozen=True)
class Caps:
    pool_max: int  # atomic
    season_max: int  # atomic
    rounds_per_week: int
    max_per_winner: int  # atomic
    clawback_days: int
    x_lookups: int
    cluster: str


class CapsError(Exception):
    """A cap is missing or not sane. Says which key."""


def to_atomic(value: object, what: str) -> int:
    """Whole $TINY (a decimal string or number, at most 6 decimals, above
    zero) as atomic units; ValueError otherwise."""
    if isinstance(value, bool) or value is None:
        raise ValueError(f"{what} is a positive number of $TINY")
    try:
        tokens = Decimal(str(value).strip())
    except (InvalidOperation, ValueError):
        raise ValueError(f"{what} is a positive number of $TINY") from None
    if not tokens.is_finite() or tokens <= 0 or tokens > MAX_TINY:
        raise ValueError(f"{what} is above zero and at most {MAX_TINY} $TINY")
    atomic = tokens * ATOMIC
    if atomic != atomic.to_integral_value():
        raise ValueError(f"{what} has at most {TINY_DECIMALS} decimals")
    return int(atomic)


def display(atomic: int) -> str:
    """Atomic units as whole $TINY, the contract's decimal string: 1234500000 -> "1234.5"."""
    whole, frac = divmod(int(atomic), ATOMIC)
    tail = str(frac).rjust(TINY_DECIMALS, "0").rstrip("0")
    return f"{whole}.{tail}" if tail else str(whole)


def _whole(env: Mapping[str, str], key: str, ceiling: int) -> int:
    raw = str(env.get(key) or "").strip()
    if not raw:
        raise CapsError(f"{key} is not set")
    try:
        value = int(raw)
    except ValueError:
        raise CapsError(f"{key} is not a whole number") from None
    if value <= 0:
        raise CapsError(f"{key} must be above zero")
    if value > ceiling:
        raise CapsError(f"{key} is above its ceiling of {ceiling}")
    return value


def _tiny(env: Mapping[str, str], key: str) -> int:
    raw = str(env.get(key) or "").strip()
    if not raw:
        raise CapsError(f"{key} is not set")
    try:
        return to_atomic(raw, key)
    except ValueError as exc:
        raise CapsError(str(exc)) from None


def load(env: Mapping[str, str]) -> Caps:
    """The caps, or CapsError naming the first key that is wrong."""
    cluster = str(env.get(CLUSTER) or "").strip().lower()
    if cluster not in CLUSTERS:
        raise CapsError(f"{CLUSTER} is devnet or mainnet")
    caps = Caps(
        pool_max=_tiny(env, POOL_MAX),
        season_max=_tiny(env, SEASON_MAX),
        rounds_per_week=_whole(env, ROUNDS_PER_WEEK, MAX_ROUNDS_PER_WEEK),
        max_per_winner=_tiny(env, MAX_PER_WINNER),
        clawback_days=_whole(env, CLAWBACK_DAYS, MAX_CLAWBACK_DAYS),
        x_lookups=_whole(env, X_LOOKUPS, MAX_X_LOOKUPS),
        cluster=cluster,
    )
    if caps.pool_max > caps.season_max:
        raise CapsError(f"{POOL_MAX} is above {SEASON_MAX}")
    if caps.max_per_winner > caps.pool_max:
        raise CapsError(f"{MAX_PER_WINNER} is above {POOL_MAX}")
    return caps


def mint(cluster: str, env: Mapping[str, str]) -> str:
    """The mint a round on `cluster` pays: $TINY on mainnet, the operator's
    GIVEAWAY_DEVNET_MINT on devnet (CapsError when it is not set)."""
    if cluster == "mainnet":
        return TINY_MINT
    value = str(env.get(DEVNET_MINT) or "").strip()
    if not solana.is_address(value) or value == TINY_MINT:
        raise CapsError(f"{DEVNET_MINT} (a devnet Token-2022 mint with {TINY_DECIMALS} decimals) is not set")
    return value


def policy_account(env: Mapping[str, str], core_cluster: str) -> dict | None:
    """The core's `[wallet_policy.accounts.solana_1]`, or None when the switch
    is off. CapsError when it is on but the caps are not usable, or name
    another cluster than the core's."""
    if str(env.get(SWITCH) or "").strip().lower() not in ("1", "true", "yes", "on"):
        return None
    loaded = load(env)
    if loaded.cluster != core_cluster:
        raise CapsError(f"{CLUSTER} is {loaded.cluster} but the core's wallet is on {core_cluster} (WALLET_CLUSTER)")
    return {
        "allowed_programs": [REWARDS_PROGRAM],
        "mints": [{"mint": mint(loaded.cluster, env), "per_tx_max_raw": loaded.pool_max, "daily_max_raw": loaded.season_max}],
    }


def on() -> bool:
    return hand.flag(SWITCH, False)


def require_on(verb: str) -> None:
    if not on():
        raise hand.Refused(f"Tiny Drops are off (GIVEAWAYS is not set in secrets/teeny.env); nothing was {verb}",
                           rule="giveaways_off")


def current() -> Caps:
    """The caps from secrets/teeny.env, refused (rule `caps`) when not sane."""
    try:
        return load(hand.secrets())
    except CapsError as exc:
        raise hand.Refused(f"the giveaway caps are not usable: {exc}", rule="caps") from None
