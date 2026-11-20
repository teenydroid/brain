"""The flavors of hands/video (docs/hands.md, "video"): each kind of video it
makes, as a group of steps (`hands/video remake keys`).

A flavor is one module here holding a `FLAVOR`, plus one line in `REGISTRY`.
media/cli.py builds the verbs from the registry, `hands/video flavors` lists
it, and media/skits.py counts the flavors that share the daily video cap
(`video_kinds`).

A step's handler is `(args, body) -> answer`, the hand contract's
(teeny_hands/hand.py). It finds the function it calls at call time, importing
its module inside the handler: the tests patch those module attributes, and
media/skits.py imports this package, so nothing here may import skits,
remakes or assets when it loads.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from ... import hand


@dataclass(frozen=True)
class Step:
    """One verb of a flavor: `hands/video <flavor> <name>`."""

    name: str
    handler: hand.Verb
    help: str
    takes_body: bool


@dataclass(frozen=True)
class Flavor:
    """One kind of video. `kinds` are the `media_assets` kinds a job of it
    books, its own row first (`kind`); `shares_video_cap` puts it under
    MEDIA_SKIT_MAX_PER_DAY with the others that do; `default_step` is what
    `hands/video <flavor>` runs with no step (only where an old verb of that
    name must keep working)."""

    name: str
    summary: str
    steps: tuple[Step, ...]
    kinds: tuple[str, ...]
    shares_video_cap: bool
    default_step: str | None = None

    def __post_init__(self) -> None:
        names = [step.name for step in self.steps]
        if not names or len(set(names)) != len(names):
            raise ValueError(f"flavor {self.name}: its steps need distinct names, at least one")
        if self.default_step is not None and self.default_step not in names:
            raise ValueError(f"flavor {self.name}: default step {self.default_step} is not one of {names}")
        if not self.kinds:
            raise ValueError(f"flavor {self.name}: it books no kind")

    @property
    def kind(self) -> str:
        """The kind of a job's own row, the one a cap counts."""
        return self.kinds[0]


from . import clip, remake, skit  # noqa: E402  (they need Step and Flavor)

#: Every flavor, in the order `hands/video flavors` lists them.
REGISTRY: Mapping[str, Flavor] = {flavor.name: flavor for flavor in (
    clip.FLAVOR,
    skit.FLAVOR,
    remake.FLAVOR,
)}


def describe(registry: Mapping[str, Flavor] = REGISTRY) -> dict[str, Any]:
    """`hands/video flavors`: each flavor, its steps and what it books."""
    return {"flavors": [{
        "name": flavor.name,
        "summary": flavor.summary,
        "steps": [{"name": s.name, "help": s.help, "takes_body": s.takes_body} for s in flavor.steps],
        "default_step": flavor.default_step,
        "kinds": list(flavor.kinds),
        "shares_video_cap": flavor.shares_video_cap,
    } for flavor in registry.values()]}


def video_kinds(registry: Mapping[str, Flavor] = REGISTRY) -> tuple[str, ...]:
    """The kinds that share the one bought video a day (media/skits.py)."""
    return tuple(flavor.kind for flavor in registry.values() if flavor.shares_video_cap)
