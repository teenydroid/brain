"""The read verbs' answers, each through the cache (`cache.cached`). Safe to
give a drafting agent: nothing here writes anywhere but the cache."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from .. import hand
from . import cache
from . import contributing as policy
from .client import GitHubApi, split_repo, user

Json = dict[str, Any]


def _gh(gh: GitHubApi | None) -> GitHubApi:
    return gh or GitHubApi.from_settings()


def whoami(gh: GitHubApi | None = None) -> Json:
    me = cache.cached("whoami", (), lambda: _gh(gh).whoami())
    return {**me, "expected": user(), "matches": me["login"].lower() == user().lower()}


def repo(value: str, gh: GitHubApi | None = None) -> Json:
    owner, name = split_repo(value)
    return {"repo": cache.cached("repo", (owner.lower(), name.lower()), lambda: _gh(gh).repo(owner, name))}


def issues(value: str, label: str | None, max_results: int, gh: GitHubApi | None = None) -> Json:
    owner, name = split_repo(value)
    key = (owner.lower(), name.lower(), label, max_results)
    return {"issues": cache.cached("issues", key, lambda: _gh(gh).issues(owner, name, label, max_results))}


def pr(value: str, number: int, gh: GitHubApi | None = None) -> Json:
    owner, name = split_repo(value)
    return {"pr": cache.cached("pr", (owner.lower(), name.lower(), number), lambda: _gh(gh).pr(owner, name, number))}


def contributing(owner: str, name: str, gh: GitHubApi | None = None) -> Json:
    """The repository's AI policy (`contributing.read_policy`), cached a day."""

    def fetch() -> Json:
        client = _gh(gh)
        return policy.read_policy(lambda path: client.file(owner, name, path))

    return {"repo": f"{owner}/{name}", **cache.cached("contributing", (owner.lower(), name.lower()), fetch)}


def _repo_of(item: Json) -> str | None:
    url = item.get("repository_url")
    if isinstance(url, str) and "/repos/" in url:
        return url.split("/repos/", 1)[1]
    return None


def _pr_item(item: Json) -> Json:
    pull = item.get("pull_request") if isinstance(item.get("pull_request"), dict) else {}
    return {
        "repo": _repo_of(item),
        "number": item.get("number"),
        "title": item.get("title"),
        "state": item.get("state"),
        "created_at": item.get("created_at"),
        "merged_at": pull.get("merged_at"),
        "html_url": item.get("html_url"),
    }


def _commit_item(item: Json) -> Json:
    commit = item.get("commit") if isinstance(item.get("commit"), dict) else {}
    committer = commit.get("committer") if isinstance(commit.get("committer"), dict) else {}
    repository = item.get("repository") if isinstance(item.get("repository"), dict) else {}
    message = commit.get("message") if isinstance(commit.get("message"), str) else ""
    return {
        "repo": repository.get("full_name"),
        "sha": (item.get("sha") or "")[:12],
        "message": message.splitlines()[0] if message else "",
        "date": committer.get("date"),
        "html_url": item.get("html_url"),
    }


def activity(days: int, gh: GitHubApi | None = None) -> Json:
    """Teeny's own last `days` on GitHub, for the ship log: pull requests
    opened, pull requests merged, and commits."""
    since = (datetime.fromtimestamp(hand.now(), timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%d")
    me = user()

    def fetch() -> Json:
        client = _gh(gh)
        opened = client.search_issues(f"author:{me} type:pr created:>={since}", 50).get("items") or []
        merged = client.search_issues(f"author:{me} type:pr is:merged merged:>={since}", 50).get("items") or []
        commits = client.search_commits(f"author:{me} committer-date:>={since}", 50).get("items") or []
        return {
            "prs": [_pr_item(i) for i in opened if isinstance(i, dict)],
            "merged": [_pr_item(i) for i in merged if isinstance(i, dict)],
            "commits": [_commit_item(i) for i in commits if isinstance(i, dict)],
        }

    return {"user": me, "since": since, "days": days, **cache.cached("activity", (me.lower(), since), fetch)}
