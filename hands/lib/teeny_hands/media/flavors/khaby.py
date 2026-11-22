"""The khaby flavor: a Khaby Lame video with its setup kept as it is and
his reaction re-made with Teeny standing where he stood (media/khaby.py).
`keys` buys the reaction's keyframes, `render` animates them and cuts them
between the kept segments, `drop` lets a keyed one go unrendered. No old
verb, so no alias and no default step."""

from __future__ import annotations

from typing import Any

from . import Flavor, Step


def keys(args: Any, body: Any) -> Any:
    from .. import khaby

    return khaby.generate_keys(body)


def render(args: Any, body: Any) -> Any:
    from .. import khaby

    return khaby.generate_khaby(body)


def drop(args: Any, body: Any) -> Any:
    from .. import khaby

    return khaby.drop_khaby(body)


FLAVOR = Flavor(
    name="khaby",
    summary="a Khaby Lame video: the setup kept from the source, the reaction re-made with Teeny in his place",
    steps=(
        Step("keys", keys, "buy one khaby video's reaction keyframes (JSON on stdin)", True),
        Step("render", render, "render one khaby video from its keyframes and the kept source (JSON on stdin)", True),
        Step("drop", drop, "let go of a khaby video's keys unrendered (JSON on stdin)", True),
    ),
    kinds=("khaby", "key", "shot"),
    shares_video_cap=True,
)
