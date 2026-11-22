"""Whether Teeny's GitHub token can administer a repository it merges into.

The branch ruleset on `main` ("main: frozen gate") is what makes the frozen
check and CI binding on Teeny's own pull requests (docs/roadmap.md, G5). A
token that can administer the repository can edit or delete that ruleset, so
the gate holds only while the token cannot. `check` asks GitHub one question
per repository that only a token with the Administration permission is
answered: the repository's Actions permissions.

Read by `bin/doctor --live`, as a warning: the fix is the operator's (a
fine-grained token without Administration or Workflows, and a repository
owned by an account Teeny's token cannot act as).
"""

from __future__ import annotations

from typing import Any, Iterable

from .client import GitHubError, split_repo

#: GitHub answers this only to a token holding Administration on the repository.
_PROBE = "/repos/{owner}/{name}/actions/permissions"


def can_administer(api: Any, repo: str) -> bool | None:
    """True when the token administers REPO, False when GitHub refuses it,
    None when the question could not be asked."""
    owner, name = split_repo(repo)
    try:
        api.call("GET", _PROBE.format(owner=owner, name=name))
    except GitHubError as exc:
        # GitHub answers 403 (and 429) for a spent rate limit too, which says
        # nothing about what the token may do.
        if exc.status == 429 or "rate limit" in str(exc).lower():
            return None
        return False if exc.status in (401, 403, 404) else None
    return True


def check(api: Any, repos: Iterable[str]) -> tuple[str, str]:
    """(`ok` | `warn`, why) over every repository the babysitter merges into."""
    repos = list(repos)
    if not repos:
        return "ok", "no repository is watched: nothing to administer"
    admin, unknown = [], []
    for repo in repos:
        got = can_administer(api, repo)
        if got:
            admin.append(repo)
        elif got is None:
            unknown.append(repo)
    if admin:
        return "warn", (f"Teeny's token can administer {', '.join(admin)}: it could edit the ruleset that makes "
                        "the frozen check binding. Use a fine-grained token without Administration or Workflows, "
                        "on a repository Teeny's account does not own (docs/roadmap.md, G5)")
    if unknown:
        return "warn", f"could not ask GitHub about {', '.join(unknown)}"
    return "ok", f"the token cannot administer {', '.join(repos)}"
