"""How long each text of a draft is, as the guard will count it, and its limit.

    python3 bin/lib/post_length.py SURFACE < TEXTS_JSON

TEXTS_JSON is a JSON list of strings (one for a post, one per post for a
thread). Prints {"limit": N, "lengths": [...], "over": [indexes over N]}.

Why: hands/x refuses a text over the limit (`the text is 304 characters,
over the 280 limit.`), and on 2026-11-17 a rewrite bin/act asked for after
the cold reader came back at 304 characters and the slot's post was lost.
bin/act measures a rewrite here before it sends one. The rule is the guard's
own, imported rather than copied: `post_length` (UTF-16 code units, as X and
the guard's hard limit count them) and `field_limit` (the surface's limit on
`text`: 280 on X). Exits 2 on a body that is not a list of strings.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path


def measure(surface: str, texts: list[str]) -> dict:
    lib = Path(__file__).resolve().parents[2] / "hands" / "lib"
    if str(lib) not in sys.path:
        sys.path.insert(0, str(lib))
    from teeny_hands.guard.gate import field_limit
    from teeny_hands.guard.style import post_length

    limit = field_limit(surface, "text")
    lengths = [post_length(t) for t in texts]
    return {"limit": limit, "lengths": lengths, "over": [i for i, n in enumerate(lengths) if n > limit]}


def main(argv: list[str]) -> int:
    if len(argv) != 1:
        print("usage: bin/lib/post_length.py SURFACE < TEXTS_JSON", file=sys.stderr)
        return 2
    try:
        texts = json.load(sys.stdin)
    except json.JSONDecodeError as error:
        print(f"not JSON: {error}", file=sys.stderr)
        return 2
    if not isinstance(texts, list) or not all(isinstance(t, str) for t in texts):
        print("the body must be a JSON list of strings", file=sys.stderr)
        return 2
    print(json.dumps(measure(argv[0], texts)))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
