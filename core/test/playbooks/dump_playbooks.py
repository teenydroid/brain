"""Dump what bin/lib/playbook.py makes of every playbook in a brain, as JSON.

    python3 -I dump_playbooks.py BRAIN HANDS_DIR

Prints {name: {"ok": <the parse>} | {"error": <the message>}} for every
BRAIN/playbooks/*.md, sorted by name. core/test/playbooks/parity.test.ts runs
it and compares the TypeScript port against it, so the port can never drift.
"""

import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO / "bin" / "lib"))

import playbook  # noqa: E402


def main(argv: list[str]) -> int:
    brain, hands = Path(argv[1]), Path(argv[2])
    out = {}
    for path in sorted((brain / "playbooks").glob("*.md")):
        try:
            out[path.stem] = {"ok": playbook.load(brain, path.stem, hands)}
        except playbook.PlaybookError as exc:
            out[path.stem] = {"error": str(exc)}
    print(json.dumps(out, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
