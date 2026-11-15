"""A post on X from a hand that holds no X key: through `hands/x post`, as a
separate process, so the guard, the posting rules and DRY_RUN that hand keeps
all apply, and the keys stay in the one hand that holds them. Used by
`hands/firsts announce` and `hands/requests vote-open`."""

from __future__ import annotations

import json
import subprocess
from typing import Any

from . import hand

Json = dict[str, Any]


def post(text: str) -> Json:
    """`hands/x post` with {text}: its answer, or Refused / HandError as it
    said. A dry run answers `dry_run` and no id."""
    exe = hand.root() / "hands" / "x"
    if not exe.is_file():
        raise hand.HandError("hands/x is not installed")
    try:
        done = subprocess.run([str(exe), "post"], input=json.dumps({"text": text}), capture_output=True, text=True,
                              timeout=180, cwd=hand.root())
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise hand.HandError(f"hands/x post: {type(exc).__name__}") from None
    try:
        answer = json.loads(done.stdout.strip().splitlines()[-1])
    except (ValueError, IndexError):
        raise hand.HandError(f"hands/x post answered no JSON (exit {done.returncode})") from None
    if done.returncode == 2:
        detail = {k: v for k, v in answer.items() if k not in ("ok", "refused")}
        raise hand.Refused(f"hands/x post refused it: {answer.get('refused')}", **detail)
    if done.returncode != 0:
        raise hand.HandError(f"hands/x post: {answer.get('error', 'failed')}")
    return answer
