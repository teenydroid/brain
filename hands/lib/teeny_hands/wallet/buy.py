"""`buy-tiny`: turn a share of what Teeny earned into $TINY. Buys only.

No body and no option: the input is USDC, the output is $TINY, the amount
is computed here. A pinned job runs it once a day (bin/tiny-buy); no model
ever does (bin/lib/playbook.py refuses every wallet verb as an output).

Every rule runs before the core hears of the buy, each refused with its `rule`:

  off           TINY_BUY=1 in secrets/teeny.env, or nothing happens
  caps          the wallet's caps (caps.py) and the buy's own settings are
                set and sane; a value over its ceiling is an error, not a cap
  cluster       mainnet only: $TINY and Jupiter's routes live there

Then, under the `wallet-pay` lock (the one `pay` takes, so the two never
race past a cap), the amount:

  since         the last buy that may have moved funds, else 30 days ago
  earned        USDC that reached Teeny's own token accounts since then, in
                transactions Teeny did not sign, from no team address
                (TINY_BUY_TEAM_ADDRESSES) as a signer or a sender
  spent         what keeping Teeny running cost over the same days, plus
                what the wallet paid out (`costs`)
  amount        TINY_BUY_SHARE x max(0, earned - spent), then clamped by
                TINY_BUY_MAX_USDC_PER_BUY, WALLET_MAX_PER_TX_USD, what is
                left of TINY_BUY_MAX_USDC_PER_DAY and WALLET_MAX_PER_DAY_USD
                in the last 24 hours, and half the USDC the wallet holds.
                Under TINY_BUY_MIN_USDC it is skipped, and the row says why.

Then the quote (Jupiter's public quote API, jupiter.py), refused unless:

  direction     its input mint is the cluster's pinned USDC and its output
                mint is $TINY, the amount exactly the one computed, ExactIn
  route         the route plan starts at USDC and ends at $TINY, every hop
  slippage      its slippage is ours (TINY_BUY_SLIPPAGE_BPS, at most 1%) and
                its minimum output agrees with it
  price_impact  the reported impact, and the impact measured against a
                quote for a hundredth of the amount, are both at most
                TINY_BUY_MAX_PRICE_IMPACT_BPS (at most 1%)

Under DRY_RUN that is the end: `would_buy` with every check. Nothing is
written, the core is asked for nothing but the wallet's address, and
nothing is signed or sent.

Live, the row is written `pending`, then the core prepares the swap
(`web3_swap_quote`, the same core and the same signer as `pay`: the keys
never leave it). Its quote must name USDC in, the exact amount, $TINY out
to Teeny's own address, and a minimum output no lower than Jupiter's, or it
is `failed` (`quote`). Its unsigned transaction is simulated over Solana
RPC (`simulateTransaction`): it must succeed, take no more USDC than the
amount, and give at least the minimum $TINY to any $TINY account Teeny
holds, or it is `failed` (`simulate`). Then `web3_swap_execute`: a
signature makes it `bought`; anything else after the execute was sent is
`in_doubt`, which counts against every cap and is never retried.

There is no sell path. The output mint is a constant, the table refuses any
other, and no argument reaches it.
"""

from __future__ import annotations

import base64
import binascii
import math
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any, Callable, Mapping

from .. import hand
from . import caps, jupiter, reads, solana, store
from .pay import Core, load_caps, own_address

#: deBridge's chain id for Solana, the core's `web3_swap_quote` chainId.
SOLANA_CHAIN_ID = 7565164
#: The only output, ever. Not a setting, not an argument.
OUTPUT_MINT = caps.TINY_MINT

SHARE_MAX = Decimal("0.5")
#: Code ceilings: anything above is a typo, refused rather than obeyed.
CEIL_PER_DAY_USD = 100
CEIL_PER_BUY_USD = 50
SLIPPAGE_BPS_MAX = 100
IMPACT_BPS_MAX = 100
DEFAULTS = {"TINY_BUY_SLIPPAGE_BPS": 50, "TINY_BUY_MAX_PRICE_IMPACT_BPS": 50}
DEFAULT_MIN_USD = "1"
FIRST_WINDOW_DAYS = 30
#: The most incoming transactions read per run. More are left uncounted,
#: which only ever makes the buy smaller.
MAX_INFLOW_TXS = 200
PAGE = 100

ROUTE_LABEL_CHARS = 200


@dataclass(frozen=True)
class BuyConfig:
    share: Decimal
    per_day_micro: int
    per_buy_micro: int
    min_micro: int
    slippage_bps: int
    impact_bps: int
    team: frozenset[str]


def _usd(env: Mapping[str, str], key: str, ceiling: int, default: str | None = None) -> int:
    raw = (env.get(key) or default or "").strip()
    if not raw:
        raise caps.CapsError(f"{key} is not set")
    micro = caps.dollars_to_micro(raw, key)
    if micro <= 0:
        raise caps.CapsError(f"{key} must be above zero")
    if micro > ceiling * caps.MICRO:
        raise caps.CapsError(f"{key} is above its ceiling of {ceiling}")
    return micro


def _bps(env: Mapping[str, str], key: str, ceiling: int) -> int:
    raw = (env.get(key) or "").strip()
    if not raw:
        return DEFAULTS[key]
    if not raw.isdigit():
        raise caps.CapsError(f"{key} is a whole number of basis points")
    value = int(raw)
    if not 1 <= value <= ceiling:
        raise caps.CapsError(f"{key} is 1..{ceiling}")
    return value


def load_config(env: Mapping[str, str], wallet: caps.Caps) -> BuyConfig:
    """The buy's settings, clamped by the wallet's caps, or CapsError naming
    the first key that is wrong."""
    raw_share = (env.get("TINY_BUY_SHARE") or "0").strip()
    try:
        share = Decimal(raw_share)
    except InvalidOperation:
        raise caps.CapsError("TINY_BUY_SHARE is not a number") from None
    if not share.is_finite() or not Decimal(0) <= share <= SHARE_MAX:
        raise caps.CapsError(f"TINY_BUY_SHARE is 0..{SHARE_MAX}")
    per_day = min(_usd(env, "TINY_BUY_MAX_USDC_PER_DAY", CEIL_PER_DAY_USD), wallet.per_day_micro)
    per_buy = min(_usd(env, "TINY_BUY_MAX_USDC_PER_BUY", CEIL_PER_BUY_USD), wallet.per_tx_micro, per_day)
    minimum = _usd(env, "TINY_BUY_MIN_USDC", CEIL_PER_BUY_USD, DEFAULT_MIN_USD)
    if minimum > per_buy:
        raise caps.CapsError("TINY_BUY_MIN_USDC is above the most one buy may be: no buy could go out")
    team = frozenset(part for part in (env.get("TINY_BUY_TEAM_ADDRESSES") or "").replace(",", " ").split() if part)
    if not team:
        raise caps.CapsError("TINY_BUY_TEAM_ADDRESSES is not set: what the team sends must never count as earned")
    bad = sorted(a for a in team if not solana.is_address(a))
    if bad:
        raise caps.CapsError("TINY_BUY_TEAM_ADDRESSES holds something that is not a Solana address")
    return BuyConfig(
        share=share,
        per_day_micro=per_day,
        per_buy_micro=per_buy,
        min_micro=minimum,
        slippage_bps=_bps(env, "TINY_BUY_SLIPPAGE_BPS", SLIPPAGE_BPS_MAX),
        impact_bps=_bps(env, "TINY_BUY_MAX_PRICE_IMPACT_BPS", IMPACT_BPS_MAX),
        team=team,
    )


def share_of(net_micro: int, share: Decimal) -> int:
    """share x max(0, net), rounded down to whole micro-dollars."""
    return int((Decimal(max(0, net_micro)) * share).to_integral_value(rounding="ROUND_FLOOR"))


def clamp(share_micro: int, bounds: list[tuple[str, int]]) -> tuple[int, str]:
    """The amount after every bound, and which one decided it."""
    amount, by = share_micro, "share"
    for name, bound in bounds:
        if max(0, bound) < amount:
            amount, by = max(0, bound), name
    return amount, by


# --- what the chain says (internal reads; `hands/wallet tiny` is the public verb) -------------


def _token_accounts(cluster: str, owner: str, mint: str) -> dict[str, int]:
    """{token account: raw amount} for one owner and mint. Internal to the
    buy; the read verbs do their own."""
    result = solana.rpc(cluster, "getTokenAccountsByOwner", [owner, {"mint": mint}, {"encoding": "jsonParsed", "commitment": "confirmed"}])
    accounts = result.get("value") if isinstance(result, dict) else None
    if not isinstance(accounts, list):
        raise hand.HandError("Solana RPC getTokenAccountsByOwner sent no account list")
    out: dict[str, int] = {}
    for account in accounts:
        try:
            info = account["account"]["data"]["parsed"]["info"]
            if info.get("mint") != mint:
                continue
            out[str(account["pubkey"])] = int(info["tokenAmount"]["amount"])
        except (KeyError, TypeError, ValueError):
            raise hand.HandError("Solana RPC sent a token account it could not read") from None
    return out


def _signers(tx: Mapping[str, Any]) -> set[str]:
    keys = (((tx.get("transaction") or {}).get("message") or {}).get("accountKeys")) or []
    out = set()
    for key in keys:
        if isinstance(key, Mapping) and key.get("signer"):
            out.add(str(key.get("pubkey")))
    return out


def _deltas(tx: Mapping[str, Any], mint: str) -> dict[str, int]:
    """{owner: post - pre} of one mint across a transaction's token balances."""
    meta = tx.get("meta") or {}
    out: dict[str, int] = {}
    for sign, key in ((-1, "preTokenBalances"), (1, "postTokenBalances")):
        for row in meta.get(key) or []:
            if not isinstance(row, Mapping) or row.get("mint") != mint:
                continue
            try:
                amount = int(row["uiTokenAmount"]["amount"])
            except (KeyError, TypeError, ValueError):
                raise hand.HandError("Solana RPC sent a token balance it could not read") from None
            owner = str(row.get("owner"))
            out[owner] = out.get(owner, 0) + sign * amount
    return out


def earned_since(cluster: str, own: str, mint: str, since: float, team: frozenset[str]) -> dict[str, Any]:
    """USDC Teeny earned since `since`: what reached its own token accounts
    in confirmed, successful transactions it did not sign, where no team
    address signed or sent. In micro-dollars (USDC is one for one)."""
    seen: set[str] = set()
    signatures: list[str] = []
    truncated = False
    for account in sorted(_token_accounts(cluster, own, mint)):
        before: str | None = None
        while not truncated:
            options: dict[str, Any] = {"limit": PAGE, "commitment": "confirmed"}
            if before:
                options["before"] = before
            page = solana.rpc(cluster, "getSignaturesForAddress", [account, options])
            if not isinstance(page, list):
                raise hand.HandError("Solana RPC getSignaturesForAddress sent no list")
            done = len(page) < PAGE
            for entry in page:
                if not isinstance(entry, Mapping) or not isinstance(entry.get("signature"), str):
                    raise hand.HandError("Solana RPC sent a signature it could not read")
                at = entry.get("blockTime")
                if not isinstance(at, (int, float)) or at <= since:
                    done = True
                    break
                if entry.get("err") is None and entry["signature"] not in seen:
                    if len(signatures) >= MAX_INFLOW_TXS:
                        truncated = True
                        break
                    seen.add(entry["signature"])
                    signatures.append(entry["signature"])
            if done or not page:
                break
            before = page[-1]["signature"]
    earned = 0
    counted = 0
    for signature in signatures:
        tx = solana.rpc(cluster, "getTransaction", [signature, {"encoding": "jsonParsed", "maxSupportedTransactionVersion": 0, "commitment": "confirmed"}])
        if not isinstance(tx, Mapping) or (tx.get("meta") or {}).get("err") is not None:
            continue
        signers = _signers(tx)
        if own in signers or signers & team:
            continue
        deltas = _deltas(tx, mint)
        gained = deltas.get(own, 0)
        senders = {owner for owner, delta in deltas.items() if delta < 0}
        if gained <= 0 or senders & team:
            continue
        earned += gained
        counted += 1
    return {"micro": earned, "transactions": counted, "truncated": truncated}


# --- the quote --------------------------------------------------------------------------------


def _int_field(quote: Mapping[str, Any], key: str) -> int:
    try:
        value = int(str(quote[key]))
    except (KeyError, TypeError, ValueError):
        raise hand.Refused(f"the quote has no readable {key}", rule="quote") from None
    if value < 0:
        raise hand.Refused(f"the quote's {key} is negative", rule="quote")
    return value


def _route(quote: Mapping[str, Any], usdc: str) -> list[str]:
    plan = quote.get("routePlan")
    if not isinstance(plan, list) or not plan:
        raise hand.Refused("the quote has no route", rule="route")
    ins: set[str] = set()
    outs: set[str] = set()
    labels = []
    for step in plan:
        info = step.get("swapInfo") if isinstance(step, Mapping) else None
        if not isinstance(info, Mapping) or not isinstance(info.get("inputMint"), str) or not isinstance(info.get("outputMint"), str):
            raise hand.Refused("a hop of the route names no mints", rule="route")
        ins.add(info["inputMint"])
        outs.add(info["outputMint"])
        labels.append(str(info.get("label") or "?"))
    # Every mint the route ends in is $TINY, and every mint it starts from is USDC.
    if outs - ins != {OUTPUT_MINT} or ins - outs != {usdc}:
        raise hand.Refused("the route does not run from USDC to $TINY", rule="route")
    return labels


def check_quote(quote: Any, amount: int, usdc: str, config: BuyConfig, checks: list[dict[str, Any]]) -> dict[str, Any]:
    """The quote's terms, or the first rule it breaks. Buy direction only."""
    if not isinstance(quote, Mapping):
        raise hand.Refused("the quote is not an object", rule="quote")
    if quote.get("outputMint") != OUTPUT_MINT:
        raise hand.Refused("the quote's output is not $TINY; Teeny only ever buys $TINY", rule="direction")
    if quote.get("inputMint") != usdc:
        raise hand.Refused("the quote's input is not the cluster's USDC", rule="direction")
    if quote.get("swapMode", "ExactIn") != "ExactIn" or _int_field(quote, "inAmount") != amount:
        raise hand.Refused("the quote is not for exactly the amount computed", rule="direction")
    _check(checks, "direction", "USDC in, $TINY out, the exact amount")
    labels = _route(quote, usdc)
    _check(checks, "route", " > ".join(labels)[:ROUTE_LABEL_CHARS])
    out = _int_field(quote, "outAmount")
    least = _int_field(quote, "otherAmountThreshold")
    if out <= 0:
        raise hand.Refused("the quote gives no $TINY", rule="quote")
    slippage = quote.get("slippageBps")
    if isinstance(slippage, bool) or not isinstance(slippage, int) or not 0 <= slippage <= config.slippage_bps:
        raise hand.Refused(f"the quote's slippage is over {config.slippage_bps} bps", rule="slippage")
    floor = out * (10_000 - config.slippage_bps) // 10_000
    if least < floor or least > out:
        raise hand.Refused("the quote's minimum output does not hold to the slippage cap", rule="slippage")
    _check(checks, "slippage", f"{slippage} bps of at most {config.slippage_bps}")
    try:
        impact = Decimal(str(quote.get("priceImpactPct")))
    except InvalidOperation:
        raise hand.Refused("the quote has no readable price impact", rule="price_impact") from None
    # Read as a fraction (0.01 is 1%): if Jupiter ever meant a percentage,
    # this reads it 100 times larger and refuses, never the other way round.
    if not impact.is_finite():
        raise hand.Refused("the quote has no readable price impact", rule="price_impact")
    impact_bps = math.ceil(abs(impact) * 10_000)
    if impact_bps > config.impact_bps:
        raise hand.Refused(f"the price impact is {impact_bps} bps, over {config.impact_bps}: Teeny does not move the market", rule="price_impact")
    return {"out": out, "least": least, "slippage_bps": slippage, "impact_bps": impact_bps, "route": labels}


def measured_impact_bps(amount: int, out: int, ref_amount: int, ref_out: int) -> int:
    """How much worse the rate for `amount` is than for a small reference
    amount, in basis points, rounded up (0 when it is no worse)."""
    if amount <= 0 or ref_amount <= 0 or ref_out <= 0:
        return 10_000
    # 1 - (out / amount) / (ref_out / ref_amount)
    worse = 10_000 * (ref_out * amount - out * ref_amount)
    return max(0, -(-worse // (ref_out * amount)))


def _check(checks: list[dict[str, Any]], rule: str, detail: str) -> None:
    checks.append({"rule": rule, "ok": True, "detail": detail})


# --- the plan ---------------------------------------------------------------------------------


def _since(conn: Any) -> float:
    last = store.last_buy(conn)
    if last is not None:
        return float(last["created_at"])
    return hand.now() - FIRST_WINDOW_DAYS * store.DAY


def _spent(since: float) -> tuple[int | None, dict[str, Any]]:
    """What running Teeny cost, and what the wallet paid out, over the whole
    days since `since`. None when a source could not be read: then nothing
    is bought, since an unread cost would look like profit."""
    days = max(1, min(366, math.ceil((hand.now() - since) / store.DAY)))
    report = reads.costs(days)
    paid = sum(caps.dollars_to_micro(v["usd"], "paid_out") for v in report.get("paid_out", {}).values())
    detail = {"days": days, "running_usd": report.get("total_usd"), "paid_out_usd": caps.usd(paid)}
    if not report.get("complete"):
        return None, detail
    return caps.dollars_to_micro(report["total_usd"], "costs") + paid, detail


def plan(conn: Any, c: caps.Caps, config: BuyConfig, own: str) -> dict[str, Any]:
    """The amount to buy and how it was reached. `skip` names why not."""
    since = _since(conn)
    earned = earned_since(c.cluster, own, c.usdc_mint, since, config.team)
    spent, spent_detail = _spent(since)
    out: dict[str, Any] = {
        "since": since,
        "earned_usd": caps.usd(earned["micro"]),
        "earned_micro": earned["micro"],
        "earned_transactions": earned["transactions"],
        "spent": spent_detail,
        "share": str(config.share),
    }
    if earned["truncated"]:
        out["earned_truncated"] = True
    if spent is None:
        out.update(spent_micro=0, amount=0, skip="what Teeny cost could not be read in full, so nothing counts as profit")
        return out
    net = earned["micro"] - spent
    share_micro = share_of(net, config.share)
    now = hand.now()
    usdc_balance = sum(_token_accounts(c.cluster, own, c.usdc_mint).values())
    bounds = [
        ("per_buy", config.per_buy_micro),
        ("per_tx", c.per_tx_micro),
        ("buy_per_day", config.per_day_micro - store.bought_since(conn, now - store.DAY)),
        ("per_day", c.per_day_micro - store.spent_since(conn, now - store.DAY)),
        ("half_balance", usdc_balance // 2),
    ]
    amount, by = clamp(share_micro, bounds)
    out.update(spent_micro=spent, net_usd=caps.usd(net), share_usd=caps.usd(share_micro), amount=amount, clamped_by=by)
    if config.share == 0:
        out["skip"] = "TINY_BUY_SHARE is 0"
    elif amount < config.min_micro:
        out["skip"] = f"${caps.usd(amount)} (set by {by}) is under the ${caps.usd(config.min_micro)} minimum"
    return out


# --- buy-tiny ---------------------------------------------------------------------------------


def _setup() -> tuple[caps.Caps, BuyConfig]:
    if not hand.flag("TINY_BUY", False):
        raise hand.Refused("buying $TINY is off (TINY_BUY=1 in secrets/teeny.env turns it on)", rule="off")
    c = load_caps()
    try:
        config = load_config(hand.secrets(), c)
    except caps.CapsError as exc:
        raise hand.Refused(f"the buy's settings are not usable: {exc}", rule="caps") from None
    if c.cluster != "mainnet":
        raise hand.Refused("$TINY and its routes are on mainnet; the wallet is on devnet", rule="cluster")
    return c, config


def _row(c: caps.Caps, p: dict[str, Any], **fields: Any) -> dict[str, Any]:
    return {
        "cluster": c.cluster,
        "input_mint": c.usdc_mint,
        "output_mint": OUTPUT_MINT,
        "usdc_raw": p.get("amount", 0),
        "usd_micro": p.get("amount", 0),
        "since": p["since"],
        "earned_micro": p["earned_micro"],
        "spent_micro": p.get("spent_micro", 0),
        "share": p["share"],
        **fields,
    }


def buy_tiny(core: Core = hand.core) -> dict[str, Any]:
    c, config = _setup()
    checks: list[dict[str, Any]] = []
    _check(checks, "off", "TINY_BUY=1")
    _check(checks, "caps", "set and within their ceilings")
    own = own_address(core)
    with hand.lock("wallet-pay"):
        conn = store.db()
        try:
            p = plan(conn, c, config, own)
            public = {k: v for k, v in p.items() if k not in ("earned_micro", "spent_micro")}
            if p.get("skip"):
                if hand.dry_run():
                    return {"dry_run": True, "skipped": p["skip"], "plan": public}
                with hand.transaction(conn):
                    buy_id = store.insert_buy(conn, **_row(c, {**p, "amount": 0}, status="skipped", reason=p["skip"]))
                return {"skipped": p["skip"], "buy_id": buy_id, "plan": public}
            amount = p["amount"]
            _check(checks, "amount", f"${caps.usd(amount)} ({p['clamped_by']})")

            terms = check_quote(jupiter.quote(c.usdc_mint, OUTPUT_MINT, amount, config.slippage_bps), amount, c.usdc_mint, config, checks)
            ref_amount = max(amount // 100, 100_000)
            ref = jupiter.quote(c.usdc_mint, OUTPUT_MINT, ref_amount, config.slippage_bps)
            if not isinstance(ref, Mapping) or ref.get("outputMint") != OUTPUT_MINT or ref.get("inputMint") != c.usdc_mint:
                raise hand.Refused("the reference quote is not USDC to $TINY", rule="direction")
            measured = measured_impact_bps(amount, terms["out"], ref_amount, _int_field(ref, "outAmount"))
            if measured > config.impact_bps:
                raise hand.Refused(
                    f"the rate is {measured} bps worse than for a small buy, over {config.impact_bps}: Teeny does not move the market",
                    rule="price_impact",
                )
            terms["impact_bps"] = max(terms["impact_bps"], measured)
            _check(checks, "price_impact", f"{terms['impact_bps']} bps of at most {config.impact_bps}")

            summary = {
                "usdc": caps.display(amount, "USDC"),
                "usdc_raw": str(amount),
                "usd": caps.usd(amount),
                "tiny_out_raw": str(terms["out"]),
                "tiny_min_raw": str(terms["least"]),
                "price_impact_bps": terms["impact_bps"],
                "slippage_bps": terms["slippage_bps"],
                "route": terms["route"],
                "cluster": c.cluster,
            }
            if hand.dry_run():
                return {"dry_run": True, "would_buy": summary, "plan": public, "checks": checks}

            with hand.transaction(conn):
                again = plan(conn, c, config, own)
                if again.get("skip") or again["amount"] < amount:
                    raise hand.Refused("the caps moved while the quote was read; nothing was bought", rule="per_day")
                buy_id = store.insert_buy(
                    conn,
                    **_row(
                        c,
                        p,
                        status="pending",
                        tiny_out_raw=terms["out"],
                        tiny_min_raw=terms["least"],
                        price_impact_bps=terms["impact_bps"],
                        slippage_bps=terms["slippage_bps"],
                        route=" > ".join(terms["route"])[:ROUTE_LABEL_CHARS],
                    ),
                )
            return _send(conn, buy_id, c, own, amount, terms, summary, core)
        finally:
            conn.close()


# --- through the core ---------------------------------------------------------------------------


def _core_terms(prepared: Any, usdc: str, own: str, amount: int) -> tuple[str, int, str]:
    """(quote id, minimum $TINY out, unsigned transaction hex) from the
    core's prepared swap, or the names of the fields that do not match."""
    prepared = prepared if isinstance(prepared, Mapping) else {}
    body = prepared.get("quote") if isinstance(prepared.get("quote"), Mapping) else {}
    token_in = body.get("tokenIn") if isinstance(body.get("tokenIn"), Mapping) else {}
    token_out = body.get("tokenOut") if isinstance(body.get("tokenOut"), Mapping) else {}
    tx = body.get("tx") if isinstance(body.get("tx"), Mapping) else {}
    try:
        least = int(str(token_out.get("minAmount")))
    except ValueError:
        least = -1
    data = tx.get("data")
    recipient = body.get("tokenOutRecipient", own)
    mismatch = [
        name
        for name, ok in (
            ("quoteId", isinstance(prepared.get("quoteId"), str) and bool(prepared.get("quoteId"))),
            ("kind", prepared.get("kind", "swap") == "swap"),
            ("tokenIn", token_in.get("address") == usdc),
            ("tokenInAmount", str(token_in.get("amount")) == str(amount)),
            ("tokenOut", token_out.get("address") == OUTPUT_MINT),
            ("tokenOutRecipient", recipient == own),
            ("minAmount", least > 0),
            ("tx", isinstance(data, str) and bool(data)),
        )
        if not ok
    ]
    if mismatch:
        raise _Mismatch(mismatch)
    return str(prepared["quoteId"]), least, str(data)


class _Mismatch(Exception):
    def __init__(self, fields: list[str]) -> None:
        super().__init__(", ".join(fields))
        self.fields = fields


def _tx_base64(data: str) -> str:
    text = data[2:] if data.startswith("0x") else data
    try:
        return base64.b64encode(binascii.unhexlify(text)).decode()
    except (binascii.Error, ValueError):
        raise _Mismatch(["tx"]) from None


def _parsed_amount(account: Any) -> int:
    if account is None:
        return 0
    try:
        return int(account["data"]["parsed"]["info"]["tokenAmount"]["amount"])
    except (KeyError, TypeError, ValueError):
        raise hand.HandError("the simulation sent a token account it could not read") from None


def simulate(c: caps.Caps, own: str, tx_b64: str, amount: int, least: int) -> str | None:
    """Why the unsigned swap must not be signed, or None when it may: it
    succeeds, takes at most `amount` USDC, and gives at least `least` $TINY
    to the $TINY accounts Teeny already holds (a first buy opens one)."""
    usdc = _token_accounts(c.cluster, own, c.usdc_mint)
    tiny = _token_accounts(c.cluster, own, OUTPUT_MINT)
    addresses = sorted(usdc) + sorted(tiny)
    result = solana.rpc(
        c.cluster,
        "simulateTransaction",
        [
            tx_b64,
            {
                "encoding": "base64",
                "sigVerify": False,
                "replaceRecentBlockhash": True,
                "commitment": "confirmed",
                "accounts": {"encoding": "jsonParsed", "addresses": addresses},
            },
        ],
    )
    value = result.get("value") if isinstance(result, Mapping) else None
    if not isinstance(value, Mapping):
        return "the simulation sent no result"
    if value.get("err") is not None:
        return f"the simulation failed: {str(value['err'])[:200]}"
    after = value.get("accounts")
    if not isinstance(after, list) or len(after) != len(addresses):
        return "the simulation did not report Teeny's token accounts"
    posts = dict(zip(addresses, (_parsed_amount(a) for a in after)))
    taken = sum(usdc.values()) - sum(posts[a] for a in usdc)
    if taken > amount:
        return f"the swap would take {caps.display(taken, 'USDC')} USDC, more than the {caps.display(amount, 'USDC')} bought with"
    if tiny:
        got = sum(posts[a] for a in tiny) - sum(tiny.values())
        if got < least:
            return "the swap would give Teeny's $TINY account less than the minimum"
    return None


def _send(conn: Any, buy_id: int, c: caps.Caps, own: str, amount: int, terms: dict[str, Any], summary: dict[str, Any], core: Core) -> dict[str, Any]:
    params = {
        "chainId": SOLANA_CHAIN_ID,
        "tokenIn": c.usdc_mint,
        "tokenInAmount": str(amount),
        "tokenOut": OUTPUT_MINT,
        "tokenOutRecipient": own,
        "senderAddress": own,
        "slippage": str(Decimal(terms["slippage_bps"]) / 100),
    }
    try:
        prepared = core("web3_swap_quote", params, timeout=60)
    except hand.CoreError as exc:
        store.update_buy(conn, buy_id, status="failed", error=str(exc)[:300])
        raise hand.HandError(f"buy {buy_id}: the core could not prepare the swap ({exc})") from None
    try:
        quote_id, least, data = _core_terms(prepared, c.usdc_mint, own, amount)
        if least < terms["least"]:
            raise _Mismatch(["minAmount"])
        tx_b64 = _tx_base64(data)
    except _Mismatch as exc:
        store.update_buy(conn, buy_id, status="failed", error=f"the core's quote did not match: {exc}")
        raise hand.Refused("the core's swap did not match the checked quote; nothing was signed", rule="quote", buy_id=buy_id, fields=exc.fields) from None
    store.update_buy(conn, buy_id, quote_id=quote_id)
    try:
        why = simulate(c, own, tx_b64, amount, least)
    except hand.HandError as exc:
        why = f"the simulation could not run: {exc}"
    if why is not None:
        store.update_buy(conn, buy_id, status="failed", error=why[:300])
        raise hand.Refused(f"{why}; nothing was signed", rule="simulate", buy_id=buy_id)
    try:
        result = core("web3_swap_execute", {"quoteId": quote_id, "confirmed": True}, timeout=120)
    except hand.CoreError as exc:
        store.update_buy(conn, buy_id, status="in_doubt", error=str(exc)[:300])
        raise hand.HandError(
            f"buy {buy_id} is in doubt: the execute was sent and did not come back ({exc}). "
            "It is never retried; check hands/wallet ledger and the chain."
        ) from None
    signature = result.get("transactionHash") if isinstance(result, Mapping) else None
    if not isinstance(signature, str) or not signature:
        store.update_buy(conn, buy_id, status="in_doubt", error="the execute answered without a signature")
        raise hand.HandError(f"buy {buy_id} is in doubt: the core answered without a signature. It is never retried.")
    store.update_buy(conn, buy_id, status="bought", signature=signature)
    return {"bought": {**summary, "buy_id": buy_id, "signature": signature, "url": solana.explorer_tx(signature, c.cluster)}}


Answer = Callable[..., Any]
