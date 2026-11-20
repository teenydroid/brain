"""The skit flavor: a parody video, one take or a job a shot, cut with the
label (media/skits.py)."""

from __future__ import annotations

from typing import Any

from . import Flavor, Step


def make(args: Any, body: Any) -> Any:
    from .. import skits

    return skits.generate_skit(body)


FLAVOR = Flavor(
    name="skit",
    summary="a parody video: one take, or one text-to-video job a shot, cut with the label",
    steps=(Step("make", make, "make one skit (JSON on stdin)", True),),
    kinds=("skit", "shot"),
    shares_video_cap=True,
    default_step="make",
)
