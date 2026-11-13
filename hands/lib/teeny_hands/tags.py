"""The lab tags a write may carry (docs/hands.md, "Lab tags"): `format`, a
short name for the shape of the post (`gm`, `take`, `confession`, ...), and
`experiment`, the id of the lab experiment it belongs to. Both are optional
and neither is published: the hand that writes the post keeps them in its
ledger beside the post, so `hands/stats board` and the lab can compare
formats by their results.

`split` takes them out of a stdin body before the hand checks the rest, so
each hand's own list of fields stays as it was.
"""

from __future__ import annotations

import re
from typing import Any, Mapping

from . import hand

#: Lowercase letters, digits and dashes, starting with a letter.
_FORMAT = re.compile(r"^[a-z][a-z0-9-]{0,31}$")
FIELDS = ("format", "experiment")
#: The ledger columns that hold them, for `hand.add_columns`.
COLUMNS = {"format": "TEXT", "experiment": "INTEGER"}


def split(body: Any) -> tuple[Any, dict[str, Any]]:
    """The body without the tags, and the tags (each None when absent). A
    body that is not an object is returned as it is, for the hand to refuse."""
    if not isinstance(body, Mapping):
        return body, {"format": None, "experiment": None}
    rest = {k: v for k, v in body.items() if k not in FIELDS}
    fmt = body.get("format")
    if fmt is not None:
        if not isinstance(fmt, str) or not _FORMAT.match(fmt.strip().lower()):
            raise hand.HandError('"format" is a short tag: lowercase letters, digits and dashes, at most 32')
        fmt = fmt.strip().lower()
    experiment = body.get("experiment")
    if experiment is not None:
        if isinstance(experiment, str) and experiment.strip().isdigit():
            experiment = int(experiment.strip())
        if isinstance(experiment, bool) or not isinstance(experiment, int) or experiment < 1:
            raise hand.HandError('"experiment" is a lab experiment id (a positive whole number)')
    return rest, {"format": fmt, "experiment": experiment}
