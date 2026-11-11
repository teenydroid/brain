"""Check that a directory can hold the core's file keyring.

    python3 bin/lib/private_dirs.py [--fix] DIR [STOP]

The core keeps its keyring (dev-keychain.json) in the brain, and its file
secret store refuses a directory that someone else could change: it walks
every component from / down, and each must be owned by this user or by root
and not writable by group or others (a root-owned sticky directory such as
/tmp excepted), with DIR itself owned by this user. A clone made under a
umask of 002 leaves the checkout and brain/ group-writable, and the core then
fails every secret write with "could not admit the secrets directory", which
reaches the agents as a model 401 rather than a boot error.

Prints the verdict on one line, exits 1 if DIR fails the rule. With --fix,
first removes group and other write from DIR and its ancestors up to STOP
(default: DIR alone), where this user owns them; nothing above STOP is
touched.
"""

from __future__ import annotations

import os
import stat
import sys
from pathlib import Path


def problems(directory: Path) -> list[str]:
    """Every component of `directory` the keyring would refuse, and why."""
    user = os.geteuid()
    found: list[str] = []
    chain = list(reversed(directory.parents)) + [directory]
    for path in chain:
        try:
            info = path.lstat()
        except OSError as err:
            found.append(f"{path}: {err.strerror}")
            continue
        leaf = path == directory
        if stat.S_ISLNK(info.st_mode):
            found.append(f"{path}: a symlink")
            continue
        if not stat.S_ISDIR(info.st_mode):
            found.append(f"{path}: not a directory")
            continue
        trusted = info.st_uid == user or (not leaf and info.st_uid == 0)
        sticky_root = not leaf and info.st_uid == 0 and info.st_mode & stat.S_ISVTX
        writable = info.st_mode & 0o022
        if not trusted:
            found.append(f"{path}: owned by uid {info.st_uid}")
        elif writable and not sticky_root:
            found.append(f"{path}: mode {stat.S_IMODE(info.st_mode):o} lets group or others write")
    return found


def fix(directory: Path, stop: Path) -> list[str]:
    """Removes group/other write from `directory` up to `stop`, where owned."""
    fixed: list[str] = []
    user = os.geteuid()
    path = directory
    while True:
        try:
            info = path.lstat()
        except OSError:
            break
        if stat.S_ISDIR(info.st_mode) and info.st_uid == user and info.st_mode & 0o022:
            os.chmod(path, stat.S_IMODE(info.st_mode) & ~0o022)
            fixed.append(str(path))
        if path == stop or path == path.parent:
            break
        path = path.parent
    return fixed


def main(argv: list[str]) -> int:
    args = list(argv)
    do_fix = False
    if args and args[0] == "--fix":
        do_fix = True
        args = args[1:]
    if not 1 <= len(args) <= 2:
        print(__doc__.strip().splitlines()[2], file=sys.stderr)
        return 2
    directory = Path(os.path.abspath(args[0]))
    stop = Path(os.path.abspath(args[1])) if len(args) == 2 else directory
    if stop != directory and stop not in directory.parents:
        print(f"{stop} is not {directory} or one of its parents", file=sys.stderr)
        return 2
    if do_fix:
        for path in fix(directory, stop):
            print(f"tightened {path} (no group or other write)", file=sys.stderr)
    found = problems(directory)
    if found:
        print("; ".join(found))
        return 1
    print("every component is private to this user or root")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
