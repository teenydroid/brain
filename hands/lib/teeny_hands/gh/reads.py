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


#: Check conclusions that mean the check failed.
RED = ("failure", "timed_out", "cancelled", "action_required", "startup_failure")


def checks(repo: str, number: int, gh: GitHubApi | None = None) -> Json:
    """A pull request's state and the checks on its head commit (docs/roadmap.md,
    A7): `red` and `pending` name the checks, never their logs. Not cached:
    a babysitter needs it fresh."""
    owner, name = split_repo(repo)
    gh = gh or GitHubApi.from_settings()
    pr = gh.pr(owner, name, number)
    if pr is None:
        raise hand.Refused(f"{owner}/{name} has no pull request {number}", rule="not_found")
    runs = gh.check_runs(owner, name, pr["head_sha"]) if pr.get("head_sha") else []
    red = sorted({str(r["name"]) for r in runs if r.get("conclusion") in RED})
    pending = sorted({str(r["name"]) for r in runs if r.get("status") != "completed"})
    green = sorted({str(r["name"]) for r in runs if r.get("conclusion") in ("success", "neutral", "skipped")})
    return {"repo": f"{owner}/{name}", "number": number, "state": pr.get("state"), "merged": pr.get("merged"),
            "head_ref": pr.get("head_ref"), "head_sha": pr.get("head_sha"), "user": pr.get("user"),
            "red": red, "pending": pending, "green": green}


#: The most of any one review text `pr_feedback` passes on, and of all of
#: them together in one answer: strangers' words, kept small.
FEEDBACK_CLIP = 1500
FEEDBACK_BUDGET = 20000
FEEDBACK_QUERY = """
query($owner: String!, $name: String!, $number: Int!) {
  repository(owner: $owner, name: $name) {
    pullRequest(number: $number) {
      number state headRefName author { login }
      reviewThreads(last: 100) { totalCount nodes { isResolved isOutdated path line
        opening: comments(first: 1) { totalCount nodes { id author { login } body createdAt } }
        latest: comments(last: 19) { nodes { id author { login } body createdAt } } } }
      reviews(last: 50) { nodes { author { login } state body submittedAt } }
      comments(last: 50) { nodes { author { login } body createdAt } }
    }
  }
}
"""


def _clipped(text: Any) -> str:
    text = text if isinstance(text, str) else ""
    return text if len(text) <= FEEDBACK_CLIP else text[:FEEDBACK_CLIP] + "…"


def _author(node: Any) -> str | None:
    who = node.get("author") if isinstance(node, dict) else None
    return who.get("login") if isinstance(who, dict) else None


def _nodes(parent: Any, key: str) -> list[dict]:
    got = parent.get(key) if isinstance(parent, dict) else None
    nodes = got.get("nodes") if isinstance(got, dict) else None
    return [n for n in (nodes if isinstance(nodes, list) else []) if isinstance(n, dict)]


def _total(connection: Any) -> int | None:
    got = connection.get("totalCount") if isinstance(connection, dict) else None
    return got if isinstance(got, int) and not isinstance(got, bool) else None


def _thread_comments(thread: dict) -> list[dict]:
    """A thread's opening comment (the finding) and its latest replies,
    each once."""
    opening = _nodes(thread, "opening")
    seen = {c.get("id") for c in opening if c.get("id") is not None}
    return opening + [c for c in _nodes(thread, "latest") if c.get("id") is None or c.get("id") not in seen]


def _said(node: dict, at: str) -> Json:
    who = _author(node)
    return {"author": who, "self": isinstance(who, str) and who.lower() == user().lower(),
            "body": _clipped(node.get("body")), "at": node.get(at)}


def pr_feedback(repo: str, number: int, gh: GitHubApi | None = None) -> Json | None:
    """A pull request's newest 100 review threads (resolved or not, with
    their file and line), its reviews' verdicts and its conversation, each
    text clipped to FEEDBACK_CLIP and all of them together to FEEDBACK_BUDGET:
    unresolved threads first, then verdicts, then comments, then resolved
    threads, the rest counted in `omitted`. Each item says whether Teeny
    wrote it (`self`). None when there is no such pull request. One GraphQL
    read, not cached: a revision needs it fresh."""
    owner, name = split_repo(repo)
    data = _gh(gh).graphql(FEEDBACK_QUERY, {"owner": owner, "name": name, "number": number})
    pull = (data.get("repository") or {}).get("pullRequest") if isinstance(data.get("repository"), dict) else None
    if not isinstance(pull, dict):
        return None
    threads = [{"path": t.get("path"), "line": t.get("line"), "resolved": bool(t.get("isResolved")),
                "outdated": bool(t.get("isOutdated")), "comments_total": _total(t.get("opening")),
                "comments": [_said(c, "createdAt") for c in _thread_comments(t)]}
               for t in reversed(_nodes(pull, "reviewThreads"))]  # newest first
    reviews = [{**_said(r, "submittedAt"), "state": r.get("state")} for r in reversed(_nodes(pull, "reviews"))]
    comments = [_said(c, "createdAt") for c in reversed(_nodes(pull, "comments"))]
    left = FEEDBACK_BUDGET
    omitted = {"threads": 0, "thread_comments": 0, "reviews": 0, "comments": 0}

    def fits(item: Json) -> bool:
        nonlocal left
        if len(item["body"]) > left:
            return False
        left -= len(item["body"])
        return True

    def kept_threads(resolved: bool) -> list[Json]:
        out = []
        for thread in (t for t in threads if t["resolved"] is resolved):
            # A reply means nothing without the finding it answers: a thread
            # whose opening comment does not fit is left out whole.
            if not thread["comments"] or not fits(thread["comments"][0]):
                omitted["threads"] += 1
                omitted["thread_comments"] += len(thread["comments"])
                continue
            said = [thread["comments"][0], *(c for c in thread["comments"][1:] if fits(c))]
            omitted["thread_comments"] += len(thread["comments"]) - len(said)
            out.append({**thread, "comments": said})
        return out

    open_kept = kept_threads(False)
    reviews_kept = [r for r in reviews if fits(r)]
    comments_kept = [c for c in comments if fits(c)]
    resolved_kept = kept_threads(True)
    omitted["reviews"] = len(reviews) - len(reviews_kept)
    omitted["comments"] = len(comments) - len(comments_kept)
    total = _total(pull.get("reviewThreads"))
    return {
        "repo": f"{owner}/{name}", "number": pull.get("number"), "state": pull.get("state"),
        "branch": pull.get("headRefName"), "author": _author(pull),
        "open_threads": sum(1 for t in threads if not t["resolved"]),
        "threads_total": total if total is not None else len(threads),
        # Only the newest 100 threads are read: open_threads counts those.
        "threads_incomplete": total is not None and total > len(threads),
        "threads": open_kept + resolved_kept,
        "reviews": reviews_kept,
        "comments": comments_kept,
        "omitted": omitted,
    }
