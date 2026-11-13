"""`swap` and `bridge`: moving Teeny's own money between its own accounts,
through LI.FI by default or deBridge (docs/hands.md, "wallet").

    swap    {"chain": "base", "from": "ETH", "to": "USDC", "amount": "0.01"}
    bridge  {"from_chain": "base", "from": "USDC", "to_chain": "solana", "to": "USDC", "amount": "5"}

Either takes an optional `"provider": "lifi" | "debridge"` for that one call;
otherwise WALLET_CONVERT_PROVIDER decides (lifi when unset). The mind never
names one: bin/lib/tick.py forwards only the fields above.

A swap stays on one chain; a bridge crosses. Either way the output goes only
to Teeny's own address on the destination chain, derived from the same
recovery phrase: neither verb can pay anyone. The quote comes from the core
(`web3_swap_quote`, `web3_bridge_quote`), which asks the provider through the
spine (spine/src/wallet_backend.rs routes on a tag this hand puts on the
body); the core signs only on `*_execute`.

The rules, each refused with its `rule`, before anything is signed:

  fields, tiny      only the fields above; $TINY is never on either side
  chain, cluster    chains the wallet knows, mainnet only (neither provider
                    quotes devnet)
  kind              a swap is one chain, a bridge two
  asset             each side is that chain's native asset or its pinned USDC,
                    and the two differ (or the chains do)
  amount            above zero, within the input asset's decimals
  caps              WALLET_MAX_CONVERT_PER_DAY_USD is set (and WALLET_ETH_USD
                    for ETH), WALLET_CONVERT_PROVIDER is lifi or debridge:
                    without the cap the wallet converts nothing
  per_tx            the input is at most WALLET_MAX_PER_TX_USD
  per_day_convert   the last 24 hours of swaps and bridges stay within
                    WALLET_MAX_CONVERT_PER_DAY_USD
  half_balance      never more than half of what the wallet holds of the input
  quote             the quote is for exactly this and loses no more than
                    WALLET_MAX_CONVERT_LOSS_BPS. LI.FI: lifi.py says what is
                    checked (the pinned LiFiDiamond, the recipient, a Solana
                    simulation, the costs). deBridge: its input token and
                    amount, its output token, an EVM transaction only to a
                    pinned deBridge contract, and deBridge's flat fee counted
                    as a loss. Either way the module's quote must not have
                    expired; USDC counts at par, another asset at the
                    provider's own USD estimate, since the fixed
                    WALLET_SOL_USD/ETH_USD are set high on purpose and would
                    hide a loss on the way out.

An ERC-20 input (USDC on an EVM network) needs an allowance first: the hand
approves exactly the input amount, to the checked spender (deBridge's `tx.to`,
or LI.FI's diamond), waits for it to confirm, and re-quotes if the module's
quote expired meanwhile (the new one passes every rule again).

Under DRY_RUN the quote is fetched and checked (it moves nothing; a LI.FI
Solana quote is simulated, which signs nothing) and the answer is
`would_convert`; nothing is approved or executed.

Live, under the `wallet-pay` lock the payments share: a `wallet_conversions`
row is written `pending` first, with the provider (and LI.FI's `tool`). An
execute that fails after it was sent is `in_doubt` and is never retried.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any, Callable, Mapping

from .. import hand
from . import caps, chains, lifi, store
from .pay import amount_raw, balance_of, chain_of, load_caps, names_tiny, own_address

Core = Callable[..., Any]

SWAP_FIELDS = {"chain", "from", "to", "amount", "ref", "provider"}
BRIDGE_FIELDS = {"from_chain", "to_chain", "from", "to", "amount", "ref", "provider"}
PROVIDER_NAMES = {"lifi": "LI.FI", "debridge": "deBridge"}
#: A quote this close to expiry is fetched again rather than executed.
EXPIRY_MARGIN_MS = 10_000
APPROVAL_WAIT_SECONDS = 120.0


@dataclass(frozen=True)
class Conversion:
    kind: str  # swap | bridge
    from_chain: str
    from_asset: str
    raw: int
    to_chain: str
    to_asset: str
    ref: str | None
    provider: str | None = None  # the body's, when it names one


def _asset(value: Any, chain: str, side: str) -> str:
    asset = str(value or "").strip().upper()
    if asset not in chains.assets(chain):
        raise hand.Refused(f"{side} on {chain} is {' or '.join(chains.assets(chain))}", rule="asset")
    return asset


def parse(kind: str, body: Any, cluster: str) -> Conversion:
    if not isinstance(body, Mapping):
        raise hand.Refused(f"{kind} takes a JSON object", rule="fields")
    if any(names_tiny(body.get(key)) for key in ("from", "to")):
        raise hand.Refused("Teeny never trades $TINY, whoever asks", rule="tiny")
    allowed = SWAP_FIELDS if kind == "swap" else BRIDGE_FIELDS
    unknown = sorted(set(body) - allowed)
    if unknown:
        raise hand.Refused(f"unknown field(s): {', '.join(unknown)}", rule="fields")
    provider = body.get("provider")
    if provider is not None and provider not in caps.PROVIDERS:
        raise hand.Refused(f"provider is {' or '.join(caps.PROVIDERS)}", rule="fields")
    if cluster != "mainnet":
        raise hand.Refused(f"swaps and bridges are quoted on mainnets only, and the wallet is on {cluster}", rule="cluster")
    if kind == "swap":
        if body.get("chain") is None:
            raise hand.Refused("a swap names its chain", rule="chain")
        src = dst = chain_of(body, cluster, "chain")
    else:
        if body.get("from_chain") is None or body.get("to_chain") is None:
            raise hand.Refused("a bridge names from_chain and to_chain", rule="chain")
        src = chain_of(body, cluster, "from_chain")
        dst = chain_of(body, cluster, "to_chain")
        if src == dst:
            raise hand.Refused("a bridge crosses chains; on one chain it is a swap", rule="kind")
    from_asset = _asset(body.get("from"), src, "from")
    to_asset = _asset(body.get("to"), dst, "to")
    if kind == "swap" and from_asset == to_asset:
        raise hand.Refused("a swap turns one asset into another", rule="asset")
    if body.get("amount") is None:
        raise hand.Refused("amount is the input, in the from asset", rule="amount")
    raw = amount_raw(body["amount"], from_asset)
    ref = body.get("ref")
    if ref is not None and (not isinstance(ref, str) or len(ref) > 100):
        raise hand.Refused("ref is a short note, at most 100 characters", rule="ref")
    return Conversion(kind, src, from_asset, raw, dst, to_asset, ref, provider)


# --- the quote --------------------------------------------------------------------------


def _token(c: Conversion, side: str, cluster: str) -> str:
    chain, asset = (c.from_chain, c.from_asset) if side == "in" else (c.to_chain, c.to_asset)
    return chains.token_address(chain, asset, cluster, caps.USDC_MINTS)


def quote_params(c: Conversion, provider: str, cluster: str, src_own: str, dst_own: str, slippage_bps: int) -> dict[str, Any]:
    """What the core is asked for: every address named, none left to default.
    The fields stay deBridge's (the module's); the provider rides as a tag on
    the one free-text field (`slippage`, `dstChainTokenOutAmount`), which the
    spine reads and takes off (spine/src/wallet_backend.rs)."""
    if c.kind == "swap":
        return {
            "chainId": chains.debridge_id(c.from_chain),
            "tokenIn": _token(c, "in", cluster),
            "tokenInAmount": str(c.raw),
            "tokenOut": _token(c, "out", cluster),
            "tokenOutRecipient": dst_own,
            "senderAddress": src_own,
            "slippage": f"{provider}:{Decimal(slippage_bps) / 100}",
        }
    return {
        "srcChainId": chains.debridge_id(c.from_chain),
        "srcChainTokenIn": _token(c, "in", cluster),
        "srcChainTokenInAmount": str(c.raw),
        "dstChainId": chains.debridge_id(c.to_chain),
        "dstChainTokenOut": _token(c, "out", cluster),
        "dstChainTokenOutAmount": f"{provider}:auto",
        "dstChainTokenOutRecipient": dst_own,
        "srcChainOrderAuthorityAddress": src_own,
        "dstChainOrderAuthorityAddress": dst_own,
    }


def _bad(reason: str) -> hand.Refused:
    return hand.Refused(f"the deBridge quote is not usable: {reason}; nothing was signed", rule="quote")


def _int(value: Any, what: str) -> int:
    try:
        number = int(str(value))
    except (TypeError, ValueError):
        raise _bad(f"{what} is not a whole number") from None
    if number < 0:
        raise _bad(f"{what} is negative")
    return number


def _usd(value: Any, what: str) -> int:
    """deBridge's approximateUsdValue (a JSON number) as micro-dollars."""
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        raise _bad(f"it gives no USD estimate for {what}")
    try:
        amount = Decimal(str(value))
    except InvalidOperation:
        raise _bad(f"its USD estimate for {what} is not a number") from None
    if not amount.is_finite() or amount < 0:
        raise _bad(f"its USD estimate for {what} is not a number")
    return int(amount * caps.MICRO)


@dataclass(frozen=True)
class Checked:
    quote_id: str
    expires_at_ms: int
    expected_raw: int
    min_raw: int
    in_market_micro: int
    out_micro: int
    fee_micro: int
    spender: str | None  # the only address an approval may name
    order_id: str | None
    provider: str = "debridge"
    tool: str | None = None


def check_quote(
    c: Conversion, provider: str, envelope: Any, cluster: str, the_caps: caps.Caps, now_ms: int, src_own: str, dst_own: str,
) -> Checked:
    """The quote, checked against the conversion, or rule `quote`: the
    module's envelope here, the provider's quote in lifi.py or below."""
    bad = lifi.bad if provider == "lifi" else _bad
    if not isinstance(envelope, Mapping):
        raise bad("the core sent no quote")
    quote_id = envelope.get("quoteId")
    if not isinstance(quote_id, str) or not quote_id:
        raise bad("it has no quoteId")
    if str(envelope.get("kind") or "").lower() != c.kind:
        raise bad(f"it is a {envelope.get('kind')!r}, not a {c.kind}")
    family = "evm" if chains.is_evm(c.from_chain) else "solana"
    if envelope.get("signingChain") not in (None, family):
        raise bad(f"it would be signed on {envelope.get('signingChain')}, not {family}")
    expires = _int(envelope.get("expiresAtMs"), "expiresAtMs")
    if expires <= now_ms:
        raise bad("it has expired")
    if provider == "lifi":
        terms = lifi.check(c, envelope.get("quote"), cluster, the_caps, src_own, dst_own)
        return Checked(
            quote_id, expires, terms.expected_raw, terms.min_raw, terms.in_market_micro, terms.out_micro,
            terms.fee_micro, terms.spender, None, "lifi", terms.tool,
        )
    return _check_debridge(c, envelope, quote_id, expires, family, cluster, the_caps)


def _check_debridge(
    c: Conversion, envelope: Mapping[str, Any], quote_id: str, expires: int, family: str, cluster: str, the_caps: caps.Caps,
) -> Checked:
    """deBridge's quote, checked against the conversion."""
    q = envelope.get("quote")
    if not isinstance(q, Mapping):
        raise _bad("it carries no deBridge quote")

    if c.kind == "swap":
        token_in, token_out = q.get("tokenIn"), q.get("tokenOut")
    else:
        est = q.get("estimation") if isinstance(q.get("estimation"), Mapping) else q
        token_in, token_out = est.get("srcChainTokenIn"), est.get("dstChainTokenOut")
    if not isinstance(token_in, Mapping) or not isinstance(token_out, Mapping):
        raise _bad("it does not say what goes in and what comes out")
    if not chains.same_address(c.from_chain, token_in.get("address"), _token(c, "in", cluster)):
        raise _bad(f"its input is not {c.from_asset} on {c.from_chain}")
    if _int(token_in.get("amount"), "the input amount") != c.raw:
        raise _bad("its input amount is not the amount asked for")
    if not chains.same_address(c.to_chain, token_out.get("address"), _token(c, "out", cluster)):
        raise _bad(f"its output is not {c.to_asset} on {c.to_chain}")
    expected = _int(token_out.get("amount"), "the output amount")
    if c.kind == "swap":
        min_raw = _int(token_out.get("minAmount", token_out.get("amount")), "the minimum output")
    else:
        min_raw = _int(token_out.get("recommendedAmount", token_out.get("amount")), "the recommended output")
    if min_raw <= 0:
        raise _bad("its output is zero")

    # What goes in, at market: USDC at par, anything else at deBridge's estimate.
    if c.from_asset == "USDC":
        in_market = c.raw
    else:
        in_market = _usd(token_in.get("approximateUsdValue"), "the input")
    # What comes out, at the least it may be: USDC at par; anything else at
    # deBridge's estimate, scaled down to the minimum.
    if c.to_asset == "USDC":
        out_micro = min_raw
    else:
        key = "recommendedApproximateUsdValue" if c.kind == "bridge" and "recommendedApproximateUsdValue" in token_out else "approximateUsdValue"
        out_estimate = _usd(token_out.get(key), "the output")
        basis = min_raw if key == "recommendedApproximateUsdValue" else expected
        out_micro = out_estimate * min_raw // basis if basis else 0

    tx = q.get("tx")
    if not isinstance(tx, Mapping):
        raise _bad("it carries no transaction")
    spender: str | None = None
    fee_micro = 0
    if family == "evm":
        to = tx.get("to")
        if not chains.is_evm_address(to) or to.lower() not in chains.DEBRIDGE_EVM_CONTRACTS:
            raise _bad("its transaction goes to a contract that is not deBridge's")
        spender = to
        value = _int(tx.get("value") or 0, "the transaction value")
        native = chains.NETWORKS[c.from_chain].native
        extra = value - (c.raw if c.from_asset == native else 0)
        if extra < 0:
            raise _bad("its transaction sends less than the input")
        if extra:
            try:
                fee_micro = caps.usd_micro(native, extra, the_caps)
            except caps.CapsError:
                raise hand.Refused(f"deBridge charges a fee in {native} and {caps.ETH_USD} is not set", rule="caps") from None
    elif c.kind == "bridge" and q.get("fixFee") is not None:
        fee_micro = caps.usd_micro("SOL", _int(q.get("fixFee"), "the flat fee"), the_caps)

    loss = in_market + fee_micro - out_micro
    if loss * 10_000 > in_market * the_caps.convert_loss_bps:
        raise _bad(
            f"it would lose ${caps.usd(max(loss, 0))} of ${caps.usd(in_market)}, over the"
            f" {the_caps.convert_loss_bps / 100:g}% {caps.CONVERT_LOSS_BPS} allows"
        )
    order_id = q.get("orderId") if isinstance(q.get("orderId"), str) else None
    return Checked(quote_id, expires, expected, min_raw, in_market, out_micro, fee_micro, spender, order_id)


# --- convert ----------------------------------------------------------------------------


def _check(checks: list[dict[str, Any]], rule: str, detail: str) -> None:
    checks.append({"rule": rule, "ok": True, "detail": detail})


def _day_rule(conn: Any, usd: int, the_caps: caps.Caps, checks: list[dict[str, Any]]) -> None:
    day = store.converted_since(conn, hand.now() - store.DAY)
    limit = the_caps.convert_day_micro or 0
    if day + usd > limit:
        raise hand.Refused(
            f"that would take the last 24 hours of swaps and bridges to ${caps.usd(day + usd)}, over the ${caps.usd(limit)} cap",
            rule="per_day_convert",
        )
    _check(checks, "per_day_convert", f"${caps.usd(day + usd)} of ${caps.usd(limit)} in 24h")


def _now_ms() -> int:
    return int(hand.now() * 1000)


def _quote(c: Conversion, provider: str, params: dict[str, Any], core: Core) -> Any:
    method = "web3_swap_quote" if c.kind == "swap" else "web3_bridge_quote"
    try:
        return core(method, params, timeout=60)
    except hand.CoreError as exc:
        raise hand.HandError(f"{PROVIDER_NAMES[provider]} could not quote the {c.kind} ({exc})") from None


def convert(kind: str, body: Any, core: Core = hand.core) -> dict[str, Any]:
    try:
        cluster = caps.cluster(hand.secrets())
    except caps.CapsError as exc:
        raise hand.Refused(f"the wallet's caps are not usable: {exc}", rule="caps") from None
    c = parse(kind, body, cluster)
    checks: list[dict[str, Any]] = []
    _check(checks, "kind", f"{c.kind}: {c.from_asset} on {c.from_chain} to {c.to_asset} on {c.to_chain}")
    the_caps = load_caps()
    if the_caps.convert_day_micro is None:
        raise hand.Refused(f"{caps.CONVERT_PER_DAY} is not set, so the wallet swaps and bridges nothing", rule="caps")
    try:
        usd = caps.usd_micro(c.from_asset, c.raw, the_caps)
    except caps.CapsError as exc:
        raise hand.Refused(str(exc), rule="caps") from None
    _check(checks, "caps", "set and within their ceilings")
    if usd > the_caps.per_tx_micro:
        raise hand.Refused(f"${caps.usd(usd)} is over the ${caps.usd(the_caps.per_tx_micro)} per transaction cap", rule="per_tx")
    _check(checks, "per_tx", f"${caps.usd(usd)} of ${caps.usd(the_caps.per_tx_micro)}")
    provider = c.provider or the_caps.convert_provider
    src_own = own_address(core, c.from_chain)
    dst_own = own_address(core, c.to_chain)
    summary = {
        "kind": c.kind,
        "provider": provider,
        "from_chain": c.from_chain,
        "from": c.from_asset,
        "amount_raw": str(c.raw),
        "amount": caps.display(c.raw, c.from_asset),
        "usd": caps.usd(usd),
        "to_chain": c.to_chain,
        "to": c.to_asset,
        "recipient": dst_own,
    }
    if c.ref:
        summary["ref"] = c.ref
    params = quote_params(c, provider, cluster, src_own, dst_own, min(the_caps.convert_loss_bps, 100))
    owns = (src_own, dst_own)

    with hand.lock("wallet-pay"):
        conn = store.db()
        try:
            _day_rule(conn, usd, the_caps, checks)
            balance = balance_of(c.from_asset, src_own, the_caps, core, c.from_chain)
            if c.raw * 2 > balance:
                raise hand.Refused(
                    f"that is more than half of the {caps.display(balance, c.from_asset)} {c.from_asset} the wallet holds on {c.from_chain}",
                    rule="half_balance",
                )
            _check(checks, "half_balance", f"{summary['amount']} of {caps.display(balance, c.from_asset)} {c.from_asset}")
            checked = check_quote(c, provider, _quote(c, provider, params, core), cluster, the_caps, _now_ms() + EXPIRY_MARGIN_MS, *owns)
            _check(checks, "quote", _quote_detail(c, checked))
            summary.update(_quoted(c, checked))
            if hand.dry_run():
                return {"dry_run": True, "would_convert": summary, "checks": checks}

            with hand.transaction(conn):
                _day_rule(conn, usd, the_caps, [])
                row_id = store.insert_row(
                    conn, "wallet_conversions", kind=c.kind, from_chain=c.from_chain, from_asset=c.from_asset,
                    amount_raw=str(c.raw), usd_micro=usd, to_chain=c.to_chain, to_asset=c.to_asset,
                    expected_raw=str(checked.min_raw), quote_id=checked.quote_id, provider=provider, tool=checked.tool,
                )
            return _execute(conn, row_id, c, provider, owns, cluster, the_caps, params, checked, summary, core)
        finally:
            conn.close()


def _quote_detail(c: Conversion, checked: Checked) -> str:
    return (
        f"at least {caps.display(checked.min_raw, c.to_asset)} {c.to_asset} (${caps.usd(checked.out_micro)})"
        f" for ${caps.usd(checked.in_market_micro)}, fee ${caps.usd(checked.fee_micro)}"
    )


def _quoted(c: Conversion, checked: Checked) -> dict[str, Any]:
    out = {
        "expected": caps.display(checked.expected_raw, c.to_asset),
        "minimum": caps.display(checked.min_raw, c.to_asset),
        "minimum_usd": caps.usd(checked.out_micro),
        "fee_usd": caps.usd(checked.fee_micro),
    }
    if checked.tool:
        out["tool"] = checked.tool
    if checked.order_id:
        out["order_id"] = checked.order_id
    return out


def _approve(conn: Any, row_id: int, c: Conversion, provider: str, spender: str, core: Core) -> str:
    """An exact ERC-20 allowance for the input, to the provider's checked contract, and
    its confirmation. An approval moves no funds, so a failure here is a
    plain failure."""
    network = chains.NETWORKS[c.from_chain]
    token = network.usdc["mainnet"]
    calldata = chains.approve_calldata(spender, c.raw)
    try:
        prepared = core("web3_dapp_call", {"contractAddress": token, "calldata": calldata, "valueRaw": "0", "evmNetwork": network.evm_network}, timeout=60)
        summary = prepared.get("quote") if isinstance(prepared, Mapping) else None
        if (
            not isinstance(summary, Mapping)
            or not chains.same_address(c.from_chain, summary.get("contractAddress"), token)
            or str(summary.get("calldata") or "").lower() != calldata
            or str(summary.get("valueRaw") or "0") != "0"
            or not prepared.get("quoteId")
        ):
            raise hand.HandError("the core's approval did not match the one asked for")
        done = core("web3_dapp_execute", {"quoteId": str(prepared["quoteId"]), "confirmed": True}, timeout=120)
        approval_tx = done.get("transactionHash") if isinstance(done, Mapping) else None
        if not isinstance(approval_tx, str) or not approval_tx:
            raise hand.HandError("the approval answered without a transaction hash")
    except (hand.HandError, AttributeError) as exc:
        store.update_row(conn, "wallet_conversions", row_id, status="failed", error=f"approval: {exc}"[:300])
        raise hand.HandError(f"conversion {row_id}: the allowance for {PROVIDER_NAMES[provider]} could not be set ({exc}); nothing was swapped") from None
    store.update_row(conn, "wallet_conversions", row_id, approval_tx=approval_tx)
    deadline = time.monotonic() + float(hand.setting("WALLET_APPROVAL_WAIT_SECONDS") or APPROVAL_WAIT_SECONDS)
    poll = float(hand.setting("WALLET_APPROVAL_POLL_SECONDS") or 3)
    while True:
        try:
            status = core("wallet_tx_status", {"chain": "evm", "hash": approval_tx, "evmNetwork": network.evm_network}, timeout=30)
        except hand.CoreError:
            status = None
        state = status.get("state") if isinstance(status, Mapping) else None
        if state == "confirmed":
            return approval_tx
        if state == "failed":
            store.update_row(conn, "wallet_conversions", row_id, status="failed", error="the approval failed on chain")
            raise hand.HandError(f"conversion {row_id}: the allowance transaction failed on chain; nothing was swapped")
        if time.monotonic() >= deadline:
            store.update_row(conn, "wallet_conversions", row_id, status="failed", error="the approval did not confirm in time")
            raise hand.HandError(
                f"conversion {row_id}: the allowance ({approval_tx}) did not confirm in time; nothing was swapped. "
                "It allows exactly this amount, so it is safe to leave."
            )
        time.sleep(poll)


def _execute(
    conn: Any, row_id: int, c: Conversion, provider: str, owns: tuple[str, str], cluster: str, the_caps: caps.Caps,
    params: dict[str, Any], checked: Checked, summary: dict[str, Any], core: Core,
) -> dict[str, Any]:
    if chains.is_evm(c.from_chain) and c.from_asset == "USDC":
        approval_tx = _approve(conn, row_id, c, provider, checked.spender or "", core)
        summary["approval_tx"] = approval_tx
        if checked.expires_at_ms <= _now_ms() + EXPIRY_MARGIN_MS:
            try:
                fresh = check_quote(c, provider, _quote(c, provider, params, core), cluster, the_caps, _now_ms() + EXPIRY_MARGIN_MS, *owns)
            except (hand.Refused, hand.HandError) as exc:
                store.update_row(conn, "wallet_conversions", row_id, status="failed", error=f"re-quote: {exc}"[:300])
                raise
            if not chains.same_address(c.from_chain, fresh.spender, checked.spender):
                store.update_row(conn, "wallet_conversions", row_id, status="failed", error="the new quote names another spender")
                raise hand.Refused("the new quote goes to another contract than the one approved; nothing was swapped", rule="quote")
            checked = fresh
            store.update_row(conn, "wallet_conversions", row_id, quote_id=checked.quote_id, expected_raw=str(checked.min_raw), tool=checked.tool)
            summary.update(_quoted(c, checked))
    method = "web3_swap_execute" if c.kind == "swap" else "web3_bridge_execute"
    try:
        result = core(method, {"quoteId": checked.quote_id, "confirmed": True}, timeout=120)
    except hand.CoreError as exc:
        store.update_row(conn, "wallet_conversions", row_id, status="in_doubt", error=str(exc)[:300])
        raise hand.HandError(
            f"conversion {row_id} is in doubt: the execute was sent and did not come back ({exc}). "
            "It is never retried; check the chain (hands/wallet conversions)."
        ) from None
    signature = result.get("transactionHash") if isinstance(result, Mapping) else None
    if not isinstance(signature, str) or not signature:
        store.update_row(conn, "wallet_conversions", row_id, status="in_doubt", error="the execute answered without a signature")
        raise hand.HandError(f"conversion {row_id} is in doubt: the core answered without a signature. It is never retried.")
    store.update_row(conn, "wallet_conversions", row_id, status="sent", signature=signature, order_id=checked.order_id)
    sent = {**summary, "conversion_id": row_id, "signature": signature, "url": chains.explorer_tx(c.from_chain, signature, cluster)}
    track = _track(provider, c.kind, signature, checked.order_id)
    if track:
        sent["track"] = track
    return {"sent": sent}


def _track(provider: str | None, kind: str, signature: str | None, order_id: str | None) -> str | None:
    """Where a bridge can be followed to its destination: deBridge's order,
    or LI.FI's scan of the source transaction."""
    if order_id:
        return f"https://app.debridge.finance/order?orderId={order_id}"
    if provider == "lifi" and kind == "bridge" and signature:
        return f"https://scan.li.fi/tx/{signature}"
    return None


def conversions(max_rows: int) -> dict[str, Any]:
    """The swaps and bridges, newest first."""
    conn = store.db()
    try:
        rows = conn.execute("SELECT * FROM wallet_conversions ORDER BY id DESC LIMIT ?", (max_rows,)).fetchall()
    finally:
        conn.close()
    out = []
    for row in rows:
        entry = store.row_dict(row) or {}
        entry["amount"] = caps.display(int(entry["amount_raw"]), entry["from_asset"])
        entry["usd"] = caps.usd(int(entry.pop("usd_micro")))
        if entry.get("signature"):
            entry["url"] = chains.explorer_tx(entry["from_chain"], entry["signature"], "mainnet")
        track = _track(entry.get("provider"), entry["kind"], entry.get("signature"), entry.get("order_id"))
        if track:
            entry["track"] = track
        out.append(entry)
    return {"conversions": out}
