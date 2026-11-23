"""Writing into the agentic mind's home, state/home/, from outside its jail.

The mind (brain/agents/mind.toml) can write anything under state/home/: a
file, a folder, or a link to somewhere else. The tick, the broker and the
operator's note all write there too, and they run outside any jail, as the
spine's user, with secrets/ in reach. A plain `open(path, "w")` would follow
a link the mind left at that path, or at a folder on the way to it, and put
the write wherever the link points.

So nothing here opens a path by name. Each folder is opened relative to its
parent's descriptor with O_NOFOLLOW (a link in its place is removed and a
real folder made), the file is created under a random name with O_EXCL, and
it is renamed over its final name inside that folder, which replaces a link
there instead of following it.
"""

from __future__ import annotations

import os
import secrets
import stat
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "hands" / "lib"))

from teeny_hands import hand  # noqa: E402

HOME = "home"


class HomeError(OSError):
    """state/home/ could not be written safely."""


def home() -> Path:
    return hand.state_dir() / HOME


def _step(parent: int, name: str) -> int:
    """NAME under the folder PARENT is open on, as a real folder's descriptor."""
    if name in ("", ".", "..") or "/" in name:
        raise HomeError(f"not a folder name: {name!r}")
    try:
        found = os.stat(name, dir_fd=parent, follow_symlinks=False)
    except FileNotFoundError:
        found = None
    if found is not None and not stat.S_ISDIR(found.st_mode):
        os.unlink(name, dir_fd=parent)  # a link or a file where a folder belongs
        found = None
    if found is None:
        try:
            os.mkdir(name, 0o755, dir_fd=parent)
        except FileExistsError:
            pass
    try:
        return os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent)
    except OSError as exc:
        raise HomeError(f"{name} in the mind's home is not a plain folder ({exc.strerror})") from None


def _folder(parts: tuple[str, ...]) -> int:
    """A descriptor on state/home/<parts...>, every step of it a real folder."""
    fd = os.open(hand.state_dir(), os.O_RDONLY | os.O_DIRECTORY)
    try:
        for name in (HOME, *parts):
            nxt = _step(fd, name)
            os.close(fd)
            fd = nxt
    except BaseException:
        os.close(fd)
        raise
    return fd


def _open(parts: tuple[str, ...]) -> int:
    """_folder, with any failure on the way (a folder that cannot be made or
    opened) as a HomeError, like every other failure here."""
    try:
        return _folder(parts)
    except HomeError:
        raise
    except OSError as exc:
        raise HomeError(f"the mind's home could not be opened at {'/'.join((HOME, *parts))} "
                        f"({exc.strerror or exc})") from None


def ensure(*parts: str) -> Path:
    """state/home/<parts...> as real folders."""
    os.close(_open(parts))
    return home().joinpath(*parts)


def write(parts: tuple[str, ...], name: str, text: str, mode: int = 0o644) -> Path:
    """Writes TEXT whole to state/home/<parts...>/NAME; never through a link."""
    if name in ("", ".", "..") or "/" in name:
        raise HomeError(f"not a file name: {name!r}")
    folder = _open(parts)
    tmp = f".{name}.{secrets.token_hex(8)}.tmp"
    try:
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, mode, dir_fd=folder)
        try:
            os.fchmod(fd, mode)
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                fh.write(text)
            os.rename(tmp, name, src_dir_fd=folder, dst_dir_fd=folder)
        except BaseException:
            try:
                os.unlink(tmp, dir_fd=folder)
            except OSError:
                pass
            raise
    except HomeError:
        raise
    except OSError as exc:
        raise HomeError(f"could not write {name} in the mind's home ({exc.strerror})") from None
    finally:
        os.close(folder)
    return home().joinpath(*parts, name)


def prune(parts: tuple[str, ...], keep: set[str], suffix: str) -> list[str]:
    """Removes from state/home/<parts...> every entry named *SUFFIX that is
    not in KEEP and is not a folder; never through a link. Answers the names
    it removed."""
    folder = _open(parts)
    removed: list[str] = []
    try:
        for name in sorted(os.listdir(folder)):
            if not name.endswith(suffix) or name in keep:
                continue
            found = os.stat(name, dir_fd=folder, follow_symlinks=False)
            if stat.S_ISDIR(found.st_mode):
                continue
            os.unlink(name, dir_fd=folder)
            removed.append(name)
    except OSError as exc:
        raise HomeError(f"could not tidy {'/'.join((HOME, *parts))} ({exc.strerror or exc})") from None
    finally:
        os.close(folder)
    return removed
