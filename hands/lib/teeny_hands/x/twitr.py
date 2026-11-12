"""twitr.sh: a pay-per-call X API, paid from Teeny's own wallet over x402.

The keys never come here. Every call is one `openhuman.x402_fetch`: the core
makes the HTTPS request, and on a 402 TinyWallet signs the payment within the
cap this module states for that call (`maxAmountAtomic`, USDC atomic units),
then the core retries and records the settlement in the module's ledger. The
core's own x402 budget (OPENHUMAN_X402_PER_REQUEST_MAX, _DAILY_MAX,
_MONTHLY_MAX) is a second, independent ceiling.

What it costs (twitr.sh/llms.txt): reads are $0.0012 a record with a $0.0024
floor; search, conversations and timelines are `upto` reads on Base, so the
signed amount is a maximum and an empty answer costs nothing. A post, reply,
like or delete is $0.036 at most. Failed calls (4xx, 5xx) are never charged.

Writes act as an X account that this wallet linked on twitr.sh (`connect`,
once, by the operator in a browser). twitr checks the account against the
PAYING wallet, and the link is made with the wallet's EVM key (SIWX), so
every call is pinned to `TWITR_NETWORK` (Base unless told otherwise): paying
a write from the Solana address would be a different wallet.

twitr asks for one paid call at a time per wallet (an overlap is a free 429),
so every call runs under the `twitr` lock.

Like client.py, nothing that leaves this module names a URL with its query,
a header, or a payment credential.
"""

from __future__ import annotations

import hashlib
import json
from decimal import Decimal, InvalidOperation
from typing import Any, Callable, Mapping, Sequence

from .. import hand
from .client import XError
from .model import Json, SearchOptions

TWITR_BASE = "https://twitr.sh"
#: Base mainnet. TwitR's SIWX and its `upto` reads are EVM-only.
DEFAULT_NETWORK = "eip155:8453"

#: USDC atomic units (6 decimals).
PER_RECORD = 1_200
FLOOR = 2_400
WRITE = 36_000
#: A free call (SIWX account endpoints) may pay nothing: the core needs a cap
#: above zero, and one atomic unit cannot cover any twitr price.
FREE = 1
#: twitr bills per record up to `resultsLimit`; keep a search to a page.
MAX_RESULTS = 100

_TITLE_CHARS = 200

Core = Callable[..., Any]


class TwitrError(XError):
    """A failed twitr call. A subclass of XError so the publish path reads
    `status` the same way: a 4xx is twitr (or X behind it) saying no and was
    not charged; None or a 5xx leaves the outcome unknown."""


def _usd_to_atomic(raw: str, what: str) -> int:
    try:
        value = Decimal(raw.strip())
    except (InvalidOperation, ValueError):
        raise hand.HandError(f"{what} is not a dollar amount") from None
    if not value.is_finite() or value <= 0:
        raise hand.HandError(f"{what} must be above zero")
    return int(value * 1_000_000)


def _title(body: str) -> str | None:
    try:
        parsed = json.loads(body)
    except (ValueError, TypeError):
        return None
    if not isinstance(parsed, Mapping):
        return None
    for key in ("error", "message", "title", "detail"):
        value = parsed.get(key)
        if isinstance(value, Mapping):
            value = value.get("message") or value.get("code")
        if isinstance(value, str) and value.strip():
            return value.strip()[:_TITLE_CHARS]
    return None


def idempotency_key(*parts: str) -> str:
    """The same logical write gives the same key, so a retry inside twitr's
    ten-minute window replays the first result instead of posting (and
    paying) twice."""
    digest = hashlib.sha256("\x1f".join(parts).encode()).hexdigest()
    return f"teeny-{digest[:40]}"


# --- shapes ------------------------------------------------------------------------------


def _first(raw: Mapping[str, Any], *names: str) -> Any:
    for name in names:
        value = raw.get(name)
        if value not in (None, ""):
            return value
    return None


def _int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.isdigit():
        return int(value)
    return None


def _id(value: Any) -> str | None:
    if isinstance(value, int) and not isinstance(value, bool):
        return str(value)
    return value if isinstance(value, str) and value else None


def _author(raw: Mapping[str, Any]) -> Mapping[str, Any]:
    for name in ("author", "user"):
        value = raw.get(name)
        if isinstance(value, Mapping):
            return value
    return {}


def to_post(raw: Any) -> Json | None:
    """A twitr tweet item in the hand's post shape (model.to_posts). twitr
    documents its items by example only, so both its camelCase names and the
    snake_case ones of the upstream scrapers are read; a post without an id
    or text is dropped."""
    if not isinstance(raw, Mapping):
        return None
    post_id = _id(_first(raw, "id", "id_str", "tweetId", "rest_id"))
    text = _first(raw, "text", "fullText", "full_text")
    if not post_id or not isinstance(text, str):
        return None
    author = _author(raw)
    post: Json = {"id": post_id, "text": text}
    for key, value in (
        ("authorId", _id(_first(raw, "authorId", "author_id", "userId")) or _id(_first(author, "id", "rest_id"))),
        ("authorUsername", _first(raw, "authorUsername", "username", "screen_name") or _first(author, "userName", "username", "screen_name")),
        ("conversationId", _id(_first(raw, "conversationId", "conversation_id"))),
        ("createdAt", _first(raw, "createdAt", "created_at")),
        ("inReplyToUserId", _id(_first(raw, "inReplyToUserId", "in_reply_to_user_id", "inReplyToUserIdStr"))),
    ):
        if value is not None:
            post[key] = value
    media = []
    for item in raw.get("media") if isinstance(raw.get("media"), list) else []:
        if isinstance(item, Mapping) and isinstance(item.get("type"), str):
            attached: Json = {"type": item["type"]}
            for key, names in (("url", ("url", "media_url_https")), ("previewUrl", ("previewUrl", "preview_image_url")), ("altText", ("altText", "alt_text"))):
                value = _first(item, *names)
                if isinstance(value, str):
                    attached[key] = value
            media.append(attached)
    if media:
        post["media"] = media
    counters = {
        "likes": _int(_first(raw, "likeCount", "like_count", "favoriteCount")),
        "replies": _int(_first(raw, "replyCount", "reply_count")),
        "reposts": _int(_first(raw, "retweetCount", "retweet_count", "repostCount")),
        "quotes": _int(_first(raw, "quoteCount", "quote_count")),
    }
    if any(value is not None for value in counters.values()):
        metrics: Json = {"id": post_id, **{k: v or 0 for k, v in counters.items()}}
        for key, names in (("impressions", ("viewCount", "impressionCount")), ("bookmarks", ("bookmarkCount",))):
            value = _int(_first(raw, *names))
            if value is not None:
                metrics[key] = value
        post["metrics"] = metrics
    return post


def to_user(raw: Any, handle: str) -> Json | None:
    """A twitr profile item in the hand's user shape (model.to_user)."""
    if not isinstance(raw, Mapping):
        return None
    user_id = _id(_first(raw, "id", "rest_id", "userId"))
    if not user_id:
        return None
    user: Json = {"id": user_id, "username": _first(raw, "userName", "username", "screen_name") or handle}
    for key, names in (
        ("name", ("name",)),
        ("description", ("description", "bio")),
        ("createdAt", ("createdAt", "created_at")),
    ):
        value = _first(raw, *names)
        if isinstance(value, str):
            user[key] = value
    verified = _first(raw, "isBlueVerified", "verified", "isVerified")
    if isinstance(verified, bool):
        user["verified"] = verified
    for key, names in (
        ("followers", ("followers", "followersCount", "followers_count")),
        ("following", ("following", "followingCount", "friends_count")),
        ("posts", ("statusesCount", "tweetCount", "statuses_count")),
    ):
        value = _int(_first(raw, *names))
        if value is not None:
            user[key] = value
    return user


def _written_id(response: Mapping[str, Any]) -> str | None:
    items = response.get("items")
    first = items[0] if isinstance(items, list) and items and isinstance(items[0], Mapping) else {}
    return _id(_first(first, "id", "tweet_id", "tweetId")) or _id(_first(response, "id", "tweet_id"))


# --- the client --------------------------------------------------------------------------


class TwitrApi:
    """The reads and raw writes of XApi, served by twitr.sh and paid from
    the wallet. Writes act as `account`, which must be linked to this wallet."""

    def __init__(self, base: str, account: str, network: str, max_call_atomic: int, core: Core = hand.core) -> None:
        self._base = base.rstrip("/")
        self.account = account.lstrip("@")
        self.network = network
        self._max_call = max_call_atomic
        self._core = core

    def __repr__(self) -> str:
        return f"TwitrApi(@{self.account})"

    @classmethod
    def from_settings(cls, core: Core = hand.core) -> "TwitrApi":
        """secrets/teeny.env:

          TWITR_ACCOUNT          the linked X handle writes act as
                                 (default X_BOT_USERNAME)
          TWITR_NETWORK          the CAIP-2 network every payment is pinned
                                 to (default Base, eip155:8453)
          TWITR_MAX_PER_CALL_USD the most one call may pay (default 0.05)
          TWITR_API_BASE         where twitr lives (tests)
        """
        account = hand.setting("TWITR_ACCOUNT") or hand.setting("X_BOT_USERNAME") or ""
        cap = _usd_to_atomic(hand.setting("TWITR_MAX_PER_CALL_USD", "0.05") or "0.05", "TWITR_MAX_PER_CALL_USD")
        return cls(
            hand.setting("TWITR_API_BASE", TWITR_BASE) or TWITR_BASE,
            account,
            hand.setting("TWITR_NETWORK", DEFAULT_NETWORK) or DEFAULT_NETWORK,
            cap,
            core,
        )

    # --- transport ----------------------------------------------------------------------

    def call(
        self,
        method: str,
        path: str,
        body: Any = None,
        price: int = FLOOR,
        idempotency: str | None = None,
        sign_in: bool = False,
    ) -> Json:
        """One request through the core. `price` is the most it may pay, in
        USDC atomic units; above TWITR_MAX_PER_CALL_USD it is refused here,
        before anything leaves. `sign_in` lets the core answer a SIWX
        challenge (the free account endpoints)."""
        if price > self._max_call:
            raise hand.Refused(
                f"this call may cost ${price / 1e6:.4f}, over TWITR_MAX_PER_CALL_USD (${self._max_call / 1e6:.4f})",
                rule="twitr_per_call",
            )
        headers = {"content-type": "application/json", "accept": "application/json"}
        if idempotency:
            headers["Idempotency-Key"] = idempotency
        params: dict[str, Any] = {
            "url": self._base + path,
            "method": method,
            "headers": headers,
            "maxAmountAtomic": price,
            "network": self.network,
        }
        if body is not None:
            params["body"] = json.dumps(body)
        if sign_in:
            params["signIn"] = True
        with hand.lock("twitr"):
            try:
                answer = self._core("x402_fetch", params, timeout=120)
            except hand.CoreError as exc:
                text = str(exc)
                if "X402_REFUSED" in text:
                    # Refused before signing: nothing was paid or sent on.
                    raise TwitrError("the wallet refused to pay twitr (over a cap)", 402) from None
                # The core may have paid and lost the answer: unknown.
                raise TwitrError("twitr call failed in the core; whether it went out is unknown") from None
        if not isinstance(answer, Mapping):
            raise TwitrError("the core answered something unreadable for twitr")
        status = answer.get("status")
        text = answer.get("body") if isinstance(answer.get("body"), str) else ""
        if not isinstance(status, int):
            raise TwitrError("the core answered twitr without a status")
        if status == 402:
            raise TwitrError(f"twitr still asks for payment{': ' + _title(text) if _title(text) else ''}", 402)
        if status >= 400:
            title = _title(text)
            raise TwitrError(f"twitr answered {status}: {title}" if title else f"twitr answered {status}", status)
        if answer.get("truncated"):
            raise TwitrError("twitr's answer was too large to read; ask for fewer results", None)
        try:
            parsed = json.loads(text or "{}")
        except ValueError:
            parsed = None
        if not isinstance(parsed, dict):
            raise TwitrError("twitr sent something unreadable", None)
        if status == 202:
            parsed.setdefault("status", "pending_confirmation")
        return parsed

    def tool(self, name: str, body: Mapping[str, Any], price: int, idempotency: str | None = None) -> Json:
        return self.call("POST", f"/api/tools/{name}", dict(body), price=price, idempotency=idempotency)

    # --- reads -----------------------------------------------------------------------

    def get_post(self, post_id: str) -> Json | None:
        response = self.tool("x_read", {"resource": "get-tweet", "id": post_id}, FLOOR)
        items = response.get("items") if isinstance(response.get("items"), list) else []
        return to_post(items[0]) if items else None

    def search_page(self, query: str, options: SearchOptions) -> Json:
        wanted = min(max(options.max_results if options.max_results is not None else 10, 1), MAX_RESULTS)
        body: Json = {"type": "search-tweets", "q": query, "resultsLimit": wanted, "queryType": "Latest"}
        if options.sort_order == "relevancy":
            body["queryType"] = "Top"
        if options.start_time:
            body["sinceDate"] = options.start_time[:10]
        if options.end_time:
            body["untilDate"] = options.end_time[:10]
        if options.since_id:
            body["advancedQuery"] = f"since_id:{options.since_id}"
        response = self.tool("x_search", body, max(FLOOR, wanted * PER_RECORD))
        posts = [p for p in (to_post(i) for i in response.get("items") or []) if p]
        if options.since_id and options.since_id.isdigit():
            posts = [p for p in posts if p["id"].isdigit() and int(p["id"]) > int(options.since_id)]
        newest = max((p["id"] for p in posts if p["id"].isdigit()), key=int, default=None)
        return {"posts": posts, "nextToken": None, "newestId": newest}

    def search_posts(self, query: str, options: SearchOptions) -> list[Json]:
        return self.search_page(query, options)["posts"]

    def get_conversation(self, conversation_id: str, max_results: int | None = None, since_id: str | None = None) -> list[Json]:
        options = SearchOptions(max_results=max_results if max_results is not None else 25, since_id=since_id)
        return self.search_posts(f"conversation_id:{conversation_id}", options)

    def user_by_username(self, username: str) -> Json | None:
        handle = username.strip().lstrip("@")
        response = self.tool("x_read", {"resource": "get-user", "id": handle}, FLOOR)
        items = response.get("items") if isinstance(response.get("items"), list) else []
        return to_user(items[0], handle) if items else None

    # --- raw writes (only teeny_hands.x.publish reaches these) -----------------------------

    def _write(self, action: str, fields: Mapping[str, Any], key: str) -> Json:
        if not self.account:
            raise hand.HandError("TWITR_ACCOUNT (or X_BOT_USERNAME) is not set: twitr writes need the linked handle")
        body = {"action": action, "account": self.account, **fields}
        return self.tool("x_write", body, WRITE, idempotency=key)

    def create_post(self, text: str, reply_to: str | None = None, quote: str | None = None, media_ids: Sequence[str] = ()) -> str:
        if media_ids:
            # X's media ids belong to X's upload, not twitr's.
            raise hand.Refused("posts with media go out through the X API", rule="twitr_media")
        if quote:
            text = f"{text} https://x.com/i/status/{quote}"
        if reply_to:
            response = self._write("reply", {"text": text, "reply_to_tweet_id": reply_to}, idempotency_key("reply", self.account, reply_to, text))
        else:
            response = self._write("post", {"text": text}, idempotency_key("post", self.account, text))
        post_id = _written_id(response)
        if not post_id:
            # Accepted (a 202 is paid and applying) but no id: whether it is
            # out is unknown, and it must not be sent again.
            raise TwitrError("twitr accepted the post but named no id; it may be out", None)
        return post_id

    def delete_post(self, post_id: str) -> bool:
        response = self._write("delete_tweet", {"target_tweet_id": post_id}, idempotency_key("delete", self.account, post_id))
        return response.get("status") != "failed"

    def like_post(self, post_id: str, user_id: str | None = None) -> None:
        self._write("like", {"target_tweet_id": post_id}, idempotency_key("like", self.account, post_id))

    # --- the linked account (free, signed in with the wallet) --------------------------------

    def connect(self, handle: str) -> Json:
        """Start linking `handle` to this wallet: a one-time link for the
        operator to open and sign in to X there. Teeny never sees a password."""
        response = self.call("POST", "/api/x-accounts/start", {"username": handle.strip().lstrip("@")}, price=FREE, sign_in=True)
        return {k: response[k] for k in ("handle", "connect_url", "expires_in") if k in response}

    def accounts(self) -> Json:
        response = self.call("GET", "/api/x-accounts", price=FREE, sign_in=True)
        return {"accounts": response.get("accounts") or []}


# --- routing -----------------------------------------------------------------------------


class Routed:
    """XApi with some calls sent to twitr instead. Reads go to twitr when
    X_TWITR_READS is on and fall back to the X API when twitr fails (a read
    is safe to repeat). Writes go to twitr when X_TWITR_WRITES is on and
    never fall back: a write that failed on twitr may still be out, and
    sending it again through X would post it twice. Posts with media, the
    mentions timeline and "who am I" stay on the X API (an owned read there
    is $0.001 a post, below twitr's floor)."""

    READS = ("get_post", "search_page", "search_posts", "get_conversation", "user_by_username")
    WRITES = ("create_post", "delete_post", "like_post")

    def __init__(self, x: Any, twitr: TwitrApi, reads: bool, writes: bool) -> None:
        self._x = x
        self._twitr = twitr
        self._reads = reads
        self._writes = writes

    def __repr__(self) -> str:
        return f"Routed({self._x!r}, reads={'twitr' if self._reads else 'x'}, writes={'twitr' if self._writes else 'x'})"

    @property
    def username(self) -> str:
        return self._x.username

    def __getattr__(self, name: str) -> Any:
        if name in self.READS and self._reads:
            return self._read(name)
        if name in self.WRITES and self._writes:
            if name == "create_post":
                return self._create_post
            return getattr(self._twitr, name)
        return getattr(self._x, name)

    def _read(self, name: str) -> Callable[..., Any]:
        def read(*args: Any, **kwargs: Any) -> Any:
            try:
                return getattr(self._twitr, name)(*args, **kwargs)
            except (TwitrError, hand.Refused) as exc:
                _warn(f"twitr {name} failed ({exc}); reading from X instead")
                return getattr(self._x, name)(*args, **kwargs)

        return read

    def _create_post(self, text: str, reply_to: str | None = None, quote: str | None = None, media_ids: Sequence[str] = ()) -> str:
        if media_ids:
            return self._x.create_post(text, reply_to=reply_to, quote=quote, media_ids=media_ids)
        return self._twitr.create_post(text, reply_to=reply_to, quote=quote)


def client(core: Core = hand.core) -> Any:
    """The X client the configuration asks for: plain XApi unless
    X_TWITR_READS or X_TWITR_WRITES is on."""
    from .client import XApi

    x = XApi.from_settings()
    reads, writes = hand.flag("X_TWITR_READS", False), hand.flag("X_TWITR_WRITES", False)
    if not (reads or writes):
        return x
    return Routed(x, TwitrApi.from_settings(core), reads, writes)


def _warn(message: str) -> None:
    import sys

    print(f"hands/x: {message}", file=sys.stderr)
