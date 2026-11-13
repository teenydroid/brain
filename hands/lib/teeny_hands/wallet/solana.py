"""Solana addresses, and the one read the core does not do yet.

The core's `wallet_balances` reports native SOL only; token balances come in
a later release. So USDC and $TINY are read here, straight from a Solana JSON-RPC
endpoint (`getTokenAccountsByOwner`, jsonParsed): SOLANA_RPC_URL from
secrets/teeny.env, else the cluster's public endpoint. The same URL is given
to the core as OPENHUMAN_WALLET_RPC_SOLANA (bin/lib/wallet_config.py), so
both read one chain.

An endpoint URL can carry a provider's API key, so no error here names it.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Any

from .. import hand
from . import caps

_ALPHABET = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"
_INDEX = {c: i for i, c in enumerate(_ALPHABET)}
TIMEOUT_SECONDS = 20.0


def b58decode(text: str) -> bytes | None:
    """Base58 (Bitcoin alphabet) to bytes; None for any character outside it."""
    if not text:
        return None
    number = 0
    for char in text:
        digit = _INDEX.get(char)
        if digit is None:
            return None
        number = number * 58 + digit
    body = number.to_bytes((number.bit_length() + 7) // 8, "big") if number else b""
    leading = len(text) - len(text.lstrip("1"))
    return b"\x00" * leading + body


def b58encode(data: bytes) -> str:
    """Bytes to base58 (Bitcoin alphabet): the inverse of b58decode."""
    number = int.from_bytes(data, "big")
    out = ""
    while number:
        number, digit = divmod(number, 58)
        out = _ALPHABET[digit] + out
    return "1" * (len(data) - len(data.lstrip(b"\x00"))) + out


def is_address(text: object) -> bool:
    """A Solana public key: base58 that decodes to exactly 32 bytes."""
    if not isinstance(text, str) or not 32 <= len(text) <= 44:
        return False
    decoded = b58decode(text)
    return decoded is not None and len(decoded) == 32


def explorer_tx(signature: str, cluster: str) -> str:
    """The transaction on Solscan, for the cluster it went out on."""
    url = f"https://solscan.io/tx/{signature}"
    return url + ("?cluster=devnet" if cluster == "devnet" else "")


def explorer_account(address: str, cluster: str) -> str:
    url = f"https://solscan.io/account/{address}"
    return url + ("?cluster=devnet" if cluster == "devnet" else "")


class RpcError(hand.HandError):
    """The RPC answered, with an error for this one request (a transaction
    it will not return, say), as against one that could not be reached."""


def rpc_url(cluster: str) -> str:
    return hand.setting("SOLANA_RPC_URL") or caps.RPC_DEFAULTS[cluster]


def rpc(cluster: str, method: str, params: list[Any]) -> Any:
    """One JSON-RPC call; its `result`. Errors name the method, not the URL."""
    body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": method, "params": params}).encode()
    request = urllib.request.Request(
        rpc_url(cluster), data=body, headers={"content-type": "application/json", "user-agent": "teeny-hands-wallet"}, method="POST"
    )
    failure: str | None = None
    payload: Any = None
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as response:
            payload = json.loads(response.read() or b"{}")
    except urllib.error.HTTPError as exc:
        failure = f"Solana RPC {method} answered HTTP {exc.code}"
    except (urllib.error.URLError, OSError, ValueError) as exc:
        failure = f"could not reach Solana RPC for {method} ({type(getattr(exc, 'reason', exc)).__name__})"
    if failure is not None:
        raise hand.HandError(failure)
    if not isinstance(payload, dict):
        raise hand.HandError(f"Solana RPC {method} sent something unreadable")
    if payload.get("error"):
        error = payload["error"]
        message = error.get("message") if isinstance(error, dict) else error
        raise RpcError(f"Solana RPC {method}: {str(message)[:200]}")
    return payload.get("result")


def token_balance(cluster: str, owner: str, mint: str) -> int:
    """The owner's balance of one mint, in atomic units, across every token
    account it holds for that mint (0 when it holds none)."""
    return token_holding(cluster, owner, mint)[0]


def token_holding(cluster: str, owner: str, mint: str) -> tuple[int, int | None]:
    """The owner's balance of one mint in atomic units, summed over every
    token account it holds for that mint, and the mint's decimals as those
    accounts report them: (0, None) when it holds no account for it. A
    plain read (`getTokenAccountsByOwner`, jsonParsed); nothing is signed."""
    result = rpc(cluster, "getTokenAccountsByOwner", [owner, {"mint": mint}, {"encoding": "jsonParsed", "commitment": "confirmed"}])
    accounts = result.get("value") if isinstance(result, dict) else None
    if not isinstance(accounts, list):
        raise hand.HandError("Solana RPC getTokenAccountsByOwner sent no account list")
    total = 0
    decimals: int | None = None
    for account in accounts:
        try:
            info = account["account"]["data"]["parsed"]["info"]
            if info.get("mint") != mint:
                continue
            total += int(info["tokenAmount"]["amount"])
            if decimals is None and info["tokenAmount"].get("decimals") is not None:
                decimals = int(info["tokenAmount"]["decimals"])
        except (KeyError, TypeError, ValueError, AttributeError):
            raise hand.HandError("Solana RPC sent a token account it could not read") from None
    return total, decimals


def token_accounts(cluster: str, owner: str, mint: str) -> dict[str, int]:
    """{token account: raw amount} for one owner and mint (`buy-tiny` and a
    swap or bridge read them to check a simulation against)."""
    result = rpc(cluster, "getTokenAccountsByOwner", [owner, {"mint": mint}, {"encoding": "jsonParsed", "commitment": "confirmed"}])
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


def _shortvec(wire: bytes, at: int) -> tuple[int, int]:
    value = shift = 0
    for _ in range(3):
        byte = wire[at]
        at += 1
        value |= (byte & 0x7F) << shift
        shift += 7
        if not byte & 0x80:
            return value, at
    raise ValueError("a length that does not end")


def tx_signers(wire: bytes) -> list[tuple[str, bool]]:
    """A serialized transaction's required signers, in order (the first pays
    the fee), each with whether its signature slot is already filled. Reads
    the layout the wallet module signs by (legacy or v0); ValueError when
    the bytes are not a transaction."""
    try:
        count, at = _shortvec(wire, 0)
        signatures = [wire[at + 64 * i:at + 64 * (i + 1)] for i in range(count)]
        message = at + 64 * count
        header = message + (1 if wire[message] & 0x80 else 0)
        required = wire[header]
        keys, at = _shortvec(wire, header + 3)
    except IndexError:
        raise ValueError("the transaction is cut short") from None
    if not 0 < required <= keys or required > count or at + 32 * keys > len(wire) or any(len(s) != 64 for s in signatures):
        raise ValueError("the transaction's header does not add up")
    return [(b58encode(wire[at + 32 * i:at + 32 * (i + 1)]), any(signatures[i])) for i in range(required)]
