"""Teeny's Reddit account, made by the browser operator (operator verbs).

  signup [--username U]   queues an operator-source `signup` task on
                          https://www.reddit.com/register. The desk makes
                          the password in the keychain on offer (handle:
                          Teeny's AgentMail inbox) and the operator fills it
                          only as {{keychain:reddit.com}}. Reddit mails a code
                          from redditmail.com: the operator ends the run
                          needs_code with code_from redditmail.com, and the
                          next offer carries the code (hands/tasks
                          CODE_SITES). It ends ok naming the username, which
                          lands in `accounts`.
  profile                 queues a `reddit_profile` task that sets the
                          display name and the bio, which says Teeny is an AI
                          (brain/DRIVE.md: "say you are an AI wherever a
                          profile has room").
"""

from __future__ import annotations

from typing import Any

from .. import hand
from . import ledger, rules
from .actions import guarded, queue_task

#: Tried in this order after the one asked for, when a name is taken.
USERNAMES = ("teenydroid", "teeny_droid", "teenydroid_ai")
DISPLAY_NAME = "Teeny"
BIO = "An AI agent (Teeny), run by its operator. Not a person."
SIGNUP_URL = "https://www.reddit.com/register"
PROFILE_URL = "https://www.reddit.com/settings/profile"
CODE_FROM = "redditmail.com"

Json = dict[str, Any]


def signup(name: str = USERNAMES[0]) -> Json:
    from ..keychain import store as keychain
    from ..tasks import queue

    first = rules.username(name)
    names = [first, *(n for n in USERNAMES if n.lower() != first.lower())]
    found = rules.account()
    if found is not None:
        raise hand.Refused(f"Teeny already has a Reddit account, u/{found['handle']}", rule="have_account")
    if not hand.setting("AGENTMAIL_INBOX"):
        raise hand.Refused("AGENTMAIL_INBOX is not set: Reddit's code is mailed to Teeny's inbox", rule="no_inbox")
    if not keychain.ready():
        raise hand.Refused("the keychain is not set up (secrets/keychain.env, KEYCHAIN_KEY): the password lives there",
                           rule="no_keychain")
    conn = queue.db()
    try:
        waiting = conn.execute(
            "SELECT id FROM browser_tasks WHERE kind = 'signup' AND start_url = ? AND status IN ('pending', 'claimed')",
            (SIGNUP_URL,)).fetchone()
    finally:
        conn.close()
    if waiting is not None:
        raise hand.Refused(f"a Reddit signup is already on the desk (task {waiting['id']})", rule="duplicate",
                           task_id=waiting["id"])
    tried = ", then ".join(names[1:])
    task = {
        "kind": "signup",
        "start_url": SIGNUP_URL,
        "goal": (
            "Sign up for a Reddit account of Teeny's own, an AI agent. Use task.email as the email and fill "
            "login.password directly into the password field (never inside a task action). Pick the username "
            f"{names[0]}; if it is taken, {tried}. Reddit mails a verification code from {CODE_FROM}: when it asks "
            f"for one, end the run with outcome needs_code and code_from {CODE_FROM}; the next run brings it in "
            "mail_codes. Skip optional steps (interests, gender, avatar). A phone or ID check ends the task with "
            "outcome needs_human."
        ),
        "done_when": ("The account exists and you are signed in as it. Report ok with account.handle: the username "
                      "you got, exactly."),
    }
    if hand.dry_run():
        return {"dry_run": True, "would_queue": task, "usernames": names}
    added = queue.add(task, source="operator")
    return {"queued": "signup", "task_id": added["task_id"], "usernames": names, "code_from": CODE_FROM}


def profile() -> Json:
    """Sets the display name and the AI bio. The words are fixed here, never
    a model's, so the guard's model judge is not asked."""
    from ..guard.gate import check_fields

    found = rules.need_account()
    verdict = check_fields({"body": BIO}, surface="reddit", kind="profile", context={"handle": found["handle"]},
                           judge=False)
    if verdict.kind != "allow":
        raise hand.Refused(f"the guard did not pass the bio: {verdict.reason}", rule="guard")
    bio = verdict.fields["body"]
    conn = rules.db()
    try:
        ledger.sync(conn)
    finally:
        conn.close()
    goal = (f"Log in to Reddit as u/{found['handle']} if you are not already. On start_url (new Reddit's profile "
            f"settings: old.reddit.com has no such page) set the display name to \"{DISPLAY_NAME}\" and the about "
            "(bio) to text, typed exactly, and save. Change nothing else.")
    return queue_task("profile", "reddit_profile", [{"target": "profile"}], PROFILE_URL, goal,
                      "Both are saved and show on the profile. Report ok.", text=bio)


__all__ = ["signup", "profile", "guarded"]
