"""A brain-owned peers file: one name a line, `#` starts a comment (the rest
of the line is a note, such as a `# community` tag). Teeny may edit these
files (they are under brain/, not frozen). Read by hands/moltbook
(`brain/moltbook-peers.txt`) and hands/x (`brain/x-peers.txt`), each with its
own idea of what a valid name is.
"""

from __future__ import annotations

from typing import Callable

from . import hand


def names(relative: str, parse: Callable[[str], str]) -> tuple[list[str], list[str]]:
    """The file's names, in order and once each (compared without case), and
    the lines that are not names (left out, and said so). `parse` turns a
    line into a name or raises `hand.HandError`. No file is no peers."""
    path = hand.root() / relative
    try:
        lines = path.read_text().splitlines()
    except FileNotFoundError:
        return [], []
    kept: list[str] = []
    seen: set[str] = set()
    skipped: list[str] = []
    for line in lines:
        text = line.split("#", 1)[0].strip()
        if not text:
            continue
        try:
            name = parse(text)
        except hand.HandError:
            skipped.append(text[:60])
            continue
        if name.lower() not in seen:
            seen.add(name.lower())
            kept.append(name)
    return kept, skipped
