"""The read verbs' answers. Public slop.cash and GitHub reads, nothing signed
in, so they are safe to give a drafting agent: nothing here writes anywhere but
the read cache. What a reader gets is model context, so long text is clipped.

The project and the job are slop.cash's manifest (`GET /api/v1/projects/:id`);
the submissions are the pull requests on the project's repositories, each with
what Slopbot (`slopdotcash[bot]`) said about it in the issue comments."""

from __future__ import annotations

import re
from typing import Any, Mapping

from .. import hand
from ..readcache import ReadCache
from ..rest import RestError, quote
from . import client

MINUTE = 60.0

CACHE = ReadCache(
    "slopcash_cache",
    "GITHUB_TOKEN",
    {
        "project": MINUTE,
        "projects": 5 * MINUTE,
        "revisions": 5 * MINUTE,
        "submissions": 2 * MINUTE,
        "submission": 2 * MINUTE,
    },
    "SLOPCASH_CACHE_ENABLED",
)

STATES = ("open", "closed", "all")
SLOPBOT_CHARS = 1500
BODY_CHARS = 4000
FILE_PAGES = 3
FOOTER = re.compile(r"made via @slopdotcash", re.IGNORECASE)
_HEADLINE = re.compile(r"^\s*\*\*(.+?)\*\*\s*$", re.MULTILINE)

Json = dict[str, Any]


def _clip(text: Any, limit: int) -> str:
    if not isinstance(text, str):
        return ""
    return text if len(text) <= limit else text[:limit] + "…"


def _get(path: str) -> Any:
    return client.site().call("GET", path)


# --- the project ------------------------------------------------------------------------


def fetch(pid: str) -> Json:
    """`GET /api/v1/projects/:id` as slop.cash answers it, uncached (a write
    reads its base here). 410 is a restricted project: refused."""
    try:
        got = _get(f"/api/v1/projects/{quote(pid)}")
    except RestError as error:
        if error.status == 410:
            raise hand.Refused("restricted", project=pid, message=_clip(error.body and error.body.get("message"), 300)) from None
        if error.status == 404:
            raise hand.HandError(f"slop.cash answered 404: no project {pid}") from None
        raise
    if not isinstance(got, Mapping) or not isinstance(got.get("project"), Mapping):
        raise hand.HandError("slop.cash sent no project manifest")
    return dict(got)


def _cached_project(pid: str) -> Json:
    return CACHE.cached("project", (pid,), lambda: fetch(pid))


def _repositories(manifest: Mapping[str, Any]) -> list[Json]:
    out = []
    for entry in manifest.get("repositories") or []:
        if isinstance(entry, Mapping) and isinstance(entry.get("id"), str):
            out.append({"repo": entry["id"], "branch": entry.get("integrationBranch"), "url": entry.get("githubUrl")})
    return out


def _dig(value: Any, *keys: str) -> Any:
    for key in keys:
        value = value.get(key) if isinstance(value, Mapping) else None
    return value


def _steering(manifest: Mapping[str, Any], vetting: str | None) -> tuple[str | None, str]:
    """Why the project is paused (None when it is not), and the next step."""
    check = "then run `hands/slopcash check --confirm` at the box (the operator signs in)"
    if manifest.get("status") == "active":
        return None, "nothing: the project is active and contributors can find it"
    state = _dig(manifest, "authority", "state")
    reason = _dig(manifest, "authority", "reason")
    repos = ", ".join(r["repo"] for r in _repositories(manifest)) or "the repository"
    if reason == "missing-repository-proof":
        return (
            "slop.cash has no proof Teeny controls the repository: Slopbot is not installed on it",
            f"install the Slopbot GitHub App on {repos} only ({client.SLOPBOT_INSTALL_URL}), {check}",
        )
    if state not in (None, "verified"):
        return (
            f"authority is {state} ({reason or 'no reason given'})",
            f"{check}; its next action says what setup still needs",
        )
    if vetting in ("queued", "pending"):
        return "the security scan of this revision has not finished", f"wait for the scan, {check}"
    return f"the project is {manifest.get('status')}", f"run `hands/slopcash check --confirm`: it activates the project when every setup check passes"


def project(pid: str | None) -> Json:
    pid = client.project_id(pid)
    got = _cached_project(pid)
    manifest = got["project"]
    vetting = _dig(got, "vetting", "state")
    why, step = _steering(manifest, vetting)
    reward = manifest.get("reward") if isinstance(manifest.get("reward"), Mapping) else {}
    funding = manifest.get("funding") if isinstance(manifest.get("funding"), Mapping) else {}
    return {
        "project": manifest.get("id", pid),
        "name": manifest.get("name"),
        "status": manifest.get("status"),
        "listing_tier": manifest.get("listingTier"),
        "revision": got.get("revision"),
        "updated_at": got.get("updatedAt"),
        "steward": _dig(manifest, "steward", "github", "login"),
        "authority": {"state": _dig(manifest, "authority", "state"), "reason": _dig(manifest, "authority", "reason")},
        "vetting": vetting,
        "why_paused": why,
        "next_step": step,
        "reward": {
            "kind": reward.get("kind"),
            "currency": reward.get("currency"),
            "monthly_cap": reward.get("monthlyCapDisplay"),
            "committed_minor": reward.get("committedMinor"),
            "payment_mode": reward.get("paymentMode"),
            "funding_state": reward.get("fundingState"),
            "cycle": reward.get("cycle"),
            "starts_at": reward.get("rewardStartAt"),
            "asset": _dig(manifest, "rewardAsset", "address"),
        },
        "funding": {"mode": funding.get("mode"), "addresses": len(funding.get("addresses") or [])},
        "repositories": _repositories(manifest),
        "url": f"https://slop.cash/projects/{pid}",
    }


def job(pid: str | None) -> Json:
    pid = client.project_id(pid)
    got = _cached_project(pid)
    manifest = got["project"]
    contribution = manifest.get("contribution") if isinstance(manifest.get("contribution"), Mapping) else {}
    return {
        "project": manifest.get("id", pid),
        "revision": got.get("revision"),
        "status": manifest.get("status"),
        "name": manifest.get("name"),
        "eyebrow": manifest.get("eyebrow"),
        "headline": manifest.get("headline"),
        "description": manifest.get("description"),
        "approvedScope": contribution.get("approvedScope"),
        "requiredTests": contribution.get("requiredTests"),
        "contributionGuide": contribution.get("contributionGuide"),
        "supportedTasks": contribution.get("supportedTasks"),
        "contribution": dict(contribution),
        "repositories": _repositories(manifest),
        "links": manifest.get("links"),
    }


def jobs(steward: str | None) -> Json:
    who = client.login(steward)
    got = CACHE.cached("projects", (), lambda: _get("/api/v1/projects"))
    listed = got.get("projects") if isinstance(got, Mapping) else None
    found = []
    for entry in listed or []:
        if not isinstance(entry, Mapping):
            continue
        login = _dig(entry, "steward", "github", "login")
        if isinstance(login, str) and login.lower() == who.lower():
            found.append({"project": entry.get("id"), "name": entry.get("name"),
                          "status": entry.get("status"), "headline": entry.get("headline")})  # fmt: skip
    return {"steward": who, "jobs": found}


def revisions(pid: str | None) -> Json:
    pid = client.project_id(pid)

    def fetch_all() -> list[Json]:
        got = _get("/api/v1/project-revisions")
        rows = got.get("revisions") if isinstance(got, Mapping) else None
        return [
            {"revision": r.get("revision"), "created_at": r.get("createdAt"), "reason": r.get("reason"),
             "manifest_sha256": r.get("manifestSha256")}
            for r in rows or [] if isinstance(r, Mapping) and r.get("projectId") == pid
        ]  # fmt: skip

    rows = CACHE.cached("revisions", (pid,), fetch_all)
    rows.sort(key=lambda r: r["revision"] if isinstance(r["revision"], int) else -1, reverse=True)
    return {"project": pid, "revisions": rows}


# --- the submissions ----------------------------------------------------------------------


def _project_repos(pid: str, only: str | None) -> list[str]:
    repos = [r["repo"] for r in _repositories(_cached_project(pid)["project"])]
    if only is None:
        if not repos:
            raise hand.HandError(f"project {pid} lists no repository")
        return repos
    wanted = client.repo(only)
    for name in repos:
        if name.lower() == wanted.lower():
            return [name]
    raise hand.HandError(f"project {pid} does not list {wanted}")


def _slopbot(entry: Mapping[str, Any]) -> Json:
    body = entry.get("body") if isinstance(entry.get("body"), str) else ""
    headline = _HEADLINE.search(body)
    return {
        "id": entry.get("id"),
        "created_at": entry.get("created_at"),
        "updated_at": entry.get("updated_at"),
        "url": entry.get("html_url"),
        "headline": headline.group(1).strip() if headline else None,
        "body": _clip(body, SLOPBOT_CHARS),
    }


def _slopbot_comments(repo: str, number: int) -> list[Json]:
    got = client.github_get(f"/repos/{repo}/issues/{number}/comments", [("per_page", "100")])
    return [
        _slopbot(c) for c in got or []
        if isinstance(c, Mapping) and _dig(c, "user", "login") == client.SLOPBOT_LOGIN
    ]  # fmt: skip


def _summary(repo: str, pr: Mapping[str, Any]) -> Json:
    body = pr.get("body") if isinstance(pr.get("body"), str) else ""
    return {
        "repo": repo,
        "number": pr.get("number"),
        "title": pr.get("title"),
        "author": _dig(pr, "user", "login"),
        "url": pr.get("html_url"),
        "state": pr.get("state"),
        "draft": bool(pr.get("draft")),
        "merged": bool(pr.get("merged")) or pr.get("merged_at") is not None,
        "created_at": pr.get("created_at"),
        "updated_at": pr.get("updated_at"),
        "head_sha": _dig(pr, "head", "sha"),
        "made_via_slopcash": bool(FOOTER.search(body)),
    }


def submissions(state: str, max_results: int) -> Json:
    if state not in STATES:
        raise hand.HandError(f"--state is one of {', '.join(STATES)}")
    pid = client.project_id(None)
    repos = _project_repos(pid, None)

    def fetch_all() -> list[Json]:
        found = []
        for repo in repos:
            query = [("state", state), ("per_page", str(max_results)), ("sort", "updated"), ("direction", "desc")]
            for pr in client.github_get(f"/repos/{repo}/pulls", query) or []:
                if isinstance(pr, Mapping) and isinstance(pr.get("number"), int):
                    found.append((repo, pr))
        found.sort(key=lambda item: str(item[1].get("updated_at") or ""), reverse=True)
        out = []
        for repo, pr in found[:max_results]:
            row = _summary(repo, pr)
            said = _slopbot_comments(repo, pr["number"])
            row["slopbot"] = said[-1] if said else None
            row["slopbot_comments"] = len(said)
            out.append(row)
        return out

    rows = CACHE.cached("submissions", (pid, tuple(repos), state, max_results), fetch_all)
    return {"project": pid, "state": state, "submissions": rows}


def _checks(repo: str, sha: str) -> Json:
    status = client.github_get(f"/repos/{repo}/commits/{sha}/status")
    runs = client.github_get(f"/repos/{repo}/commits/{sha}/check-runs", [("per_page", "100")])
    statuses = [
        {"context": s.get("context"), "state": s.get("state")}
        for s in (status.get("statuses") if isinstance(status, Mapping) else None) or [] if isinstance(s, Mapping)
    ]  # fmt: skip
    check_runs = [
        {"name": r.get("name"), "status": r.get("status"), "conclusion": r.get("conclusion")}
        for r in (runs.get("check_runs") if isinstance(runs, Mapping) else None) or [] if isinstance(r, Mapping)
    ]  # fmt: skip
    bad = ("failure", "timed_out", "cancelled", "action_required", "startup_failure")
    if any(s["state"] in ("failure", "error") for s in statuses) or any(r["conclusion"] in bad for r in check_runs):
        overall = "failure"
    elif any(s["state"] == "pending" for s in statuses) or any(r["status"] != "completed" for r in check_runs):
        overall = "pending"
    elif statuses or check_runs:
        overall = "success"
    else:
        overall = "none"
    return {"state": overall, "statuses": statuses, "check_runs": check_runs}


def _files(repo: str, number: int) -> tuple[list[Json], bool]:
    files: list[Json] = []
    for page in range(1, FILE_PAGES + 1):
        got = client.github_get(f"/repos/{repo}/pulls/{number}/files", [("per_page", "100"), ("page", str(page))])
        batch = [f for f in got or [] if isinstance(f, Mapping)]
        for f in batch:
            row = {"path": f.get("filename"), "status": f.get("status"),
                   "additions": f.get("additions") or 0, "deletions": f.get("deletions") or 0}  # fmt: skip
            if f.get("previous_filename"):
                row["previous_path"] = f["previous_filename"]
            files.append(row)
        if len(batch) < 100:
            return files, False
    return files, True


def submission(number: int, repo: str | None) -> Json:
    if not isinstance(number, int) or number < 1:
        raise hand.HandError("NUMBER is a pull request number")
    pid = client.project_id(None)
    name = _project_repos(pid, repo)[0]

    def fetch_one() -> Json:
        pr = client.github_get(f"/repos/{name}/pulls/{number}")
        if not isinstance(pr, Mapping):
            raise hand.HandError("GitHub sent no pull request")
        out = _summary(name, pr)
        files, truncated = _files(name, number)
        paths = set()
        for f in files:
            paths.update(p for p in (f.get("path"), f.get("previous_path")) if isinstance(p, str))
        out.update({
            "body": _clip(pr.get("body"), BODY_CHARS),
            "base": _dig(pr, "base", "ref"),
            "mergeable_state": pr.get("mergeable_state"),
            "files": files,
            "files_truncated": truncated,
            "changed_files": pr.get("changed_files", len(files)),
            "additions": pr.get("additions"),
            "deletions": pr.get("deletions"),
            "total_changed_lines": sum(f["additions"] + f["deletions"] for f in files),
            "touched_paths": sorted(paths),
            "checks": _checks(name, out["head_sha"]) if out["head_sha"] else {"state": "none"},
            "slopbot": _slopbot_comments(name, number),
        })  # fmt: skip
        return out

    return {"project": pid, **CACHE.cached("submission", (pid, name, number), fetch_one)}
