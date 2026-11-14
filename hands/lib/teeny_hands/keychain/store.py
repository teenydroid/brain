"""Teeny's keychain: one password per site account, kept encrypted in
state/keychain.db (docs/hands.md, "keychain").

  keychain(site, handle, nonce, sealed, created_at)

Each password is sealed with AES-256-GCM (the `cryptography` package's
AESGCM, python3-cryptography in the image) under KEYCHAIN_KEY from
secrets/keychain.env: 32 random bytes, base64. The site and the handle are
the associated data, so a sealed row moved to another site does not open.
The key never leaves this module and no row holds a password in clear.

Who reads a password back, and how:
  - the browser, through `secret` (bin/keychain-fill, the core's
    `[browser] keychain_command`): the core asks for one site's password
    only after it has checked that the page it is about to type into is on
    that site, and types it in place of the operator's `{{keychain:<site>}}`
    placeholder. It never reaches a model, a log or a ledger.
  - the operator (the person), through `reveal`, on a terminal only.
Nothing else prints one: `create` and `list` answer with sites and handles.
"""

from __future__ import annotations

import base64
import os
import re
import secrets as _random
import sqlite3
import string
from pathlib import Path
from typing import Any

from .. import hand

KEY_FILE = "keychain.env"
KEY_NAME = "KEYCHAIN_KEY"
_SITE = re.compile(r"^(?=.{3,253}$)(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}$")
_HANDLE = re.compile(r"^[A-Za-z0-9._@+-]{1,100}$")
#: A generated password: long, from letters, digits and a few symbols most
#: sites accept, with at least one of each kind.
PASSWORD_LENGTH = 24
_SYMBOLS = "-_.!@#%+="
_ALPHABET = string.ascii_letters + string.digits + _SYMBOLS

SCHEMA = """
CREATE TABLE IF NOT EXISTS keychain (
    site       TEXT NOT NULL,
    handle     TEXT NOT NULL,
    nonce      BLOB NOT NULL,
    sealed     BLOB NOT NULL,
    created_at REAL NOT NULL,
    PRIMARY KEY (site, handle)
);
"""

Json = dict[str, Any]


def path() -> Path:
    return hand.state_dir() / "keychain.db"


def _db() -> sqlite3.Connection:
    target = path()
    target.parent.mkdir(parents=True, exist_ok=True)
    fresh = not target.exists()
    conn = sqlite3.connect(target, timeout=30)
    conn.row_factory = sqlite3.Row
    if fresh:
        os.chmod(target, 0o600)
    conn.executescript(SCHEMA)
    return conn


def site(value: Any) -> str:
    text = str(value or "").strip().lower().rstrip(".")
    if text.startswith("www."):
        text = text[4:]
    if not _SITE.match(text):
        raise hand.HandError("--site is a site's domain, like example.com")
    return text


def handle(value: Any) -> str:
    text = str(value or "").strip()
    if not _HANDLE.match(text):
        raise hand.HandError("--handle is the user name or address on that site (letters, digits, . _ @ + -)")
    return text


def _key() -> bytes:
    """KEYCHAIN_KEY from secrets/keychain.env, never from the environment
    (a model's shell must not be able to hand one in)."""
    file = hand.secrets_dir() / KEY_FILE
    try:
        values = hand._parse_env_file(file.read_text())  # noqa: SLF001 - the hands' one env-file reader
    except OSError as exc:
        raise hand.HandError(f"no keychain key: secrets/{KEY_FILE} cannot be read ({exc.strerror})") from exc
    raw = values.get(KEY_NAME, "").strip()
    try:
        key = base64.b64decode(raw, validate=True)
    except ValueError as exc:
        raise hand.HandError(f"{KEY_NAME} in secrets/{KEY_FILE} is not base64") from exc
    if len(key) != 32:
        raise hand.HandError(f"{KEY_NAME} in secrets/{KEY_FILE} must be 32 bytes, base64")
    return key


def _cipher():
    try:
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    except ImportError as exc:  # pragma: no cover - the image installs it
        raise hand.HandError("the keychain needs python3-cryptography") from exc
    return AESGCM(_key())


def _aad(site_: str, handle_: str) -> bytes:
    return f"teeny-keychain\n{site_}\n{handle_}".encode()


def generate() -> str:
    """A fresh password with a lower, an upper, a digit and a symbol."""
    while True:
        word = "".join(_random.choice(_ALPHABET) for _ in range(PASSWORD_LENGTH))
        if (any(c.islower() for c in word) and any(c.isupper() for c in word)
                and any(c.isdigit() for c in word) and any(c in _SYMBOLS for c in word)):
            return word


def create(site_: str, handle_: str) -> Json:
    """A new password for one account, sealed and stored. An account that
    already has one keeps it: creating twice is refused, never a reset."""
    site_, handle_ = site(site_), handle(handle_)
    cipher = _cipher()
    nonce = os.urandom(12)
    sealed = cipher.encrypt(nonce, generate().encode(), _aad(site_, handle_))
    conn = _db()
    try:
        with conn:
            exists = conn.execute("SELECT 1 FROM keychain WHERE site = ? AND handle = ?", (site_, handle_)).fetchone()
            if exists:
                raise hand.Refused(f"{handle_} on {site_} already has a password; it is kept", rule="exists",
                                   site=site_, handle=handle_)
            conn.execute("INSERT INTO keychain (site, handle, nonce, sealed, created_at) VALUES (?, ?, ?, ?, ?)",
                         (site_, handle_, nonce, sealed, hand.now()))
    finally:
        conn.close()
    return {"site": site_, "handle": handle_, "created": True}


def listing() -> Json:
    conn = _db()
    try:
        rows = conn.execute("SELECT site, handle, created_at FROM keychain ORDER BY site, handle").fetchall()
    finally:
        conn.close()
    return {"accounts": [dict(r) for r in rows]}


def _open(site_: str, handle_: str | None) -> tuple[str, str]:
    """The handle and the password of an account on a site (the only one, or
    the one named)."""
    conn = _db()
    try:
        if handle_ is None:
            rows = conn.execute("SELECT * FROM keychain WHERE site = ?", (site_,)).fetchall()
        else:
            rows = conn.execute("SELECT * FROM keychain WHERE site = ? AND handle = ?", (site_, handle_)).fetchall()
    finally:
        conn.close()
    if not rows:
        raise hand.Refused(f"no password for {site_}", rule="unknown", site=site_)
    if len(rows) > 1:
        raise hand.Refused(f"{len(rows)} accounts on {site_}; name one with --handle", rule="ambiguous", site=site_)
    row = rows[0]
    try:
        word = _cipher().decrypt(row["nonce"], row["sealed"], _aad(row["site"], row["handle"])).decode()
    except Exception as exc:  # noqa: BLE001 - InvalidTag and friends: never echo what failed to open
        raise hand.HandError(f"the password for {site_} does not open with this key") from exc
    return row["handle"], word


def secret_for(host: str, handle_: str | None = None) -> str:
    """The password of the account on the site `host` is, or is under: the
    longest stored site that matches. For the browser's fill only."""
    host = host.strip().lower().rstrip(".")
    conn = _db()
    try:
        sites = [r[0] for r in conn.execute("SELECT DISTINCT site FROM keychain")]
    finally:
        conn.close()
    matching = sorted((s for s in sites if host == s or host.endswith("." + s)), key=len, reverse=True)
    if not matching:
        raise hand.Refused(f"no password for {host}", rule="unknown", site=host)
    return _open(matching[0], handle_)[1]
