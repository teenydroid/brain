"""Jupiter's public quote API, read only: what a USDC to $TINY swap would give.

    GET https://api.jup.ag/swap/v1/quote
        ?inputMint=..&outputMint=..&amount=..&slippageBps=..
        &swapMode=ExactIn&restrictIntermediateTokens=true

The answer names its mints, the amounts in atomic units, `otherAmountThreshold`
(the least the swap may give after slippage), `priceImpactPct` and the
`routePlan`, hop by hop. A quote moves nothing and needs no key; with
JUPITER_API_KEY set in secrets/teeny.env it is sent as `x-api-key`.
JUPITER_QUOTE_URL moves the endpoint (https only; the tests point it at
127.0.0.1).

Nothing here builds, signs or sends a transaction. No error names the URL or
the key.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

from .. import hand

QUOTE_URL = "https://api.jup.ag/swap/v1/quote"
TIMEOUT_SECONDS = 20.0


def quote_url() -> str:
    url = hand.setting("JUPITER_QUOTE_URL") or QUOTE_URL
    if not (url.startswith("https://") or url.startswith("http://127.0.0.1:")):
        raise hand.HandError("JUPITER_QUOTE_URL must be an https URL")
    return url


def quote(input_mint: str, output_mint: str, amount_raw: int, slippage_bps: int) -> dict[str, Any]:
    """One ExactIn quote. The answer is returned as Jupiter sent it; the
    caller checks every field it relies on."""
    query = urllib.parse.urlencode(
        {
            "inputMint": input_mint,
            "outputMint": output_mint,
            "amount": str(amount_raw),
            "slippageBps": str(slippage_bps),
            "swapMode": "ExactIn",
            "restrictIntermediateTokens": "true",
        }
    )
    headers = {"accept": "application/json", "user-agent": "teeny-hands-wallet"}
    key = hand.setting("JUPITER_API_KEY")
    if key:
        headers["x-api-key"] = key
    request = urllib.request.Request(f"{quote_url()}?{query}", headers=headers, method="GET")
    failure: str | None = None
    payload: Any = None
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as response:
            payload = json.loads(response.read() or b"{}")
    except urllib.error.HTTPError as exc:
        failure = f"Jupiter's quote answered HTTP {exc.code}"
    except (urllib.error.URLError, OSError, ValueError) as exc:
        failure = f"could not reach Jupiter's quote ({type(getattr(exc, 'reason', exc)).__name__})"
    if failure is not None:
        raise hand.HandError(failure)
    if not isinstance(payload, dict):
        raise hand.HandError("Jupiter's quote sent something unreadable")
    if payload.get("error") or payload.get("errorCode"):
        raise hand.HandError(f"Jupiter's quote: {str(payload.get('error') or payload.get('errorCode'))[:200]}")
    return payload
