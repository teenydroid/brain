"""The X v2 client: the transport, the two ways it authenticates, the reads
and the raw writes. Ported from crates/teeny-x/src/{client,writes,error}.rs.

Reads go out on the app's bearer when there is one and on the account's
OAuth 1.0a pair otherwise. A few calls are the account's to make and always
sign as it: "who am I" and every write.

The raw writes here run no rule. The one way to publish is
`teeny_hands.x.publish`, which runs the guard, the posting rules and
`DRY_RUN` first; the command line never reaches these directly.

Nothing that leaves this module names a URL, a header or a key. An error
says which status X answered and X's own title for it, because a URL can carry
a query the model wrote and a header carries the keys. Exceptions from
urllib are not chained either, since their repr carries the URL.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Any, Mapping, Sequence

from .. import hand
from . import model
from .model import Json, SearchOptions
from .oauth import OAuth1, percent_encode

#: Where X lives. `X_API_BASE` in secrets/teeny.env moves it (the tests'
#: fake X).
X_API_BASE = "https://api.x.com"

#: The post fields every read asks for. The counters ride along on the same
#: request: a field, not a post, so nothing extra is billed.
#: `in_reply_to_user_id` is asked for as a field, not an expansion: X bills
#: every user object a response carries ($0.010 each), and expanding it would
#: send the account's own user back with every reply it reads.
POST_FIELDS = "created_at,conversation_id,text,attachments,public_metrics,in_reply_to_user_id"
#: Photos carry `url`; videos and GIFs only a preview frame.
MEDIA_FIELDS = "media_key,type,url,preview_image_url,alt_text"
USER_FIELDS = "username"
#: What a lookup by handle asks for about the account.
USER_LOOKUP_FIELDS = "created_at,description,public_metrics,verified"
EXPANSIONS = "author_id,attachments.media_keys"

#: How long one call may take. A post that times out may still have gone
#: out, which is why the reply path treats a transport error as "in doubt".
TIMEOUT_SECONDS = 30.0
#: How much of X's own error wording is kept.
_TITLE_CHARS = 200


def number_setting(name: str, default: float, low: float, high: float) -> float:
    """A numeric switch from secrets/teeny.env, held to its range. A value
    that does not parse is the default, as the Rust config read it: a typo
    must not turn a rule off."""
    raw = hand.setting(name)
    try:
        value = float(raw) if raw is not None else default
    except ValueError:
        return default
    if value != value:  # NaN
        return default
    return min(max(value, low), high)


class XError(hand.HandError):
    """A failed X call. `status` is X's HTTP status, or None when X was not
    reached or answered something unreadable (the outcome is then unknown)."""

    def __init__(self, message: str, status: int | None = None) -> None:
        super().__init__(message)
        self.status = status


def _title(body: bytes) -> str | None:
    """X's own name for an error: the problem's `title`, else the first
    error's `title` or `message`. Only these short fields are kept: the rest
    of a body can echo the request back."""
    try:
        parsed = json.loads(body)
    except (ValueError, UnicodeDecodeError):
        return None
    if not isinstance(parsed, Mapping):
        return None
    candidates = [parsed.get("title")]
    errors = parsed.get("errors")
    if isinstance(errors, list) and errors and isinstance(errors[0], Mapping):
        candidates += [errors[0].get("title"), errors[0].get("message")]
    for candidate in candidates:
        if isinstance(candidate, str) and candidate.strip():
            return candidate.strip()[:_TITLE_CHARS]
    return None


def http_error(status: int, body: bytes) -> XError:
    title = _title(body)
    return XError(f"X answered {status}: {title}" if title else f"X answered {status}", status)


def _errors_summary(response: Mapping[str, Any]) -> str:
    """The titles of a 2xx answer's partial `errors`, for "X did not return
    the one thing the call was for"."""
    errors = response.get("errors")
    names = []
    for error in errors if isinstance(errors, list) else []:
        if isinstance(error, Mapping):
            name = error.get("title") or error.get("message") or error.get("detail")
            if isinstance(name, str):
                names.append(name[:_TITLE_CHARS])
    return "; ".join(names) or "no reason given"


class XApi:
    """A client for the slice of X v2 Teeny uses, acting as the account."""

    def __init__(self, base: str, oauth: OAuth1, bearer: str | None, username: str) -> None:
        self._base = base.rstrip("/")
        self._oauth = oauth
        self._bearer = bearer or None
        self.username = username.lstrip("@")

    def __repr__(self) -> str:
        return f"XApi(@{self.username})"

    @classmethod
    def from_settings(cls) -> "XApi":
        """The client secrets/teeny.env describes. A missing key is named,
        never shown."""
        oauth = OAuth1(
            hand.require("X_API_KEY"),
            hand.require("X_API_SECRET"),
            hand.require("X_ACCESS_TOKEN"),
            hand.require("X_ACCESS_TOKEN_SECRET"),
        )
        return cls(
            hand.setting("X_API_BASE", X_API_BASE) or X_API_BASE,
            oauth,
            hand.setting("X_BEARER_TOKEN"),
            hand.setting("X_BOT_USERNAME", "") or "",
        )

    # --- transport ------------------------------------------------------------------

    def call(
        self,
        method: str,
        path: str,
        query: Sequence[tuple[str, str]] = (),
        body: Any = None,
        user: bool = False,
    ) -> Json:
        """Sends one request and reads its JSON object. `path` starts with
        `/2/`. `user` signs as the account; otherwise the app bearer is used
        when there is one."""
        url = self._base + path
        if query:
            url += "?" + "&".join(f"{percent_encode(k)}={percent_encode(v)}" for k, v in query)
        if not user and self._bearer:
            authorization = f"Bearer {self._bearer}"
        else:
            authorization = self._oauth.header(method, url)
        headers = {"authorization": authorization, "user-agent": "teeny-hands-x"}
        data = None
        if body is not None:
            data = json.dumps(body).encode()
            headers["content-type"] = "application/json"
        request = urllib.request.Request(url, data=data, headers=headers, method=method)
        failure: XError | None = None
        try:
            with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as response:
                text = response.read()
        except urllib.error.HTTPError as exc:
            try:
                error_body = exc.read()
            except OSError:
                error_body = b""
            failure = http_error(exc.code, error_body)
        except (urllib.error.URLError, OSError, ValueError) as exc:
            # The reason's class only: its text can name the host.
            failure = XError(f"could not reach X ({type(getattr(exc, 'reason', exc)).__name__})")
        if failure is not None:
            # Raised outside the except block, so the urllib error (whose
            # repr names the URL) is neither chained nor in the context.
            raise failure
        try:
            parsed = json.loads(text or b"{}")
        except (ValueError, UnicodeDecodeError):
            parsed = None
        if not isinstance(parsed, dict):
            raise XError("X sent something unreadable")
        return parsed

    # --- reads -------------------------------------------------------------------------

    def get_post(self, post_id: str) -> Json | None:
        """One post with its author, attachments and counters; None when X
        does not have it (deleted, protected, not yet visible)."""
        response = self.call("GET", f"/2/tweets/{percent_encode(post_id)}", _post_query())
        posts = model.to_posts([response.get("data")], response.get("includes"))
        return posts[0] if posts else None

    def search_page(self, query: str, options: SearchOptions) -> Json:
        """One page of a recent search (the last 7 days)."""
        # X's floor is 10, so asking for fewer still bills for 10.
        wanted = options.max_results if options.max_results is not None else 10
        params = [("query", query), ("max_results", str(min(max(wanted, 10), 100)))]
        for name, value in (
            ("since_id", options.since_id),
            ("start_time", options.start_time),
            ("end_time", options.end_time),
            ("sort_order", options.sort_order),
            ("pagination_token", options.pagination_token),
        ):
            if value is not None:
                params.append((name, value))
        params += _post_query()
        response = self.call("GET", "/2/tweets/search/recent", params)
        meta = response.get("meta") if isinstance(response.get("meta"), Mapping) else {}
        page: Json = {"posts": model.to_posts(response.get("data"), response.get("includes"))}
        page["nextToken"] = meta.get("next_token")
        page["newestId"] = meta.get("newest_id")
        return page

    def search_posts(self, query: str, options: SearchOptions) -> list[Json]:
        """The first page of a recent search."""
        return self.search_page(query, options)["posts"]

    def mentions_page(self, user_id: str, since_id: str | None = None, max_results: int = 20) -> Json:
        """The newest page of posts that mention the account, after
        `since_id` when given. Replies to the account's posts are mentions
        too, so this is also how it reads its replies.

        Signed as the account, never on the app bearer: X bills the account's
        own timelines read with its own user token as "owned reads", $0.001 a
        post instead of $0.005 (docs.x.com, pricing). The id must be the
        authenticated account's."""
        params = [("max_results", str(min(max(max_results, 5), 100)))]
        if since_id:
            params.append(("since_id", since_id))
        params += _post_query()
        response = self.call("GET", f"/2/users/{percent_encode(user_id)}/mentions", params, user=True)
        meta = response.get("meta") if isinstance(response.get("meta"), Mapping) else {}
        return {
            "posts": model.to_posts(response.get("data"), response.get("includes")),
            "newestId": meta.get("newest_id"),
        }

    def get_conversation(
        self, conversation_id: str, max_results: int | None = None, since_id: str | None = None
    ) -> list[Json]:
        """Replies under a post, found through its conversation id; 25 unless
        told. `since_id` reads only the replies newer than one already seen:
        a search bills every post it returns, so a re-read pays only for what
        is new."""
        options = SearchOptions(max_results=max_results if max_results is not None else 25, since_id=since_id)
        return self.search_posts(f"conversation_id:{conversation_id}", options)

    def identity(self) -> Json:
        """The account the credentials act as, from `/2/users/me`. Signed as
        the account: the app bearer identifies the app."""
        response = self.call("GET", "/2/users/me", [("user.fields", "username,public_metrics")], user=True)
        identity = model.to_identity(response.get("data"), self.username)
        if identity is None:
            raise XError(f"X did not identify the authenticated account: {_errors_summary(response)}")
        return identity

    def user_by_username(self, username: str) -> Json | None:
        """The account behind `username` (a leading `@` is ignored); None when
        X has no such account."""
        handle = username.strip().lstrip("@")
        response = self.call(
            "GET", f"/2/users/by/username/{percent_encode(handle)}", [("user.fields", USER_LOOKUP_FIELDS)]
        )
        return model.to_user(response.get("data"), handle)

    # --- raw writes (only teeny_hands.x.publish calls these) ---------------------------------

    def create_post(
        self,
        text: str,
        reply_to: str | None = None,
        quote: str | None = None,
        media_ids: Sequence[str] = (),
    ) -> str:
        """Publishes a post, attached to whatever is named; the new post's id."""
        body: Json = {"text": text}
        if reply_to:
            body["reply"] = {"in_reply_to_tweet_id": reply_to}
        if quote:
            body["quote_tweet_id"] = quote
        if media_ids:
            body["media"] = {"media_ids": list(media_ids)}
        response = self.call("POST", "/2/tweets", body=body, user=True)
        data = response.get("data")
        post_id = data.get("id") if isinstance(data, Mapping) else None
        if not isinstance(post_id, str) or not post_id:
            # X said 2xx but named no post: whether it is out is unknown.
            raise XError(f"X did not return a post id: {_errors_summary(response)}")
        return post_id

    def delete_post(self, post_id: str) -> bool:
        """Deletes one of the account's posts. False when X did not say it did."""
        response = self.call("DELETE", f"/2/tweets/{percent_encode(post_id)}", user=True)
        data = response.get("data")
        return bool(isinstance(data, Mapping) and data.get("deleted") is True)

    def like_post(self, post_id: str, user_id: str) -> None:
        """Likes a post as the account (`user_id`). Idempotent on X's side."""
        response = self.call(
            "POST", f"/2/users/{percent_encode(user_id)}/likes", body={"tweet_id": post_id}, user=True
        )
        if isinstance(response.get("errors"), list) and response["errors"]:
            raise XError(f"X refused the like: {_errors_summary(response)}")


def _post_query() -> list[tuple[str, str]]:
    return [
        ("tweet.fields", POST_FIELDS),
        ("user.fields", USER_FIELDS),
        ("media.fields", MEDIA_FIELDS),
        ("expansions", EXPANSIONS),
    ]
