"""A drafting turn's answer as a list of action items, or exit 1 saying why.

    python3 bin/lib/answer.py < answer.txt    # prints the items as JSON

The model answers JSON, sometimes in a code fence or after a stray word.
This takes the first JSON object or array in the text; every item must be an
object with a string `action`. Shared by bin/act and bin/inbound-consume.

A model sometimes writes a raw newline inside a string, which strict JSON
refuses. Only when the strict parse fails is the text read again allowing
that: a newline or a tab may then stand in any field but a title or subject
(one line each). Any other control character (NUL, ESC, ...), raw or escaped,
refuses that answer. Refused, not stripped: "ESC[31m" without its ESC is
still not what the model meant, and bin/act asks for another draft. An answer
that parses strictly is returned as it always was.
"""

from __future__ import annotations

import json
import re
import sys
from typing import Any

_CONTROL = re.compile(r"[\x00-\x1f\x7f-\x9f]")
_CONTROL_IN_PROSE = re.compile(r"[\x00-\x08\x0b-\x1f\x7f-\x9f]")  # all but tab and newline
_ONE_LINE = ("title", "subject")


def _refuse_control(value: Any, field: str = "") -> None:
    """Raise ValueError at a control character that must not reach a post."""
    if isinstance(value, dict):
        for key, inner in value.items():
            _refuse_control(key, "a field's name")
            _refuse_control(inner, key)
    elif isinstance(value, list):
        for inner in value:
            _refuse_control(inner, field)
    elif isinstance(value, str):
        found = (_CONTROL if field in _ONE_LINE else _CONTROL_IN_PROSE).search(value)
        if found:
            raise ValueError(f"control character U+{ord(found.group()):04X} in {field or 'the answer'}")


def _parse(text: str) -> Any:
    try:
        return json.JSONDecoder().raw_decode(text)[0]
    except json.JSONDecodeError as strict:
        try:
            value, _ = json.JSONDecoder(strict=False).raw_decode(text)
        except json.JSONDecodeError:
            raise strict from None
    _refuse_control(value)
    return value


def items(text: str) -> list[dict]:
    text = text.strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[1] if "\n" in text else ""
        text = text.rsplit("```", 1)[0]
    starts = [i for i in (text.find("{"), text.find("[")) if i >= 0]
    if not starts:
        raise ValueError("no JSON in the answer")
    value = _parse(text[min(starts):])
    found = value if isinstance(value, list) else [value]
    if not all(isinstance(i, dict) and isinstance(i.get("action"), str) for i in found):
        raise ValueError("every item needs an action")
    return found


if __name__ == "__main__":
    try:
        print(json.dumps(items(sys.stdin.read())))
    except ValueError as exc:
        sys.exit(str(exc))
