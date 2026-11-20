"""The verbs of hands/image and hands/video (docs/hands.md, "image" and "video").

  hands/image
    A  generate {prompt, alt?}            one still of Teeny, from its character sheet, under 5 MB
    R  list [--max N]                     the images made, newest first, and today's spend
    R  scenes [--last N]                  the last N pictures' scenes and the words they share (default 16)
    R  get ID                             one
  hands/video: one verb group a flavor (media/flavors/, REGISTRY)
    R  flavors                            the flavors, their steps, the kinds they book, the shared cap
    A  clip make {prompt, image_prompt?}  a still of Teeny, animated as `prompt` says (alias: generate)
    A  skit make {shots, aspect?, story, figures?, logline, look?, setting?, score?, wait_seconds?, bed?}
                                          a parody video: one take (one job, one script) or one
                                          text-to-video job a shot, cut with the label (media/skits.py);
                                          off until MEDIA_SKIT_MAX_PER_DAY is set (alias: skit)
    A  remake keys {study, source, story, figures?, logline, aspect?, shots, wait_seconds?}
                                          a remake's keyframes: each source shot's first frame
                                          edited into the new cast plus Teeny (media/remakes.py);
                                          off until MEDIA_REMAKE_MAX_PER_DAY is set (alias: remake-keys)
    A  remake render {remake_id, sound, wait_seconds?}
                                          the remake: its keyframes animated, trimmed to the
                                          source's shots and cut with the source's audio and
                                          the label; replays what remake keys stored (alias: remake)
    A  remake drop {remake_id}            a keyed remake let go unrendered: its shots'
                                          reservations released, its keys kept on the ledger
                                          (alias: remake-drop)
    R  list [--max N]                     the videos made, newest first, and today's spend
    R  get ID                             one

The old flat verbs stay as aliases of the steps (ALIASES, and a flavor's name
alone runs its `default_step`), answering exactly as before: bin/lib/studio.py
and the frozen tests call them. A flavor without a default step and a step
it lacks are the contract's errors ("needs a verb", "bad arguments").

Generating spends Surplus credit, under the caps in teeny_hands.media.assets,
and publishes nothing: a post attaches the file through hands/x upload.
"""

from __future__ import annotations

import argparse
from typing import Mapping

from .. import hand
from . import assets, flavors


def _bounded(low: int, high: int, what: str):
    def parse(value: str) -> int:
        number = int(value)
        if not low <= number <= high:
            raise argparse.ArgumentTypeError(f"{what} is {low}..{high}")
        return number

    return parse


#: The old flat spellings of flavor steps, kept since bin/lib/studio.py and
#: the frozen tests call them. A flavor's own name with no step is its
#: `default_step` (`video skit`, `video remake`), so it needs no line here.
ALIASES = {
    "generate": ("clip", "make"),
    "remake-keys": ("remake", "keys"),
    "remake-drop": ("remake", "drop"),
}


def parser(kind: str, registry: Mapping[str, flavors.Flavor] | None = None) -> argparse.ArgumentParser:
    """hands/<kind>'s verbs; for video, built from `registry` (the flavors'
    REGISTRY unless a test hands it another)."""
    registry = flavors.REGISTRY if registry is None else registry
    root = argparse.ArgumentParser(prog=f"hands/{kind}", description=f"{kind.capitalize()}s of Teeny for its posts.")
    verbs = root.add_subparsers(dest="verb")

    def verb(name: str, handler, help: str, body: bool = False, under=None) -> argparse.ArgumentParser:
        sub = (under or verbs).add_parser(name, help=help)
        sub.set_defaults(handler=handler, takes_body=body)
        return sub

    if kind == "video":
        verb("flavors", lambda a, _: flavors.describe(registry), "the flavors of video, their steps and what they book")
        for flavor in registry.values():
            group = verbs.add_parser(flavor.name, help=flavor.summary)
            if flavor.default_step is not None:
                default = flavor.step(flavor.default_step)
                group.set_defaults(handler=default.handler, takes_body=default.takes_body)
            steps = group.add_subparsers(dest=f"{flavor.name}_step")
            for step in flavor.steps:
                verb(step.name, step.handler, step.help, body=step.takes_body, under=steps)
        for alias, (name, step_name) in ALIASES.items():
            step = next(s for s in registry[name].steps if s.name == step_name)
            verb(alias, step.handler, f"{name} {step_name}, the old spelling", body=step.takes_body)
    else:
        verb("generate", lambda a, body: assets.generate_image(body), f"make one {kind} (JSON on stdin)", body=True)
    verb("list", lambda a, _: assets.listing(kind, a.max), f"the {kind}s made, newest first").add_argument(
        "--max", type=_bounded(1, 100, "--max"), default=20
    )
    verb("get", lambda a, _: assets.get(a.id), f"one {kind}").add_argument("id", type=_bounded(1, 2**62, "ID"), metavar="ID")
    if kind == "image":
        verb("scenes", lambda a, _: assets.scenes(a.last), "the scenes of the last pictures, and the words they repeat").add_argument(
            "--last", type=_bounded(1, 100, "--last"), default=16
        )
    return root


def main(kind: str, argv: list[str]) -> int:
    return hand.run(kind, parser(kind), argv)
