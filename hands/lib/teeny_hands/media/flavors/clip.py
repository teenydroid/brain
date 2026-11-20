"""The clip flavor: a still of Teeny, animated (media/assets.py)."""

from __future__ import annotations

from typing import Any

from . import Flavor, Step


def make(args: Any, body: Any) -> Any:
    from .. import assets

    return assets.generate_video(body)


FLAVOR = Flavor(
    name="clip",
    summary="a still of Teeny from its character sheet, animated as the prompt says",
    steps=(Step("make", make, "make one clip {prompt, image_prompt?} (JSON on stdin)", True),),
    kinds=("video", "image"),
    shares_video_cap=False,
)
