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
        raise hand.HandError(f"Solana RPC {method}: {str(message)[:200]}")
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
