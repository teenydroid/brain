"""The seed and the draw order, exactly as the /rewards page re-runs them
(docs/drops-schema.md, "The draw"; web/src/lib/drops.ts):

    seed.sha256 = hex(sha256(utf8("<id>:<blockhash>")))
    draw_order  = the eligible handles, sorted ascending by
                  hex(sha256(bytes.fromhex(seed.sha256) + utf8(handle.lower()))),
                  ties (which should not happen) by handle

`blockhash` is the base58 blockhash of `seed.slot`, the first finalized slot
whose block time is at or after the deadline. Nobody knows it before the
deadline and nobody can choose it after, so anyone can re-run the draw and
no one, Teeny included, could have steered it.

The slot is found over Solana JSON-RPC at GIVEAWAY_RPC_URL (required; the
round's cluster): `getSlot` (finalized) for the newest, then a search over
`getBlocks` and `getBlockTime` down to the first produced slot at or after
the deadline, then `getBlock` for its hash. Tests replace `rpc`.
"""

from __future__ import annotations

import hashlib
import json
import urllib.error
import urllib.request
from typing import Any, Callable, Iterable

from .. import hand
from ..wallet import solana

RPC_SETTING = "GIVEAWAY_RPC_URL"
FINALIZED = {"commitment": "finalized"}
#: Roughly how long a slot takes, for the first guess of where to look.
SLOT_SECONDS = 0.4
#: How far getBlocks is asked to look in one call.
WINDOW = 1000


def seed_hash(round_id: int, blockhash: str) -> str:
    return hashlib.sha256(f"{int(round_id)}:{blockhash}".encode()).hexdigest()


def draw_key(seed_hex: str, handle: str) -> str:
    return hashlib.sha256(bytes.fromhex(seed_hex) + handle.lower().encode()).hexdigest()


def order(seed_hex: str, handles: Iterable[str]) -> list[str]:
    """The handles, lowercased, in draw order."""
    keyed = sorted((draw_key(seed_hex, h), h.lower()) for h in handles)
    return [handle for _, handle in keyed]


# --- the seed slot ---------------------------------------------------------------------------


class NotFinal(hand.Refused):
    def __init__(self, why: str) -> None:
        super().__init__(f"the seed is not final yet: {why}", rule="seed_not_final")


def _http(method: str, params: list[Any]) -> Any:
    url = hand.setting(RPC_SETTING)
    if not url:
        raise hand.HandError(f"{RPC_SETTING} is not set in secrets/teeny.env")
    body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": method, "params": params}).encode()
    request = urllib.request.Request(url, data=body, headers={"content-type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(request, timeout=solana.TIMEOUT_SECONDS) as response:
            payload = json.loads(response.read() or b"{}")
    except urllib.error.HTTPError as exc:
        raise hand.HandError(f"{method}: the giveaway RPC answered HTTP {exc.code}") from None
    except (urllib.error.URLError, TimeoutError, OSError, ValueError):
        raise hand.HandError(f"{method}: the giveaway RPC could not be reached") from None
    if payload.get("error"):
        error = payload["error"]
        raise solana.RpcError(f"{method}: {error.get('message', error) if isinstance(error, dict) else error}")
    return payload.get("result")


#: One JSON-RPC call: (method, params) -> result. Tests stand in for it.
rpc: Callable[[str, list[Any]], Any] = _http


def _produced_from(start: int, tip: int) -> int | None:
    """The first produced (finalized) slot at or after `start`, up to `tip`."""
    at = start
    while at <= tip:
        end = min(at + WINDOW, tip)
        blocks = rpc("getBlocks", [at, end, FINALIZED]) or []
        if blocks:
            return int(min(blocks))
        at = end + 1
    return None


def _time(slot: int) -> int:
    value = rpc("getBlockTime", [slot])
    if not isinstance(value, int):
        raise hand.HandError(f"the giveaway RPC has no block time for slot {slot}")
    return value


def seed_slot(deadline: float) -> dict[str, Any]:
    """{slot, blockhash}: the first finalized slot with a block time at or
    after `deadline`. Refused (rule `seed_not_final`) until one exists."""
    tip = rpc("getSlot", [FINALIZED])
    if not isinstance(tip, int):
        raise hand.HandError("the giveaway RPC did not say its finalized slot")
    recent = rpc("getBlocks", [max(0, tip - WINDOW), tip, FINALIZED]) or []
    if not recent:
        raise hand.HandError("the giveaway RPC has no finalized block near its tip")
    tip_slot = int(max(recent))
    tip_time = _time(tip_slot)
    if tip_time < deadline:
        raise NotFinal("no finalized block after the deadline yet")
    # Below: a produced slot before the deadline. Above: one at or after it.
    step = int((tip_time - deadline) / SLOT_SECONDS) + WINDOW
    low = None
    for _ in range(40):
        guess = max(0, tip_slot - step)
        found = _produced_from(guess, tip_slot)
        if found is not None and found < tip_slot and _time(found) < deadline:
            low = found
            break
        if guess == 0:
            low = -1
            break
        step *= 2
    if low is None:
        raise hand.HandError("could not find a block before the deadline")
    high = tip_slot
    while high - low > 1:
        middle = (low + high) // 2
        found = _produced_from(middle, high)
        if found is None or found >= high:
            low = middle
        elif _time(found) >= deadline:
            high = found
        else:
            low = found
    block = rpc("getBlock", [high, {**FINALIZED, "transactionDetails": "none", "rewards": False,
                                    "maxSupportedTransactionVersion": 0}])
    blockhash = block.get("blockhash") if isinstance(block, dict) else None
    if not solana.is_address(blockhash):
        raise hand.HandError(f"the giveaway RPC gave no blockhash for slot {high}")
    return {"slot": high, "blockhash": blockhash}
