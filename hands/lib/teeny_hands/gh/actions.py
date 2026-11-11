"""The only way hands/gh writes. Every action runs, in this one invocation:
the body's shape, the guard on every word it would publish
(`check_fields`, surface github), the rules, DRY_RUN, GitHub, and the ledger
(`gh_actions`). A caller that forgets a step cannot skip it.

The rules:
- Teeny creates and pushes to repositories it owns (`GITHUB_USER`) only, and
  pushes only from a folder under state/workbench/, and never anything that
  looks like a secret (`gitpush.scan`).
- A pull request to somebody else's repository carries DISCLOSURE, and is
  refused when that project's policy forbids AI contributions
  (`contributing`), when Teeny already has an open pull request there, or when
  today's `GH_MAX_EXTERNAL_PRS_PER_DAY` (UTC) are spent.
- Under DRY_RUN nothing is written anywhere: GitHub, git or the ledger. The
  answer says what would have happened (`would_*`), after every rule ran.
"""

from __future__ import annotations

import json
import re
import sys
from datetime import datetime, timezone
from typing import Any, Mapping

from .. import hand
from . import open_db
from . import gitpush, reads
from .client import GitHubApi, is_own, split_repo, user

try:
    # Imported by name, so a test can stand in for the other hand's gate.
    from ..guard.gate import check_fields
except ImportError:  # the guard hand is not installed: nothing is published

    def check_fields(fields: Mapping[str, str], **_: Any) -> Any:
        raise hand.HandError("the content guard (hands/guard) is not installed, so nothing can be published")


#: Appended to every pull request Teeny opens on somebody else's project.
DISCLOSURE = (
    "—\nopened by Teeny, an AI agent (github.com/teenydroid), operated by @senamakel."
    " close it if it's not useful."
)
DEFAULT_MAX_EXTERNAL_PRS_PER_DAY = 2

SCHEMA = """
CREATE TABLE IF NOT EXISTS gh_actions (
    id       INTEGER PRIMARY KEY AUTOINCREMENT,
    at       REAL NOT NULL,
    verb     TEXT NOT NULL,
    repo     TEXT NOT NULL,
    external INTEGER NOT NULL DEFAULT 0,
    ref      TEXT,
    detail   TEXT NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS gh_actions_verb_at ON gh_actions (verb, at);
"""

Json = dict[str, Any]

_REPO_NAME = re.compile(r"^[A-Za-z0-9._-]{1,100}$")


# --- the body ------------------------------------------------------------------------------


def _body(body: Any, required: tuple[str, ...], optional: tuple[str, ...] = ()) -> dict[str, Any]:
    if not isinstance(body, Mapping):
        raise hand.HandError(f"stdin must be a JSON object with {', '.join(required)}")
    unknown = sorted(set(body) - {*required, *optional})
    if unknown:
        raise hand.HandError(f"unknown field(s) in the body: {', '.join(unknown)}")
    for key in required:
        if key not in body:
            raise hand.HandError(f'"{key}" is required')
    return dict(body)


def _string(body: Mapping[str, Any], key: str) -> str:
    value = body.get(key)
    if not isinstance(value, str) or not value.strip():
        raise hand.HandError(f'"{key}" must be a non-empty string')
    return value


def _number(body: Mapping[str, Any], key: str) -> int:
    value = body.get(key)
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise hand.HandError(f'"{key}" must be an issue or pull request number')
    return value


# --- the guard ---------------------------------------------------------------------------------


def guarded(fields: Mapping[str, str], kind: str, context: Mapping[str, Any], judge: bool = True) -> dict[str, str]:
    """The guard's say on every field; the fields to publish when it allows.
    `judge=False` is `check_fields`'s: operator text only, never a verb's."""
    verdict = check_fields(dict(fields), surface="github", kind=kind, context=dict(context), judge=judge)
    if verdict.kind == "allow" and isinstance(verdict.fields, dict) and set(verdict.fields) == set(fields):
        return dict(verdict.fields)
    if verdict.kind == "hold":
        raise hand.Refused("held", category=verdict.category, hold_id=verdict.hold_id, field=verdict.field)
    if verdict.kind == "refuse":
        raise hand.Refused(verdict.reason, rewrite=True, field=verdict.field)
    raise hand.HandError(f"the guard answered {verdict.kind!r}, which hands/gh does not publish on")


# --- the ledger ------------------------------------------------------------------------------


def _db():
    return open_db(SCHEMA)


def _record(verb: str, repo: str, external: bool, ref: Any, **detail: Any) -> None:
    """Writes the ledger row for an action that happened. Logged, never
    raised: an error now would invite a retry of something already done."""
    try:
        conn = _db()
        try:
            conn.execute(
                "INSERT INTO gh_actions (at, verb, repo, external, ref, detail) VALUES (?, ?, ?, ?, ?, ?)",
                (hand.now(), verb, repo, int(external), None if ref is None else str(ref), json.dumps(detail, ensure_ascii=False)),
            )
        finally:
            conn.close()
    except Exception as exc:  # noqa: BLE001
        print(f"hands/gh: did {verb} on {repo} but could not record it: {type(exc).__name__}", file=sys.stderr)


def _day_start() -> float:
    now = datetime.fromtimestamp(hand.now(), timezone.utc)
    return now.replace(hour=0, minute=0, second=0, microsecond=0).timestamp()


def external_prs_today() -> int:
    conn = _db()
    try:
        row = conn.execute(
            "SELECT count(*) FROM gh_actions WHERE verb = 'open-pr' AND external = 1 AND at >= ?", (_day_start(),)
        ).fetchone()
    finally:
        conn.close()
    return int(row[0])


def max_external_prs() -> int:
    raw = hand.setting("GH_MAX_EXTERNAL_PRS_PER_DAY")
    try:
        value = int(raw) if raw is not None else DEFAULT_MAX_EXTERNAL_PRS_PER_DAY
    except ValueError:
        return DEFAULT_MAX_EXTERNAL_PRS_PER_DAY
    return max(0, min(value, 20))


# --- the actions --------------------------------------------------------------------------------


def _own_identity(gh: GitHubApi) -> str:
    """The token's account, which must be GITHUB_USER: a token for anybody
    else would create and push under their name."""
    login = gh.whoami()["login"]
    if login.lower() != user().lower():
        raise hand.Refused(f"the token belongs to {login}, not {user()}; nothing was done", rule="identity")
    return login


def create_repo(body: Any, gh: GitHubApi | None = None) -> Json:
    body = _body(body, ("name",), ("description", "private"))
    name = _string(body, "name")
    if not _REPO_NAME.match(name) or name in (".", "..") or name.endswith(".git"):
        raise hand.HandError('"name" must be a plain repository name')
    private = body.get("private", False)
    if not isinstance(private, bool):
        raise hand.HandError('"private" must be true or false')
    description = body.get("description") or ""
    if not isinstance(description, str):
        raise hand.HandError('"description" must be a string')
    repo = f"{user()}/{name}"
    if description.strip():
        description = guarded({"description": description}, "repo", {"repo": repo})["description"]
    if hand.dry_run():
        return {"dry_run": True, "would_create": repo, "description": description, "private": private}
    gh = gh or GitHubApi.from_settings()
    _own_identity(gh)
    made = gh.create_repo(name, description, private)
    _record("create-repo", repo, False, made.get("html_url"), private=private)
    return {"created": made.get("full_name") or repo, "repo": made}


def push(body: Any, gh: GitHubApi | None = None) -> Json:
    body = _body(body, ("workdir", "repo", "branch", "message"))
    path = gitpush.workdir(body["workdir"])
    owner, name = split_repo(body["repo"])
    repo = f"{owner}/{name}"
    if not is_own(owner):
        raise hand.Refused(f"Teeny pushes only to its own repositories ({user()}/...), not {repo}", rule="owner")
    ref = gitpush.branch(body["branch"])
    message = guarded({"message": _string(body, "message")}, "commit", {"repo": repo, "branch": ref})["message"]
    findings = gitpush.scan(path)
    if findings:
        raise hand.Refused("the push would publish what looks like a secret", rule="secrets", findings=findings)
    if hand.dry_run():
        return {"dry_run": True, "would_push": repo, "branch": ref, "message": message, "workdir": str(path)}
    gh = gh or GitHubApi.from_settings()
    login = _own_identity(gh)
    with hand.lock(f"gh-push-{name.lower()}"):
        sha = gitpush.commit_and_push(path, owner, name, ref, message, hand.require("GITHUB_TOKEN"), login)
    _record("push", repo, False, sha, branch=ref)
    return {"pushed": repo, "branch": ref, "commit": sha}


def _check_external(gh: GitHubApi, owner: str, name: str) -> None:
    """The rules for a pull request on somebody else's project, cheapest
    first: the day's cap, one open PR at a time, the project's policy."""
    repo = f"{owner}/{name}"
    cap = max_external_prs()
    if external_prs_today() >= cap:
        raise hand.Refused(f"today's {cap} pull request(s) to other projects are spent", rule="daily_cap")
    open_prs = gh.search_issues(f"repo:{repo} author:{user()} type:pr state:open", 5)
    if int(open_prs.get("total_count") or 0) > 0:
        raise hand.Refused(f"Teeny already has an open pull request in {repo}; one at a time", rule="open_pr")
    verdict = reads.contributing(owner, name, gh)
    if verdict["forbids_ai"]:
        raise hand.Refused(
            f"{repo}'s contributing policy does not welcome AI contributions", rule="contributing", policy=verdict
        )


def open_pr(body: Any, gh: GitHubApi | None = None) -> Json:
    body = _body(body, ("repo", "head", "base", "title", "body"))
    owner, name = split_repo(body["repo"])
    repo = f"{owner}/{name}"
    head = _string(body, "head")
    base = gitpush.branch(_string(body, "base"))
    external = not is_own(owner)
    if ":" in head:
        head_owner, _, head_branch = head.partition(":")
        if not is_own(head_owner):
            raise hand.Refused(f"Teeny opens pull requests from its own branches only, not {head}", rule="owner")
        gitpush.branch(head_branch)
    else:
        gitpush.branch(head)
        if external:
            head = f"{user()}:{head}"
    fields = guarded(
        {"title": _string(body, "title"), "body": _string(body, "body")}, "pr", {"repo": repo, "head": head}
    )
    text = fields["body"].rstrip() + "\n\n" + DISCLOSURE if external else fields["body"]
    if gh is None and (external or not hand.dry_run()):
        gh = GitHubApi.from_settings()
    with hand.lock("gh-pr"):
        if external:
            assert gh is not None
            _check_external(gh, owner, name)
        if hand.dry_run():
            return {
                "dry_run": True,
                "would_open_pr": repo,
                "head": head,
                "base": base,
                "title": fields["title"],
                "body": text,
                "external": external,
            }
        assert gh is not None
        opened = gh.open_pr(owner, name, head, base, fields["title"], text)
        _record("open-pr", repo, external, opened.get("number"), url=opened.get("html_url"), head=head)
    return {"opened": repo, "number": opened.get("number"), "html_url": opened.get("html_url"), "external": external}


def comment(body: Any, gh: GitHubApi | None = None) -> Json:
    body = _body(body, ("repo", "number", "body"))
    owner, name = split_repo(body["repo"])
    repo = f"{owner}/{name}"
    number = _number(body, "number")
    text = guarded({"body": _string(body, "body")}, "comment", {"repo": repo, "number": number})["body"]
    if hand.dry_run():
        return {"dry_run": True, "would_comment": repo, "number": number, "body": text}
    gh = gh or GitHubApi.from_settings()
    made = gh.comment(owner, name, number, text)
    _record("comment", repo, not is_own(owner), made.get("id"), number=number, url=made.get("html_url"))
    return {"commented": repo, "number": number, "html_url": made.get("html_url")}


def _own_repo(value: Any, what: str) -> tuple[str, str, str]:
    owner, name = split_repo(value)
    repo = f"{owner}/{name}"
    if not is_own(owner):
        raise hand.Refused(f"Teeny {what} on its own repositories ({user()}/...) only, not {repo}", rule="owner")
    return owner, name, repo


def open_issue(body: Any, gh: GitHubApi | None = None, *, judge: bool = True) -> Json:
    """`judge=False` is for an operator script's own fixed text (the health
    alarm, bin/lib/health.py), never for anything the command line passes."""
    body = _body(body, ("repo", "title", "body"))
    owner, name, repo = _own_repo(body["repo"], "opens issues")
    fields = guarded({"title": _string(body, "title"), "body": _string(body, "body")}, "issue", {"repo": repo}, judge)
    if hand.dry_run():
        return {"dry_run": True, "would_open_issue": repo, "title": fields["title"], "body": fields["body"]}
    gh = gh or GitHubApi.from_settings()
    opened = gh.open_issue(owner, name, fields["title"], fields["body"])
    _record("open-issue", repo, False, opened.get("number"), url=opened.get("html_url"))
    return {"opened_issue": repo, "number": opened.get("number"), "html_url": opened.get("html_url")}


def close_issue(body: Any, gh: GitHubApi | None = None, *, judge: bool = True) -> Json:
    """`judge=False` as in `open_issue`."""
    body = _body(body, ("repo", "number"), ("comment",))
    owner, name, repo = _own_repo(body["repo"], "closes issues")
    number = _number(body, "number")
    text = None
    if "comment" in body:
        text = guarded({"body": _string(body, "comment")}, "comment", {"repo": repo, "number": number}, judge)["body"]
    if hand.dry_run():
        return {"dry_run": True, "would_close_issue": repo, "number": number, "comment": text}
    gh = gh or GitHubApi.from_settings()
    if text is not None:
        made = gh.comment(owner, name, number, text)
        _record("comment", repo, False, made.get("id"), number=number, url=made.get("html_url"))
    closed = gh.close_issue(owner, name, number)
    _record("close-issue", repo, False, number, url=closed.get("html_url"))
    return {"closed_issue": repo, "number": number, "html_url": closed.get("html_url")}


def fork(body: Any, gh: GitHubApi | None = None) -> Json:
    body = _body(body, ("repo",))
    owner, name = split_repo(body["repo"])
    repo = f"{owner}/{name}"
    if is_own(owner):
        raise hand.Refused(f"{repo} is already Teeny's own", rule="owner")
    if hand.dry_run():
        return {"dry_run": True, "would_fork": repo}
    gh = gh or GitHubApi.from_settings()
    _own_identity(gh)
    made = gh.fork(owner, name)
    _record("fork", repo, True, made.get("full_name"))
    return {"forked": repo, "repo": made}
