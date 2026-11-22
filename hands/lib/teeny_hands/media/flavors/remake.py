"""The remake flavor: a popular short re-made as one take from a keyframe
swap of its opening frame (media/remakes.py; a key a shot only when one take
falls back). `keys` buys the keyframe, `render` makes the film and cuts it
with the source's sound and the label, `drop` lets a keyed remake go
unrendered."""

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
    summary="a popular short re-made as one take with public figures and Teeny, from a keyframe swap",
    steps=(
        Step("keys", keys, "buy one remake's keyframes (JSON on stdin)", True),
        Step("render", render, "render one remake from its keyframes (JSON on stdin)", True),
        Step("drop", drop, "let go of a remake's keys unrendered (JSON on stdin)", True),
    ),
    kinds=("remake", "key", "shot"),
    shares_video_cap=True,
    default_step="render",
)
