"""The GitHub REST client: the transport and the handful of calls Teeny uses.

Nothing that leaves this module names a URL, a header or the token. An error
says which status GitHub answered and GitHub's own short message for it,
because a URL can carry a query the model wrote and a header carries the
token. urllib's exceptions are not chained either: their repr names the URL.

The raw writes here run no rule. The only way to one is
`teeny_hands.gh.actions`, which runs the guard, the rules and DRY_RUN first.
"""

from __future__ import annotations

import base64
import json
import re
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Mapping, Sequence

from .. import hand

#: Where GitHub's API lives. `GITHUB_API_BASE` moves it (the tests' fake).
GITHUB_API_BASE = "https://api.github.com"
#: The account Teeny codes as.
DEFAULT_USER = "teenydroid"
TIMEOUT_SECONDS = 30.0
_MESSAGE_CHARS = 200

Json = dict[str, Any]

_NAME = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9._-]{0,99})$")
_OWNER = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9-]{0,38})$")


def user() -> str:
    """The account Teeny codes as: `GITHUB_USER`, default teenydroid."""
    return (hand.setting("GITHUB_USER", DEFAULT_USER) or DEFAULT_USER).lstrip("@")


def split_repo(value: Any) -> tuple[str, str]:
    """OWNER/REPO, checked, so nothing but a repository can reach a path."""
    if not isinstance(value, str) or value.count("/") != 1:
        raise hand.HandError("a repository is OWNER/REPO")
    owner, name = value.split("/")
    if not _OWNER.match(owner) or not _NAME.match(name) or name in (".", "..") or name.endswith(".git"):
        raise hand.HandError(f"not a repository name: {value!r}")
    return owner, name


def is_own(owner: str) -> bool:
    return owner.lower() == user().lower()


class GitHubError(hand.HandError):
    """A failed GitHub call. `status` is GitHub's HTTP status, or None when it
    was not reached or answered something unreadable."""

    def __init__(self, message: str, status: int | None = None) -> None:
        super().__init__(message)
        self.status = status


def _message(body: bytes) -> str | None:
    try:
        parsed = json.loads(body)
    except (ValueError, UnicodeDecodeError):
        return None
    if isinstance(parsed, Mapping) and isinstance(parsed.get("message"), str) and parsed["message"].strip():
        return parsed["message"].strip()[:_MESSAGE_CHARS]
    return None


class GitHubApi:
    """A client for the slice of the GitHub REST API Teeny uses."""

    def __init__(self, base: str, token: str) -> None:
        self._base = base.rstrip("/")
        self._token = token

    def __repr__(self) -> str:
        return "GitHubApi()"

    @classmethod
    def from_settings(cls) -> "GitHubApi":
        return cls(hand.setting("GITHUB_API_BASE", GITHUB_API_BASE) or GITHUB_API_BASE, hand.require("GITHUB_TOKEN"))

    # --- transport ----------------------------------------------------------------------

    def call(self, method: str, path: str, query: Sequence[tuple[str, str]] = (), body: Any = None) -> Any:
        """One request; its JSON. A 404 raises with status 404 (callers that
        expect one catch it)."""
        url = self._base + path
        if query:
            url += "?" + urllib.parse.urlencode(list(query))
        headers = {
            "authorization": f"Bearer {self._token}",
            "accept": "application/vnd.github+json",
            "x-github-api-version": "2022-11-28",
            "user-agent": "teeny-hands-gh",
        }
        data = None
        if body is not None:
            data = json.dumps(body).encode()
            headers["content-type"] = "application/json"
        request = urllib.request.Request(url, data=data, headers=headers, method=method)
        failure: GitHubError | None = None
        text = b""
        try:
            with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as response:
                text = response.read()
        except urllib.error.HTTPError as exc:
            try:
                error_body = exc.read()
            except OSError:
                error_body = b""
            message = _message(error_body)
            failure = GitHubError(
                f"GitHub answered {exc.code}: {message}" if message else f"GitHub answered {exc.code}", exc.code
            )
        except (urllib.error.URLError, OSError, ValueError) as exc:
            failure = GitHubError(f"could not reach GitHub ({type(getattr(exc, 'reason', exc)).__name__})")
        if failure is not None:
            raise failure
        if not text:
            return {}
        try:
            return json.loads(text)
        except (ValueError, UnicodeDecodeError):
            raise GitHubError("GitHub sent something unreadable") from None

    def _path(self, *parts: str) -> str:
        return "/" + "/".join(urllib.parse.quote(part, safe="") for part in parts)

    # --- reads ----------------------------------------------------------------------------

    def whoami(self) -> Json:
        me = self.call("GET", "/user")
        if not isinstance(me, Mapping) or not isinstance(me.get("login"), str):
            raise GitHubError("GitHub did not say who the token belongs to")
        return {"login": me["login"], "id": me.get("id"), "name": me.get("name"), "html_url": me.get("html_url")}

    def repo(self, owner: str, name: str) -> Json | None:
        try:
            got = self.call("GET", self._path("repos", owner, name))
        except GitHubError as error:
            if error.status == 404:
                return None
            raise
        return to_repo(got)

    def issues(self, owner: str, name: str, label: str | None, max_results: int) -> list[Json]:
        query = [("state", "open"), ("per_page", str(max_results))]
        if label:
            query.append(("labels", label))
        got = self.call("GET", self._path("repos", owner, name, "issues"), query)
        return [to_issue(item) for item in (got if isinstance(got, list) else []) if "pull_request" not in item]

    def pr(self, owner: str, name: str, number: int) -> Json | None:
        try:
            got = self.call("GET", self._path("repos", owner, name, "pulls", str(number)))
        except GitHubError as error:
            if error.status == 404:
                return None
            raise
        return to_pr(got)

    def file(self, owner: str, name: str, path: str) -> str | None:
        """A file's text from the default branch; None when there is none."""
        quoted = "/".join(urllib.parse.quote(part, safe="") for part in path.split("/"))
        try:
            got = self.call("GET", f"{self._path('repos', owner, name, 'contents')}/{quoted}")
        except GitHubError as error:
            if error.status == 404:
                return None
            raise
        if not isinstance(got, Mapping) or got.get("type", "file") != "file":
            return None
        content = got.get("content")
        if not isinstance(content, str):
            return None
        try:
            return base64.b64decode(content).decode("utf-8", "replace")
        except (ValueError, TypeError):
            return None

    def search_issues(self, q: str, max_results: int = 50) -> Json:
        got = self.call("GET", "/search/issues", [("q", q), ("per_page", str(max_results))])
        return got if isinstance(got, dict) else {}

    def search_commits(self, q: str, max_results: int = 50) -> Json:
        got = self.call("GET", "/search/commits", [("q", q), ("per_page", str(max_results))])
        return got if isinstance(got, dict) else {}

    # --- raw writes (only teeny_hands.gh.actions calls these) ---------------------------------

    def create_repo(self, name: str, description: str, private: bool) -> Json:
        return to_repo(self.call("POST", "/user/repos", body={"name": name, "description": description, "private": private}))

    def open_pr(self, owner: str, name: str, head: str, base: str, title: str, body: str) -> Json:
        got = self.call(
            "POST", self._path("repos", owner, name, "pulls"), body={"title": title, "head": head, "base": base, "body": body}
        )
        return {"number": got.get("number"), "html_url": got.get("html_url")}

    def comment(self, owner: str, name: str, number: int, body: str) -> Json:
        got = self.call("POST", self._path("repos", owner, name, "issues", str(number), "comments"), body={"body": body})
        return {"id": got.get("id"), "html_url": got.get("html_url")}

    def fork(self, owner: str, name: str) -> Json:
        return to_repo(self.call("POST", self._path("repos", owner, name, "forks"), body={}))

    def open_issue(self, owner: str, name: str, title: str, body: str) -> Json:
        got = self.call("POST", self._path("repos", owner, name, "issues"), body={"title": title, "body": body})
        return {"number": got.get("number"), "html_url": got.get("html_url")}

    def close_issue(self, owner: str, name: str, number: int) -> Json:
        got = self.call(
            "PATCH", self._path("repos", owner, name, "issues", str(number)),
            body={"state": "closed", "state_reason": "completed"},
        )
        return {"number": got.get("number"), "state": got.get("state"), "html_url": got.get("html_url")}


# --- shapes ----------------------------------------------------------------------------------

_BODY_CHARS = 4000


def _clip(text: Any) -> str | None:
    if not isinstance(text, str):
        return None
    return text if len(text) <= _BODY_CHARS else text[:_BODY_CHARS] + "…"


def _login(value: Any) -> str | None:
    return value.get("login") if isinstance(value, Mapping) else None


def to_repo(got: Any) -> Json:
    got = got if isinstance(got, Mapping) else {}
    return {
        key: got.get(key)
        for key in (
            "full_name",
            "description",
            "private",
            "fork",
            "archived",
            "default_branch",
            "language",
            "stargazers_count",
            "open_issues_count",
            "html_url",
            "pushed_at",
        )
    } | {"owner": _login(got.get("owner"))}


def to_issue(got: Mapping[str, Any]) -> Json:
    return {
        "number": got.get("number"),
        "title": got.get("title"),
        "user": _login(got.get("user")),
        "labels": [label.get("name") for label in got.get("labels") or [] if isinstance(label, Mapping)],
        "comments": got.get("comments"),
        "created_at": got.get("created_at"),
        "html_url": got.get("html_url"),
        "body": _clip(got.get("body")),
    }


def to_pr(got: Any) -> Json:
    got = got if isinstance(got, Mapping) else {}
    head = got.get("head") if isinstance(got.get("head"), Mapping) else {}
    base = got.get("base") if isinstance(got.get("base"), Mapping) else {}
    return {
        "number": got.get("number"),
        "title": got.get("title"),
        "state": got.get("state"),
        "draft": got.get("draft"),
        "merged": got.get("merged"),
        "user": _login(got.get("user")),
        "head": head.get("label"),
        "base": base.get("ref"),
        "comments": got.get("comments"),
        "review_comments": got.get("review_comments"),
        "html_url": got.get("html_url"),
        "body": _clip(got.get("body")),
    }
