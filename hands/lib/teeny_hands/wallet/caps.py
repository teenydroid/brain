"""The wallet's hard caps, and the addresses it knows by heart.

Read from secrets/teeny.env by three callers, so they cannot disagree:
hands/wallet (every `pay`), bin/lib/wallet_config.py (the core's
`[wallet_policy]`, written at every boot) and bin/doctor (which refuses to
boot a checkout whose wallet keys are set without sane caps).

  WALLET_CLUSTER                     devnet (the default) or mainnet
  WALLET_MAX_PER_TX_USD              the largest single payment, in dollars
  WALLET_MAX_PER_DAY_USD             the most paid out in any rolling 24 hours
  WALLET_MAX_PER_RECIPIENT_WEEK_USD  the most paid to one address in any rolling 7 days
  WALLET_SOL_USD                     what one SOL counts as, in dollars, for the caps

There is no price feed. SOL is counted at the fixed WALLET_SOL_USD the
operator sets, and it should be set at or above the market price: a SOL
payment then counts for at least what it is worth, so the dollar caps bind
early rather than late. USDC counts one for one.

Every amount is held as whole micro-dollars (10^-6) or the asset's atomic
units, never a float. A cap that is missing, zero, unreadable or above its
ceiling is an error, never a default: a typo must not lift a cap.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Mapping

#: $TINY on Solana (brain/CONTEXT.md). Teeny never sends it, whoever asks.
TINY_MINT = "2AF7CqwieUjUPALL7icuZtL3X7wENdjUjGBMmfV2pump"

#: USDC's mint per cluster, pinned: a quote or a policy naming another mint
#: for "USDC" is not USDC.
USDC_MINTS = {
    "mainnet": "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v",
    "devnet": "4zMMC9srt5Ri5X14GAgXhaHii3GnPAEERYPJgZJDncDU",
}

#: The public Solana RPC per cluster, when SOLANA_RPC_URL is unset.
RPC_DEFAULTS = {
    "mainnet": "https://api.mainnet-beta.solana.com",
    "devnet": "https://api.devnet.solana.com",
}

CLUSTERS = ("devnet", "mainnet")
ASSETS = ("SOL", "USDC")
DECIMALS = {"SOL": 9, "USDC": 6}

PER_TX = "WALLET_MAX_PER_TX_USD"
PER_DAY = "WALLET_MAX_PER_DAY_USD"
PER_RECIPIENT_WEEK = "WALLET_MAX_PER_RECIPIENT_WEEK_USD"
SOL_USD = "WALLET_SOL_USD"
CAP_KEYS = (PER_TX, PER_DAY, PER_RECIPIENT_WEEK, SOL_USD)

#: Anything above these is a typo or a mistake, not a policy: the doctor
#: refuses to boot it and the hand refuses to pay under it.
CEILINGS = {PER_TX: 500, PER_DAY: 2000, PER_RECIPIENT_WEEK: 2000, SOL_USD: 10000}

MICRO = 1_000_000


class CapsError(Exception):
    """The caps are missing or not sane. Says which key, never a secret."""


@dataclass(frozen=True)
class Caps:
    cluster: str
    per_tx_micro: int
    per_day_micro: int
    recipient_week_micro: int
    sol_usd_micro: int

    @property
    def usdc_mint(self) -> str:
        return USDC_MINTS[self.cluster]


def configured(env: Mapping[str, str]) -> bool:
    """Whether any WALLET_* key is set: from then on every cap must be."""
    return any(key.startswith("WALLET_") and str(value).strip() for key, value in env.items())


def cluster(env: Mapping[str, str]) -> str:
    value = (env.get("WALLET_CLUSTER") or "devnet").strip().lower()
    if value not in CLUSTERS:
        raise CapsError(f"WALLET_CLUSTER is devnet or mainnet, not {value!r}")
    return value


def dollars_to_micro(raw: object, what: str) -> int:
    """A decimal dollar amount ("12.5") as whole micro-dollars. At most six
    decimals; never a float on the way."""
    try:
        value = Decimal(str(raw).strip())
    except (InvalidOperation, ValueError):
        raise CapsError(f"{what} is not a number") from None
    if not value.is_finite():
        raise CapsError(f"{what} is not a number")
    micro = value * MICRO
    if micro != micro.to_integral_value():
        raise CapsError(f"{what} has more than 6 decimals")
    return int(micro)


def load(env: Mapping[str, str]) -> Caps:
    """The caps, or CapsError naming the first key that is wrong."""
    values: dict[str, int] = {}
    for key in CAP_KEYS:
        raw = (env.get(key) or "").strip()
        if not raw:
            raise CapsError(f"{key} is not set")
        micro = dollars_to_micro(raw, key)
        if micro <= 0:
            raise CapsError(f"{key} must be above zero")
        if micro > CEILINGS[key] * MICRO:
            raise CapsError(f"{key} is above its ceiling of {CEILINGS[key]}")
        values[key] = micro
    if values[PER_TX] > values[PER_DAY]:
        raise CapsError(f"{PER_TX} is above {PER_DAY}")
    if values[PER_RECIPIENT_WEEK] < values[PER_TX]:
        raise CapsError(f"{PER_RECIPIENT_WEEK} is below {PER_TX}: no single payment could go out")
    return Caps(cluster(env), values[PER_TX], values[PER_DAY], values[PER_RECIPIENT_WEEK], values[SOL_USD])


def usd_micro(asset: str, raw: int, caps: Caps) -> int:
    """What `raw` atomic units of `asset` count as against the caps, in
    micro-dollars, rounded up."""
    if asset == "USDC":
        return raw
    if asset == "SOL":
        return -(-raw * caps.sol_usd_micro // 10**9)
    raise CapsError(f"no cap counts {asset}")


def raw_for_usd(asset: str, micro: int, caps: Caps) -> int:
    """The most of `asset`, in atomic units, that stays within `micro`
    micro-dollars (rounded down): the core's caps in its own units."""
    if asset == "USDC":
        return micro
    if asset == "SOL":
        return micro * 10**9 // caps.sol_usd_micro
    raise CapsError(f"no cap counts {asset}")


def policy_assets(caps: Caps) -> list[dict[str, object]]:
    """`[[wallet_policy.assets]]` for the core: SOL, and USDC with its mint
    pinned, each capped at the dollar caps in its own units. The core keeps
    each asset's totals apart; the hand also keeps the combined ones."""
    out: list[dict[str, object]] = []
    for asset in ASSETS:
        entry: dict[str, object] = {"chain": "solana", "symbol": asset}
        if asset == "USDC":
            entry["token_address"] = caps.usdc_mint
        entry["per_tx_max_raw"] = raw_for_usd(asset, caps.per_tx_micro, caps)
        entry["daily_max_raw"] = raw_for_usd(asset, caps.per_day_micro, caps)
        entry["recipient_weekly_max_raw"] = raw_for_usd(asset, caps.recipient_week_micro, caps)
        out.append(entry)
    return out


def display(raw: int, asset: str) -> str:
    """Atomic units as a plain decimal ("1.5"), trailing zeros dropped."""
    decimals = DECIMALS[asset]
    sign = "-" if raw < 0 else ""
    whole, frac = divmod(abs(raw), 10**decimals)
    text = f"{whole}.{frac:0{decimals}d}".rstrip("0").rstrip(".")
    return sign + text


def usd(micro: int) -> str:
    """Micro-dollars as dollars with cents ("12.50"), rounded half up."""
    cents = (micro + 5_000) // 10_000 if micro >= 0 else -((-micro + 5_000) // 10_000)
    sign = "-" if cents < 0 else ""
    whole, frac = divmod(abs(cents), 100)
    return f"{sign}{whole}.{frac:02d}"
