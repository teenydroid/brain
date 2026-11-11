"""Teeny's public diary: one page a day, pushed to teenydroid/diary.

    python3 bin/lib/diary.py [--preview DIR]

Every page is `days/YYYY/MM/YYYY-MM-DD.md` in the diary repository, built
only from Teeny's own records:

- `## diary`: the entry `bin/reflect` wrote for that day (state/diary/<date>.md)
- `## posted` and `## replied`: Teeny's live posts on X (x_posts)
- `## moltbook`: its published Moltbook posts (moltbook_posts)
- `## shipped`: build jobs that shipped (build_jobs)

Other people's words are never copied in. A reply links the post it answers.
A day with nothing to show gets no page, and an empty section is left out.

Every run rebuilds every page from scratch, so it is idempotent: pages that
did not change make no commit. `days/README.md` lists the days newest first
between its `days:` markers.

Under DRY_RUN (the default) the pages are written to state/diary-preview/ and
nothing touches git. Live, the repository is cloned to state/diary-repo/ (and
fast-forwarded on later runs), the pages are written there and committed, and
the commit is pushed with DIARY_GITHUB_TOKEN (or GITHUB_TOKEN). The token is
handed to git through a credential helper that reads it from the environment,
so it never appears in a URL, in .git/config or in argv.

Before anything is committed the staged diff is checked for every value in
secrets/*.env that is 12 characters or longer, and for the usual shapes of a
key (private key blocks, GitHub, Slack, OpenAI-style and AWS tokens). A hit
unstages everything, commits nothing and fails the run, naming the file but
never the value.

Prints one JSON object: {"ok", "dry_run", "days", "changed", "pushed", ...}.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import sqlite3
import subprocess
import sys
from pathlib import Path
from typing import Any, Iterable
from zoneinfo import ZoneInfo

CODE = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(CODE / "hands" / "lib"))

from teeny_hands import hand  # noqa: E402

DEFAULT_TZ = "Asia/Kolkata"
DEFAULT_REPO = "teenydroid/diary"
DEFAULT_X_USER = "teenydroid"
MARK_START, MARK_END = "<!-- days:start -->", "<!-- days:end -->"
#: Moltbook statuses that mean the post is up.
MOLTBOOK_LIVE = ("posted", "verified")
#: Shapes of a credential, whatever the env files hold.
KEY_SHAPES = re.compile(
    r"-----BEGIN [A-Z ]*PRIVATE KEY-----"
    r"|\bgh[pousr]_[A-Za-z0-9]{30,}"
    r"|\bgithub_pat_[A-Za-z0-9_]{30,}"
    r"|\bxox[abprs]-[A-Za-z0-9-]{10,}"
    r"|\bsk-[A-Za-z0-9_-]{20,}"
    r"|\bAKIA[0-9A-Z]{16}\b"
)
#: The shortest env value treated as a secret: shorter ones (ports, flags,
#: usernames) would match everything.
MIN_SECRET = 12

Json = dict[str, Any]


def _tz() -> ZoneInfo:
    # The same clock bin/reflect files its entries by.
    return ZoneInfo(os.environ.get("CALENDAR_TZ") or DEFAULT_TZ)


def _local(at: float) -> dt.datetime:
    return dt.datetime.fromtimestamp(at, _tz())


def _rows(conn: sqlite3.Connection, query: str, args: Iterable[Any] = ()) -> list[sqlite3.Row]:
    """A query against a table some other hand creates: no table yet, no rows."""
    try:
        return conn.execute(query, tuple(args)).fetchall()
    except sqlite3.OperationalError as error:
        if "no such table" in str(error) or "no such column" in str(error):
            return []
        raise


# --- gathering ------------------------------------------------------------------------------------------


def gather() -> dict[str, Json]:
    """Everything there is to say, keyed by local date (YYYY-MM-DD)."""
    days: dict[str, Json] = {}

    def day(at: float) -> Json:
        key = _local(at).strftime("%Y-%m-%d")
        return days.setdefault(key, {"posted": [], "replied": [], "moltbook": [], "shipped": []})

    diary_dir = hand.state_dir() / "diary"
    if diary_dir.is_dir():
        for path in sorted(diary_dir.glob("*.md")):
            if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", path.stem):
                continue
            body = _entry_body(path.read_text(encoding="utf-8"))
            if body:
                days.setdefault(path.stem, {"posted": [], "replied": [], "moltbook": [], "shipped": []})["diary"] = body

    conn = hand.db()
    for r in _rows(conn, "SELECT id, kind, text, in_reply_to, quote_of, media, created_at FROM x_posts ORDER BY created_at, id"):
        item = {"id": r["id"], "at": r["created_at"], "text": r["text"], "media": _count(r["media"]),
                "in_reply_to": r["in_reply_to"], "quote_of": r["quote_of"]}
        day(r["created_at"])["replied" if r["kind"] == "reply" else "posted"].append(item)
    marks = ",".join("?" * len(MOLTBOOK_LIVE))
    for r in _rows(conn, f"SELECT post_id, at, submolt, title FROM moltbook_posts WHERE status IN ({marks}) ORDER BY at, id",
                   MOLTBOOK_LIVE):
        day(r["at"])["moltbook"].append({"id": r["post_id"], "at": r["at"], "submolt": r["submolt"], "title": r["title"]})
    for r in _rows(conn, "SELECT repo, summary, shipped_at, repo_url, commit_url FROM build_jobs"
                         " WHERE status = 'shipped' AND shipped_at IS NOT NULL ORDER BY shipped_at, id"):
        day(r["shipped_at"])["shipped"].append({"at": r["shipped_at"], "repo": r["repo"], "summary": r["summary"] or "",
                                                "repo_url": r["repo_url"], "commit_url": r["commit_url"]})
    return days


def _entry_body(text: str) -> str:
    """bin/reflect writes `# <date>` then the entry; the page has its own title."""
    lines = text.strip().splitlines()
    if lines and lines[0].startswith("# "):
        lines = lines[1:]
    return "\n".join(lines).strip()


def _count(media: str | None) -> int:
    try:
        value = json.loads(media or "[]")
    except ValueError:
        return 0
    return len(value) if isinstance(value, list) else 0


# --- rendering ------------------------------------------------------------------------------------------


def _x_link(post_id: str) -> str:
    user = hand.setting("X_USERNAME") or DEFAULT_X_USER
    return f"https://x.com/{user}/status/{post_id}"


def _quote(text: str) -> list[str]:
    return [f"> {line}" if line.strip() else ">" for line in text.strip().splitlines()] or [">"]


def render_day(date: str, data: Json) -> str:
    title = dt.date.fromisoformat(date).strftime("%A %d %B %Y")
    zone = _local(0).tzname() or ""
    out = [f"# {title}", ""]
    if data.get("diary"):
        out += ["## diary", "", data["diary"], ""]
    if data["posted"]:
        out += ["## posted", ""]
        for p in data["posted"]:
            extra = []
            if p["quote_of"]:
                extra.append(f"quoting [this post]({_x_link_any(p['quote_of'])})")
            if p["media"]:
                extra.append("with an image" if p["media"] == 1 else f"with {p['media']} images")
            out.append(f"**{_local(p['at']):%H:%M}** {zone} · [on x]({_x_link(p['id'])})" + (" · " + ", ".join(extra) if extra else ""))
            out += [""] + _quote(p["text"]) + [""]
    if data["replied"]:
        n = len(data["replied"])
        out += ["## replied", "", f"{n} {'reply' if n == 1 else 'replies'}.", ""]
        for p in data["replied"]:
            to = f" · to [this post]({_x_link_any(p['in_reply_to'])})" if p["in_reply_to"] else ""
            out.append(f"**{_local(p['at']):%H:%M}** {zone} · [on x]({_x_link(p['id'])}){to}")
            out += [""] + _quote(p["text"]) + [""]
    if data["moltbook"]:
        out += ["## moltbook", ""]
        for m in data["moltbook"]:
            link = f"[{_md(m['title'])}](https://www.moltbook.com/post/{m['id']})" if m["id"] else _md(m["title"])
            out.append(f"- **{_local(m['at']):%H:%M}** {zone} · {link} in m/{m['submolt']}")
        out.append("")
    if data["shipped"]:
        out += ["## shipped", ""]
        for s in data["shipped"]:
            repo = f"[{s['repo']}]({s['repo_url']})" if s["repo_url"] else s["repo"]
            commit = f" ([commit]({s['commit_url']}))" if s["commit_url"] else ""
            summary = f": {_md(s['summary'].strip())}" if s["summary"].strip() else ""
            out.append(f"- {repo}{commit}{summary}")
        out.append("")
    return "\n".join(out).rstrip() + "\n"


def _x_link_any(post_id: str) -> str:
    """A post by anyone: x.com resolves /i/status/ to whoever wrote it."""
    return f"https://x.com/i/status/{post_id}"


def _md(text: str) -> str:
    return re.sub(r"([\[\]])", r"\\\1", " ".join(text.split()))


def _worth_a_page(data: Json) -> bool:
    return bool(data.get("diary") or data["posted"] or data["replied"] or data["moltbook"] or data["shipped"])


def page_path(date: str) -> Path:
    return Path("days") / date[:4] / date[5:7] / f"{date}.md"


def render_index(existing: str, dates: list[str]) -> str:
    lines = [f"- [{d}]({page_path(d).relative_to('days').as_posix()})" for d in sorted(dates, reverse=True)]
    block = "\n".join([MARK_START, *lines, MARK_END])
    if MARK_START in existing and MARK_END in existing:
        head, rest = existing.split(MARK_START, 1)
        tail = rest.split(MARK_END, 1)[1]
        return head + block + tail
    return "# Days\n\nNewest first.\n\n" + block + "\n"


def write_pages(root: Path, days: dict[str, Json]) -> list[str]:
    """Write every page worth having under root; returns the dates written."""
    dates = [d for d, data in sorted(days.items()) if _worth_a_page(data)]
    for date in dates:
        path = root / page_path(date)
        path.parent.mkdir(parents=True, exist_ok=True)
        text = render_day(date, days[date])
        if not path.is_file() or path.read_text(encoding="utf-8") != text:
            path.write_text(text, encoding="utf-8")
    index = root / "days" / "README.md"
    index.parent.mkdir(parents=True, exist_ok=True)
    old = index.read_text(encoding="utf-8") if index.is_file() else ""
    # The index keeps days already published even if this box lost their
    # records: pages are never deleted from here.
    published = sorted({p.stem for p in (root / "days").glob("*/*/*.md")} | set(dates))
    new = render_index(old, published)
    if new != old:
        index.write_text(new, encoding="utf-8")
    return dates


# --- the secret gate ------------------------------------------------------------------------------------


def secret_values() -> list[str]:
    values: set[str] = set()
    folder = hand.secrets_dir()
    for path in sorted(folder.glob("*.env")) if folder.is_dir() else []:
        try:
            parsed = hand._parse_env_file(path.read_text(encoding="utf-8"))
        except OSError:
            continue
        values.update(v for v in parsed.values() if len(v) >= MIN_SECRET)
    return sorted(values)


def check_secrets(diff: str) -> None:
    """Raises hand.Refused if the diff carries a secret. Only added lines count."""
    added = "\n".join(line[1:] for line in diff.splitlines() if line.startswith("+") and not line.startswith("+++"))
    if KEY_SHAPES.search(added):
        raise hand.Refused("something shaped like a key")
    for value in secret_values():
        if value in added:
            raise hand.Refused("a value from secrets/")


# --- git ------------------------------------------------------------------------------------------------

#: Reads the token from the environment at the moment git asks; nothing is
#: written down.
CREDENTIAL_HELPER = "!f() { test \"$1\" = get && echo username=x-access-token && echo \"password=$TEENY_DIARY_TOKEN\"; }; f"


def _git(repo: Path | None, *args: str, token: str | None = None) -> str:
    env = {**os.environ, "GIT_TERMINAL_PROMPT": "0"}
    cmd = ["git"]
    if token:
        env["TEENY_DIARY_TOKEN"] = token
        cmd += ["-c", "credential.helper=", "-c", f"credential.helper={CREDENTIAL_HELPER}"]
    if repo is not None:
        cmd += ["-C", str(repo)]
    done = subprocess.run(cmd + list(args), capture_output=True, text=True, env=env, timeout=90)
    if done.returncode != 0:
        # git's message names the remote and the failing step; the token is
        # never in it (it only lives in the helper's environment).
        raise hand.HandError(f"git {args[0]} failed: {(done.stderr or done.stdout).strip()[-400:]}")
    return done.stdout


def _checkout(token: str) -> Path:
    repo = hand.state_dir() / "diary-repo"
    # DIARY_REMOTE (a full URL or path) is for tests and mirrors.
    url = hand.setting("DIARY_REMOTE") or f"https://github.com/{hand.setting('DIARY_REPO') or DEFAULT_REPO}.git"
    branch = hand.setting("DIARY_BRANCH") or "main"
    if not (repo / ".git").is_dir():
        _git(None, "clone", "-q", "--branch", branch, url, str(repo), token=token)
    else:
        _git(repo, "remote", "set-url", "origin", url)
        _git(repo, "fetch", "-q", "origin", branch, token=token)
        # Only this script writes here, and only under days/: anything local
        # that is not on origin is a commit a failed push left behind, which
        # rebases cleanly on top.
        _git(repo, "checkout", "-q", branch)
        _git(repo, "rebase", "-q", f"origin/{branch}")
    _git(repo, "config", "user.name", hand.setting("DIARY_GIT_NAME") or "Teeny")
    _git(repo, "config", "user.email", hand.setting("DIARY_GIT_EMAIL") or "tiny@tinyhumans.ai")
    return repo


def publish(days: dict[str, Json]) -> Json:
    token = hand.setting("DIARY_GITHUB_TOKEN") or hand.setting("GITHUB_TOKEN")
    if not token:
        raise hand.HandError("no token: set DIARY_GITHUB_TOKEN (or GITHUB_TOKEN) in secrets/teeny.env")
    branch = hand.setting("DIARY_BRANCH") or "main"
    with hand.lock("diary", timeout=60):
        repo = _checkout(token)
        dates = write_pages(repo, days)
        _git(repo, "add", "--", "days")
        changed = [p for p in _git(repo, "diff", "--cached", "--name-only").splitlines() if p]
        if changed:
            try:
                check_secrets(_git(repo, "diff", "--cached", "--unified=0"))
            except hand.Refused as why:
                _git(repo, "reset", "-q")
                _git(repo, "checkout", "-q", "--", ".")
                _git(repo, "clean", "-qfd", "--", "days")
                raise hand.Refused("the diary carries a secret, nothing committed", why=why.reason, files=changed) from None
            stamp = _local(hand.now()).strftime("%Y-%m-%d")
            _git(repo, "commit", "-q", "-m", f"diary: {stamp}, {len(changed)} file(s)")
        ahead = int(_git(repo, "rev-list", "--count", f"origin/{branch}..HEAD").strip() or 0)
        if ahead:
            _git(repo, "push", "-q", "origin", f"HEAD:{branch}", token=token)
    return {"days": len(dates), "changed": changed, "pushed": ahead}


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--preview", metavar="DIR", help="write the pages to DIR and touch nothing else")
    args = parser.parse_args(argv)
    try:
        days = gather()
        if args.preview or hand.dry_run():
            root = Path(args.preview) if args.preview else hand.state_dir() / "diary-preview"
            dates = write_pages(root, days)
            hand.emit({"ok": True, "dry_run": True, "preview": str(root), "days": len(dates), "pushed": 0})
            return 0
        hand.emit({"ok": True, "dry_run": False, **publish(days)})
        return 0
    except hand.Refused as refused:
        hand.emit({"ok": False, "refused": refused.reason, **refused.detail})
        return 2
    except hand.HandError as error:
        hand.emit({"ok": False, "error": str(error)})
        return 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
