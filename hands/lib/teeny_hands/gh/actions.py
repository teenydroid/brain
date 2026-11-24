"""The only way hands/gh writes. Every action runs, in this one invocation:
the body's shape, the guard on every word it would publish
(`check_fields`, surface github), the rules, DRY_RUN, GitHub, and the ledger
(`gh_actions`). A caller that forgets a step cannot skip it.

The rules:
- Teeny creates and pushes to repositories it owns (`GITHUB_USER`) only, and
  pushes only from a folder under state/workbench/ (bin/broker stages a copy
  of a folder of the mind's home there), only that folder's files (never its
  own git history or config), as a commit on top of what the branch has, and
  never anything that looks like a secret (`gitpush.Staged`, `gitpush.scan`).
- A branch of its own clone (`push_branch`) goes only to a teeny/* branch,
  never main, so nothing Teeny pushes there is merged by pushing it.
- A pull request to somebody else's repository carries DISCLOSURE, and is
  refused when that project's policy forbids AI contributions
  (`contributing`), when Teeny already has an open pull request there, or when
  today's `GH_MAX_EXTERNAL_PRS_PER_DAY` (UTC, default 5, 0 off) are spent. An
  issue there is held to the same rules and `GH_MAX_EXTERNAL_ISSUES_PER_DAY`
  (default 5, 0 off): the operator, 2026-11-24, "allow 5 external prs and
  issues".
- Under DRY_RUN nothing is written anywhere: GitHub, git or the ledger. The
  answer says what would have happened (`would_*`), after every rule ran.
"""

from __future__ import annotations

import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
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
#: Pull requests and issues a UTC day on other people's projects, each (the
#: operator, 2026-11-24: "allow 5 external prs and issues"); 0 turns one off.
DEFAULT_MAX_EXTERNAL_PRS_PER_DAY = 5
DEFAULT_MAX_EXTERNAL_ISSUES_PER_DAY = 5
MAX_EXTERNAL_CEILING = 20
#: The only branches `push_branch` writes: a pull request's head, never main.
PR_BRANCH_PREFIX = "teeny/"

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
        raise hand.Refused("held", category=verdict.category, hold_id=verdict.hold_id, field=verdict.field,
                           judge_failed=verdict.judge_failed)
    if verdict.kind == "refuse":
        raise hand.Refused(verdict.reason, rewrite=True, field=verdict.field, hold_id=verdict.hold_id)
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


def _cap(name: str, default: int) -> int:
    """A daily cap from secrets/teeny.env: DEFAULT when unset or unreadable,
    0 (off) to MAX_EXTERNAL_CEILING."""
    raw = hand.setting(name)
    try:
        value = int(raw) if raw is not None else default
    except ValueError:
        return default
    return max(0, min(value, MAX_EXTERNAL_CEILING))


def max_external_prs() -> int:
    return _cap("GH_MAX_EXTERNAL_PRS_PER_DAY", DEFAULT_MAX_EXTERNAL_PRS_PER_DAY)


def external_issues_today() -> int:
    conn = _db()
    try:
        row = conn.execute(
            "SELECT count(*) FROM gh_actions WHERE verb = 'open-issue' AND external = 1 AND at >= ?", (_day_start(),)
        ).fetchone()
    finally:
        conn.close()
    return int(row[0])


def max_external_issues() -> int:
    return _cap("GH_MAX_EXTERNAL_ISSUES_PER_DAY", DEFAULT_MAX_EXTERNAL_ISSUES_PER_DAY)


def _spend_external(kind: str, cap: int, setting: str, used: int) -> None:
    """Refuses when the day's KIND on other projects are off (CAP 0) or spent."""
    if cap == 0:
        raise hand.Refused(f"{kind} on other people's projects are off ({setting}=0)", rule="off")
    if used >= cap:
        raise hand.Refused(f"today's {cap} {kind} on other people's projects are spent ({setting}, UTC day); "
                           "the next opens at 00:00 UTC", rule="daily_cap")


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
    """A folder under state/workbench/ as the next commit of BRANCH of an own
    repository (gitpush.Staged): on top of what that branch has, or for a new
    branch on top of BASE when given (a fork's main, for a pull request).
    PARENT, when given, is the commit the folder was made from: a branch (or
    base) that has moved since is refused, never laid over."""
    body = _body(body, ("workdir", "repo", "branch", "message"), ("base", "parent"))
    path = gitpush.workdir(body["workdir"])
    owner, name = split_repo(body["repo"])
    repo = f"{owner}/{name}"
    if not is_own(owner):
        raise hand.Refused(f"Teeny pushes only to its own repositories ({user()}/...), not {repo}", rule="owner")
    ref = gitpush.branch(body["branch"])
    base = gitpush.branch(body["base"]) if "base" in body else None
    expect = body.get("parent")
    if expect is not None and not (isinstance(expect, str) and _SHA.match(expect.lower())):
        raise hand.HandError('"parent" is the full 40-character commit the folder was made from')
    expect = expect.lower() if expect else None
    message = guarded({"message": _string(body, "message")}, "commit", {"repo": repo, "branch": ref})["message"]
    rehearsal = hand.dry_run()
    # Reading the branch needs the token for a private repository, and the
    # push needs it; a rehearsal reads with it when it is there.
    token = hand.setting("GITHUB_TOKEN") if rehearsal else hand.require("GITHUB_TOKEN")
    with hand.lock(f"gh-push-{name.lower()}"):
        staged = gitpush.Staged(path, owner, name, ref, base, token, None, rehearsal, expect)
        try:
            if staged.findings:
                raise hand.Refused("the push would publish what looks like a secret", rule="secrets",
                                   findings=staged.findings)
            where = {"onto": staged.onto, "parent": staged.parent, "changed": staged.changed,
                     **({"note": staged.note} if staged.note else {})}
            if rehearsal:
                return {"dry_run": True, "would_push": repo, "branch": ref, "message": message, "workdir": str(path),
                        **where}
            staged.user = _own_identity(gh or GitHubApi.from_settings())
            sha = staged.commit_and_push(message)
        finally:
            staged.close()
    _record("push", repo, False, sha, branch=ref, onto=staged.onto)
    return {"pushed": repo, "branch": ref, "commit": sha, **where}


def push_branch(git_dir: str | Path, repo: str, branch: str, since: str, gh: GitHubApi | None = None,
                expect: str | None = None) -> Json:
    """Pushes BRANCH of the clone whose git directory is GIT_DIR (under
    state/) to the same branch of an own repository, for a pull request. Only
    a teeny/* branch, never forced, and refused when what it adds to SINCE
    looks like a secret. In-process only (Teeny's own clone,
    teeny_hands.build.selfclone): no verb takes a git directory. EXPECT, the
    branch's head as the caller read it, makes the push land only while the
    branch is still there (gitpush.push_ref)."""
    owner, name = split_repo(repo)
    repo = f"{owner}/{name}"
    if not is_own(owner):
        raise hand.Refused(f"Teeny pushes only to its own repositories ({user()}/...), not {repo}", rule="owner")
    ref = gitpush.branch(branch)
    if not ref.startswith(PR_BRANCH_PREFIX):
        raise hand.Refused(f"a clone's branch is pushed only as {PR_BRANCH_PREFIX}<name>, never {ref}", rule="branch")
    path = Path(git_dir).resolve()
    if hand.state_dir().resolve() not in path.parents:
        raise hand.Refused("a clone's git directory lives under state/", rule="git_dir")
    findings = gitpush.scan_branch(path, since, ref)
    if findings:
        raise hand.Refused("the push would publish what looks like a secret", rule="secrets", findings=findings)
    if hand.dry_run():
        return {"dry_run": True, "would_push": repo, "branch": ref, "commit": gitpush.head_of(path, ref)}
    gh = gh or GitHubApi.from_settings()
    login = _own_identity(gh)
    with hand.lock(f"gh-push-{name.lower()}"):
        sha = gitpush.push_ref(path, owner, name, ref, hand.require("GITHUB_TOKEN"), login, expect=expect)
    _record("push", repo, False, sha, branch=ref)
    return {"pushed": repo, "branch": ref, "commit": sha}


def _check_external(gh: GitHubApi, owner: str, name: str) -> None:
    """The rules for a pull request on somebody else's project, cheapest
    first: the day's cap, one open PR at a time, the project's policy."""
    repo = f"{owner}/{name}"
    _spend_external("pull requests", max_external_prs(), "GH_MAX_EXTERNAL_PRS_PER_DAY", external_prs_today())
    open_prs = gh.search_issues(f"repo:{repo} author:{user()} type:pr state:open", 5)
    if int(open_prs.get("total_count") or 0) > 0:
        raise hand.Refused(f"Teeny already has an open pull request in {repo}; one at a time", rule="open_pr")
    verdict = reads.contributing(owner, name, gh)
    if verdict["forbids_ai"]:
        raise hand.Refused(
            f"{repo}'s contributing policy does not welcome AI contributions", rule="contributing", policy=verdict
        )


def check_external(gh: GitHubApi, owner: str, name: str) -> None:
    """The external rules, for a caller that must know before it pushes
    (an upstream build job, teeny_hands.build.upstream). Reads GitHub only;
    `open_pr` applies them again under its lock."""
    if is_own(owner):
        raise hand.Refused(f"{owner}/{name} is Teeny's own, not another project", rule="owner")
    _check_external(gh, owner, name)


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


#: The commit statuses Teeny sets, and on what: only its own repositories,
#: only a `teeny/...` context (bin/evolve's `teeny/eval`).
STATUS_STATES = ("success", "failure", "pending", "error")
_SHA = re.compile(r"^[0-9a-f]{40}$")


def set_status(body: Any, gh: GitHubApi | None = None) -> Json:
    """A commit status on SHA of an own repository: bin/evolve's `teeny/eval`
    on a candidate's head, which the babysitter reads before it merges.
    In-process only (no verb sets one): the description is fixed text of
    an operator script, so the guard has nothing of a model's to read."""
    body = _body(body, ("repo", "sha", "state", "context", "description"))
    owner, name, repo = _own_repo(body["repo"], "sets commit statuses")
    sha = _string(body, "sha").lower()
    if not _SHA.match(sha):
        raise hand.HandError("a commit status needs a full 40-character sha")
    state = _string(body, "state")
    if state not in STATUS_STATES:
        raise hand.HandError(f"a commit status is one of {', '.join(STATUS_STATES)}, not {state!r}")
    context = _string(body, "context")
    if not context.startswith(PR_BRANCH_PREFIX):
        raise hand.Refused(f"Teeny sets only its own {PR_BRANCH_PREFIX}... statuses, not {context}", rule="context")
    description = _string(body, "description")[:140]
    if hand.dry_run():
        return {"dry_run": True, "would_set_status": repo, "sha": sha, "state": state, "context": context,
                "description": description}
    gh = gh or GitHubApi.from_settings()
    # The babysitter counts a teeny/eval status only from GITHUB_USER.
    _own_identity(gh)
    got = gh.set_status(owner, name, sha, state, context, description)
    _record("set-status", repo, False, sha, state=state, context=context)
    return {"status": repo, "sha": sha, "state": state, "context": context, "id": got.get("id")}


def rejection(owner: str, name: str, number: int, gh: GitHubApi) -> Json | None:
    """When `number` on someone else's repository is a pull request of
    Teeny's that was closed without being merged, the guard context that
    says so (`{"people": [...]}`: the repository's owner); else None. A
    comment there that names anybody is then held for a person
    (teeny_hands.guard.gate, category `rejection`; evals/invariants.md,
    no-reprisal)."""
    if is_own(owner):
        return None
    pr = gh.pr(owner, name, number)
    if pr is None or not isinstance(pr.get("user"), str) or not is_own(pr["user"]):
        return None
    if pr.get("state") != "closed" or pr.get("merged"):
        return None
    return {"people": [owner]}


def comment(body: Any, gh: GitHubApi | None = None, *, judge: bool = True) -> Json:
    """`judge=False` as in `open_issue`: an operator script's own text."""
    body = _body(body, ("repo", "number", "body"))
    owner, name = split_repo(body["repo"])
    repo = f"{owner}/{name}"
    number = _number(body, "number")
    context: Json = {"repo": repo, "number": number}
    if not is_own(owner) and not hand.dry_run():
        # A rehearsal asks GitHub nothing, so it cannot tell a rejection;
        # a live comment always can.
        gh = gh or GitHubApi.from_settings()
        rejected = rejection(owner, name, number, gh)
        if rejected is not None:
            context["rejection"] = rejected
    text = guarded({"body": _string(body, "body")}, "comment", context, judge)["body"]
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
    alarm, bin/lib/health.py), never for anything the command line passes.
    `labels` (optional, up to 5) are added after it opens; one the repository
    refuses never fails the call (`labelled` says what stuck)."""
    body = _body(body, ("repo", "title", "body"), ("labels",))
    owner, name = split_repo(body["repo"])
    if not is_own(owner):
        return _open_external_issue(body, owner, name, gh, judge)
    owner, name, repo = _own_repo(body["repo"], "opens issues")
    labels = body.get("labels")
    if labels is not None and not (isinstance(labels, list) and 1 <= len(labels) <= 5
                                   and all(isinstance(x, str) and 0 < len(x) <= 50 for x in labels)):
        raise hand.HandError('"labels" must be a list of 1 to 5 label names')
    fields = guarded({"title": _string(body, "title"), "body": _string(body, "body")}, "issue", {"repo": repo}, judge)
    if hand.dry_run():
        return {"dry_run": True, "would_open_issue": repo, "title": fields["title"], "body": fields["body"],
                **({"labels": labels} if labels else {})}
    gh = gh or GitHubApi.from_settings()
    opened = gh.open_issue(owner, name, fields["title"], fields["body"])
    _record("open-issue", repo, False, opened.get("number"), url=opened.get("html_url"))
    out = {"opened_issue": repo, "number": opened.get("number"), "html_url": opened.get("html_url")}
    if labels and opened.get("number"):
        # Asked for, never required (as hands/ladder labels its proposals): a
        # label the repository does not have leaves the issue open, unlabelled.
        try:
            gh.call("POST", f"/repos/{owner}/{name}/issues/{int(opened['number'])}/labels", body={"labels": labels})
            out["labelled"] = list(labels)
        except Exception:  # noqa: BLE001 - the issue is open either way
            out["labelled"] = []
    return out


def _open_external_issue(body: Mapping[str, Any], owner: str, name: str, gh: GitHubApi | None, judge: bool) -> Json:
    """An issue on somebody else's project (docs/mind.md, "Reach"): through
    the guard, carrying DISCLOSURE like a pull request there, and under the
    same kind of rules, cheapest first: the day's cap
    (`GH_MAX_EXTERNAL_ISSUES_PER_DAY`), one open issue of Teeny's a project,
    the project's own policy on AI contributions. No labels: those are the
    maintainers'. A rehearsal asks GitHub nothing."""
    repo = f"{owner}/{name}"
    if max_external_issues() == 0:
        _spend_external("issues", 0, "GH_MAX_EXTERNAL_ISSUES_PER_DAY", 0)  # refuses, saying so
    fields = guarded({"title": _string(body, "title"), "body": _string(body, "body")}, "issue", {"repo": repo}, judge)
    text = fields["body"].rstrip() + "\n\n" + DISCLOSURE
    if hand.dry_run():
        return {"dry_run": True, "would_open_issue": repo, "title": fields["title"], "body": text, "external": True}
    # The cap and the one-open-issue rule are read, then spent: one lock across
    # both, the one open_pr holds, so two calls cannot both pass.
    with hand.lock("gh-pr"):
        _spend_external("issues", max_external_issues(), "GH_MAX_EXTERNAL_ISSUES_PER_DAY", external_issues_today())
        gh = gh or GitHubApi.from_settings()
        open_issues = gh.search_issues(f"repo:{repo} author:{user()} type:issue state:open", 5)
        if open_issues.get("incomplete_results"):
            # GitHub's search timed out: a zero count proves nothing.
            raise hand.Refused(f"could not tell whether Teeny has an open issue in {repo}; try again later",
                               rule="open_issue")
        if int(open_issues.get("total_count") or 0) > 0:
            raise hand.Refused(f"Teeny already has an open issue in {repo}; one at a time", rule="open_issue")
        verdict = reads.contributing(owner, name, gh)
        if verdict["forbids_ai"]:
            raise hand.Refused(
                f"{repo}'s contributing policy does not welcome AI contributions", rule="contributing", policy=verdict
            )
        opened = gh.open_issue(owner, name, fields["title"], text)
        _record("open-issue", repo, True, opened.get("number"), url=opened.get("html_url"))
        return {"opened_issue": repo, "number": opened.get("number"), "html_url": opened.get("html_url"), "external": True}


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


def edit_pr(body: Any, gh: GitHubApi | None = None) -> Json:
    """A new title or body (or both) on a pull request of an own repository,
    through the guard. In-process only (the mind's clone revising its own
    pull request, teeny_hands.build.mindclone): no verb edits one."""
    body = _body(body, ("repo", "number"), ("title", "body"))
    owner, name, repo = _own_repo(body["repo"], "edits pull requests")
    number = _number(body, "number")
    given = {key: _string(body, key) for key in ("title", "body") if key in body}
    if not given:
        raise hand.HandError('an edit sets "title", "body" or both')
    fields = guarded(given, "pr", {"repo": repo, "number": number})
    if hand.dry_run():
        return {"dry_run": True, "would_edit_pr": repo, "number": number, **fields}
    gh = gh or GitHubApi.from_settings()
    edited = gh.edit_pr(owner, name, number, fields)
    _record("edit-pr", repo, False, number, fields=sorted(fields), url=edited.get("html_url"))
    return {"edited_pr": repo, "number": number, "html_url": edited.get("html_url")}


def close_pr(body: Any, gh: GitHubApi | None = None) -> Json:
    """Closes a pull request of an own repository, never merging it, after an
    optional last comment through the guard. In-process only (the mind
    closing its own pull request, teeny_hands.build.mindclone, which also
    holds who wrote it and which branch it is on)."""
    body = _body(body, ("repo", "number"), ("comment",))
    owner, name, repo = _own_repo(body["repo"], "closes pull requests")
    number = _number(body, "number")
    text = None
    if "comment" in body:
        text = guarded({"body": _string(body, "comment")}, "comment", {"repo": repo, "number": number})["body"]
    if hand.dry_run():
        return {"dry_run": True, "would_close_pr": repo, "number": number, "comment": text}
    gh = gh or GitHubApi.from_settings()
    if text is not None:
        made = gh.comment(owner, name, number, text)
        _record("comment", repo, False, made.get("id"), number=number, url=made.get("html_url"))
    closed = gh.close_pr(owner, name, number)
    _record("close-pr", repo, False, number, url=closed.get("html_url"))
    return {"closed_pr": repo, "number": number, "html_url": closed.get("html_url")}


def reply_thread(body: Any, gh: GitHubApi | None = None) -> Json:
    """A reply in a review thread of an own repository's pull request,
    through the guard. In-process only (the babysitter's agent's answers,
    teeny_hands.babysit.sitter, which holds whose pull request and which
    threads): no verb replies in one."""
    body = _body(body, ("repo", "number", "thread_id", "body"))
    _, _, repo = _own_repo(body["repo"], "answers review threads")
    number, thread = _number(body, "number"), _string(body, "thread_id")
    text = guarded({"body": _string(body, "body")}, "comment", {"repo": repo, "number": number})["body"]
    if hand.dry_run():
        return {"dry_run": True, "would_reply": repo, "number": number, "thread_id": thread, "body": text}
    made = (gh or GitHubApi.from_settings()).reply_thread(thread, text)
    _record("reply-thread", repo, False, thread, number=number, url=made.get("html_url"))
    return {"replied": repo, "number": number, "thread_id": thread, "html_url": made.get("html_url")}


def resolve_thread(body: Any, gh: GitHubApi | None = None) -> Json:
    """Resolves a review thread of an own repository's pull request.
    In-process only, as `reply_thread`."""
    body = _body(body, ("repo", "number", "thread_id"))
    _, _, repo = _own_repo(body["repo"], "resolves review threads")
    number, thread = _number(body, "number"), _string(body, "thread_id")
    if hand.dry_run():
        return {"dry_run": True, "would_resolve": repo, "number": number, "thread_id": thread}
    done = (gh or GitHubApi.from_settings()).resolve_thread(thread)
    _record("resolve-thread", repo, False, thread, number=number)
    return {"resolved": bool(done.get("resolved")), "number": number, "thread_id": thread}


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
