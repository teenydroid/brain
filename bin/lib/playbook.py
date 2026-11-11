"""Read one playbook: brain/playbooks/<name>.md, a prompt with a TOML front
matter between `+++` lines. Used by bin/act (to run it) and bin/doctor (to
check every playbook a cron names); never by a model.

    python3 bin/lib/playbook.py BRAIN NAME [HANDS_DIR]

Prints {name, description, output, surface, max_per_day, inputs: [{name,
argv}], prompt} or exits non-zero with what is wrong.

    +++
    description = "the morning post"
    output = "post"            # post | reply | like | none | "hand:<name> <verb>"
    surface = "x"              # where a hand: output's text goes, for the guard
                               # (its surfaces: x, github, moltbook, mail, ...)
    max_per_day = 1            # actions taken a day, 1-100; skips are free
    [[inputs]]
    name = "mentions"
    run = "hands/x search '@teenydroid -from:teenydroid' --max 10"
    required = true            # optional: an empty result skips the run
                               # before the draft (no model turn); empty is
                               # null, [], {}, "" or an object of only those
    +++
    the prompt: what to read in the inputs and what to draft.

Teeny writes playbooks; bin/act, which runs them, is frozen. So the rules on
what a playbook can reach are here: an input only ever runs a hand (with
DRY_RUN=1 forced by bin/act, so a mislabelled verb cannot write), and the
output is a hand verb that applies its own rules, never a shell command.
"""

from __future__ import annotations

import json
import re
import shlex
import sys
import tomllib
from pathlib import Path

NAME = re.compile(r"[a-z0-9][a-z0-9-]*")
SIMPLE_OUTPUTS = ("post", "reply", "like", "none")
# Hands bin/act never hands a draft to: the guard is called by bin/act
# itself, and a draft must not reach the guard's own admin verbs; the
# builder's queue belongs to the builder and bin/build-ship, not to a
# playbook's draft (a playbook may still queue a browser task with
# hand:tasks add).
NOT_OUTPUTS = ("guard", "build")
# Hands that hold money: a draft reaches them only through the verbs named
# here (none for the wallet: a model never drafts a payment), and an input
# runs only their read verbs.
MONEY_OUTPUTS = {"wallet": (), "bounty": ("pick",)}
MONEY_READS = {
    "wallet": ("address", "balance", "ledger", "costs", "receipt", "receipts", "caps"),
    "bounty": ("list", "get", "announcements"),
}
MAX_INPUTS = 8


class PlaybookError(Exception):
    pass


def guard_surfaces() -> tuple[str, ...]:
    """The guard's own surface list (hands/guard holds.SURFACES), so a
    playbook cannot name a surface the guard would refuse at the action."""
    lib = Path(__file__).resolve().parents[2] / "hands" / "lib"
    if str(lib) not in sys.path:
        sys.path.insert(0, str(lib))
    from teeny_hands.guard.holds import SURFACES

    return tuple(SURFACES)


def _hand_argv(run: str, where: str, hands_dir: Path | None) -> list[str]:
    try:
        argv = shlex.split(run)
    except ValueError as exc:
        raise PlaybookError(f"{where}: {exc}") from None
    if not argv or not re.fullmatch(r"hands/[a-z0-9][a-z0-9-]*", argv[0]):
        raise PlaybookError(f"{where}: an input runs a hand (hands/<name> ...), got {run!r}")
    if hands_dir is not None and not (hands_dir / argv[0].split("/", 1)[1]).is_file():
        raise PlaybookError(f"{where}: no hand {argv[0]!r}")
    reads = MONEY_READS.get(argv[0].split("/", 1)[1])
    if reads is not None and (len(argv) < 2 or argv[1] not in reads):
        raise PlaybookError(f"{where}: an input runs only a read verb of {argv[0]} ({', '.join(reads)})")
    return argv


def load(brain: Path, name: str, hands_dir: Path | None = None) -> dict:
    if not NAME.fullmatch(name):
        raise PlaybookError(f"bad playbook name {name!r}")
    path = brain / "playbooks" / f"{name}.md"
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise PlaybookError(f"{path}: {exc.strerror}") from None
    parts = text.split("+++", 2)
    if len(parts) != 3 or parts[0].strip():
        raise PlaybookError(f"{path}: must start with a +++ TOML front matter +++ block")
    try:
        meta = tomllib.loads(parts[1])
    except tomllib.TOMLDecodeError as exc:
        raise PlaybookError(f"{path}: {exc}") from None
    prompt = parts[2].strip()
    if not prompt:
        raise PlaybookError(f"{path}: the prompt is empty")

    extra = set(meta) - {"description", "output", "surface", "max_per_day", "inputs"}
    if extra:
        raise PlaybookError(f"{path}: unknown keys: {', '.join(sorted(extra))}")
    description = meta.get("description")
    if not isinstance(description, str) or not description.strip():
        raise PlaybookError(f"{path}: needs a description")

    output = meta.get("output")
    if not isinstance(output, str):
        raise PlaybookError(f"{path}: needs an output")
    if output.startswith("hand:"):
        argv = output[len("hand:"):].split()
        if len(argv) != 2 or not all(NAME.fullmatch(a) for a in argv):
            raise PlaybookError(f"{path}: output hand:<name> <verb>, got {output!r}")
        if argv[0] in NOT_OUTPUTS:
            raise PlaybookError(f"{path}: hands/{argv[0]} cannot be an output")
        if argv[0] in MONEY_OUTPUTS and argv[1] not in MONEY_OUTPUTS[argv[0]]:
            allowed = ", ".join(MONEY_OUTPUTS[argv[0]]) or "nothing"
            raise PlaybookError(f"{path}: hands/{argv[0]} {argv[1]} cannot be an output (it may take: {allowed})")
        if hands_dir is not None and not (hands_dir / argv[0]).is_file():
            raise PlaybookError(f"{path}: no hand hands/{argv[0]}")
    elif output not in SIMPLE_OUTPUTS:
        raise PlaybookError(f"{path}: output is one of {', '.join(SIMPLE_OUTPUTS)} or hand:<name> <verb>, got {output!r}")

    surface = meta.get("surface", "x")
    surfaces = guard_surfaces()
    if not isinstance(surface, str) or surface not in surfaces:
        raise PlaybookError(f"{path}: surface is one of {', '.join(surfaces)}, got {surface!r}")

    max_per_day = meta.get("max_per_day", 1)
    if not isinstance(max_per_day, int) or isinstance(max_per_day, bool) or not 1 <= max_per_day <= 100:
        raise PlaybookError(f"{path}: max_per_day is a whole number from 1 to 100")

    inputs = meta.get("inputs", [])
    if not isinstance(inputs, list) or len(inputs) > MAX_INPUTS:
        raise PlaybookError(f"{path}: inputs is a list of at most {MAX_INPUTS} [[inputs]] tables")
    parsed = []
    for i, entry in enumerate(inputs):
        where = f"{path}: inputs[{i}]"
        if not isinstance(entry, dict) or set(entry) - {"name", "run", "required"} or not isinstance(entry.get("run"), str):
            raise PlaybookError(f"{where}: an input is {{name, run, required?}}")
        if not isinstance(entry.get("required", False), bool):
            raise PlaybookError(f"{where}: required is true or false")
        input_name = entry.get("name", f"input-{i}")
        if not isinstance(input_name, str) or not NAME.fullmatch(input_name):
            raise PlaybookError(f"{where}: name must be lowercase letters, digits and dashes")
        parsed.append(
            {
                "name": input_name,
                "argv": _hand_argv(entry["run"], where, hands_dir),
                "required": entry.get("required", False),
            }
        )

    return {
        "name": name,
        "description": description.strip(),
        "output": output,
        "surface": surface,
        "max_per_day": max_per_day,
        "inputs": parsed,
        "prompt": prompt,
    }


def main(argv: list[str]) -> int:
    if len(argv) not in (3, 4):
        print("usage: playbook.py BRAIN NAME [HANDS_DIR]", file=sys.stderr)
        return 2
    try:
        book = load(Path(argv[1]), argv[2], Path(argv[3]) if len(argv) == 4 else None)
    except PlaybookError as exc:
        print(exc, file=sys.stderr)
        return 1
    print(json.dumps(book))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
