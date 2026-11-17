"""Shared by the hands/babysit tests: hands/gh's scratch root and fake
GitHub, and one call that routes a whole pull request into it."""

from __future__ import annotations

import base64
from pathlib import Path
from typing import Any

from gh.support import T0, TOKEN, Scratch  # noqa: F401 - re-exported

REPO = "teenydroid/brain"
DIARY = "teenydroid/diary"
HEAD = "a" * 40
BASE = "b" * 40
FROZEN = (Path(__file__).resolve().parents[3] / "evals" / "frozen.txt").read_text()


def patch(*added: str) -> str:
    return "@@ -0,0 +1,{} @@\n".format(len(added)) + "".join(f"+{line}\n" for line in added)


class BabysitScratch(Scratch):
    settings = {"BABYSIT_OPERATORS": "senamakel", "BABYSIT_MAINTAINERS": "alice"}

    def pull_request(
        self,
        number: int = 7,
        repo: str = REPO,
        author: str = "alice",
        title: str = "fix(cron): stop catch-up on ordered jobs",
        body: str = "Small fix.",
        branch: str = "cron-fix",
        files: list[dict[str, Any]] | None = None,
        commits: list[dict[str, Any]] | None = None,
        reviews: list[dict[str, Any]] | None = None,
        comments: list[dict[str, Any]] | None = None,
        checks: list[dict[str, Any]] | None = None,
        statuses: list[dict[str, Any]] | None = None,
        status_list: list[dict[str, Any]] | None = None,
        mergeable: bool | None = True,
        mergeable_state: str = "clean",
        draft: bool = False,
        fork: str | None = None,
        state: str = "open",
        labels: list[str] | None = None,
        permissions: dict[str, str] | None = None,
    ) -> None:
        """Routes one pull request and everything the audit reads about it.
        By default: by a maintainer, green, approved by the operator at HEAD.

        `statuses` are the head's commit statuses, newest first, each with
        its `creator`. They are served as GitHub does: the combined status
        (`/commits/{ref}/status`) carries the newest per context and no
        creator, the list (`/commits/{ref}/statuses`) every one with its
        creator. `status_list` overrides the list, for a history the
        combined view hides (an older status under a newer one)."""
        owner = repo.split("/")[0]
        head_repo = fork or repo
        self.gh.route("GET", f"/repos/{repo}/pulls/{number}", {
            "number": number, "title": title, "body": body, "state": state, "draft": draft, "merged": False,
            "user": {"login": author}, "author_association": "MEMBER",
            "head": {"label": f"{head_repo.split('/')[0]}:{branch}", "ref": branch, "sha": HEAD,
                     "repo": {"full_name": head_repo}},
            "base": {"ref": "main", "sha": BASE, "repo": {"full_name": repo}},
            "mergeable": mergeable, "mergeable_state": mergeable_state,
            "labels": [{"name": n} for n in labels or []],
            "html_url": f"https://github.com/{repo}/pull/{number}",
        })
        self.gh.route("GET", f"/repos/{repo}/pulls", [{"number": number, "user": {"login": author},
                                                       "head": {"sha": HEAD}, "base": {"ref": "main"}}])
        self.gh.route("GET", f"/repos/{repo}/pulls/{number}/files", files if files is not None else [
            {"filename": "brain/playbooks/morning.md", "status": "modified", "additions": 1, "deletions": 1,
             "patch": patch("say gm a little later")},
        ])
        self.gh.route("GET", f"/repos/{repo}/pulls/{number}/commits", commits if commits is not None else [
            {"sha": HEAD, "author": {"login": author},
             "commit": {"message": "fix: a small fix", "verification": {"verified": True}}},
        ])
        self.gh.route("GET", f"/repos/{repo}/pulls/{number}/reviews", reviews if reviews is not None else [
            {"user": {"login": "senamakel"}, "state": "APPROVED", "commit_id": HEAD, "body": ""},
        ])
        self.gh.route("GET", f"/repos/{repo}/issues/{number}/comments", comments or [])
        self.gh.route("GET", f"/repos/{repo}/pulls/{number}/comments", [])
        self.gh.route("GET", f"/repos/{repo}/commits/{HEAD}/check-runs", {"check_runs": checks if checks is not None else [
            {"name": "check", "status": "completed", "conclusion": "success"},
        ]})
        newest: dict[str, dict[str, Any]] = {}
        for s in statuses or []:
            newest.setdefault(s["context"], {k: v for k, v in s.items() if k != "creator"})
        self.gh.route("GET", f"/repos/{repo}/commits/{HEAD}/status",
                      {"state": "success", "statuses": list(newest.values())})
        listed = status_list if status_list is not None else statuses or []
        self.gh.route("GET", f"/repos/{repo}/commits/{HEAD}/statuses",
                      [dict(s, id=len(listed) - i) for i, s in enumerate(listed)])
        content = base64.b64encode(FROZEN.encode()).decode()
        self.gh.route("GET", f"/repos/{repo}/contents/evals/frozen.txt", {"type": "file", "content": content})
        self.gh.route("GET", f"/repos/{repo}", {"full_name": repo, "default_branch": "main", "owner": {"login": owner}})
        for login, role in (permissions or {}).items():
            self.gh.route("GET", f"/repos/{repo}/collaborators/{login}/permission", {"role_name": role})

    def rules(self, result: dict[str, Any]) -> dict[str, str]:
        """rule -> severity, for the findings of an audit."""
        return {f["rule"]: f["severity"] for f in result["findings"]}
