"""The operator's writes to slop.cash. Each signs in itself, in process
(`signin`), so they are for a person at the box and never for an agent.

post-job edits the listing: a JSON file of the fields to change, checked here
before anything is sent, a preview unless confirmed (and under DRY_RUN), then
the full current manifest with only those fields changed, PUT against the
revision it was read at. slop.cash keeps status and listing tier itself;
presentation fields publish at once, a changed `contribution` pauses the
project and queues a rescan.

check runs slop.cash's setup checks (it activates the project when they pass),
and setup lists the signed-in account's projects."""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any, Mapping

from .. import hand
from ..rest import RestError, quote
from . import client, reads, signin

PRESENTATION = ("name", "eyebrow", "headline", "description", "links")
EDITABLE = PRESENTATION + ("contribution",)
#: slop.cash answers these 422 owner_field_restricted: they stay with its admins.
RESTRICTED = ("steward", "authority", "terms", "status", "listingTier", "repositories", "skill", "reviewSkill")
CONTRIBUTION = (
    "approvedScope", "contributionGuide", "supportedTasks", "requiredTests",
    "reviewExpectations", "securityContact", "closurePolicy", "maintainerNotes",
)  # fmt: skip
REASON_CHARS = 500
_MESSAGE_CHARS = 200

#: What to do about each of slop.cash's refusals.
HINTS = {
    "stale_revision": "the project changed since it was read; run post-job again",
    "no_change": "the edit changes nothing",
    "owner_field_restricted": "that field changes only through slop.cash's admins",
    "slopbot_required": f"install the Slopbot GitHub App on the repository ({client.SLOPBOT_INSTALL_URL}), then try again",
    "authority_required": "the signed-in account needs write access to the repository",
    "origin_forbidden": "slop.cash refused the request's origin; SLOPCASH_API_BASE must be slop.cash itself",
    "session_expired": "the session ended; run the verb again",
    "unauthorized": "the session ended; run the verb again",
    "project_restricted": "the project is restricted; only slop.cash's moderators can lift that",
    "invalid_manifest": "slop.cash found the edited manifest invalid",
    "github_unavailable": "slop.cash could not reach GitHub; try again",
}

Json = dict[str, Any]


def _refused_by_slopcash(error: RestError) -> hand.HandError:
    code = client.error_code(error)
    if code is None:
        return error
    body = error.body if isinstance(error.body, Mapping) else {}
    said = body.get("message") if isinstance(body.get("message"), str) else ""
    detail = f" ({said.strip()[:_MESSAGE_CHARS]})" if said.strip() else ""
    hint = HINTS.get(code)
    return hand.HandError(f"slop.cash refused: {code}{detail}" + (f": {hint}" if hint else ""))


def _call(session: signin.Session, method: str, path: str, body: Any = None) -> Any:
    try:
        return session.call(method, path, body)
    except RestError as error:
        raise _refused_by_slopcash(error) from None


# --- the edit ------------------------------------------------------------------------------


def _load(path: str) -> Json:
    try:
        text = Path(path).read_text(encoding="utf-8")
    except OSError as exc:
        raise hand.HandError(f"cannot read --file: {exc.strerror or type(exc).__name__}") from None
    try:
        edit = json.loads(text)
    except json.JSONDecodeError as exc:
        raise hand.HandError(f"--file is not JSON: {exc.msg}") from None
    if not isinstance(edit, dict) or not edit:
        raise hand.HandError("--file must be a JSON object of the fields to change")
    return edit


def _checked(edit: Json) -> Json:
    """The edit, or a refusal (a field the owner may not change, or one that
    is not the listing) or an error (a value of the wrong kind)."""
    restricted = [f for f in edit if f in RESTRICTED]
    if restricted:
        raise hand.Refused("owner_field_restricted", rule="owner_field_restricted", fields=restricted)
    other = [f for f in edit if f not in EDITABLE]
    contribution = edit.get("contribution")
    if isinstance(contribution, dict):
        other += [f"contribution.{k}" for k in contribution if k not in CONTRIBUTION]
    if other:
        raise hand.Refused("not_editable", rule="not_editable", fields=other, editable=list(EDITABLE))
    for field in ("name", "eyebrow", "headline", "description"):
        if field in edit and (not isinstance(edit[field], str) or not edit[field].strip()):
            raise hand.HandError(f'"{field}" must be non-empty text')
    if "links" in edit:
        links = edit["links"]
        if not isinstance(links, dict) or not all(isinstance(k, str) and isinstance(v, str) for k, v in links.items()):
            raise hand.HandError('"links" must be an object of names to URLs')
    if "contribution" in edit:
        if not isinstance(contribution, dict) or not contribution:
            raise hand.HandError('"contribution" must be an object of the contribution fields to change')
        for key, value in contribution.items():
            if not isinstance(value, str):
                raise hand.HandError(f'"contribution.{key}" must be text')
    return edit


def apply(current: Mapping[str, Any], edit: Json) -> tuple[Json, list[Json]]:
    """The manifest with the edit applied (a partial `contribution` merges over
    the current one; every other field is replaced whole), and what changed."""
    manifest = copy.deepcopy(dict(current))
    changes: list[Json] = []
    for field, value in edit.items():
        if field == "contribution":
            merged = dict(manifest.get("contribution") or {})
            for key, text in value.items():
                if merged.get(key) != text:
                    changes.append({"field": f"contribution.{key}", "before": merged.get(key), "after": text})
                merged[key] = text
            manifest["contribution"] = merged
        else:
            if manifest.get(field) != value:
                changes.append({"field": field, "before": manifest.get(field), "after": value})
            manifest[field] = copy.deepcopy(value)
    return manifest, changes


def _base(pid: str) -> tuple[Json, int]:
    got = reads.fetch(pid)
    revision = got.get("revision")
    if not isinstance(revision, int) or isinstance(revision, bool):
        raise hand.HandError("slop.cash did not say which revision the project is at")
    return dict(got["project"]), revision


def post_job(path: str, reason: str | None, confirm: bool) -> Json:
    pid = client.project_id(None)
    edit = _checked(_load(path))
    if reason is not None and not 1 <= len(reason.strip()) <= REASON_CHARS:
        raise hand.HandError(f"--reason is 1 to {REASON_CHARS} characters")
    if not confirm or hand.dry_run():
        current, revision = _base(pid)
        _, changes = apply(current, edit)
        executable = any(c["field"].startswith("contribution.") for c in changes)
        out: Json = {
            "preview": True,
            "project": pid,
            "revision": revision,
            "changes": changes,
            "pauses_project": executable,
            "queues_rescan": executable,
            "next": "nothing to publish" if not changes else "run it again with --confirm (and DRY_RUN=0) to sign in and publish",
        }
        if hand.dry_run():
            out["dry_run"] = True
        return out
    session = signin.sign_in()
    current, revision = _base(pid)
    manifest, changes = apply(current, edit)
    if not changes:
        return {"unchanged": True, "project": pid, "revision": revision, "signed_in_as": session.login}
    body: Json = {"manifest": manifest, "expectedRevision": revision}
    if reason is not None:
        body["reason"] = reason.strip()
    answer = _call(session, "PUT", f"/api/v1/projects/{quote(pid)}", body)
    answer = answer if isinstance(answer, Mapping) else {}
    project = answer.get("project") if isinstance(answer.get("project"), Mapping) else {}
    setup = answer.get("setup") if isinstance(answer.get("setup"), Mapping) else {}
    return {
        "posted": pid,
        "revision": project.get("revision", answer.get("revision")),
        "status": project.get("status"),
        "changes": answer.get("changes"),
        "setup_state": setup.get("state"),
        "next_action": setup.get("nextAction"),
        "signed_in_as": session.login,
    }


# --- setup ---------------------------------------------------------------------------------


def _setup_summary(setup: Mapping[str, Any]) -> Json:
    checks = setup.get("checks") if isinstance(setup.get("checks"), Mapping) else {}
    return {
        "state": setup.get("state"),
        "status": setup.get("status"),
        "revision": setup.get("revision"),
        "activated_revision": setup.get("activatedRevision"),
        "next_action": setup.get("nextAction"),
        "checks": {name: (c.get("state") if isinstance(c, Mapping) else c) for name, c in checks.items()},
        "funding": setup.get("funding"),
    }


def check(scan: bool, confirm: bool) -> Json:
    pid = client.project_id(None)
    body: Json = {"requestScan": True} if scan else {}
    if not confirm or hand.dry_run():
        out: Json = {"would_check": pid, "request_scan": scan,
                     "next": "run it again with --confirm (and DRY_RUN=0) to sign in and run slop.cash's setup checks"}  # fmt: skip
        if hand.dry_run():
            out["dry_run"] = True
        return out
    session = signin.sign_in()
    answer = _call(session, "POST", f"/api/v1/project-setup/{quote(pid)}/check", body)
    setup = answer.get("setup") if isinstance(answer, Mapping) and isinstance(answer.get("setup"), Mapping) else {}
    return {"checked": pid, **_setup_summary(setup), "signed_in_as": session.login}


def setup() -> Json:
    session = signin.sign_in()
    answer = _call(session, "GET", "/api/v1/project-setup")
    answer = answer if isinstance(answer, Mapping) else {}
    return {"projects": answer.get("projects", []), "applications": answer.get("applications", []), "signed_in_as": session.login}
