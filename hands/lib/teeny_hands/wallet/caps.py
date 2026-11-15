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

Optional, each switching on one more thing the wallet may do; unset, that
thing is refused (rule `caps`) and nothing else changes:

  WALLET_ETH_USD                     what one ETH counts as; without it no ETH moves
  WALLET_MAX_CONVERT_PER_DAY_USD     swaps and bridges, by input value, in any rolling
                                     24 hours; without it neither runs
  WALLET_MAX_CONVERT_LOSS_BPS        the most a swap or bridge may lose to fees and
                                     slippage, in basis points of its input (default
                                     300, ceiling 1000)
  WALLET_CONVERT_PROVIDER            who quotes swaps and bridges: lifi or debridge
                                     for both; unset, LI.FI bridges and deBridge
                                     swaps; anything else is an error
  WALLET_MAX_X402_PER_REQUEST_USD    the most one paid HTTP request (x402) may cost
  WALLET_MAX_X402_PER_DAY_USD        x402 spend in any rolling 24 hours; without both
                                     no x402 payment is made

There is no price feed. SOL is counted at the fixed WALLET_SOL_USD the
operator sets, and it should be set at or above the market price: a SOL
payment then counts for at least what it is worth, so the dollar caps bind
early rather than late. ETH the same, at
WALLET_ETH_USD. USDC counts one for one, on every chain.

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
DECIMALS = {"SOL": 9, "ETH": 18, "USDC": 6}

PER_TX = "WALLET_MAX_PER_TX_USD"
PER_DAY = "WALLET_MAX_PER_DAY_USD"
PER_RECIPIENT_WEEK = "WALLET_MAX_PER_RECIPIENT_WEEK_USD"
SOL_USD = "WALLET_SOL_USD"
CAP_KEYS = (PER_TX, PER_DAY, PER_RECIPIENT_WEEK, SOL_USD)

ETH_USD = "WALLET_ETH_USD"
CONVERT_PER_DAY = "WALLET_MAX_CONVERT_PER_DAY_USD"
CONVERT_LOSS_BPS = "WALLET_MAX_CONVERT_LOSS_BPS"
X402_PER_REQUEST = "WALLET_MAX_X402_PER_REQUEST_USD"
X402_PER_DAY = "WALLET_MAX_X402_PER_DAY_USD"
CONVERT_PROVIDER = "WALLET_CONVERT_PROVIDER"
#: Who may quote a swap or a bridge (convert.py).
PROVIDERS = ("lifi", "debridge")
#: Who quotes each kind when WALLET_CONVERT_PROVIDER is unset: the cheaper
#: on Teeny's sizes. A deBridge bridge pays a flat 0.015 SOL from Solana
#: (about 22% of $8), LI.FI about 3%; a small Solana swap is the other way
#: round (deBridge about 0.6%, LI.FI 3.1%). docs/hands.md, "swap and bridge".
DEFAULT_PROVIDERS = {"bridge": "lifi", "swap": "debridge"}
OPTIONAL_KEYS = (ETH_USD, CONVERT_PER_DAY, X402_PER_REQUEST, X402_PER_DAY)

#: Anything above these is a typo or a mistake, not a policy: the doctor
#: refuses to boot it and the hand refuses to pay under it.
CEILINGS = {
    PER_TX: 500, PER_DAY: 2000, PER_RECIPIENT_WEEK: 2000, SOL_USD: 10000,
    ETH_USD: 100000, CONVERT_PER_DAY: 2000, X402_PER_REQUEST: 5, X402_PER_DAY: 100,
}
DEFAULT_LOSS_BPS = 300
MAX_LOSS_BPS = 1000

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
    eth_usd_micro: int | None = None
    convert_day_micro: int | None = None
    convert_loss_bps: int = DEFAULT_LOSS_BPS
    x402_request_micro: int | None = None
    x402_day_micro: int | None = None
    convert_provider: str | None = None  # None: DEFAULT_PROVIDERS by kind

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


def _dollars(env: Mapping[str, str], key: str, required: bool) -> int | None:
    raw = (env.get(key) or "").strip()
    if not raw:
        if required:
            raise CapsError(f"{key} is not set")
        return None
    micro = dollars_to_micro(raw, key)
    if micro <= 0:
        raise CapsError(f"{key} must be above zero")
    if micro > CEILINGS[key] * MICRO:
        raise CapsError(f"{key} is above its ceiling of {CEILINGS[key]}")
    return micro


def _loss_bps(env: Mapping[str, str]) -> int:
    raw = (env.get(CONVERT_LOSS_BPS) or "").strip()
    if not raw:
        return DEFAULT_LOSS_BPS
    if not raw.isdigit() or not 0 < int(raw) <= MAX_LOSS_BPS:
        raise CapsError(f"{CONVERT_LOSS_BPS} is a whole number of basis points, 1..{MAX_LOSS_BPS}")
    return int(raw)


def provider_for(the_caps: Caps, kind: str) -> str:
    """Who quotes a `kind` ("swap" or "bridge") when the call names nobody."""
    return the_caps.convert_provider or DEFAULT_PROVIDERS[kind]


def _provider(env: Mapping[str, str]) -> str | None:
    raw = (env.get(CONVERT_PROVIDER) or "").strip()
    if not raw:
        return None
    if raw not in PROVIDERS:
        raise CapsError(f"{CONVERT_PROVIDER} is {' or '.join(PROVIDERS)}, not {raw!r}")
    return raw


def load(env: Mapping[str, str]) -> Caps:
    """The caps, or CapsError naming the first key that is wrong."""
    values: dict[str, int] = {key: _dollars(env, key, True) for key in CAP_KEYS}  # type: ignore[misc]
    optional = {key: _dollars(env, key, False) for key in OPTIONAL_KEYS}
    if values[PER_TX] > values[PER_DAY]:
        raise CapsError(f"{PER_TX} is above {PER_DAY}")
    if values[PER_RECIPIENT_WEEK] < values[PER_TX]:
        raise CapsError(f"{PER_RECIPIENT_WEEK} is below {PER_TX}: no single payment could go out")
    if (optional[X402_PER_REQUEST] is None) != (optional[X402_PER_DAY] is None):
        raise CapsError(f"{X402_PER_REQUEST} and {X402_PER_DAY} are set together or not at all")
    if optional[X402_PER_REQUEST] is not None and optional[X402_PER_REQUEST] > optional[X402_PER_DAY]:
        raise CapsError(f"{X402_PER_REQUEST} is above {X402_PER_DAY}")
    return Caps(
        cluster(env), values[PER_TX], values[PER_DAY], values[PER_RECIPIENT_WEEK], values[SOL_USD],
        eth_usd_micro=optional[ETH_USD],
        convert_day_micro=optional[CONVERT_PER_DAY],
        convert_loss_bps=_loss_bps(env),
        x402_request_micro=optional[X402_PER_REQUEST],
        x402_day_micro=optional[X402_PER_DAY],
        convert_provider=_provider(env),
    )


def usd_micro(asset: str, raw: int, caps: Caps) -> int:
    """What `raw` atomic units of `asset` count as against the caps, in
    micro-dollars, rounded up."""
    if asset == "USDC":
        return raw
    if asset == "SOL":
        return -(-raw * caps.sol_usd_micro // 10**9)
    if asset == "ETH":
        if caps.eth_usd_micro is None:
            raise CapsError(f"{ETH_USD} is not set, so no ETH may move")
        return -(-raw * caps.eth_usd_micro // 10**18)
    raise CapsError(f"no cap counts {asset}")


def raw_for_usd(asset: str, micro: int, caps: Caps) -> int:
    """The most of `asset`, in atomic units, that stays within `micro`
    micro-dollars (rounded down): the core's caps in its own units."""
    if asset == "USDC":
        return micro
    if asset == "SOL":
        return micro * 10**9 // caps.sol_usd_micro
    if asset == "ETH":
        if caps.eth_usd_micro is None:
            raise CapsError(f"{ETH_USD} is not set, so no ETH may move")
        return micro * 10**18 // caps.eth_usd_micro
    raise CapsError(f"no cap counts {asset}")


def policy_assets(caps: Caps) -> list[dict[str, object]]:
    """`[[wallet_policy.assets]]` for the core: SOL, and USDC with its mint
    pinned, each capped at the dollar caps in its own units; on mainnet also
    USDC on every EVM network the wallet knows (its contract pinned) and ETH
    there when WALLET_ETH_USD is set. The core keeps each asset's totals
    apart; the hand also keeps the combined ones."""
    from . import chains  # chains imports hand, which caps must not need at import

    rows: list[tuple[dict[str, object], str]] = []
    for asset in ASSETS:
        entry: dict[str, object] = {"chain": "solana", "symbol": asset}
        if asset == "USDC":
            entry["token_address"] = caps.usdc_mint
        rows.append((entry, asset))
    if caps.cluster == "mainnet":
        for network in chains.NETWORKS.values():
            rows.append(({"chain": "evm", "symbol": "USDC", "evm_network": network.evm_network, "token_address": network.usdc["mainnet"]}, "USDC"))
            if caps.eth_usd_micro is not None:
                rows.append(({"chain": "evm", "symbol": network.native, "evm_network": network.evm_network}, network.native))
    out: list[dict[str, object]] = []
    for entry, asset in rows:
        entry["per_tx_max_raw"] = min(raw_for_usd(asset, caps.per_tx_micro, caps), U64_MAX)
        entry["daily_max_raw"] = min(raw_for_usd(asset, caps.per_day_micro, caps), U64_MAX)
        entry["recipient_weekly_max_raw"] = min(raw_for_usd(asset, caps.recipient_week_micro, caps), U64_MAX)
        out.append(entry)
    return out


#: The core holds a cap as a u64.
U64_MAX = 2**64 - 1


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
