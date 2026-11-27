"""The verbs of hands/upstream (docs/hands.md, "upstream"; docs/roadmap.md, G9).

  R  releases   the `openclaw` version Teeny pins (core/package.json, checked
                against core/package-lock.json), the npm registry's latest
                openclaw, whether that is newer, and the lines of its GitHub
                release notes (the changelog), read-only
  A  propose    stdin {version, why}: one pull request on Teeny's own
                repository that moves the pin to that version, once per
                version, and only for the registry's latest, when it is newer

Upstream is the npm registry (`openclaw`, the `latest` tag) and the release
notes at github.com/openclaw/openclaw. Both are only read. A bump edits
core/package.json (an exact version, no range) and core/package-lock.json
(`npm install --package-lock-only --ignore-scripts` in a scratch directory),
pushes one teeny/pin-bump-openclaw-<version> branch of the two files, and
opens the pull request through `gh open-pr`'s own code (the guard, the
caps, DRY_RUN). Merging it, and the live test after, are the operator's: it
is never merged from here. Release notes are text from outside, so each line
is cut short and screened by the guard's lexicon before it is passed on.
Under DRY_RUN `propose` answers with the PR it would open and touches nothing.
"""

from __future__ import annotations

import argparse
import base64
import json
import re
import subprocess
import tempfile
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Callable, Mapping

from .. import hand

UPSTREAM_REPO = "openclaw/openclaw"
REGISTRY_URL = "https://registry.npmjs.org/openclaw/latest"
PACKAGE_PATH = "core/package.json"
LOCK_PATH = "core/package-lock.json"
DEFAULT_REPO = "teenydroid/brain"
MAX_LINES = 40
MAX_LINE = 300
BRANCH_PREFIX = "teeny/pin-bump-openclaw-"
VERSION = re.compile(r"\d{4}\.\d{1,2}\.\d{1,3}")
_URL = re.compile(r"<?https?://\S+>?")
_DEP = re.compile(r'("openclaw"\s*:\s*")(\d{4}\.\d{1,2}\.\d{1,3})(")')
_SCHEMA = """
CREATE TABLE IF NOT EXISTS pin_proposals (
    tag     TEXT PRIMARY KEY,
    at      REAL NOT NULL,
    repo    TEXT NOT NULL,
    number  INTEGER
);
"""

Fetch = Callable[[], Mapping[str, Any]]
Runner = Callable[[str, str, str], str]


def _key(version: str) -> tuple[int, ...]:
    return tuple(int(part) for part in version.split("."))


def pin() -> str:
    """The openclaw version core/package.json pins, which must be an exact
    version and the one core/package-lock.json resolves."""
    root = hand.root()
    try:
        package = json.loads((root / PACKAGE_PATH).read_text(encoding="utf-8"))
        lock = json.loads((root / LOCK_PATH).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise hand.HandError(f"could not read the pin in {PACKAGE_PATH} and {LOCK_PATH}") from exc
    wanted = ((package.get("dependencies") or {}).get("openclaw")) if isinstance(package, dict) else None
    if not isinstance(wanted, str) or not VERSION.fullmatch(wanted):
        raise hand.HandError(f"{PACKAGE_PATH} does not pin openclaw to an exact version")
    locked = (((lock.get("packages") or {}).get("node_modules/openclaw")) or {}).get("version") if isinstance(lock, dict) else None
    if locked != wanted:
        raise hand.HandError(f"{LOCK_PATH} resolves openclaw {locked}, not the pinned {wanted}")
    return wanted


def registry_latest() -> Mapping[str, Any]:
    """The npm registry's `latest` openclaw: {version, tarball, integrity}."""
    request = urllib.request.Request(REGISTRY_URL, headers={"accept": "application/json", "user-agent": "teeny-hands-upstream"})
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            doc = json.loads(response.read(1_000_000))
    except (urllib.error.URLError, OSError, ValueError) as exc:
        raise hand.HandError("could not read the openclaw version from the npm registry") from exc
    return doc


def _latest(fetch: Fetch | None) -> dict[str, str]:
    doc = (fetch or registry_latest)()
    version = doc.get("version") if isinstance(doc, Mapping) else None
    if not isinstance(version, str) or not VERSION.fullmatch(version):
        raise hand.HandError("the npm registry's latest openclaw is not a plain release version")
    return {"version": version}


def _api(gh: Any):
    if gh is not None:
        return gh
    from ..gh import client  # noqa: PLC0415

    return client.GitHubApi.from_settings()


def _notes(api: Any, version: str) -> tuple[str | None, str | None, list[str]]:
    """(url, published_at, screened lines) of openclaw's release notes for VERSION."""
    from ..gh import client  # noqa: PLC0415
    from ..guard.screen import screen_text  # noqa: PLC0415

    owner, name = UPSTREAM_REPO.split("/")
    try:
        rel = api.call("GET", f"/repos/{owner}/{name}/releases/tags/v{version}")
    except client.GitHubError as error:
        if error.status == 404:
            return None, None, []
        raise
    if not isinstance(rel, Mapping):
        return None, None, []
    lines: list[str] = []
    for raw in str(rel.get("body") or "").splitlines():
        # No links: the guard only lets this account link to its own sites, and a link in a
        # release note is somebody else's.
        line = " ".join(_URL.sub("", raw).split())[:MAX_LINE]
        if line and screen_text(line, "inbound") is None:
            lines.append(line)
        if len(lines) >= MAX_LINES:
            break
    return str(rel.get("html_url") or f"https://github.com/{UPSTREAM_REPO}/releases/tag/v{version}"), rel.get("published_at"), lines


def releases(_args: Any = None, _body: Any = None, gh: Any = None, fetch: Fetch | None = None) -> Mapping[str, Any]:
    pinned = pin()
    latest = _latest(fetch)["version"]
    newer = _key(latest) > _key(pinned)
    out: dict[str, Any] = {"pin": pinned, "latest": latest, "newer": newer, "package": "openclaw"}
    if newer:
        url, published, lines = _notes(_api(gh), latest)
        out.update({"url": url, "published_at": published, "changelog": lines})
    return out


def _contents(api: Any, repo: str, path: str) -> tuple[str, str]:
    got = api.call("GET", f"/repos/{repo}/contents/{path}")
    if not isinstance(got, Mapping) or not isinstance(got.get("content"), str) or not isinstance(got.get("sha"), str):
        raise hand.HandError(f"could not read {path} on {repo}")
    return base64.b64decode(got["content"]).decode("utf-8"), got["sha"]


def npm_lock(package_text: str, lock_text: str, version: str) -> str:
    """The lockfile for PACKAGE_TEXT, made by npm in a scratch directory with
    no scripts run. Only the registry is read."""
    with tempfile.TemporaryDirectory(prefix="teeny-pin-bump-") as tmp:
        (Path(tmp) / "package.json").write_text(package_text, encoding="utf-8")
        (Path(tmp) / "package-lock.json").write_text(lock_text, encoding="utf-8")
        got = subprocess.run(["npm", "install", "--package-lock-only", "--ignore-scripts", "--no-audit", "--no-fund"],
                             cwd=tmp, capture_output=True, text=True, timeout=300)
        if got.returncode != 0:
            raise hand.HandError(f"npm could not lock openclaw {version}")
        return (Path(tmp) / "package-lock.json").read_text(encoding="utf-8")


def _check_lock(lock_text: str, version: str) -> None:
    try:
        lock = json.loads(lock_text)
        top = lock["packages"][""]["dependencies"]["openclaw"]
        entry = lock["packages"]["node_modules/openclaw"]
    except (ValueError, KeyError, TypeError) as exc:
        raise hand.HandError("the new lockfile does not name openclaw") from exc
    if top != version or entry.get("version") != version or not str(entry.get("resolved", "")).startswith("https://registry.npmjs.org/"):
        raise hand.Refused(f"the new lockfile does not resolve openclaw {version} from the npm registry", rule="lockfile")


def _push_bump(api: Any, repo: str, version: str, branch: str, runner: Runner) -> None:
    from ..gh import client  # noqa: PLC0415

    package_text, package_sha = _contents(api, repo, PACKAGE_PATH)
    lock_text, lock_sha = _contents(api, repo, LOCK_PATH)
    new_package, count = _DEP.subn(lambda m: m.group(1) + version + m.group(3), package_text)
    if count != 1:
        raise hand.HandError(f"{PACKAGE_PATH} does not pin openclaw to an exact version")
    new_lock = runner(new_package, lock_text, version)
    _check_lock(new_lock, version)
    base = api.call("GET", f"/repos/{repo}/git/ref/heads/main")
    sha = ((base or {}).get("object") or {}).get("sha")
    if not isinstance(sha, str):
        raise hand.HandError(f"could not read main on {repo}")
    try:
        api.call("POST", f"/repos/{repo}/git/refs", body={"ref": f"refs/heads/{branch}", "sha": sha})
    except client.GitHubError as error:
        if error.status == 422:
            raise hand.Refused(f"the branch {branch} already exists", rule="branch") from error
        raise
    message = f"I bumped openclaw to {version}"
    for path, text, blob in ((PACKAGE_PATH, new_package, package_sha), (LOCK_PATH, new_lock, lock_sha)):
        api.call("PUT", f"/repos/{repo}/contents/{path}", body={
            "message": message, "branch": branch, "sha": blob, "content": base64.b64encode(text.encode("utf-8")).decode("ascii")})


def propose(_args: Any, body: Any, gh: Any = None, fetch: Fetch | None = None, runner: Runner | None = None) -> Mapping[str, Any]:
    from ..gh import actions  # noqa: PLC0415

    if not isinstance(body, dict) or set(body) - {"version", "why"}:
        raise hand.Refused("a proposal is {version, why}", rule="shape")
    version, why = body.get("version"), body.get("why")
    if not isinstance(version, str) or not VERSION.fullmatch(version):
        raise hand.Refused("version is an exact openclaw version such as 2026.11.10", rule="shape")
    if not isinstance(why, str) or not why.strip() or len(why) > 1500:
        raise hand.Refused("why is 1 to 1500 characters", rule="shape")
    conn = hand.open_db(_SCHEMA)
    try:
        if conn.execute("SELECT 1 FROM pin_proposals WHERE tag = ?", (version,)).fetchone():
            raise hand.Refused(f"the pin bump to {version} was already proposed", rule="once")
    finally:
        conn.close()
    found = releases(gh=gh, fetch=fetch)
    if version != found["latest"] or not found["newer"]:
        raise hand.Refused(f"{version} is not an openclaw release newer than the pin that the npm registry calls latest", rule="release")
    api = _api(gh)
    lines = [f"openclaw {version} is the npm registry's latest, and I pin `{found['pin']}` in "
             f"`{PACKAGE_PATH}`. This edits that line to the exact new version and updates `{LOCK_PATH}` with "
             "`npm install --package-lock-only --ignore-scripts`.", "", "## What the changelog says", ""]
    lines += [f"- {line}" for line in found["changelog"]] or ["- (no release notes were readable)"]
    lines += ["", "## Why I want it", "", why.strip(), "", "---",
              "Opened by Teeny (docs/roadmap.md, G9). I never merge this: the operator reviews it and runs the live test."]
    repo = hand.setting("LADDER_ISSUE_REPO", DEFAULT_REPO) or DEFAULT_REPO
    branch = f"{BRANCH_PREFIX}{version}"
    pr = {"repo": repo, "head": branch, "base": "main", "title": f"Bump openclaw to {version}", "body": "\n".join(lines)}
    with hand.lock("pin-proposal", timeout=30):
        if hand.dry_run():
            got = actions.open_pr(pr, api)
            return {"dry_run": True, "would_open_pr": repo, "version": version, "head": branch, "title": got["title"], "body": got["body"]}
        _push_bump(api, repo, version, branch, runner or npm_lock)
        got = actions.open_pr(pr, api)
        conn = hand.open_db(_SCHEMA)
        try:
            with hand.transaction(conn):
                conn.execute("INSERT OR IGNORE INTO pin_proposals (tag, at, repo, number) VALUES (?, ?, ?, ?)",
                             (version, hand.now(), repo, got.get("number")))
        finally:
            conn.close()
    return {"opened_pr": repo, "number": got.get("number"), "html_url": got.get("html_url"), "version": version}


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="hands/upstream", description="openclaw releases and the pin.")
    verbs = p.add_subparsers(dest="verb")
    verbs.add_parser("releases").set_defaults(handler=lambda a, b: releases(a, b))
    verbs.add_parser("propose").set_defaults(handler=lambda a, b: propose(a, b), takes_body=True)
    return p


def main(argv: list[str]) -> int:
    return hand.run("upstream", parser(), argv)
