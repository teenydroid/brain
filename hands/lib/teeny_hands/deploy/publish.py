"""hands/deploy: publish what Teeny built as a static site on a subdomain of
its own site (docs/roadmap.md, A11; docs/hands.md, "deploy").

  publish JOB    the site folder of a shipped build job (its workdir's
                 `site/`, `public/`, `dist/` or `docs/`, the first there is),
                 copied to DEPLOY_ROOT/<slug>/ and served by nginx at
                 https://<slug>.<DEPLOY_DOMAIN>/ (deploy/nginx/sites.conf)
  list           what is published, newest first
  unpublish SLUG the operator takes a site down

Static files only, and nothing that runs on the server: every file must have
an extension on STATIC (no .php, .py, .sh, .cgi, no .htaccess), no symlink,
no dotfile, no file over DEPLOY_MAX_FILE_BYTES, no more than MAX_FILES, and
the whole site under DEPLOY_MAX_BYTES. The nginx vhost serves the folder as
files with no interpreter, GET and HEAD only, and a Content-Security-Policy
that keeps a page from loading or posting anywhere but its own subdomain.

Every public word goes through the guard: each text file (HTML, Markdown,
plain text, SVG) is screened by the outbound lexicon (`held`), and a page
with a form (`form`: a static site never collects anything) or a script
that loads from elsewhere (`remote_script`) is refused. The site's name is
the job's slug, never chosen freely: a name cannot be used to impersonate.

Off until DEPLOY=1 (`off`). Under DRY_RUN the answer is `would_publish`
with the files and nothing is copied. The copy is atomic: the new site is
written beside the old one and swapped in by rename, so a visitor sees the
whole old site or the whole new one.
"""

from __future__ import annotations

import os
import re
import shutil
import sqlite3
from pathlib import Path
from typing import Any

from .. import hand

Json = dict[str, Any]

#: The only files a site may hold, by extension.
STATIC = (".html", ".htm", ".css", ".js", ".mjs", ".json", ".txt", ".md", ".svg", ".png", ".jpg", ".jpeg",
          ".gif", ".webp", ".ico", ".woff", ".woff2", ".xml", ".csv", ".map")
TEXT = (".html", ".htm", ".md", ".txt", ".svg")
SITE_DIRS = ("site", "public", "dist", "docs")
MAX_FILES = 500
#: Names nobody's build may take: they mean something on any domain.
RESERVED = ("www", "api", "mail", "admin", "app", "wallet", "pay", "login", "auth", "teeny", "x402", "status")
SLUG = re.compile(r"[a-z0-9](?:[a-z0-9-]{0,38}[a-z0-9])?")
FORM = re.compile(r"<\s*form\b", re.IGNORECASE)
REMOTE_SCRIPT = re.compile(r"<\s*script\b[^>]*\bsrc\s*=\s*[\"']?\s*(?:https?:)?//", re.IGNORECASE)
DEFAULT_DOMAIN = "teeny.enamakel.com"

SCHEMA = """
CREATE TABLE IF NOT EXISTS deploys (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id      INTEGER,
    site        TEXT NOT NULL,
    at          REAL NOT NULL,
    files       INTEGER NOT NULL,
    bytes       INTEGER NOT NULL,
    url         TEXT NOT NULL,
    status      TEXT NOT NULL CHECK (status IN ('published', 'unpublished')),
    note        TEXT
);
CREATE INDEX IF NOT EXISTS deploys_site ON deploys (site, at);
"""


def db() -> sqlite3.Connection:
    return hand.open_db(SCHEMA)


def root() -> Path:
    """Where the sites live: DEPLOY_ROOT, else state/sites/."""
    value = hand.setting("DEPLOY_ROOT")
    path = Path(value) if value else hand.state_dir() / "sites"
    return path if path.is_absolute() else hand.root() / path


def domain() -> str:
    value = (hand.setting("DEPLOY_DOMAIN") or DEFAULT_DOMAIN).strip().lower()
    if not re.fullmatch(r"[a-z0-9.-]{3,200}", value) or ".." in value:
        raise hand.HandError("DEPLOY_DOMAIN is not a domain name")
    return value


def url_of(site: str) -> str:
    return f"https://{site}.{domain()}/"


def _job(job_id: int) -> sqlite3.Row:
    conn = hand.db()
    try:
        row = conn.execute("SELECT id, kind, slug, workdir, status FROM build_jobs WHERE id = ?", (job_id,)).fetchone()
    except sqlite3.OperationalError:
        row = None
    finally:
        conn.close()
    if row is None:
        raise hand.Refused(f"there is no build job {job_id}", rule="unknown")
    return row


def site_folder(workdir: Path) -> Path:
    for name in SITE_DIRS:
        folder = workdir / name
        if folder.is_dir() and not folder.is_symlink():
            return folder
    raise hand.Refused(f"the build has no site folder ({', '.join(SITE_DIRS)})", rule="no_site")


def check(folder: Path) -> list[tuple[Path, int]]:
    """Every file of the site and its size, or the first rule it breaks."""
    from ..guard.screen import OUTBOUND, screen_text

    max_file = hand.number("DEPLOY_MAX_FILE_BYTES", 1_000_000, 1_000, 5_000_000)
    max_total = hand.number("DEPLOY_MAX_BYTES", 5_000_000, 10_000, 20_000_000)
    files: list[tuple[Path, int]] = []
    total = 0
    for dirpath, dirnames, filenames in os.walk(folder, followlinks=False):
        here = Path(dirpath)
        for name in dirnames:
            if name.startswith(".") or (here / name).is_symlink():
                raise hand.Refused(f"{(here / name).relative_to(folder)}: no hidden folders or links", rule="static")
        for name in sorted(filenames):
            path = here / name
            rel = path.relative_to(folder)
            if name.startswith(".") or path.is_symlink() or not path.is_file():
                raise hand.Refused(f"{rel}: no hidden files, links or anything but a plain file", rule="static")
            if path.suffix.lower() not in STATIC:
                raise hand.Refused(f"{rel}: only static files ({' '.join(STATIC)})", rule="static")
            size = path.stat().st_size
            if size > max_file:
                raise hand.Refused(f"{rel} is {size} bytes, over {max_file}", rule="size")
            total += size
            if total > max_total:
                raise hand.Refused(f"the site is over {max_total} bytes", rule="size")
            files.append((rel, size))
            if len(files) > MAX_FILES:
                raise hand.Refused(f"the site has more than {MAX_FILES} files", rule="size")
            if path.suffix.lower() in TEXT or path.suffix.lower() in (".js", ".mjs"):
                text = path.read_text(encoding="utf-8", errors="replace")
                if path.suffix.lower() in (".html", ".htm", ".svg"):
                    if FORM.search(text):
                        raise hand.Refused(f"{rel}: a static site collects nothing (no forms)", rule="form")
                    if REMOTE_SCRIPT.search(text):
                        raise hand.Refused(f"{rel}: no script loaded from elsewhere", rule="remote_script")
                if path.suffix.lower() in TEXT:
                    found = screen_text(re.sub(r"<[^>]+>", " ", text), OUTBOUND)
                    if found is not None:
                        raise hand.Refused(f"{rel}: the guard held its words ({found.category})", rule="held")
    if not any(rel.as_posix() in ("index.html", "index.htm") for rel, _ in files):
        raise hand.Refused("the site has no index.html at its root", rule="no_index")
    return files


def publish(job_id: int) -> Json:
    if not hand.flag("DEPLOY", False):
        raise hand.Refused("deploying is off: DEPLOY=1 in secrets/teeny.env", rule="off")
    job = _job(job_id)
    if job["status"] != "shipped":
        raise hand.Refused(f"build job {job_id} is {job['status']}; only a shipped build is published", rule="status")
    if job["kind"] == "self":
        raise hand.Refused("a change to Teeny itself is not a site", rule="kind")
    site = str(job["slug"])
    if not SLUG.fullmatch(site) or site in RESERVED:
        raise hand.Refused(f"{site!r} cannot be a subdomain", rule="name")
    workdir = (hand.root() / job["workdir"]).resolve()
    if hand.state_dir().resolve() / "workbench" not in workdir.parents:
        raise hand.Refused("the build's folder is not on the workbench", rule="workdir")
    folder = site_folder(workdir)
    files = check(folder)
    size = sum(n for _, n in files)
    out = {"site": site, "url": url_of(site), "files": len(files), "bytes": size}
    if hand.dry_run():
        return {"dry_run": True, "would_publish": {**out, "paths": [p.as_posix() for p, _ in files][:50]}}
    base = root()
    base.mkdir(parents=True, exist_ok=True)
    with hand.lock("deploy"):
        fresh, live, old = base / f".{site}.new", base / site, base / f".{site}.old"
        for leftover in (fresh, old):
            if leftover.exists():
                shutil.rmtree(leftover)
        fresh.mkdir()
        for rel, _ in files:
            target = fresh / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(folder / rel, target)
            target.chmod(0o644)
        if live.exists():
            live.rename(old)
        fresh.rename(live)
        if old.exists():
            shutil.rmtree(old)
        conn = db()
        try:
            with hand.transaction(conn):
                conn.execute("INSERT INTO deploys (job_id, site, at, files, bytes, url, status) VALUES (?, ?, ?, ?, ?, ?, 'published')",
                             (job_id, site, hand.now(), len(files), size, out["url"]))
        finally:
            conn.close()
    return {"published": out}


def listing(max_rows: int = 50) -> Json:
    conn = db()
    try:
        rows = conn.execute("SELECT * FROM deploys ORDER BY id DESC LIMIT ?", (max_rows,)).fetchall()
    finally:
        conn.close()
    return {"deploys": [{k: r[k] for k in r.keys() if r[k] is not None} for r in rows], "root": str(root()), "domain": domain()}


def unpublish(site: str, note: str) -> Json:
    if not SLUG.fullmatch(site or ""):
        raise hand.Refused("that is not a site name", rule="name")
    live = root() / site
    if not live.is_dir():
        raise hand.Refused(f"{site} is not published", rule="unknown")
    shutil.rmtree(live)
    conn = db()
    try:
        with hand.transaction(conn):
            conn.execute("INSERT INTO deploys (site, at, files, bytes, url, status, note) VALUES (?, ?, 0, 0, ?, 'unpublished', ?)",
                         (site, hand.now(), url_of(site), (note or "")[:300]))
    finally:
        conn.close()
    return {"unpublished": site}
