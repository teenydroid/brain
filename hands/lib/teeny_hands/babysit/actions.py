"""The only way hands/babysit writes. Every write runs, in this one
invocation: the watched-repository check, its switch, a fresh audit, the
rules, DRY_RUN, GitHub, and the ledger (`babysit_actions`).

  triage  the verdict as labels (`babysit:<verdict>`, and `security`,
          `prompt-injection`, `frozen-path` when they apply) and, once per
          head and verdict, the audit as a comment through hands/gh's
          `comment` (the guard, DRY_RUN, the gh ledger). Off unless BABYSIT=1.
  merge   a pull request whose fresh audit is `ready`, at the head the
          caller named, into the default branch, under the merge lock and
          BABYSIT_MERGES_PER_DAY. Off unless BABYSIT_MERGE=1.
  sweep   both, for every open pull request on the watched repositories.

The comment is the babysitter's own words: rules, places and check names.
It never quotes the pull request, so a payload is not repeated back where a
model on the other side would read it.
"""

from __future__ import annotations

from typing import Any, Mapping

from .. import hand
from ..gh import actions as gh_actions
from . import audit as auditing
from . import ledger, policy
from .api import PullsApi

Json = dict[str, Any]

LABEL_PREFIX = "babysit:"
DEFAULT_MERGES_PER_DAY = 3
MERGE_METHODS = ("merge", "squash", "rebase")

_HEADLINES = {
    "blocked": "blocked: the babysitter will not merge this, approved or not.",
    "failing": "failing: something is red or in conflict.",
    "needs_operator": "needs the operator: an operator has to approve this exact head before it can merge.",
    "needs_review": "needs a review: a maintainer has to approve this exact head before it can merge.",
    "waiting": "waiting: checks are running or GitHub is still working it out.",
    "ready": "ready: approved at this head, green, nothing flagged.",
}


def _body(body: Any, required: tuple[str, ...], optional: tuple[str, ...] = ()) -> dict[str, Any]:
    if not isinstance(body, Mapping):
        raise hand.HandError(f"stdin must be a JSON object with {', '.join(required)}")
    unknown = sorted(set(body) - {*required, *optional})
    if unknown:
        raise hand.HandError(f"unknown field(s) in the body: {', '.join(unknown)}")
    for key in required:
        if key not in body:
            raise hand.HandError(f'"{key}" is required')
    number = body.get("number")
    if "number" in required and (isinstance(number, bool) or not isinstance(number, int) or number < 1):
        raise hand.HandError('"number" must be a pull request number')
    return dict(body)


# --- what the audit says, as labels and words -----------------------------------------------


def labels_for(result: Mapping[str, Any]) -> list[str]:
    rules = {str(f["rule"]) for f in result.get("findings", [])}
    labels = [LABEL_PREFIX + str(result["verdict"]).replace("_", "-")]
    if result.get("injections"):
        labels.append("prompt-injection")
    if rules & {"secret", "secret_path", "injection:bidi_control", "injection:unicode_tags"}:
        labels.append("security")
    if "frozen" in rules:
        labels.append("frozen-path")
    return labels


def report(result: Mapping[str, Any]) -> str:
    """The audit as a comment: fixed words, rule names, paths and check names."""
    lines = [f"**babysitter** · {_HEADLINES.get(str(result['verdict']), str(result['verdict']))}", ""]
    sha = str(result.get("head_sha") or "")[:12]
    lines.append(f"head `{sha}` · author tier `{result.get('tier')}`")
    if result.get("approved_by"):
        lines.append("approved at this head by " + ", ".join(f"`{who}`" for who in result["approved_by"]))
    findings = [f for f in result.get("findings", []) if f.get("severity") != "note"]
    notes = [f for f in result.get("findings", []) if f.get("severity") == "note"]
    if findings:
        lines += ["", "| severity | rule | where |", "|---|---|---|"]
        for f in findings:
            lines.append(f"| {f['severity']} | `{f['rule']}` | `{f['where']}` |")
    if notes:
        lines += ["", "notes: " + ", ".join(f"`{f['rule']}`" for f in notes)]
    if result.get("injections"):
        lines += ["", f"{result['injections']} prompt-injection pattern(s) found and logged. "
                      "the babysitter does not quote them; an operator reads the pull request itself."]
    lines += ["", "_automated audit by hands/babysit. a person approves; the babysitter only presses the button._"]
    return "\n".join(lines)


# --- triage -----------------------------------------------------------------------------------


def _switch(name: str, what: str) -> None:
    if not hand.flag(name, False):
        raise hand.Refused(f"{what} is off ({name} in secrets/teeny.env)", rule=f"{name.lower()}_off")


def triage_result(result: Mapping[str, Any], api: PullsApi) -> Json:
    """Labels and the comment for an audit already made."""
    repo, number, sha = str(result["repo"]), int(result["number"]), result.get("head_sha")
    if result.get("verdict") == "closed":
        return {"repo": repo, "number": number, "skipped": "closed"}
    owner, name, _ = policy.watched(repo)
    want = labels_for(result)
    have = [str(label) for label in result.get("labels", [])]
    stale = [label for label in have if label.startswith(LABEL_PREFIX) and label not in want]
    add = [label for label in want if label not in have]
    key = f"{sha}:{result['verdict']}"
    commented = ledger.acted("comment", repo, number, key)
    out: Json = {"repo": repo, "number": number, "verdict": result["verdict"]}
    if hand.dry_run():
        return {**out, "dry_run": True, "would_label": add, "would_unlabel": stale,
                "would_comment": not commented, "body": report(result)}
    for label in stale:
        api.remove_label(owner, name, number, label)
    if add:
        api.add_labels(owner, name, number, add)
    if add or stale:
        ledger.record_action("label", repo, number, sha, added=add, removed=stale)
    out.update(labelled=add, unlabelled=stale)
    if not commented:
        posted = gh_actions.comment({"repo": repo, "number": number, "body": report(result)}, api.gh)
        ledger.record_action("comment", repo, number, key, url=posted.get("html_url"))
        out["commented"] = posted.get("html_url")
    return out


def triage(body: Any, api: PullsApi | None = None) -> Json:
    body = _body(body, ("repo", "number"))
    _, _, repo = policy.watched(str(body["repo"]))
    _switch("BABYSIT", "triage")
    api = api or PullsApi()
    result = auditing.audit(repo, body["number"], api)
    return {**triage_result(result, api), "audit": result}


# --- merge ------------------------------------------------------------------------------------


def merges_per_day() -> int:
    return hand.number("BABYSIT_MERGES_PER_DAY", DEFAULT_MERGES_PER_DAY, 1, 10)


def merge(body: Any, api: PullsApi | None = None) -> Json:
    body = _body(body, ("repo", "number", "head_sha"))
    owner, name, repo = policy.watched(str(body["repo"]))
    number, sha = int(body["number"]), body["head_sha"]
    if not isinstance(sha, str) or len(sha) != 40:
        raise hand.HandError('"head_sha" must be the full 40-character head the caller audited')
    _switch("BABYSIT_MERGE", "merging")
    method = (hand.setting("BABYSIT_MERGE_METHOD", "merge") or "merge").lower()
    if method not in MERGE_METHODS:
        method = "merge"
    api = api or PullsApi()
    with hand.lock("babysit-merge"):
        done = ledger.count_since("merge", hand.day_start())
        if done >= merges_per_day():
            raise hand.Refused(f"{done} merges today already (BABYSIT_MERGES_PER_DAY)", rule="daily_cap")
        result = auditing.audit(repo, number, api)
        if result["verdict"] != "ready":
            raise hand.Refused(f"the audit says {result['verdict']}, not ready", rule="not_ready",
                               verdict=result["verdict"], findings=result["findings"])
        if result["head_sha"] != sha:
            raise hand.Refused("the head moved since the caller's audit", rule="head_moved")
        default = (api.gh.repo(owner, name) or {}).get("default_branch")
        if not default or result.get("base") != default:
            raise hand.Refused("the babysitter merges into the default branch only", rule="base")
        if hand.dry_run():
            return {"dry_run": True, "would_merge": repo, "number": number, "head_sha": sha, "method": method,
                    "approved_by": result["approved_by"]}
        merged = api.merge(owner, name, number, sha, method)
        if merged.get("merged"):
            ledger.record_action("merge", repo, number, sha, merge_sha=merged.get("sha"), method=method,
                                 approved_by=result["approved_by"], audit_id=result.get("audit_id"))
        return {"repo": repo, "number": number, "merged": bool(merged.get("merged")), "merge_sha": merged.get("sha"),
                "approved_by": result["approved_by"]}


# --- the sweep (the job) -------------------------------------------------------------------------


def sweep(max_results: int = 20, api: PullsApi | None = None) -> Json:
    """Every open pull request on the watched repositories: audited always,
    triaged when BABYSIT=1, merged when BABYSIT_MERGE=1 and it is ready. One
    pull request's error does not stop the rest."""
    api = api or PullsApi()
    listed = auditing.queue(None, max_results, api)
    triaged, merged, errors = [], [], []
    for result in listed["pulls"]:
        if "verdict" not in result:
            errors.append(result)
            continue
        try:
            if hand.flag("BABYSIT", False):
                triaged.append(triage_result(result, api))
            if hand.flag("BABYSIT_MERGE", False) and result["verdict"] == "ready":
                merged.append(merge({"repo": result["repo"], "number": result["number"],
                                     "head_sha": result["head_sha"]}, api))
        except hand.Refused as refused:
            errors.append({"repo": result["repo"], "number": result["number"], "refused": refused.reason,
                           **{k: v for k, v in refused.detail.items() if k in ("rule", "verdict")}})
        except hand.HandError as exc:
            errors.append({"repo": result["repo"], "number": result["number"], "error": str(exc)})
    return {"counts": listed["counts"], "audited": len(listed["pulls"]), "triaged": triaged, "merged": merged,
            "errors": errors}
