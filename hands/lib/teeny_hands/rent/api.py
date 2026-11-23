"""RentAHuman, reached as its MCP tools over plain JSON-RPC (`POST /api/mcp`
with `X-API-Key`). The tools are RentAHuman's documented, versioned surface
(https://rentahuman.ai/docs/api.md): amounts are whole US cents, a write
takes an `idempotencyKey`, and a refusal says whether nothing changed
(`isRetrySafe`). The REST paths under /api answer an older shape.

What leaves this file never names the key, a header or the URL: errors are
the tool's own code and short message.
"""

from __future__ import annotations

import json
import urllib.parse
from typing import Any, Mapping

from .. import hand, rest

API_BASE = "https://rentahuman.ai"
_AGENT = "teeny-hands/rent"
_LOCAL = ("127.0.0.1", "localhost", "::1")
_MESSAGE_CHARS = 300


class ToolError(hand.HandError):
    """RentAHuman refused a tool call. `retry_safe` True means nothing was
    changed; False or None means the write may have happened."""

    def __init__(self, tool: str, code: str, message: str, retry_safe: bool | None, details: Any = None) -> None:
        super().__init__(f"rentahuman {tool}: {code}: {message}")
        self.tool = tool
        self.code = code
        self.retry_safe = retry_safe
        self.details = details if isinstance(details, Mapping) else {}


class Uncertain(hand.HandError):
    """A write whose outcome is unknown: the service was not reached, timed
    out or failed on its side. Read the state before doing anything else."""


def base() -> str:
    value = (hand.setting("RENTAHUMAN_API_BASE", API_BASE) or API_BASE).rstrip("/")
    parts = urllib.parse.urlsplit(value)
    if parts.scheme != "https" and (parts.hostname or "") not in _LOCAL:
        raise hand.HandError("RENTAHUMAN_API_BASE must be https")
    return value


def _clip(text: Any, key: str) -> str:
    out = str(text or "").replace(key, "[the key]").strip()
    return out[:_MESSAGE_CHARS]


def _payload(result: Mapping[str, Any]) -> Any:
    structured = result.get("structuredContent")
    if structured is not None:
        return structured
    for part in result.get("content") or ():
        if isinstance(part, Mapping) and part.get("type") == "text":
            try:
                return json.loads(part.get("text") or "")
            except ValueError:
                return {"text": part.get("text")}
    return {}


def tool(name: str, arguments: Mapping[str, Any] | None = None, *, write: bool = False) -> Any:
    """One tool call; its JSON reply. A refusal is a ToolError. With `write`,
    a failure that may have changed something is an Uncertain."""
    key = hand.require("RENTAHUMAN_API_KEY")
    client = rest.RestApi("rentahuman", base(), key, _AGENT)
    body = {"jsonrpc": "2.0", "id": 1, "method": "tools/call",
            "params": {"name": name, "arguments": dict(arguments or {})}}
    try:
        answer = client.call("POST", "/api/mcp", body=body, auth=False, headers={"x-api-key": key})
    except rest.RestError as exc:
        if exc.status == 429:
            raise hand.Refused("RentAHuman says to slow down", retry_after=exc.retry_after) from None
        # 4xx at the transport is a refused request; anything else may have run.
        if write and (exc.status is None or exc.status >= 500):
            raise Uncertain(f"rentahuman {name}: {_clip(exc, key)}") from None
        raise hand.HandError(f"rentahuman {name}: {_clip(exc, key)}") from None
    if not isinstance(answer, Mapping):
        if write:
            raise Uncertain(f"rentahuman {name}: an unreadable answer")
        raise hand.HandError(f"rentahuman {name}: an unreadable answer")
    if answer.get("error"):
        error = answer["error"] if isinstance(answer["error"], Mapping) else {"message": answer["error"]}
        # A JSON-RPC error is the server refusing the request itself (auth,
        # a bad method): the tool never ran.
        raise hand.HandError(f"rentahuman {name}: {_clip(error.get('message'), key)}")
    result = answer.get("result")
    if not isinstance(result, Mapping):
        if write:
            raise Uncertain(f"rentahuman {name}: no result")
        raise hand.HandError(f"rentahuman {name}: no result")
    payload = _payload(result)
    if result.get("isError"):
        error = payload.get("error") if isinstance(payload, Mapping) else None
        error = error if isinstance(error, Mapping) else {}
        code = str(error.get("code") or "error")
        message = _clip(error.get("message") or error.get("fix") or "refused", key)
        retry_safe = error.get("isRetrySafe")
        if code == "rate_limited":
            details = error.get("details") if isinstance(error.get("details"), Mapping) else {}
            raise hand.Refused("RentAHuman says to slow down", retry_after=details.get("retryAfterSeconds"))
        raise ToolError(name, code, message, retry_safe if isinstance(retry_safe, bool) else None, error.get("details"))
    return payload
