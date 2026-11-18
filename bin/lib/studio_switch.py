"""The one switch between the studio and the old way of posting on X
(docs/architecture.md, "The studio").

    python3 bin/lib/studio_switch.py on                  -> prints 1 or 0
    python3 bin/lib/studio_switch.py replaced PLAYBOOK   -> exit 0 when the studio owns its slot now
    python3 bin/lib/studio_switch.py veto PLAYBOOK       -> prints the editor-veto kind, exit 1 when none

`STUDIO` decides, read the way bin/studio reads every switch: the process
environment, then secrets/teeny.env, then off. Cron jobs run with an
allowlisted environment, so on the box the file decides.

With STUDIO=1 the studio owns every original post on X:

- the playbooks it replaced (REPLACED) are not run: bin/act skips one
  before any input or model turn, whoever asks for it (a cron job, a
  catch-up, a person, an evolve lab);
- the mind's `post_x` files a pitch for the studio instead of posting
  (bin/lib/tick.py);
- the told-to post and the outward quote (VETOED) still run, and their
  text goes past the studio's editor first, which may pick it or none.

With STUDIO=0 (the rollback) everything is as it was before the cutover:
the studio's cron jobs run the old playbook of their slot instead
(`bin/studio --slot S --legacy PLAYBOOK`), bin/act runs the replaced
playbooks, the mind posts, and nothing is vetoed.

This list is code, not a playbook field: the brain is Teeny's to edit, and
what the studio replaced is not.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Mapping

#: The playbooks whose slots the studio took (brain/cron.toml): kept on disk
#: for the rollback, never run while STUDIO=1.
REPLACED = ("x-quote", "morning", "take", "show", "shiplog", "growth")
#: The posts that stay on their own jobs and pass the editor as a single
#: candidate: playbook -> the kind bin/studio --veto records.
VETOED = {"brief": "brief", "peer-quote": "peer-quote"}

ON_WORDS = ("1", "true", "yes", "on")


def _filed(secrets_file: Path, name: str) -> str | None:
    try:
        text = secrets_file.read_text(encoding="utf-8")
    except OSError:
        return None
    value = None
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, rest = line.partition("=")
        if key.strip().removeprefix("export ").strip() == name:
            rest = rest.strip()
            if len(rest) >= 2 and rest[0] == rest[-1] and rest[0] in "\"'":
                rest = rest[1:-1]
            value = rest
    return value


def secrets_file(environ: Mapping[str, str]) -> Path:
    root = Path(environ.get("TEENY_ROOT") or Path(__file__).resolve().parents[2])
    return Path(environ.get("TEENY_SECRETS") or root / "secrets") / "teeny.env"


def on(environ: Mapping[str, str] | None = None, secrets: Path | None = None) -> bool:
    """Whether the studio owns the posts: STUDIO from the environment, else
    secrets/teeny.env, else off."""
    environ = os.environ if environ is None else environ
    value = environ.get("STUDIO")
    if value in (None, ""):
        value = _filed(secrets or secrets_file(environ), "STUDIO")
    return (value or "0").strip().lower() in ON_WORDS


def replaced(playbook: str, environ: Mapping[str, str] | None = None) -> bool:
    """Whether PLAYBOOK's slot is the studio's now (and so it is not run)."""
    return playbook in REPLACED and on(environ)


def veto_kind(playbook: str, environ: Mapping[str, str] | None = None) -> str | None:
    """The editor-veto kind for PLAYBOOK while the studio is on, else None."""
    return VETOED.get(playbook) if on(environ) else None


def main(argv: list[str]) -> int:
    if argv[:1] == ["on"] and len(argv) == 1:
        print(1 if on() else 0)
        return 0
    if argv[:1] == ["replaced"] and len(argv) == 2:
        return 0 if replaced(argv[1]) else 1
    if argv[:1] == ["veto"] and len(argv) == 2:
        kind = veto_kind(argv[1])
        if kind:
            print(kind)
            return 0
        return 1
    print("usage: studio_switch.py on | replaced PLAYBOOK | veto PLAYBOOK", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
