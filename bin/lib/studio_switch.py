"""The one switch between the studio and the old way of posting on X
(docs/architecture.md, "The studio").

    python3 bin/lib/studio_switch.py on                  -> prints 1 or 0
    python3 bin/lib/studio_switch.py replaced PLAYBOOK   -> exit 0 when the studio owns its slot now, 3 when not
    python3 bin/lib/studio_switch.py veto PLAYBOOK       -> prints the editor-veto kind, exit 3 when none
    python3 bin/lib/studio_switch.py doctor              -> "ok|warn|fail <what>" for bin/doctor

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

The switch fails closed. Only ON_WORDS and OFF_WORDS are read (unset, or
empty, is off); anything else (`STUDIO=maybe`, a typo), or a teeny.env that
cannot be read as text, is a SwitchError: neither path posts, and bin/doctor
fails. The process environment wins over the file when it sets STUDIO at
all; cron's shell jobs and the compose services pass none, so on the box the
file decides, and a person can preview either side with STUDIO=... set.

The command line answers by exit code, so a crash is never read as an
answer: 0 yes, 3 no, anything else an error (the caller posts nothing).
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
OFF_WORDS = ("0", "false", "no", "off", "")
#: The command line's exit codes (`main`): 0 yes, NO no, anything else an error.
NO = 3
ERROR = 4


class SwitchError(Exception):
    """STUDIO cannot be read: neither the studio nor the old paths may post."""


def _filed(secrets_file: Path, name: str) -> str | None:
    try:
        text = secrets_file.read_text(encoding="utf-8")
    except FileNotFoundError:
        return None
    except UnicodeDecodeError as exc:
        raise SwitchError(f"{secrets_file} is not UTF-8 text ({exc.reason} at byte {exc.start})") from exc
    except OSError as exc:
        raise SwitchError(f"{secrets_file} cannot be read: {exc.strerror or exc}") from exc
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


def read(environ: Mapping[str, str] | None = None, secrets: Path | None = None) -> tuple[bool, str]:
    """(on, where it was read): STUDIO from the environment when it is set
    there (even empty: an explicit off), else secrets/teeny.env, else off.
    SwitchError on a word that is neither on nor off, or a file that is not
    text."""
    environ = os.environ if environ is None else environ
    if "STUDIO" in environ:
        value, where = environ["STUDIO"], "the environment"
    else:
        path = secrets or secrets_file(environ)
        value, where = _filed(path, "STUDIO"), str(path)
        if value is None:
            return False, "unset"
    word = value.strip().lower()
    if word in ON_WORDS:
        return True, where
    if word in OFF_WORDS:
        return False, where
    raise SwitchError(f"STUDIO={value.strip()[:20]!r} in {where} is neither on ({', '.join(ON_WORDS)}) nor off "
                      f"({', '.join(w for w in OFF_WORDS if w)}): nothing posts on X until it is one of them")


def on(environ: Mapping[str, str] | None = None, secrets: Path | None = None) -> bool:
    """Whether the studio owns the posts (`read`). SwitchError when STUDIO
    cannot be read: the caller must post nothing."""
    return read(environ, secrets)[0]


def replaced(playbook: str, environ: Mapping[str, str] | None = None) -> bool:
    """Whether PLAYBOOK's slot is the studio's now (and so it is not run)."""
    return playbook in REPLACED and on(environ)


def veto_kind(playbook: str, environ: Mapping[str, str] | None = None) -> str | None:
    """The editor-veto kind for PLAYBOOK while the studio is on, else None."""
    return VETOED.get(playbook) if on(environ) else None


def doctor(environ: Mapping[str, str] | None = None) -> tuple[str, str]:
    """bin/doctor's check: (ok | warn | fail, what it found). Fail when STUDIO
    cannot be read; warn, with the studio on, when the judges
    (SAFETY_JUDGE_MODEL, read from the file as the hands read it) would run
    on the writer's model: a live slot then posts nothing."""
    environ = os.environ if environ is None else environ
    try:
        value, where = read(environ)
    except SwitchError as exc:
        return "fail", str(exc)
    if not value:
        return "ok", f"off ({where}): the legacy playbooks and the mind's post_x post"
    path = secrets_file(environ)
    writer = (environ.get("STUDIO_WRITER_MODEL") or _filed(path, "STUDIO_WRITER_MODEL") or "").strip()
    judge = (_filed(path, "SAFETY_JUDGE_MODEL") or "").strip()
    if judge == writer:
        return "warn", (f"on ({where}), but SAFETY_JUDGE_MODEL is the writer's model ({writer or 'both the core default'}):"
                        " every live slot stays empty until the judges run on another model")
    return "ok", f"on ({where}): writer {writer or 'core default'}, judges {judge or 'core default'}"


def main(argv: list[str]) -> int:
    """0 yes, NO (3) no, ERROR (4) when STUDIO cannot be read, 2 a usage
    error. `on` also prints 1 or 0."""
    if argv == ["doctor"]:
        level, what = doctor()
        print(f"{level} {what}")
        return 0
    try:
        if argv[:1] == ["on"] and len(argv) == 1:
            value = on()
            print(1 if value else 0)
            return 0 if value else NO
        if argv[:1] == ["replaced"] and len(argv) == 2:
            return 0 if replaced(argv[1]) else NO
        if argv[:1] == ["veto"] and len(argv) == 2:
            kind = veto_kind(argv[1])
            if kind:
                print(kind)
                return 0
            return NO
    except SwitchError as exc:
        print(f"[studio_switch] {exc}", file=sys.stderr)
        return ERROR
    print("usage: studio_switch.py on | replaced PLAYBOOK | veto PLAYBOOK | doctor", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
