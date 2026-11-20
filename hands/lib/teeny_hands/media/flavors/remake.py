"""The remake flavor: a popular short re-made shot by shot, by keyframe swap
(media/remakes.py). `keys` buys the keyframes, `render` animates and cuts
them, `drop` lets a keyed remake go unrendered."""

from __future__ import annotations

from typing import Any

from . import Flavor, Step


def keys(args: Any, body: Any) -> Any:
    from .. import remakes

    return remakes.generate_keys(body)


def render(args: Any, body: Any) -> Any:
    from .. import remakes

    return remakes.generate_remake(body)


def drop(args: Any, body: Any) -> Any:
    from .. import remakes

    return remakes.drop_remake(body)


FLAVOR = Flavor(
    name="remake",
    summary="a popular short re-made shot by shot with public figures and Teeny, by keyframe swap",
    steps=(
        Step("keys", keys, "buy one remake's keyframes (JSON on stdin)", True),
        Step("render", render, "render one remake from its keyframes (JSON on stdin)", True),
        Step("drop", drop, "let go of a remake's keys unrendered (JSON on stdin)", True),
    ),
    kinds=("remake", "key", "shot"),
    shares_video_cap=True,
    default_step="render",
)
