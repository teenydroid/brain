"""The X account's own profile, as last read, and whether its bio says what
the account is (docs/roadmap.md, P1; DIRECTIVE.md, Level 2, "Says what it
is").

X's developer guidelines (https://docs.x.com/developer-guidelines) ask an
automated account to switch on the "Automated" label, to "state clearly that
it's a bot and who operates it" in the bio, and to be associated with a
human-managed account. The label is not in anything the API returns, so only
the bio can be checked here; the label is the operator's to confirm in X's
settings.

`remember` keeps the profile `hands/stats snapshot` already reads every day
(`/2/users/by/username`, with `description`), so no extra call is billed and
`bin/doctor` checks the cached row: never a live call at boot. `check` is
that doctor check. It warns, never fails: a missing line is the operator's
to fix on X, not a reason to refuse a boot.
"""

from __future__ import annotations

import re
import sqlite3
import time
from pathlib import Path
from typing import Any, Mapping

SCHEMA = """
CREATE TABLE IF NOT EXISTS x_profile (
    handle      TEXT PRIMARY KEY,
    user_id     TEXT,
    name        TEXT,
    description TEXT NOT NULL DEFAULT '',
    read_at     REAL NOT NULL
);
"""

#: The setting naming the person who runs the account (an X handle).
OPERATOR = "X_OPERATOR_HANDLE"
#: A cached profile older than this is stale (the stats job runs daily).
STALE_DAYS = 8
#: Words that say the account is automated.
_AUTOMATED = re.compile(r"\b(automated|bot|ai agent|an ai|autonomous agent)\b", re.IGNORECASE)
#: Any "run by @someone" when no operator handle is set.
_RUN_BY = re.compile(r"\b(?:run|operated|managed|built|made|automated|maintained)\s+by\s+@([A-Za-z0-9_]{1,15})\b", re.IGNORECASE)
_HANDLE = re.compile(r"^@?([A-Za-z0-9_]{1,15})$")


def remember(conn: sqlite3.Connection, handle: str, user: Mapping[str, Any], at: float) -> None:
    """Keeps the account's profile as X just returned it: one row per handle."""
    conn.executescript(SCHEMA)
    conn.execute(
        "INSERT INTO x_profile (handle, user_id, name, description, read_at) VALUES (?, ?, ?, ?, ?)"
        " ON CONFLICT(handle) DO UPDATE SET user_id = excluded.user_id, name = excluded.name,"
        " description = excluded.description, read_at = excluded.read_at",
        (handle.lstrip("@").lower(), user.get("id"), user.get("name"), str(user.get("description") or ""), at),
    )


def disclosure(description: str, operator: str | None) -> tuple[bool, str]:
    """Does the bio say it is automated and who runs it? (ok, why)."""
    if not _AUTOMATED.search(description or ""):
        return False, "the bio does not say the account is automated (a bot, an AI agent)"
    if operator:
        named = re.search(rf"(?<![A-Za-z0-9_])@{re.escape(operator)}(?![A-Za-z0-9_])", description, re.IGNORECASE)
        if not named:
            return False, f"the bio does not name the operator @{operator}"
        return True, f"the bio says it is automated and names @{operator}"
    found = _RUN_BY.search(description)
    if not found:
        return False, "the bio does not name who runs it (\"run by @handle\"), and X_OPERATOR_HANDLE is unset"
    return True, f"the bio says it is automated and run by @{found.group(1)} (set X_OPERATOR_HANDLE to pin who)"


def check(state_db: Path, settings: Mapping[str, str], now: float | None = None) -> tuple[str, str]:
    """The doctor's profile check, from the cached row only: ("ok" | "warn",
    detail). No X keys is ok: there is no account to check."""
    keys = ("X_API_KEY", "X_API_SECRET", "X_ACCESS_TOKEN", "X_ACCESS_TOKEN_SECRET")
    if not all(settings.get(k) for k in keys):
        return "ok", "no X keys set: no account to check"
    handle = (settings.get("X_BOT_USERNAME") or "teenydroid").lstrip("@").lower()
    operator_text = settings.get(OPERATOR) or ""
    match = _HANDLE.match(operator_text.strip())
    operator = match.group(1) if match else None
    if operator_text and not operator:
        return "warn", f"{OPERATOR} is not an X handle"
    row = None
    if state_db.is_file():
        try:
            conn = sqlite3.connect(f"file:{state_db}?mode=ro", uri=True)
            try:
                row = conn.execute("SELECT description, read_at FROM x_profile WHERE handle = ?", (handle,)).fetchone()
            finally:
                conn.close()
        except sqlite3.Error:
            row = None
    if row is None:
        return "warn", f"no cached profile for @{handle} yet (bin/stats-snapshot keeps it daily); the bio is unchecked"
    description, read_at = row
    age_days = ((now if now is not None else time.time()) - float(read_at)) / 86400
    ok, why = disclosure(description or "", operator)
    label = "; the Automated label is not in the API: confirm it in X's settings"
    stale = f" (read {age_days:.0f} days ago: stale)" if age_days > STALE_DAYS else ""
    if not ok:
        return "warn", f"@{handle}: {why}{stale}{label}"
    if stale:
        return "warn", f"@{handle}: {why}{stale}{label}"
    return "ok", f"@{handle}: {why}{label}"
