"""One pull request, read end to end into findings and a verdict.

A finding is `{rule, severity, where}` and sometimes a `detail` that is
always the babysitter's own words (a rule name, a path, a check's name),
never the pull request's. The severities:

  block     never merged by the babysitter, approved or not
  fail      broken now: a red check, a conflict, changes requested
  operator  merged only with an operator's approval of the current head
  wait      not yet: a check is running, a draft, GitHub still computing
  note      in the report, changes nothing

The verdict is the first that holds, in this order:

  blocked         a block finding
  failing         a fail finding
  needs_operator  an operator finding and no operator approved this head
  needs_review    nobody trusted (an operator or a maintainer, never the
                  author and never Teeny) approved this head, and it is
                  not a self-merge candidate (below)
  waiting         a wait finding
  ready           the babysitter may merge it (`actions.merge`)

Every merge needs a person's approval of the exact head it merges: the
babysitter is the hand on the button (L4, "a person signs off the risky
steps"), never the one who decides the change is good.

One exception, the self-merge path (`self-merge` at L5 in
evals/autonomy.toml; the operator's decision of 2026-11-15, "if the
internal babysitter checks out, we merge"). With BABYSIT_SELF_MERGE=1, a
pull request on teenydroid/brain whose author is GITHUB_USER (tier teeny),
whose head branch is teeny/gen-<n>[-c<i>] on the same repository, skips
`needs_review`: the `teeny/eval` commit status on its exact head, set by
Teeny or an operator, stands in for the approval. A missing or untrusted
one is a wait (`eval_missing`, `eval_untrusted`), a pending one a wait and
a failed one a fail (the ordinary check rules). Nothing else changes: a
block, fail or operator finding stops it exactly as before, so a frozen
path, a sensitive path or an injection in Teeny's own diff still needs the
operator. `self_merge` in the answer says a ready verdict came this way.
"""

from __future__ import annotations

from typing import Any, Callable

from .. import hand
from ..gh import reads as gh_reads
from ..gh import secretscan
from ..gh.client import user
from . import injection, ledger, policy
from .api import PullsApi

Json = dict[str, Any]

SEVERITIES = ("block", "fail", "operator", "wait", "note")
VERDICTS = ("blocked", "failing", "needs_operator", "needs_review", "waiting", "ready")

DEFAULT_MAX_FILES = 100
#: What a path in a finding may look like. A filename is the author's text
#: too: one that is not a plain path is named by its digest instead.
_PLAIN_PATH = __import__("re").compile(r"[A-Za-z0-9._@+/-]{1,200}")
DEFAULT_MAX_LINES = 3000


def _screen(text: str) -> Any:
    """The guard's lexical screen, inbound reading (imported by name, so a
    test can stand in). No model and no hold row: the babysitter keeps its
    own ledger."""
    from ..guard.screen import INBOUND, screen_text

    return screen_text(text, INBOUND)


class _Audit:
    def __init__(self, repo: str, number: int, api: PullsApi) -> None:
        self.owner, self.name, self.repo = policy.watched(repo)
        self.number = number
        self.api = api
        self.findings: list[Json] = []
        self.injections: list[Json] = []
        self._roles: dict[str, str | None] = {}

    # --- helpers -----------------------------------------------------------------------

    def add(self, rule: str, severity: str, where: str, detail: str | None = None) -> None:
        assert severity in SEVERITIES
        finding = {"rule": rule, "severity": severity, "where": where}
        if detail:
            finding["detail"] = detail
        if finding not in self.findings:
            self.findings.append(finding)

    def where(self, path: str) -> str:
        """`path` as a finding may name it: itself when it is a plain path
        that says nothing to a model, else `path#<digest>`."""
        if _PLAIN_PATH.fullmatch(path) and not injection.scan(path.replace("/", " ").replace("-", " ").replace("_", " "), "path"):
            return path
        return f"path#{injection._digest(path)}"

    def role(self, login: str) -> str | None:
        if login not in self._roles:
            self._roles[login] = self.api.permission(self.owner, self.name, login)
        return self._roles[login]

    def tier(self, login: str | None) -> str:
        return policy.tier(login, self.role)

    def injected(self, hits: list[injection.Hit], who: str | None, who_tier: str, severity: Callable[[str], str]) -> None:
        for hit in hits:
            self.injections.append({**hit.as_dict(), "who": who, "tier": who_tier})
            self.add(f"injection:{hit.rule}", severity(hit.rule), hit.where)

    # --- the reads ------------------------------------------------------------------------

    def run(self) -> Json:
        pr = self.api.pull(self.owner, self.name, self.number)
        if pr is None:
            raise hand.Refused(f"{self.repo} has no pull request {self.number}", rule="not_found")
        self.pr = pr
        self.author = pr.get("user")
        self.author_tier = self.tier(self.author)
        self.head_sha = pr.get("head_sha")
        self.evolve = self._is_evolve_pr()
        self.eval_state: str | None = None
        if pr.get("state") != "open":
            return self._answer(closed=True)

        self._prose()
        files, files_cut = self.api.files(self.owner, self.name, self.number)
        self._files(files, files_cut)
        commits, commits_cut = self.api.commits(self.owner, self.name, self.number)
        self._commits(commits, commits_cut)
        self._conversation()
        self._shape()
        self._checks()
        reviews = self.api.reviews(self.owner, self.name, self.number)
        return self._answer(reviews=reviews)

    def _is_evolve_pr(self) -> bool:
        """A self-merge candidate: the switch on, teenydroid/brain, Teeny's
        own pull request from a teeny/gen-* branch of the same repository."""
        pr = self.pr
        return (policy.self_merge_on() and policy.is_brain(self.repo) and self.author_tier == policy.TEENY
                and bool(pr.get("head_repo")) and pr.get("head_repo") == pr.get("base_repo")
                and policy.is_evolve_branch(pr.get("head_ref")))

    def _prose_severity(self, rule: str) -> str:
        if self.author_tier == policy.STRANGER:
            return "block"
        return "operator"

    def _prose(self) -> None:
        """The words on the pull request itself, by its author."""
        for where, text in (("title", self.pr.get("title")), ("body", self.pr.get("body")),
                            ("branch", self.pr.get("head_ref"))):
            if not isinstance(text, str) or not text:
                continue
            self.injected(injection.scan(text, where), self.author, self.author_tier, self._prose_severity)
            if where != "branch":
                found = _screen(text)
                if found is not None:
                    # A judged match (lexicon.JUDGED_TERMS: a bare kill or
                    # bullet word) means nothing held on sight was in the
                    # text; the guard sends that to its judge rather than
                    # holding it, and the babysitter has no judge, so it is a
                    # note. "kill you", "gun" and the rest stay operator.
                    self.add("lexicon", "note" if found.judged else "operator", where, found.category)

    def _diff_severity(self, rule: str) -> str:
        if rule in injection.ALWAYS_HOSTILE:
            return "block"
        return {policy.STRANGER: "block", policy.TEENY: "operator"}.get(self.author_tier, "note")

    def _files(self, files: list[Json], cut: bool) -> None:
        max_files = hand.number("BABYSIT_MAX_FILES", DEFAULT_MAX_FILES, 1, 3000)
        max_lines = hand.number("BABYSIT_MAX_LINES", DEFAULT_MAX_LINES, 1, 100_000)
        lines = sum(int(f.get("additions") or 0) + int(f.get("deletions") or 0) for f in files)
        if cut or len(files) > max_files or lines > max_lines:
            self.add("too_large", "operator", "diff", f"{len(files)} files, {lines} lines")
        if not files:
            self.add("empty_diff", "wait", "diff")
        frozen = policy.frozen_checker(self._frozen_list()) if policy.is_brain(self.repo) else None
        for f in files:
            raw = str(f.get("filename") or "")
            path = self.where(raw)
            status = f.get("status")
            names = [raw] + ([str(f["previous_filename"])] if f.get("previous_filename") else [])
            for name in names:
                if self.where(name) != name:
                    self.add("odd_path", "block" if self.author_tier == policy.STRANGER else "operator",
                             self.where(name), "a filename that is not a plain path")
                self._path(name, status, frozen)
            patch = f.get("patch")
            if patch is None:
                if status != "removed" and (f.get("additions") or f.get("deletions") or status == "added"):
                    self.add("unreadable_diff", "operator", path, "binary or too large for GitHub to show")
                continue
            added = "\n".join(line[1:] for line in patch.splitlines() if line.startswith("+") and not line.startswith("+++"))
            secret = secretscan.content_finding(path, added)
            if secret is not None:
                self.add("secret", "block", path, secret.rule)
            self.injected(injection.scan(added, f"diff:{path}", prose=False), self.author, self.author_tier,
                          self._diff_severity)

    def _frozen_list(self) -> str | None:
        try:
            return self.api.text_file(self.owner, self.name, "evals/frozen.txt")
        except hand.HandError:
            return None

    def _path(self, raw: str, status: Any, frozen: Callable[[str], str | None] | None) -> None:
        path = self.where(raw)
        stranger = self.author_tier == policy.STRANGER
        secret = secretscan.path_finding(raw)
        if secret is not None and status != "removed":
            self.add("secret_path", "block", path, secret.rule)
        what = policy.sensitive(raw)
        if what is not None:
            fork = self.pr.get("head_repo") and self.pr.get("head_repo") != self.pr.get("base_repo")
            self.add("sensitive", "block" if stranger and fork and raw.startswith(".github/") else "operator",
                     path, what)
        if policy.is_test_removal(raw, status):
            self.add("removes_test", "operator", path)
        if frozen is not None:
            pattern = frozen(raw)
            if pattern is not None:
                # Teeny never sends a diff to a frozen path (docs/governance.md,
                # frozen-paths); only the operator merges one.
                severity = "block" if self.author_tier in (policy.TEENY, policy.STRANGER) else "operator"
                self.add("frozen", severity, path, pattern)
        if policy.is_brain(self.repo) and stranger and policy.prompt_surface(raw):
            self.add("prompt_surface", "block", path)
        if policy.is_diary(self.repo):
            self._diary_path(raw, path, status)

    def _diary_path(self, raw: str, path: str, status: Any) -> None:
        if not policy.any_match(raw, policy.DIARY_LAYOUT):
            self.add("diary_layout", "block", path, "not a file the diary has")
        elif policy.any_match(raw, policy.DIARY_APPEND_ONLY):
            if status in ("modified", "removed", "renamed", "changed"):
                self.add("diary_history", "block", path, "written once; an edit breaks every later hash")
            else:
                self.add("diary_data", "operator", path, "bin/receipts writes it, not a pull request")
        elif policy.any_match(raw, policy.DIARY_DATA):
            self.add("diary_data", "operator", path, "bin/receipts writes it, not a pull request")
        elif policy.any_match(raw, policy.DIARY_VERIFIER):
            self.add("diary_verifier", "operator", path, "what makes the receipts checkable")

    def _commits(self, commits: list[Json], cut: bool) -> None:
        if cut:
            self.add("too_many_commits", "operator", "commits")
        unverified = 0
        for c in commits:
            sha = str(c.get("sha") or "")[:12]
            who = c.get("author") or self.author
            who_tier = self.tier(who) if who != self.author else self.author_tier
            self.injected(injection.scan(c.get("message") or "", f"commit:{sha}"), who, who_tier,
                          lambda rule, t=who_tier: "block" if t == policy.STRANGER else "operator")
            secret = secretscan.content_finding(f"commit:{sha}", c.get("message") or "")
            if secret is not None:
                self.add("secret", "block", f"commit:{sha}", secret.rule)
            if not c.get("verified"):
                unverified += 1
            if who and who != self.author and who_tier == policy.STRANGER:
                self.add("foreign_commit", "operator", f"commit:{sha}", "a commit by someone untrusted")
        if unverified:
            self.add("unsigned_commits", "note", "commits", f"{unverified} without a verified signature")

    def _conversation(self) -> None:
        """Comments and review comments, by anyone. Trusted people's words are
        theirs to write; anyone else's are read for a try at the reviewer."""
        for c in self.api.comments(self.owner, self.name, self.number):
            who = c.get("user")
            who_tier = self.tier(who)
            if who_tier in policy.TRUSTED:
                continue
            where = f"{c.get('kind')}:{c.get('id')}"
            self.injected(injection.scan(c.get("body") or "", where, markers=self._markers(who)), who, who_tier,
                          lambda rule: "operator")

    def _markers(self, who: str | None) -> Any:
        """The hidden comments `who` may leave without a `hidden_comment`
        finding: on teenydroid/brain, PR-Agent's markers in comments by the
        login its workflow posts as. A content exemption, not trust: the
        author is still a stranger and every other rule still reads it."""
        if policy.is_brain(self.repo) and who == policy.PR_AGENT_LOGIN:
            return injection.PR_AGENT_MARKERS
        return None

    def _shape(self) -> None:
        pr = self.pr
        if pr.get("draft"):
            self.add("draft", "wait", "pr")
        if pr.get("head_repo") and pr.get("head_repo") != pr.get("base_repo"):
            self.add("from_fork", "note", "pr", str(pr.get("head_repo")))
        if self.author_tier == policy.STRANGER:
            self.add("stranger", "note", "pr", "the author cannot write to this repository")
        mergeable, state = pr.get("mergeable"), pr.get("mergeable_state")
        if mergeable is False or state == "dirty":
            self.add("conflict", "fail", "pr")
        elif mergeable is None:
            self.add("mergeability_unknown", "wait", "pr")
        elif state == "behind":
            self.add("behind_base", "wait", "pr")

    def _checks(self) -> None:
        if not self.head_sha:
            self.add("no_head", "wait", "pr")
            return
        runs = self.api.check_runs(self.owner, self.name, self.head_sha)
        status = self.api.status(self.owner, self.name, self.head_sha)
        red = sorted({str(r["name"]) for r in runs if r.get("conclusion") in gh_reads.RED}
                     | {str(s["context"]) for s in status["statuses"] if s.get("state") in ("failure", "error")})
        pending = sorted({str(r["name"]) for r in runs if r.get("status") != "completed"}
                         | {str(s["context"]) for s in status["statuses"] if s.get("state") == "pending"})
        for name in red:
            self.add("check_red", "fail", f"check:{name}")
        for name in pending:
            self.add("check_pending", "wait", f"check:{name}")
        if not runs and not status["statuses"]:
            self.add("no_checks", "wait", "checks", "nothing has checked this head yet")
        if self.evolve:
            self._eval(status["statuses"])

    def _eval(self, statuses: list[Json]) -> None:
        """teeny/eval on the head of a self-merge candidate. Its pending and
        failed states are already a wait and a fail (above)."""
        where = f"check:{policy.EVAL_CONTEXT}"
        found = [s for s in statuses if s.get("context") == policy.EVAL_CONTEXT]
        if not found:
            self.add("eval_missing", "wait", where, "no teeny/eval status on this head yet")
            return
        self.eval_state = str(found[0].get("state"))
        # The combined status names no creator: who set the newest teeny/eval
        # on this head comes from the statuses list. It must be that newest
        # one, in the same state the combined view shows, so an older trusted
        # status cannot vouch for a newer one someone else set.
        newest = self.api.status_creator(self.owner, self.name, self.head_sha, policy.EVAL_CONTEXT)
        if (newest is None or newest.get("state") != found[0].get("state")
                or self.tier(newest.get("creator")) not in (policy.TEENY, policy.OPERATOR)):
            self.add("eval_untrusted", "wait", where, "set by someone other than Teeny or an operator")

    # --- the verdict -----------------------------------------------------------------------

    def _reviews(self, reviews: list[Json]) -> tuple[list[str], list[str]]:
        """Who approved this head (trusted people other than the author and
        Teeny), and which trusted people's latest word is changes requested."""
        latest: dict[str, Json] = {}
        for r in reviews:
            who = r.get("user")
            if not who or r.get("state") not in ("APPROVED", "CHANGES_REQUESTED", "DISMISSED"):
                continue
            latest[who] = r
            who_tier = self.tier(who)
            if who_tier not in policy.TRUSTED:
                self.injected(injection.scan(r.get("body") or "", f"review:{who}", markers=self._markers(who)),
                              who, who_tier,
                              lambda rule: "operator")
        approved, blocking = [], []
        for who, r in latest.items():
            if who.lower() in (str(self.author or "").lower(), user().lower()):
                continue
            if self.tier(who) not in policy.TRUSTED:
                continue
            if r.get("state") == "APPROVED" and r.get("commit_id") == self.head_sha:
                approved.append(who)
            elif r.get("state") == "CHANGES_REQUESTED":
                blocking.append(who)
        return sorted(approved), sorted(blocking)

    def _answer(self, reviews: list[Json] | None = None, closed: bool = False) -> Json:
        approved: list[str] = []
        self_merge = False
        if closed:
            verdict = "closed"
        else:
            approved, blocking = self._reviews(reviews or [])
            for who in blocking:
                self.add("changes_requested", "fail", f"review:{who}")
            by_operator = [who for who in approved if self.tier(who) == policy.OPERATOR]
            severities = {f["severity"] for f in self.findings}
            if "block" in severities:
                verdict = "blocked"
            elif "fail" in severities:
                verdict = "failing"
            elif "operator" in severities and not by_operator:
                verdict = "needs_operator"
            elif not approved and not self.evolve:
                verdict = "needs_review"
            elif "wait" in severities:
                verdict = "waiting"
            elif not approved and self.eval_state != "success":
                # A self-merge candidate merges unapproved only on a passed
                # teeny/eval, whatever else its status says.
                verdict = "needs_review"
            else:
                verdict = "ready"
                self_merge = not approved
        title_hit = any(f["where"] == "title" for f in self.findings)
        result: Json = {
            "repo": self.repo,
            "number": self.number,
            "state": self.pr.get("state"),
            "author": self.author,
            "tier": self.author_tier,
            "head_sha": self.head_sha,
            "base": self.pr.get("base"),
            # The title is a stranger's text: shown only when nothing in it
            # tried to talk to a model or tripped the screen.
            "title": None if title_hit else self.pr.get("title"),
            "verdict": verdict,
            "approved_by": approved,
            "self_merge": self_merge,
            "eval": self.eval_state,
            "labels": [str(label) for label in self.pr.get("labels") or []],
            "findings": sorted(self.findings, key=lambda f: (SEVERITIES.index(f["severity"]), f["rule"], f["where"])),
            "injections": len(self.injections),
            "html_url": self.pr.get("html_url"),
        }
        if title_hit:
            result["title_withheld"] = True
        result["audit_id"] = ledger.record_audit(result, self.injections)
        return result


def audit(repo: str, number: int, api: PullsApi | None = None) -> Json:
    """The audit of one pull request, recorded in the ledger. Reads only."""
    return _Audit(repo, number, api or PullsApi()).run()


def queue(repo: str | None, max_results: int, api: PullsApi | None = None) -> Json:
    """The watched repositories' open pull requests, each audited."""
    api = api or PullsApi()
    targets = [policy.watched(repo)[2]] if repo else list(policy.repos())
    out = []
    for target in targets:
        owner, name, full = policy.watched(target)
        for pr in api.open_pulls(owner, name, max_results):
            try:
                out.append(audit(full, int(pr["number"]), api))
            except (hand.Refused, hand.HandError) as exc:
                out.append({"repo": full, "number": pr.get("number"), "error": str(exc)})
    order = {v: i for i, v in enumerate(VERDICTS)}
    out.sort(key=lambda a: (order.get(a.get("verdict"), len(order)), a.get("repo"), a.get("number") or 0))
    counts: dict[str, int] = {}
    for a in out:
        counts[a.get("verdict", "error")] = counts.get(a.get("verdict", "error"), 0) + 1
    return {"repos": targets, "counts": counts, "pulls": out}
