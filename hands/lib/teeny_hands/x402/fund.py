"""Keeping the x402 rail funded (docs/hands.md, "x402")."""

from __future__ import annotations

from typing import Any, Callable

from .. import hand
from . import settings

Core = Callable[..., Any]
Json = dict[str, Any]

#: What the pinned job answers while X402_FUND is not set.
OFF: Json = {"skipped": "x402 funding is off (X402_FUND=1 in secrets/teeny.env turns it on)"}


def fund(core: Core = hand.core) -> Json:
    if not settings.fund_on(hand.secrets()):
        return dict(OFF)
    raise hand.HandError("not yet")
