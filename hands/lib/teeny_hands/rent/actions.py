"""What hands/rent does: post a bounty for a person to do something in the
world for Teeny, hire one applicant, review what they send, pay them, and
pass everything they say on to the mind as data.

The life of a rent job (store.py names the statuses):

1. `post` checks the brief (the safety lines below, then the outbound guard),
   asks RentAHuman for a dry run to learn the all-in cost (the worker's pay
   plus the service fee), holds it to RENT_MAX_PER_BOUNTY_USD and to what is
   left of RENT_MAX_PER_DAY_USD, then posts it once with the idempotency key
   `teeny-rent-<job>`. RentAHuman pays for the bounty from its wallet at
   that moment: posting is the only call that spends.
2. `poll` (the pinned rent-poll job) brings applications, submitted work and
   the worker's messages to the mind as `rent` stimuli, every stranger's
   word through the inbound guard first. A bounty nobody picked from within
   RENT_AUTO_ACCEPT_HOURS gets RentAHuman's best match.
3. `accept` hires one applicant from the money already held.
4. `review` approves (and pays), asks for a redo, or rejects, against the
   bounty's done-criteria. Paying is final and is never sent twice: the row
   is `paying` before the call, and a pay whose outcome is unknown becomes
   `in_doubt` for the operator, never a retry.

Under DRY_RUN every verb reads and rehearses: `post` runs RentAHuman's own
dry run and keeps a `rehearsed` row; nothing is posted, accepted or paid.
"""

from __future__ import annotations

import hashlib
import json
import re
import sys
import urllib.parse
import urllib.request
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Mapping

from .. import hand, stimuli
from . import api, settings, store

Json = dict[str, Any]

SURFACE = "rentahuman"
CATEGORIES = ("computer-gigs", "creative-gigs", "crew-gigs", "domestic-gigs", "event-gigs", "labor-gigs",
              "talent-gigs", "writing-gigs")
PROOF_TYPES = ("photo", "video", "text", "link")
VERDICTS = ("approve", "redo", "reject")
MIN_PAY_CENTS = 100
MAX_HOURS = 24
#: What every brief says, so the person knows before applying.
CONSENT = ("You are hired by Teeny, an AI agent. The photos and video you send may be posted publicly on Teeny's"
           " social accounts.")
REQUIREMENT = "Willing to be filmed or photographed for a public post"
#: Evidence files: at most this many per submission, each at most this big.
MAX_FILES = 10
MAX_FILE_BYTES = 200 * 1024 * 1024
_EXTENSIONS = {"image/jpeg": ".jpg", "image/png": ".png", "image/webp": ".webp", "image/heic": ".heic",
               "video/mp4": ".mp4", "video/quicktime": ".mov", "video/webm": ".webm"}

# What a brief never asks for, whatever the guard thinks (RentAHuman's rules,
# and Teeny's): secrets and credentials, identity papers, money details, and
# where somebody lives.
_FORBIDDEN = [
    (re.compile(r"\b(pass ?words?|passcodes?|one[- ]time codes?|otp|2fa codes?|verification codes?|seed phrases?|"
                r"private keys?|api keys?)\b", re.I), "asks for a password, a code or a key"),
    (re.compile(r"\b(ssn|social security|passports?|driver'?s licen[cs]es?|government id|id cards?)\b", re.I),
     "asks for identity papers"),
    (re.compile(r"\b(bank accounts?|routing numbers?|card numbers?|credit cards?|iban|cvv)\b", re.I),
     "asks for bank or card details"),
    (re.compile(r"\b(home address|where (he|she|they) lives?|door codes?|date of birth)\b", re.I),
     "asks for where someone lives or their birth date"),
    (re.compile(r"\b\d{1,5}\s+(?:[A-Z][a-z]+\s+){1,3}(street|st|avenue|ave|road|rd|boulevard|blvd|lane|ln|drive|dr|"
                r"court|ct|way)\b\.?", re.I), "names a street address (a city or a public landmark only)"),
]


# --- small helpers ------------------------------------------------------------------------------


def _settings() -> settings.Rent:
    try:
        return settings.load(hand.secrets())
    except settings.SettingsError as exc:
        raise hand.HandError(str(exc)) from None


def _tell(kind: str, ref: str, text: str, who: str | None = None, payload: Mapping[str, Any] | None = None) -> bool:
    """A stimulus for the mind. One that cannot be written never undoes the
    act it reports."""
    try:
        return stimuli.add("rent", kind, ref, text, who=who, payload=payload)
    except (hand.HandError, OSError) as exc:
        print(f"rent: could not tell the mind: {exc}", file=sys.stderr)
        return False


def _text(body: Mapping[str, Any], key: str, low: int, high: int, required: bool = True) -> str | None:
    value = body.get(key)
    if value in (None, "") and not required:
        return None
    if not isinstance(value, str) or not value.strip():
        raise hand.Refused(f"{key} is required")
    value = value.strip()
    if not low <= len(value) <= high:
        raise hand.Refused(f"{key} is {low} to {high} characters")
    return value


def _cents(value: Any, what: str) -> int:
    try:
        amount = Decimal(str(value))
    except (InvalidOperation, ValueError):
        raise hand.Refused(f"{what} is a number of US dollars") from None
    if not amount.is_finite():
        raise hand.Refused(f"{what} is a number of US dollars")
    cents = amount * 100
    if cents != cents.to_integral_value():
        raise hand.Refused(f"{what} has at most two decimals")
    return int(cents)


def _screen_brief(fields: Mapping[str, str]) -> None:
    for name, value in fields.items():
        for pattern, why in _FORBIDDEN:
            if pattern.search(value):
                raise hand.Refused(f"the {name} {why}; a bounty never does")


def _guard(fields: Mapping[str, str], kind: str) -> dict[str, str]:
    from ..guard import gate

    verdict = gate.check_fields(fields, surface=SURFACE, kind=kind, context={"hand": "rent"})
    if verdict.kind == "hold":
        raise hand.Refused(f"held by the guard: {verdict.reason}", hold_id=getattr(verdict, "hold_id", None))
    if verdict.kind != "allow":
        raise hand.Refused(verdict.reason)
    return dict(verdict.fields)


def _inbound(text: str, context: Mapping[str, Any]) -> str | None:
    """A stranger's words as the mind may read them, or None when the guard
    held them."""
    from ..guard import gate

    if not text.strip():
        return ""
    verdict = gate.inbound(text, surface=SURFACE, context=dict(context))
    return verdict.text if verdict.kind == "allow" else None


def _idem(*parts: Any) -> str:
    return "teeny-rent-" + "-".join(str(p) for p in parts)


def _amount(value: Any) -> int | None:
    if isinstance(value, Mapping):
        value = value.get("amount")
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return int(value)


# --- post ------------------------------------------------------------------------------------------


def _request(body: Mapping[str, Any]) -> tuple[str, Json, int]:
    """The create_bounty arguments a body asks for (no dryRun, no key), its
    title and the worker's pay in cents. Refused when the body is wrong."""
    if not isinstance(body, Mapping):
        raise hand.Refused("post takes a JSON body: title, brief, done, price_usd, and city or remote")
    title = _text(body, "title", 5, 200)
    brief = _text(body, "brief", 20, 4000)
    done = _text(body, "done", 10, 2000)
    proof_details = _text(body, "proof_details", 1, 2000, required=False)
    fields = {"title": title, "description": brief, "criteria": done}
    if proof_details:
        fields["details"] = proof_details
    _screen_brief(fields)
    pay = _cents(body.get("price_usd"), "price_usd")
    if pay < MIN_PAY_CENTS:
        raise hand.Refused("price_usd is at least 1")
    hours = body.get("hours", 1)
    if isinstance(hours, bool) or not isinstance(hours, (int, float)) or not 0.1 <= hours <= MAX_HOURS:
        raise hand.Refused(f"hours is 0.1 to {MAX_HOURS}")
    category = body.get("category") or "talent-gigs"
    if category not in CATEGORIES:
        raise hand.Refused(f"category is one of {', '.join(CATEGORIES)}")
    proof = body.get("proof") or ["photo", "video"]
    if not isinstance(proof, list) or not proof or any(p not in PROOF_TYPES for p in proof):
        raise hand.Refused(f"proof is a list of {', '.join(PROOF_TYPES)}")
    skills = body.get("skills") or []
    if not isinstance(skills, list) or len(skills) > 10 or any(not isinstance(s, str) or len(s) > 60 for s in skills):
        raise hand.Refused("skills is at most 10 short words")

    styled = _guard(fields, "post")
    args: Json = {
        "title": styled["title"],
        "description": f"{styled['description']}\n\n{CONSENT}",
        "category": category,
        "pay": {"type": "fixed", "amount": pay, "estimatedHours": hours},
        "spots": {"total": 1},
        "requirements": [REQUIREMENT],
        "proof": {"criteria": styled["criteria"], "types": list(dict.fromkeys(proof))},
    }
    if styled.get("details"):
        args["proof"]["details"] = styled["details"]
    if skills:
        args["skills"] = skills
    city = body.get("city")
    if city:
        location: Json = {"city": str(city)[:100], "isRemoteAllowed": bool(body.get("remote", False))}
        for key in ("state", "country"):
            if body.get(key):
                location[key] = str(body[key])[:100]
        args["location"] = location
    elif not body.get("remote"):
        raise hand.Refused("say where: a city (with state and country), or remote: true")
    return styled["title"], args, pay


def _cost(preview: Any) -> int:
    dry = preview.get("dryRun") if isinstance(preview, Mapping) else None
    cost = dry.get("cost") if isinstance(dry, Mapping) else None
    total = _amount(cost.get("total")) if isinstance(cost, Mapping) else None
    if total is None or total <= 0:
        raise hand.HandError("rentahuman create_bounty: the dry run gave no cost; nothing was posted")
    return total


def _bounty(reply: Any) -> Json:
    """The bounty a create_bounty or get_bounty answer describes."""
    if not isinstance(reply, Mapping):
        return {}
    inner = reply.get("bounty") if isinstance(reply.get("bounty"), Mapping) else reply
    funding = inner.get("funding") if isinstance(inner.get("funding"), Mapping) else {}
    return {
        "id": inner.get("id") or inner.get("bountyId") or reply.get("bountyId"),
        "status": inner.get("status"),
        "url": inner.get("url"),
        "funding": funding.get("status"),
        "pay_url": reply.get("payUrl") or inner.get("payUrl"),
    }


def _land(conn, job: Mapping[str, Any], reply: Any) -> Json:
    """A create answer onto the job's row: posted, or a draft to pay for."""
    found = _bounty(reply)
    if not found["id"]:
        raise api.Uncertain("rentahuman create_bounty: the answer named no bounty")
    funded = found["status"] == "open" and found["funding"] in (None, "funded")
    status = "posted" if funded else "draft"
    store.update(conn, job["id"], status=status, bounty_id=str(found["id"]), url=found["url"],
                 pay_url=found["pay_url"])
    if status == "draft":
        _tell("draft", str(job["id"]),
              f"rent job #{job['id']} ({job['title']}) is saved as a draft: the RentAHuman wallet did not cover"
              " it. The operator tops up the wallet or pays it; nobody sees it until then.",
              payload={"job": job["id"], "pay_url": found["pay_url"]})
    return {"job": job["id"], "status": status, "bounty": found["id"], "url": found["url"],
            "pay_url": found["pay_url"]}


def post(body: Any) -> Json:
    rules = _settings()
    if not rules.on:
        raise hand.Refused("renting is off (RENT_MAX_PER_DAY_USD=0)")
    title, args, pay = _request(body)
    conn = store.db()
    if len(store.open_jobs(conn)) >= rules.open_max:
        raise hand.Refused(f"{rules.open_max} rent jobs are already open (RENT_OPEN_MAX); finish one first")
    preview = api.tool("create_bounty", {**args, "dryRun": True})
    total = _cost(preview)
    if total > rules.per_bounty_cents:
        raise hand.Refused(f"it would cost ${total / 100:.2f} all in, over RENT_MAX_PER_BOUNTY_USD"
                           f" (${rules.per_bounty_cents / 100:.2f}); offer less", total_cents=total)
    if hand.dry_run():
        with hand.transaction(conn):
            job_id = store.insert(conn, "rehearsed", title, args, pay, total)
        return {"dry_run": True, "job": job_id, "would_post": args, "total_cents": total,
                "note": "rehearsal: RentAHuman priced it; nothing was posted"}
    with hand.lock("rent"):
        with hand.transaction(conn):
            room = rules.per_day_cents - store.spent_today(conn)
            if total > room:
                raise hand.Refused(f"it would cost ${total / 100:.2f}, and only ${max(room, 0) / 100:.2f} is left of"
                                   " today's RENT_MAX_PER_DAY_USD", total_cents=total)
            if len(store.open_jobs(conn)) >= rules.open_max:
                raise hand.Refused(f"{rules.open_max} rent jobs are already open (RENT_OPEN_MAX)")
            job_id = store.insert(conn, "posting", title, args, pay, total)
            store.update(conn, job_id, idem=_idem(job_id))
        job = store.row(conn, job_id)
        return _send_post(conn, job)


def _send_post(conn, job: Mapping[str, Any]) -> Json:
    """Sends (or sends again) a job's create with its own key. RentAHuman
    answers a repeated key with the first bounty."""
    args = json.loads(job["request"])
    try:
        reply = api.tool("create_bounty", {**args, "idempotencyKey": job["idem"]}, write=True)
    except api.ToolError as exc:
        if exc.code == "duplicate_listing" and exc.details.get("existingBountyId"):
            with hand.transaction(conn):
                store.update(conn, job["id"], status="failed", note=f"{exc.code}: {exc}")
            raise hand.Refused(f"RentAHuman already has this listing (bounty {exc.details['existingBountyId']});"
                               " nothing new was posted") from None
        if exc.retry_safe is False:
            raise api.Uncertain(str(exc)) from None  # stays `posting`; poll sends it again
        with hand.transaction(conn):
            store.update(conn, job["id"], status="failed", note=f"{exc.code}: {exc}")
        raise hand.Refused(str(exc), code=exc.code, job=job["id"]) from None
    except api.Uncertain:
        raise  # stays `posting`; poll sends it again with the same key
    with hand.transaction(conn):
        return _land(conn, job, reply)


# --- accept -----------------------------------------------------------------------------------------


def accept(body: Any) -> Json:
    if not isinstance(body, Mapping):
        raise hand.Refused("accept takes {job, application}")
    application = body.get("application")
    if not isinstance(application, str) or not application.strip():
        raise hand.Refused("application is the applicant's id from the rent stimulus")
    conn = store.db()
    job = store.need(conn, body.get("job"))
    return _accept(conn, job, application.strip())


def _accept(conn, job: Mapping[str, Any], application: str) -> Json:
    if job["status"] != "posted":
        raise hand.Refused(f"rent job #{job['id']} is {job['status']}, not open for hiring")
    if hand.dry_run():
        return {"dry_run": True, "job": job["id"], "would_accept": application}
    try:
        reply = api.tool("accept_application",
                         {"applicationId": application, "idempotencyKey": _idem(job["id"], "accept", application)},
                         write=True)
    except api.ToolError as exc:
        raise hand.Refused(str(exc), code=exc.code) from None
    cost = _amount(reply.get("cost")) if isinstance(reply, Mapping) else None
    with hand.transaction(conn):
        store.update(conn, job["id"], status="hired", application_id=application, hire_id=str(reply.get("hireId") or application),
                     conversation_id=reply.get("conversationId"), hired_at=hand.now())
    if cost is not None and cost > job["total_cents"]:
        _tell("charge", str(job["id"]),
              f"rent job #{job['id']}: hiring cost ${cost / 100:.2f}, more than the ${job['total_cents'] / 100:.2f}"
              " it was posted for (the applicant asked another price). The operator should look.",
              payload={"job": job["id"], "cost_cents": cost})
    return {"job": job["id"], "status": "hired", "hire": reply.get("hireId"), "cost_cents": cost}


# --- review and pay -------------------------------------------------------------------------------------


def review(body: Any) -> Json:
    if not isinstance(body, Mapping):
        raise hand.Refused("review takes {job, verdict: approve|redo|reject, reason}")
    verdict = body.get("verdict")
    if verdict not in VERDICTS:
        raise hand.Refused(f"verdict is one of {', '.join(VERDICTS)}")
    conn = store.db()
    job = store.need(conn, body.get("job"))
    if job["status"] not in ("hired", "submitted"):
        raise hand.Refused(f"rent job #{job['id']} is {job['status']}: nothing waits for review")
    hire = api.tool("get_hire", {"hireId": job["hire_id"]})
    submission = hire.get("submission") if isinstance(hire, Mapping) else None
    if not isinstance(submission, Mapping) or submission.get("status") != "pending_review":
        raise hand.Refused(f"rent job #{job['id']} has no work waiting for review")
    submission_id = str(submission.get("id"))
    if verdict == "approve":
        return pay(conn, job, submission_id)
    reason = _text(body, "reason", 5, 1000)
    reason = _guard({"message": reason}, "message")["message"]
    if hand.dry_run():
        return {"dry_run": True, "job": job["id"], "would": verdict, "reason": reason}
    tool = "request_redo" if verdict == "redo" else "reject_submission"
    try:
        api.tool(tool, {"hireId": job["hire_id"], "submissionId": submission_id, "message": reason}, write=True)
    except api.ToolError as exc:
        raise hand.Refused(str(exc), code=exc.code) from None
    status = "hired" if verdict == "redo" else "rejected"
    with hand.transaction(conn):
        store.update(conn, job["id"], status=status, submission_id=submission_id)
    return {"job": job["id"], "status": status}


def pay(conn, job: Mapping[str, Any], submission_id: str) -> Json:
    """Releases a hire's held pay, once. The row is `paying` before the call;
    an answer that is not a clear yes or a clear "nothing changed" leaves it
    `in_doubt` for the operator."""
    if hand.dry_run():
        return {"dry_run": True, "job": job["id"], "would_pay": job["pay_cents"]}
    with hand.lock("rent"):
        with hand.transaction(conn):
            current = store.row(conn, job["id"])
            if current is None or current["status"] not in ("hired", "submitted"):
                raise hand.Refused(f"rent job #{job['id']} is {current and current['status']}; it is paid once")
            store.update(conn, job["id"], status="paying", submission_id=submission_id)
        args = {"hireId": job["hire_id"], "submissionId": submission_id}
        try:
            try:
                reply = api.tool("pay_hire", args, write=True)
            except api.ToolError as exc:
                if exc.code != "payment_sent_finishing":
                    raise
                # Released; the approval did not finish. RentAHuman says to call again.
                reply = api.tool("pay_hire", args, write=True)
        except api.ToolError as exc:
            if exc.retry_safe is True:
                with hand.transaction(conn):
                    store.update(conn, job["id"], status="submitted")
                raise hand.Refused(str(exc), code=exc.code) from None
            return _doubt(conn, job, str(exc))
        except hand.HandError as exc:
            return _doubt(conn, job, str(exc))
        with hand.transaction(conn):
            store.update(conn, job["id"], status="paid", paid_at=hand.now())
    return {"job": job["id"], "status": "paid", "pay_status": reply.get("payStatus") if isinstance(reply, Mapping) else None}


def _doubt(conn, job: Mapping[str, Any], why: str) -> Json:
    with hand.transaction(conn):
        store.update(conn, job["id"], status="in_doubt", note=why[:500])
    _tell("in_doubt", str(job["id"]),
          f"rent job #{job['id']} ({job['title']}): the pay may or may not have gone out ({why}). It is never sent"
          " again by itself; the operator checks it on rentahuman.ai.",
          payload={"job": job["id"], "hire": job["hire_id"]})
    raise hand.HandError(f"rent job #{job['id']}: pay in doubt; the operator checks it", job=job["id"])


# --- message -------------------------------------------------------------------------------------------


def message(body: Any) -> Json:
    if not isinstance(body, Mapping):
        raise hand.Refused("message takes {job, text}")
    conn = store.db()
    job = store.need(conn, body.get("job"))
    if not job.get("conversation_id"):
        raise hand.Refused(f"rent job #{job['id']} has nobody to write to yet (no hire)")
    text = _text(body, "text", 1, 2000)
    _screen_brief({"message": text})
    text = _guard({"message": text}, "message")["message"]
    if hand.dry_run():
        return {"dry_run": True, "job": job["id"], "would_send": text}
    key = _idem(job["id"], "msg", hashlib.sha256(text.encode()).hexdigest()[:16])
    try:
        reply = api.tool("send_message", {"conversationId": job["conversation_id"], "content": text,
                                          "idempotencyKey": key}, write=True)
    except api.ToolError as exc:
        raise hand.Refused(str(exc), code=exc.code) from None
    sent = reply.get("message") if isinstance(reply, Mapping) else None
    if isinstance(sent, Mapping) and sent.get("id"):
        with hand.transaction(conn):
            store.seen(conn, job["id"], "message", str(sent["id"]), {"mine": True})
    return {"job": job["id"], "sent": True}


# --- poll ------------------------------------------------------------------------------------------------


def _download(url: Any, dest: Path) -> int:
    """One evidence file into DEST; its size. https only (loopback in tests),
    no key sent, MAX_FILE_BYTES at most."""
    parts = urllib.parse.urlsplit(str(url or ""))
    if parts.scheme != "https" and (parts.hostname or "") not in ("127.0.0.1", "localhost"):
        raise hand.HandError("an evidence file that is not https")
    request = urllib.request.Request(str(url), headers={"user-agent": "teeny-hands/rent"})
    size = 0
    with urllib.request.urlopen(request, timeout=120) as response, open(dest, "wb") as out:
        while True:
            chunk = response.read(1 << 20)
            if not chunk:
                break
            size += len(chunk)
            if size > MAX_FILE_BYTES:
                out.close()
                dest.unlink(missing_ok=True)
                raise hand.HandError("an evidence file over the size limit")
            out.write(chunk)
    return size


def _evidence(job: Mapping[str, Any], submission: Mapping[str, Any]) -> list[Json]:
    folder = hand.state_dir() / "rent" / str(job["id"]) / str(submission.get("id"))
    folder.mkdir(parents=True, exist_ok=True)
    saved: list[Json] = []
    for index, file in enumerate((submission.get("files") or [])[:MAX_FILES], start=1):
        if not isinstance(file, Mapping):
            continue
        kind = str(file.get("contentType") or "")
        dest = folder / f"{index}{_EXTENSIONS.get(kind, '.bin')}"
        try:
            size = dest.stat().st_size if dest.exists() else _download(file.get("url"), dest)
        except (hand.HandError, OSError, ValueError) as exc:
            saved.append({"type": file.get("type"), "error": str(exc)[:200]})
            continue
        saved.append({"type": file.get("type"), "path": str(dest.relative_to(hand.root())), "bytes": size,
                      "live_capture": bool(file.get("isLiveCapture")), "captured_at": file.get("capturedAt")})
    return saved


def _who(person: Any) -> str | None:
    return str(person.get("name"))[:80] if isinstance(person, Mapping) and person.get("name") else None


def _applications(conn, job: Mapping[str, Any], rules: settings.Rent) -> Json:
    reply = api.tool("list_applications", {"bountyId": job["bounty_id"], "status": "pending", "sort": "best",
                                           "limit": 20})
    found = [a for a in (reply.get("data") or []) if isinstance(a, Mapping) and a.get("id")]
    told = 0
    for app in found:
        with hand.transaction(conn):
            fresh = store.seen(conn, job["id"], "application", str(app["id"]))
        if not fresh:
            continue
        applicant = app.get("applicant") if isinstance(app.get("applicant"), Mapping) else {}
        words = _inbound(str(app.get("message") or ""), {"hand": "rent", "job": job["id"]})
        record = applicant.get("verifiedTrackRecord") if isinstance(applicant.get("verifiedTrackRecord"), Mapping) else {}
        summary = (f"{_who(applicant) or 'someone'} applied to rent job #{job['id']} ({job['title']}):"
                   f" rated {applicant.get('rating', '?')} over {applicant.get('reviewCount', 0)} reviews,"
                   f" {record.get('paidJobs', 0)} paid jobs.")
        if words is None:
            summary += " Their note was held by the guard."
        elif words:
            summary += f" They wrote: {words}"
        summary += f' Hire them with {{"do": "rent_accept", "job": {job["id"]}, "application": "{app["id"]}"}}.'
        _tell("application", f"{job['id']}:{app['id']}", summary, who=_who(applicant),
              payload={"job": job["id"], "application": app["id"]})
        told += 1
    accepted = None
    waited = hand.now() - float(job["at"])
    if found and rules.auto_accept_hours and waited >= rules.auto_accept_hours * 3600:
        best = found[0]
        accepted = _accept(conn, job, str(best["id"]))
        _tell("hired", f"{job['id']}:{best['id']}",
              f"rent job #{job['id']} ({job['title']}): nobody picked in {rules.auto_accept_hours}h, so"
              f" {_who(best.get('applicant')) or 'the best match'} was hired.",
              payload={"job": job["id"], "application": best["id"]})
    return {"applications": told, "accepted": accepted}


def _hire(conn, job: Mapping[str, Any]) -> Json:
    hire = api.tool("get_hire", {"hireId": job["hire_id"]})
    if not isinstance(hire, Mapping):
        return {}
    out: Json = {}
    if hire.get("payStatus") == "paid" or hire.get("status") == "paid":
        with hand.transaction(conn):
            store.update(conn, job["id"], status="paid", paid_at=job.get("paid_at") or hand.now())
        return {"paid": True}
    if hire.get("status") in ("cancelled", "canceled", "expired"):
        with hand.transaction(conn):
            store.update(conn, job["id"], status="cancelled", note=f"hire {hire.get('status')}")
        return {"cancelled": True}
    if hire.get("conversationId") and not job.get("conversation_id"):
        with hand.transaction(conn):
            store.update(conn, job["id"], conversation_id=hire["conversationId"])
    submission = hire.get("submission")
    if isinstance(submission, Mapping) and submission.get("status") == "pending_review":
        attempt = submission.get("attempt") if isinstance(submission.get("attempt"), Mapping) else {}
        ref = f"{submission.get('id')}:{attempt.get('used', 0)}"
        with hand.transaction(conn):
            fresh = store.seen(conn, job["id"], "submission", ref)
            store.update(conn, job["id"], status="submitted", submission_id=str(submission.get("id")))
        if fresh:
            files = _evidence(job, submission)
            words = _inbound(str(submission.get("text") or ""), {"hand": "rent", "job": job["id"]})
            request = json.loads(job["request"])
            text = (f"Work came in for rent job #{job['id']} ({job['title']}). The bounty asked for: "
                    f"{request.get('proof', {}).get('criteria')}. {len(files)} file(s) saved under"
                    f" state/rent/{job['id']}/.")
            if words is None:
                text += " Their note was held by the guard."
            elif words:
                text += f" They wrote: {words}"
            text += (f' Judge it against the criteria only, then {{"do": "rent_review", "job": {job["id"]},'
                     ' "verdict": "approve|redo|reject", "reason": "..."}. Approving pays them; it is final.')
            _tell("submitted", f"{job['id']}:{ref}", text, who=_who(hire.get("worker")),
                  payload={"job": job["id"], "submission": submission.get("id"), "files": files})
            out["submitted"] = True
    return out


def _messages(conn, job: Mapping[str, Any]) -> int:
    if not job.get("conversation_id"):
        return 0
    reply = api.tool("list_messages", {"conversationId": job["conversation_id"], "limit": 100})
    told = 0
    for msg in reply.get("data") or []:
        if not isinstance(msg, Mapping) or not msg.get("id"):
            continue
        sender = msg.get("sender") if isinstance(msg.get("sender"), Mapping) else {}
        if sender.get("role") not in ("human", "worker"):
            continue
        with hand.transaction(conn):
            fresh = store.seen(conn, job["id"], "message", str(msg["id"]))
        if not fresh:
            continue
        words = _inbound(str(msg.get("content") or ""), {"hand": "rent", "job": job["id"]})
        if not words:
            continue
        _tell("message", f"{job['id']}:{msg['id']}",
              f"The person on rent job #{job['id']} ({job['title']}) wrote: {words}"
              f' (answer with {{"do": "rent_message", "job": {job["id"]}, "text": "..."}})',
              who=_who(sender), payload={"job": job["id"], "message": msg["id"]})
        told += 1
    return told


def _follow(conn, job: Mapping[str, Any], rules: settings.Rent) -> Json:
    status = job["status"]
    if status == "posting":
        if hand.dry_run():
            return {"would_resend": True}
        return _send_post(conn, job)
    if status == "draft":
        found = _bounty(api.tool("get_bounty", {"bountyId": job["bounty_id"]}))
        if found["status"] == "open" and found["funding"] in (None, "funded"):
            with hand.transaction(conn):
                store.update(conn, job["id"], status="posted")
            return {"funded": True}
        return {}
    if status == "posted":
        hires = api.tool("list_hires", {"bountyId": job["bounty_id"], "limit": 5})
        live = [h for h in (hires.get("data") or []) if isinstance(h, Mapping) and h.get("id")
                and h.get("status") not in ("cancelled", "canceled", "expired")]
        if live:  # hired on the website, or an accept whose answer was lost
            with hand.transaction(conn):
                store.update(conn, job["id"], status="hired", hire_id=str(live[0]["id"]), hired_at=hand.now())
            return {"hired": live[0]["id"]}
        found = _bounty(api.tool("get_bounty", {"bountyId": job["bounty_id"]}))
        if found["status"] in ("cancelled", "canceled", "closed", "expired", "completed"):
            with hand.transaction(conn):
                store.update(conn, job["id"], status="cancelled", note=f"bounty {found['status']}")
            return {"closed": found["status"]}
        return _applications(conn, job, rules)
    out = _hire(conn, job)
    out["messages"] = _messages(conn, store.row(conn, job["id"]) or job)
    return out


def poll() -> Json:
    """Every open job, followed once. One job's failure never stops the rest."""
    rules = _settings()
    if not hand.setting("RENTAHUMAN_API_KEY"):
        return {"skipped": "no RENTAHUMAN_API_KEY in secrets/teeny.env"}
    conn = store.db()
    results: list[Json] = []
    errors: list[Json] = []
    with hand.lock("rent-poll", timeout=10):
        for job in store.open_jobs(conn):
            try:
                results.append({"job": job["id"], **_follow(conn, job, rules)})
            except hand.Refused as exc:
                errors.append({"job": job["id"], "refused": exc.reason})
            except hand.HandError as exc:
                errors.append({"job": job["id"], "error": str(exc)})
    return {"followed": len(results), "results": results, "errors": errors}


# --- the operator's and the reads --------------------------------------------------------------------------


def cancel(job_id: int, reason: str) -> Json:
    conn = store.db()
    job = store.need(conn, job_id)
    if job["status"] not in ("draft", "posted") or not job.get("bounty_id"):
        raise hand.Refused(f"rent job #{job['id']} is {job['status']}; only an unhired bounty is cancelled here")
    key = _idem(job["id"], "cancel")
    if hand.dry_run():
        quote = api.tool("cancel_bounty", {"bountyId": job["bounty_id"], "reason": reason, "preview": True,
                                           "idempotencyKey": key})
        return {"dry_run": True, "job": job["id"], "quote": quote}
    try:
        reply = api.tool("cancel_bounty", {"bountyId": job["bounty_id"], "reason": reason, "idempotencyKey": key},
                         write=True)
        if isinstance(reply, Mapping) and reply.get("status") == "confirmation_required":
            reply = api.tool("cancel_bounty", {"bountyId": job["bounty_id"], "idempotencyKey": key,
                                               "confirmationToken": reply.get("confirmationToken")}, write=True)
    except api.ToolError as exc:
        raise hand.Refused(str(exc), code=exc.code) from None
    with hand.transaction(conn):
        store.update(conn, job["id"], status="cancelled", note=f"cancelled: {reason}")
    return {"job": job["id"], "status": "cancelled", "answer": reply}


def humans(skill: str | None, city: str | None, country: str | None, limit: int) -> Json:
    args: Json = {"limit": limit}
    for key, value in (("skill", skill), ("city", city), ("country", country)):
        if value:
            args[key] = value
    return {"humans": api.tool("list_humans", args)}


def listing(status: str | None) -> Json:
    return {"jobs": store.listing(store.db(), status)}


def get(job_id: int) -> Json:
    conn = store.db()
    job = store.public(store.need(conn, job_id))
    job["events"] = [dict(r) for r in conn.execute(
        "SELECT kind, ref, at FROM rent_events WHERE job_id = ? ORDER BY at", (job["id"],))]
    return {"job": job}


def status() -> Json:
    rules = _settings()
    conn = store.db()
    out: Json = {
        "on": rules.on,
        "caps": {"per_bounty_cents": rules.per_bounty_cents, "per_day_cents": rules.per_day_cents,
                 "open_max": rules.open_max, "auto_accept_hours": rules.auto_accept_hours},
        "spent_today_cents": store.spent_today(conn),
        "open": [store.public(j) for j in store.open_jobs(conn)],
        "in_doubt": [store.public(j) for j in store.open_jobs(conn, ("in_doubt", "paying"))],
        "dry_run": hand.dry_run(),
    }
    if hand.setting("RENTAHUMAN_API_KEY"):
        try:
            out["wallet"] = api.tool("get_wallet")
        except hand.HandError as exc:
            out["wallet_error"] = str(exc)
    else:
        out["wallet_error"] = "no RENTAHUMAN_API_KEY in secrets/teeny.env"
    return out
