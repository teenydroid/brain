"""The LI.FI quote, checked: what convert.py asks before a LI.FI swap or
bridge is signed (docs/hands.md, "`swap` and `bridge`").

The core answers `web3_swap_quote` / `web3_bridge_quote` with the wallet
module's envelope around the spine's translation of LI.FI's `/v1/quote`
(spine/src/lifi.rs): LI.FI's JSON, its `transactionRequest` replaced by `tx`,
the one transaction the module will sign (EVM `{to, data, value}` with value
in decimal wei, Solana `{data}` as 0x hex), and `"provider": "lifi"`.

Refused (rule `quote`) unless:

  provider    the answer is LI.FI's
  route       it starts and ends on the chains asked for (LI.FI's ids)
  input       its input token and amount are exactly the ones asked for
  output      its output token is the pinned one asked for
  recipient   it is from Teeny's own address and to Teeny's own address on
              the destination, and the signed transaction itself carries that
              address (a route that keeps the recipient off-chain, such as a
              deposit address, cannot be checked and is refused)
  evm         the transaction goes to the pinned LiFiDiamond; an ERC-20 input
              is approved only to `estimate.approvalAddress`, and only when
              that is the diamond; the native value is at least the input
  solana      it decodes; Teeny pays the fee and its slot is unsigned; any
              other signer has already signed (the module signs only
              Teeny's slot). It is simulated over Solana RPC (sigVerify off,
              the blockhash replaced): it must succeed, and an input of USDC
              must take no more than the amount
  loss        the minimum output, valued, is worth at least the input less
              WALLET_MAX_CONVERT_LOSS_BPS, after the costs below

Valuation: USDC at par; anything else at LI.FI's own USD estimates (the fixed
WALLET_SOL_USD / WALLET_ETH_USD are set high on purpose and would hide a loss
on the way out). The output counts at `toAmountMin`. Costs are the fees LI.FI
lists as not included in the output, plus the gas it lists; on EVM, native
value sent beyond the input when that is more; on Solana, the SOL the
simulation takes beyond the input when that is more. The last one matters:
live, a 0.1 SOL bridge to Base through Mayan took 0.00337 SOL more than the
input and the fee, rent for accounts it opened at Teeny's cost, which LI.FI's
costs do not list.

LI.FI's quotes carry no expiry of their own: the minimum output binds on
chain, and the module refreshes a Solana blockhash before it signs. The
envelope's `expiresAtMs` is the module's, checked by convert.py for both
providers.
"""

from __future__ import annotations

import base64
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any, Mapping

from .. import hand
from . import caps, chains, solana

#: LI.FI's chain id for Solana; an EVM network's is its own.
SOLANA_ID = 1151111081099710

#: The LiFiDiamond, the only contract a LI.FI EVM transaction may go to and
#: the only spender an approval may name. The same address on every network
#: the wallet knows: LI.FI's `/v1/chains` (`diamondAddress`) and live quotes
#: on Base, Arbitrum and Optimism, 2026-11-13.
DIAMOND = {
    "base": "0x1231deb6f5749ef6ce6943a275a1d3e7486f4eae",
    "arbitrum": "0x1231deb6f5749ef6ce6943a275a1d3e7486f4eae",
    "ethereum": "0x1231deb6f5749ef6ce6943a275a1d3e7486f4eae",
    "optimism": "0x1231deb6f5749ef6ce6943a275a1d3e7486f4eae",
}


@dataclass(frozen=True)
class Terms:
    expected_raw: int
    min_raw: int
    in_market_micro: int
    out_micro: int
    fee_micro: int
    spender: str | None  # the diamond, for an ERC-20 input
    tool: str | None


def chain_id(chain: str) -> int:
    return SOLANA_ID if chain == chains.SOLANA else chains.NETWORKS[chain].debridge_id


def bad(reason: str) -> hand.Refused:
    return hand.Refused(f"the LI.FI quote is not usable: {reason}; nothing was signed", rule="quote")


def _int(value: Any, what: str) -> int:
    try:
        number = int(str(value))
    except (TypeError, ValueError):
        raise bad(f"{what} is not a whole number") from None
    if number < 0:
        raise bad(f"{what} is negative")
    return number


def _decimal(value: Any, what: str) -> Decimal:
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        raise bad(f"it gives no {what}")
    try:
        number = Decimal(str(value))
    except InvalidOperation:
        raise bad(f"its {what} is not a number") from None
    if not number.is_finite() or number < 0:
        raise bad(f"its {what} is not a number")
    return number


def _usd(value: Any, what: str) -> int:
    """A LI.FI USD amount (a decimal string) as micro-dollars."""
    return int(_decimal(value, f"USD estimate for {what}") * caps.MICRO)


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _costs(estimate: Mapping[str, Any]) -> int:
    """Fees LI.FI charges on top of the input, plus its gas, in micro-dollars."""
    total = 0
    for fee in estimate.get("feeCosts") or []:
        if _mapping(fee).get("included") is not True:
            total += _usd(_mapping(fee).get("amountUSD"), "a fee")
    for gas in estimate.get("gasCosts") or []:
        total += _usd(_mapping(gas).get("amountUSD"), "the gas")
    return total


def _native_price(q: Mapping[str, Any], chain: str, the_caps: caps.Caps) -> int:
    """Micro-dollars for one whole unit of the chain's native asset: LI.FI's
    own price where the quote names that token, else the fixed price."""
    action, estimate = _mapping(q.get("action")), _mapping(q.get("estimate"))
    native = chains.SOL_NATIVE if chain == chains.SOLANA else chains.EVM_NATIVE
    tokens = [action.get("fromToken"), action.get("toToken")]
    tokens += [_mapping(cost).get("token") for cost in [*(estimate.get("gasCosts") or []), *(estimate.get("feeCosts") or [])]]
    for token in map(_mapping, tokens):
        if token.get("chainId") == chain_id(chain) and chains.same_address(chain, token.get("address"), native) and token.get("priceUSD"):
            return int(_decimal(token["priceUSD"], "native price") * caps.MICRO)
    asset = "SOL" if chain == chains.SOLANA else chains.NETWORKS[chain].native
    try:
        return caps.usd_micro(asset, 10 ** caps.DECIMALS[asset], the_caps)
    except caps.CapsError:
        raise hand.Refused(f"LI.FI gives no {asset} price and {caps.ETH_USD} is not set", rule="caps") from None


def _recipient_bytes(chain: str, address: str) -> bytes:
    if chains.is_evm(chain):
        return bytes.fromhex(address[2:])
    return solana.b58decode(address) or b"\x00"


def check(c: Any, q: Any, cluster: str, the_caps: caps.Caps, src_own: str, dst_own: str) -> Terms:
    """LI.FI's quote (the envelope's `quote`) checked against the conversion
    `c` (convert.Conversion), or rule `quote`."""
    if not isinstance(q, Mapping) or q.get("provider") != "lifi":
        raise bad("the answer is not LI.FI's")
    action, estimate = _mapping(q.get("action")), _mapping(q.get("estimate"))
    if action.get("fromChainId") != chain_id(c.from_chain) or action.get("toChainId") != chain_id(c.to_chain):
        raise bad(f"it does not go from {c.from_chain} to {c.to_chain}")
    token_in = chains.token_address(c.from_chain, c.from_asset, cluster, caps.USDC_MINTS)
    token_out = chains.token_address(c.to_chain, c.to_asset, cluster, caps.USDC_MINTS)
    if not chains.same_address(c.from_chain, _mapping(action.get("fromToken")).get("address"), token_in):
        raise bad(f"its input is not {c.from_asset} on {c.from_chain}")
    if _int(action.get("fromAmount"), "the input amount") != c.raw or _int(estimate.get("fromAmount", c.raw), "the input amount") != c.raw:
        raise bad("its input amount is not the amount asked for")
    if not chains.same_address(c.to_chain, _mapping(action.get("toToken")).get("address"), token_out):
        raise bad(f"its output is not {c.to_asset} on {c.to_chain}")
    if not chains.same_address(c.from_chain, action.get("fromAddress"), src_own):
        raise bad("it is not from Teeny's own address")
    if not chains.same_address(c.to_chain, action.get("toAddress"), dst_own):
        raise bad(f"its recipient is not Teeny's own address on {c.to_chain}")
    expected = _int(estimate.get("toAmount"), "the output amount")
    min_raw = _int(estimate.get("toAmountMin"), "the minimum output")
    if min_raw <= 0 or min_raw > expected:
        raise bad("its minimum output is zero or above its estimate")

    in_market = c.raw if c.from_asset == "USDC" else _usd(estimate.get("fromAmountUSD"), "the input")
    if c.to_asset == "USDC":
        out_micro = min_raw
    else:
        out_micro = _usd(estimate.get("toAmountUSD"), "the output") * min_raw // expected
    listed = _costs(estimate)

    tx = _mapping(q.get("tx"))
    spender: str | None = None
    if chains.is_evm(c.from_chain):
        extra, spender = _check_evm(c, tx, estimate)
    else:
        extra = _check_solana(c, tx, cluster, src_own, dst_own, expected)
    extra_micro = extra * _native_price(q, c.from_chain, the_caps) // 10 ** caps.DECIMALS["SOL" if c.from_chain == chains.SOLANA else "ETH"] if extra > 0 else 0
    fee_micro = max(listed, extra_micro)

    loss = in_market + fee_micro - out_micro
    if loss * 10_000 > in_market * the_caps.convert_loss_bps:
        raise bad(
            f"it would lose ${caps.usd(max(loss, 0))} of ${caps.usd(in_market)}, over the"
            f" {the_caps.convert_loss_bps / 100:g}% {caps.CONVERT_LOSS_BPS} allows"
        )
    tool = q.get("tool") if isinstance(q.get("tool"), str) else None
    return Terms(expected, min_raw, in_market, out_micro, fee_micro, spender, tool)


def _check_evm(c: Any, tx: Mapping[str, Any], estimate: Mapping[str, Any]) -> tuple[int, str | None]:
    """(native wei sent beyond the input, the approval spender)."""
    diamond = DIAMOND[c.from_chain]
    to = tx.get("to")
    if not chains.is_evm_address(to) or to.lower() != diamond:
        raise bad("its transaction goes to a contract that is not LI.FI's diamond")
    data = tx.get("data")
    try:
        calldata = bytes.fromhex(str(data).removeprefix("0x"))
    except ValueError:
        raise bad("its transaction data is not hex") from None
    if _recipient_bytes(c.to_chain, c.recipient) not in calldata:
        raise bad("its transaction does not name the recipient")
    spender = None
    native = chains.NETWORKS[c.from_chain].native
    if c.from_asset != native:
        approval = estimate.get("approvalAddress")
        if not chains.is_evm_address(approval) or approval.lower() != diamond:
            raise bad("its approval would go to a contract that is not LI.FI's diamond")
        spender = approval
    value = _int(tx.get("value") or 0, "the transaction value")
    extra = value - (c.raw if c.from_asset == native else 0)
    if extra < 0:
        raise bad("its transaction sends less than the input")
    return extra, spender


def _check_solana(c: Any, tx: Mapping[str, Any], cluster: str, own: str, dst_own: str, expected: int) -> int:
    """Lamports the simulation takes beyond the input (and, for SOL out on
    Solana, beyond what comes back)."""
    try:
        wire = bytes.fromhex(str(tx.get("data") or "").removeprefix("0x"))
        signers = solana.tx_signers(wire)
    except ValueError:
        raise bad("its Solana transaction does not decode") from None
    payer, payer_signed = signers[0]
    if payer != own or payer_signed:
        raise bad("its Solana transaction is not paid for and signed by Teeny")
    if not all(signed for _, signed in signers[1:]):
        raise bad("its Solana transaction needs a signature Teeny cannot give")
    if _recipient_bytes(c.to_chain, dst_own) not in wire:
        raise bad("its transaction does not name the recipient")

    usdc = solana.token_accounts(cluster, own, caps.USDC_MINTS[cluster]) if c.from_asset == "USDC" else {}
    before = solana.rpc(cluster, "getBalance", [own, {"commitment": "confirmed"}])
    lamports_before = _int(_mapping(before).get("value"), "the SOL balance")
    addresses = [own, *sorted(usdc)]
    result = solana.rpc(cluster, "simulateTransaction", [
        base64.b64encode(wire).decode(),
        {"encoding": "base64", "sigVerify": False, "replaceRecentBlockhash": True, "commitment": "confirmed",
         "accounts": {"encoding": "jsonParsed", "addresses": addresses}},
    ])
    value = _mapping(_mapping(result).get("value"))
    if not value:
        raise bad("the simulation sent no result")
    if value.get("err") is not None:
        raise bad(f"its simulation failed: {str(value['err'])[:200]}")
    after = value.get("accounts")
    if not isinstance(after, list) or len(after) != len(addresses) or not all(isinstance(a, Mapping) for a in after):
        raise bad("the simulation did not report Teeny's accounts")
    if usdc:
        try:
            left = sum(int(a["data"]["parsed"]["info"]["tokenAmount"]["amount"]) for a in after[1:])
        except (KeyError, TypeError, ValueError):
            raise bad("the simulation sent a token account it could not read") from None
        if sum(usdc.values()) - left > c.raw:
            raise bad("its simulation takes more USDC than the amount")
    debit = lamports_before - _int(after[0].get("lamports"), "the simulated SOL balance")
    back = expected if c.to_asset == "SOL" and c.to_chain == chains.SOLANA else 0
    return debit + back - (c.raw if c.from_asset == "SOL" else 0)
