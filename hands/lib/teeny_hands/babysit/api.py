"""The pull-request slice of GitHub the babysitter reads and writes, on top
of hands/gh's transport (`GitHubApi.call`), so the token, the error rules
and `GITHUB_API_BASE` are hands/gh's and nothing here holds a key.

The raw writes (labels, merge) run no rule. Only `actions` calls them.
"""

from __future__ import annotations

from typing import Any, Mapping

from ..gh.client import GitHubApi, GitHubError, to_pr

Json = dict[str, Any]

#: Pages of 100 read for a pull request's files, commits, reviews and
#: comments. GitHub lists at most 3000 files for one pull request; a
#: pull request past this is too big to audit and is held as such.
MAX_PAGES = 5


def _login(value: Any) -> str | None:
    return value.get("login") if isinstance(value, Mapping) else None


class PullsApi:
    """Pull-request reads and the babysitter's two raw writes."""

    def __init__(self, gh: GitHubApi | None = None) -> None:
        self.gh = gh or GitHubApi.from_settings()

    def _pages(self, path: str, key: str | None = None) -> tuple[list[Any], bool]:
        """Every item of a listing, and whether it was cut at MAX_PAGES."""
        items: list[Any] = []
        for page in range(1, MAX_PAGES + 1):
            got = self.gh.call("GET", path, [("per_page", "100"), ("page", str(page))])
            batch = got.get(key) if key and isinstance(got, Mapping) else got
            batch = batch if isinstance(batch, list) else []
            items.extend(batch)
            if len(batch) < 100:
                return items, False
        return items, True

    def _p(self, owner: str, name: str, *rest: str) -> str:
        return self.gh._path("repos", owner, name, *rest)

    # --- reads ------------------------------------------------------------------------

    def open_pulls(self, owner: str, name: str, max_results: int) -> list[Json]:
        got = self.gh.call(
            "GET", self._p(owner, name, "pulls"),
            [("state", "open"), ("per_page", str(max_results)), ("sort", "updated"), ("direction", "desc")],
        )
        return [self.pull_shape(item) for item in (got if isinstance(got, list) else []) if isinstance(item, Mapping)]

    def pull(self, owner: str, name: str, number: int) -> Json | None:
        try:
            got = self.gh.call("GET", self._p(owner, name, "pulls", str(number)))
        except GitHubError as error:
            if error.status == 404:
                return None
            raise
        return self.pull_shape(got)

    @staticmethod
    def pull_shape(got: Any) -> Json:
        got = got if isinstance(got, Mapping) else {}
        head = got.get("head") if isinstance(got.get("head"), Mapping) else {}
        base = got.get("base") if isinstance(got.get("base"), Mapping) else {}
        head_repo = head.get("repo") if isinstance(head.get("repo"), Mapping) else {}
        base_repo = base.get("repo") if isinstance(base.get("repo"), Mapping) else {}
        shape = to_pr(got)
        # to_pr clips the body for a drafting agent; the audit reads all of it.
        shape["body"] = got.get("body") if isinstance(got.get("body"), str) else None
        shape.update({
            "author_association": got.get("author_association"),
            "head_repo": head_repo.get("full_name"),
            "base_repo": base_repo.get("full_name"),
            "base_sha": base.get("sha"),
            "mergeable": got.get("mergeable"),
            "mergeable_state": got.get("mergeable_state"),
            "labels": [label.get("name") for label in got.get("labels") or [] if isinstance(label, Mapping)],
            "additions": got.get("additions"),
            "deletions": got.get("deletions"),
            "changed_files": got.get("changed_files"),
            "updated_at": got.get("updated_at"),
        })
        return shape

    def files(self, owner: str, name: str, number: int) -> tuple[list[Json], bool]:
        items, cut = self._pages(self._p(owner, name, "pulls", str(number), "files"))
        return [
            {
                "filename": f.get("filename"),
                "previous_filename": f.get("previous_filename"),
                "status": f.get("status"),
                "additions": f.get("additions"),
                "deletions": f.get("deletions"),
                "patch": f.get("patch") if isinstance(f.get("patch"), str) else None,
            }
            for f in items if isinstance(f, Mapping)
        ], cut

    def commits(self, owner: str, name: str, number: int) -> tuple[list[Json], bool]:
        items, cut = self._pages(self._p(owner, name, "pulls", str(number), "commits"))
        out = []
        for c in items:
            if not isinstance(c, Mapping):
                continue
            commit = c.get("commit") if isinstance(c.get("commit"), Mapping) else {}
            verification = commit.get("verification") if isinstance(commit.get("verification"), Mapping) else {}
            out.append({
                "sha": c.get("sha"),
                "author": _login(c.get("author")),
                "message": commit.get("message") if isinstance(commit.get("message"), str) else "",
                "verified": bool(verification.get("verified")),
            })
        return out, cut

    def reviews(self, owner: str, name: str, number: int) -> list[Json]:
        items, _ = self._pages(self._p(owner, name, "pulls", str(number), "reviews"))
        return [
            {"user": _login(r.get("user")), "state": r.get("state"), "commit_id": r.get("commit_id"),
             "body": r.get("body") if isinstance(r.get("body"), str) else "",
             "author_association": r.get("author_association")}
            for r in items if isinstance(r, Mapping)
        ]

    def comments(self, owner: str, name: str, number: int) -> list[Json]:
        """The conversation's comments and the review comments on lines."""
        issue, _ = self._pages(self._p(owner, name, "issues", str(number), "comments"))
        lines, _ = self._pages(self._p(owner, name, "pulls", str(number), "comments"))
        return [
            {"id": c.get("id"), "user": _login(c.get("user")), "author_association": c.get("author_association"),
             "body": c.get("body") if isinstance(c.get("body"), str) else "", "kind": kind}
            for kind, batch in (("comment", issue), ("line", lines)) for c in batch if isinstance(c, Mapping)
        ]

    def check_runs(self, owner: str, name: str, ref: str) -> list[Json]:
        return self.gh.check_runs(owner, name, ref)

    def status(self, owner: str, name: str, ref: str) -> Json:
        """The combined commit status (the older statuses API some CI uses)."""
        got = self.gh.call("GET", self._p(owner, name, "commits", ref, "status"))
        got = got if isinstance(got, Mapping) else {}
        statuses = got.get("statuses") if isinstance(got.get("statuses"), list) else []
        return {
            "state": got.get("state"),
            "statuses": [{"context": s.get("context"), "state": s.get("state"), "creator": _login(s.get("creator"))}
                         for s in statuses if isinstance(s, Mapping)],
        }

    def permission(self, owner: str, name: str, login: str) -> str | None:
        """`admin`, `maintain`, `write`, `triage`, `read` or None."""
        try:
            got = self.gh.call("GET", self._p(owner, name, "collaborators", login, "permission"))
        except GitHubError as error:
            if error.status in (403, 404):
                return None
            raise
        if not isinstance(got, Mapping):
            return None
        role = got.get("role_name") or got.get("permission")
        return role if isinstance(role, str) else None

    def text_file(self, owner: str, name: str, path: str) -> str | None:
        return self.gh.file(owner, name, path)

    # --- raw writes (only teeny_hands.babysit.actions calls these) -----------------------

    def add_labels(self, owner: str, name: str, number: int, labels: list[str]) -> list[str]:
        got = self.gh.call("POST", self._p(owner, name, "issues", str(number), "labels"), body={"labels": labels})
        return [label.get("name") for label in (got if isinstance(got, list) else []) if isinstance(label, Mapping)]

    def remove_label(self, owner: str, name: str, number: int, label: str) -> None:
        try:
            self.gh.call("DELETE", self._p(owner, name, "issues", str(number), "labels", label))
        except GitHubError as error:
            if error.status != 404:
                raise

    def merge(self, owner: str, name: str, number: int, sha: str, method: str) -> Json:
        """Merges only if the head is still `sha` (GitHub answers 409
        otherwise), so a push after the audit cannot ride along."""
        got = self.gh.call(
            "PUT", self._p(owner, name, "pulls", str(number), "merge"), body={"sha": sha, "merge_method": method}
        )
        got = got if isinstance(got, Mapping) else {}
        return {"merged": bool(got.get("merged")), "sha": got.get("sha")}
