"""Fit a playbook's inputs into the draft message the core will take.

    python3 bin/lib/act_fit.py SHARE [TOTAL] < INPUTS_JSON

INPUTS_JSON is bin/act's inputs object ({input name: what its hand answered,
as JSON, or as text when it was not JSON}). Each input is cut to at most
SHARE bytes, and, given TOTAL, all of them together to at most TOTAL bytes,
measured as compact JSON in UTF-8 (how jq's `tojson` puts them in the
message). Prints {"inputs": {...}, "cut": [names cut, sorted]}.

Why: the core screens every turn's message before the model sees it, and
holds one over 64 KiB (MAX_PROMPT_BYTES in openhuman's prompt_injection
detector, reason `input.oversized`) as "Prompt flagged for security review",
every time. On 2026-11-15, -16 and -17 self-improve's eight inputs made an
86,357-char message and its draft turn failed in two seconds each day; other
playbooks run at about 64.5k chars, at the edge.

How it cuts: the biggest input first, so a small one is never cut while a
bigger one could be. Within an input, the longest list loses its last item
(hands answer newest first, and `--limit`/`--max` keep the front too), so
what the model reads is still JSON, in whole items. Only when no list is left
to shorten is the longest string cut, with "..." on the end. Before, bin/act
cut each input's text at 20,000 chars, which left the JSON broken and sent it
to the model as one escaped string. Python 3.11 standard library only.
"""

from __future__ import annotations

import json
import sys
from typing import Any

ELLIPSIS = "..."


def size(value: Any) -> int:
    """Bytes of `value` as compact JSON, as jq's tojson writes it."""
    return len(json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode())


def _lists(value: Any, path: tuple = ()):
    """(path, list) for every non-empty list in `value`, at any depth."""
    if isinstance(value, list):
        if value:
            yield path, value
        for i, item in enumerate(value):
            yield from _lists(item, path + (i,))
    elif isinstance(value, dict):
        for key, item in value.items():
            yield from _lists(item, path + (key,))


def _strings(value: Any, path: tuple = ()):
    """(path, string) for every string in `value` longer than the ellipsis."""
    if isinstance(value, str):
        if len(value) > len(ELLIPSIS):
            yield path, value
    elif isinstance(value, list):
        for i, item in enumerate(value):
            yield from _strings(item, path + (i,))
    elif isinstance(value, dict):
        for key, item in value.items():
            yield from _strings(item, path + (key,))


def _set(value: Any, path: tuple, new: Any) -> Any:
    if not path:
        return new
    parent = value
    for step in path[:-1]:
        parent = parent[step]
    parent[path[-1]] = new
    return value


def shrink(value: Any, excess: int) -> Any | None:
    """`value` a step smaller, about `excess` bytes if one step can, or None
    when nothing is left to cut. Mutates and returns `value`."""
    lists = list(_lists(value))
    if lists:
        path, longest = max(lists, key=lambda found: size(found[1]))
        longest.pop()
        return value
    strings = list(_strings(value))
    if not strings:
        return None
    path, longest = max(strings, key=lambda found: len(found[1].encode()))
    # Cut by characters; JSON escapes make a char at least a byte.
    keep = max(0, len(longest) - len(ELLIPSIS) - max(excess, 1))
    keep = min(keep, len(longest) - len(ELLIPSIS) - 1)
    return _set(value, path, longest[:keep] + ELLIPSIS)


def fit_one(value: Any, limit: int) -> tuple[Any, bool]:
    """`value` cut to at most `limit` bytes (or as near as it goes), and
    whether it was cut."""
    cut = False
    while (now := size(value)) > limit:
        smaller = shrink(value, now - limit)
        if smaller is None:
            break
        value, cut = smaller, True
    return value, cut


def fit(inputs: dict, share: int, total: int | None = None) -> tuple[dict, list[str]]:
    """The inputs cut to `share` bytes each and `total` together, and the
    names of those cut."""
    cut: set[str] = set()
    for name in inputs:
        inputs[name], was_cut = fit_one(inputs[name], share)
        if was_cut:
            cut.add(name)
    if total is not None:
        sizes = {name: size(value) for name, value in inputs.items()}
        stuck: set[str] = set()
        while size(inputs) > total and len(stuck) < len(inputs):
            name = max((n for n in inputs if n not in stuck), key=lambda n: sizes[n])
            smaller = shrink(inputs[name], size(inputs) - total)
            if smaller is None:
                stuck.add(name)
                continue
            inputs[name] = smaller
            sizes[name] = size(smaller)
            cut.add(name)
    return inputs, sorted(cut)


def main(argv: list[str]) -> int:
    if len(argv) not in (2, 3):
        print("usage: act_fit.py SHARE [TOTAL] < INPUTS_JSON", file=sys.stderr)
        return 2
    share = int(argv[1])
    total = int(argv[2]) if len(argv) == 3 else None
    inputs = json.load(sys.stdin)
    if not isinstance(inputs, dict):
        print("act_fit.py: the inputs are not a JSON object", file=sys.stderr)
        return 2
    fitted, cut = fit(inputs, share, total)
    print(json.dumps({"inputs": fitted, "cut": cut}, ensure_ascii=False, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
