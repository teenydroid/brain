"""Links the account is allowed to publish.

A link in a reply is the one thing on a timeline that does something when
tapped, and a drainer is a page that looks like the project's own site
until a wallet connects to it. Nothing in the drafter's context is trusted,
so the rule is not "do not repeat suspicious links", which asks the model
to spot them. The rule is a list: a host is on it or the link does not go
out. Everything else here is about not being fooled into thinking a link
is on the list. A port of `crates/teeny-safety/src/links.rs` (itself
`legacy/src/safety/links.ts`).
"""

from __future__ import annotations

import re
import urllib.parse
from dataclasses import dataclass, field
from typing import Iterable

from .. import hand


@dataclass(frozen=True)
class AllowlistEntry:
    """One allowlist entry."""

    # Lower-case host. Matches itself and any subdomain.
    host: str
    # When set, the URL's path must start with this, as whole segments.
    path_prefix: str | None = None
    # Only the bare host with no path is allowed: `pump.fun/` in config.
    root_only: bool = False


# What the account may link to without anyone adding to the list: its own
# sites, code and community, and the platforms it is on. The launchpad is
# root-only, so the origin story can name it and a stranger's coin page on
# it still cannot go out.
DEFAULT_LINK_ALLOWLIST: tuple[str, ...] = (
    "tinyhumans.ai",
    "tinyhumansworld.com",
    "tiny.place",
    "github.com/tinyhumansai",
    "x.com",
    "twitter.com",
    "t.me/TinyHumanAi",
    "pump.fun/",
    # Teeny's own code, its Moltbook, its inbox, and the
    # explorer its wallet's receipts are read on.
    "github.com/teenydroid",
    "moltbook.com",
    "www.moltbook.com",
    "agentmail.to",
    "solscan.io",
)


def parse_allowlist(entries: Iterable[str]) -> list[AllowlistEntry]:
    """Reads `host[/path]` entries, as `LINK_ALLOWLIST` and the operator write them."""
    parsed = []
    for raw in entries:
        entry = raw.strip().lower()
        for scheme in ("https://", "http://"):
            if entry.startswith(scheme):
                entry = entry[len(scheme) :]
                break
        if not entry:
            continue
        host, slash, path = entry.partition("/")
        if not slash:
            parsed.append(AllowlistEntry(host))
        elif path == "":
            parsed.append(AllowlistEntry(host, root_only=True))
        else:
            parsed.append(AllowlistEntry(host, path_prefix=("/" + path).rstrip("/")))
    return parsed


def split_setting(value: str) -> list[str]:
    """`LINK_ALLOWLIST` as a list. The Rust config split on commas; whitespace
    separates too here, because no entry can contain it and a list pasted
    one per line should not become one broken entry."""
    return [entry for entry in re.split(r"[,\s]+", value) if entry]


def configured_allowlist() -> list[AllowlistEntry]:
    """The built-in list plus `LINK_ALLOWLIST` from secrets/teeny.env."""
    extra = split_setting(hand.setting("LINK_ALLOWLIST", "") or "")
    return parse_allowlist([*DEFAULT_LINK_ALLOWLIST, *extra])


# Common enough that a bare "name.tld" in a post is a link everybody taps.
LINKABLE_TLDS = frozenset(
    """
    com net org io ai co me app dev xyz fun place so gg to ly sh link site online info biz tv cc
    finance exchange cash money crypto sol eth wtf lol club live life world zone network tech
    digital cloud page run one top pro vip win bet best store shop space website today news art
    design us uk de fr ru cn in jp br ca au nl ch es it se no fi pl kr tw hk sg id ph vn th tr ua
    cz be at dk ie pt gr nz za mx ar cl ng ke eu is im ac st fm am ws tk ml ga cf gq pw icu click
    monster rest bond cyou buzz cam quest sbs lat mov zip work guru ninja email team trade markets
    capital fund ventures partners solutions services agency studio games game social chat wiki
    blog tools systems foundation dao nft coin token wallet swap dex defi
    """.split()
)

_DOTS = re.compile(r"\[\s*\.\s*\]|\(\s*\.\s*\)|\{\s*\.\s*\}|\s*\(dot\)\s*|\s*\[dot\]\s*|\s+dot\s+", re.IGNORECASE)
_HXXP = re.compile(r"hxxps?://", re.IGNORECASE)
_SPACED_SCHEME = re.compile(r"h\s*t\s*t\s*p\s*(s?)\s*:\s*/\s*/", re.IGNORECASE)


def deobfuscate(text: str) -> str:
    """Ways people write a link so a filter will not see one, folded back into a
    plain URL: "tinyhumans[.]ai", "tinyhumans(dot)ai", "hxxps://"."""
    text = _DOTS.sub(".", text)
    text = _HXXP.sub(lambda m: m.group(0).replace("x", "t").replace("X", "t"), text)
    return _SPACED_SCHEME.sub(r"http\1://", text)


# `(?<![\w@.-])` in the legacy pattern, `\w` being ASCII there: a link glued to
# a word, an address or a handle is not a link of its own.
_URL_PATTERN = re.compile(
    r"(?<![A-Za-z0-9_@.\-])"
    r"""(?:(?:[hH][tT][tT][pP][sS]?://|[wW][wW][wW]\.)[^\s<>"'()\[\]{}]+"""
    r"""|(?:[a-zA-Z0-9-]+\.)+[a-zA-Z]{2,24}(?::[0-9]{2,5})?(?:/[^\s<>"'()\[\]{}]*)?)"""
)


def _has_scheme(raw: str) -> bool:
    lower = raw[:8].lower()
    return lower.startswith("http://") or lower.startswith("https://")


@dataclass(frozen=True)
class Url:
    """An http(s) URL as a browser reads it: the parts the allowlist looks at."""

    scheme: str
    username: str
    password: str
    host: str
    port: int | None
    path: str
    query: str | None
    fragment: str | None


_SCHEME = re.compile(r"^([a-zA-Z][a-zA-Z0-9+.\-]*):(.*)$", re.DOTALL)
_DEFAULT_PORTS = {"http": 80, "https": 443}
# What a WHATWG host may not contain once percent-decoded.
_FORBIDDEN_HOST = set(" #%/:<>?@[\\]^|\t\n\r\x00")


def parse_url(absolute: str) -> Url | None:
    """The URL as the WHATWG parser the Rust used (`url::Url`) reads it, for
    the shapes that decide whether a link is what it claims: the last `@`
    ends the credentials, a backslash is a slash, the scheme's own port is no
    port, and a non-ASCII host is its punycode. `None` when a browser would
    not open it."""
    found = _SCHEME.match(absolute.strip())
    if not found:
        return None
    scheme = found.group(1).lower()
    if scheme not in _DEFAULT_PORTS:
        return None
    rest = found.group(2).replace("\\", "/").lstrip("/")
    end = len(rest)
    for stop in "/?#":
        at = rest.find(stop)
        if at != -1:
            end = min(end, at)
    authority, tail = rest[:end], rest[end:]
    username = password = ""
    if "@" in authority:
        userinfo, _, authority = authority.rpartition("@")
        username, _, password = userinfo.partition(":")
    host, colon, port_text = authority.partition(":")
    port: int | None = None
    if colon and port_text:
        if not port_text.isascii() or not port_text.isdigit() or int(port_text) > 65535:
            return None
        port = int(port_text)
        if port == _DEFAULT_PORTS[scheme]:
            port = None
    host = urllib.parse.unquote(host)
    if not host.isascii():
        try:
            host = host.encode("idna").decode("ascii")
        except UnicodeError:
            return None
    host = host.lower()
    if not host or any(c in _FORBIDDEN_HOST for c in host):
        return None
    fragment: str | None = None
    query: str | None = None
    if "#" in tail:
        tail, _, fragment = tail.partition("#")
    if "?" in tail:
        tail, _, query = tail.partition("?")
    return Url(scheme, username, password, host, port, tail or "/", query, fragment)


@dataclass(frozen=True)
class FoundLink:
    """One link found in a text."""

    # As it appeared (de-obfuscated), so it can be cut out again.
    raw: str
    # Absolute, with a scheme; None when it did not parse as a URL.
    url: Url | None


def find_links(text: str) -> list[FoundLink]:
    """Every link in the text, obfuscated ones included. A bare "name.tld" counts
    only when the tld is one people actually get links on, so "node.js" and
    "v1.2" are words; anything with a scheme, a www, or a path is a link."""
    found = []
    for match in _URL_PATTERN.finditer(deobfuscate(text)):
        raw = match.group(0).rstrip(".,;:!?")
        if not raw:
            continue
        explicit = _has_scheme(raw) or raw[:4].lower() == "www."
        if not explicit:
            host = re.split(r"[/:?#]", raw)[0]
            tld = host.rsplit(".", 1)[-1].lower()
            # With a path, a made-up tld is still worth stripping: it costs a
            # word and it cannot be tapped anyway.
            if "/" not in raw and tld not in LINKABLE_TLDS:
                continue
        absolute = raw if _has_scheme(raw) else f"https://{raw}"
        found.append(FoundLink(raw, parse_url(absolute)))
    return found


_ASCII_HOST = re.compile(r"^[a-z0-9.\-]+\Z")
_IPV4 = re.compile(r"^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+\Z")


def judge_link(link: FoundLink, allowlist: list[AllowlistEntry]) -> str | None:
    """Whether one link may go out: None when it may, else why not, for the
    log and the hold.

    The shape of the URL is checked before the host, because every trick for
    making a bad host look like a good one lives in the shape:
    "tinyhumans.ai@evil.example", "tinyhumans.ai.evil.example", a Cyrillic
    "а" in the name, a raw IP, a port."""
    url = link.url
    if url is None:
        return "not a parseable URL"
    if url.scheme not in ("https", "http"):
        return f"scheme {url.scheme}: is not http(s)"
    if url.username or url.password:
        return "credentials in the URL"
    host = url.host
    if not _ASCII_HOST.match(host):
        return "non-ascii host"
    if any(label.startswith("xn--") for label in host.split(".")):
        return "punycode host"
    if _IPV4.match(host):
        return "raw IP address"
    if url.port is not None:
        return "explicit port"

    path = url.path.rstrip("/")
    has_query = bool(url.query)
    has_fragment = bool(url.fragment)
    for entry in allowlist:
        if host != entry.host and not host.endswith(f".{entry.host}"):
            continue
        if entry.root_only:
            if not path and not has_query and not has_fragment:
                return None
            continue
        if entry.path_prefix is not None:
            prefix = entry.path_prefix.lower()
            lower = path.lower()
            if lower == prefix or lower.startswith(f"{prefix}/"):
                return None
            continue
        return None
    return f"{host} is not on the link allowlist"


@dataclass
class LinkFilterResult:
    """A text with its disallowed links cut."""

    text: str
    # Each link that was cut, with why ({"link", "reason"}). Empty when the
    # text was already clean.
    removed: list[dict[str, str]] = field(default_factory=list)
    # Links that were checked and left in.
    kept: list[str] = field(default_factory=list)


_RUNS_OF_BLANKS = re.compile(r"[ \t]{2,}")
_BLANKS_BEFORE_PUNCTUATION = re.compile(r"[ \t]+([,.!?])")
_EMPTY_BRACKETS = re.compile(r"\(\s*\)")


def filter_links(text: str, allowlist: list[AllowlistEntry]) -> LinkFilterResult:
    """The text with every disallowed link cut out. Cutting rather than refusing,
    because a reply is almost never about its link: "check the repo:
    github.com/evil" is still a reply without the tail. The caller decides
    whether what is left is worth sending."""
    removed: list[dict[str, str]] = []
    kept: list[str] = []
    cleaned = deobfuscate(text)
    for link in find_links(text):
        reason = judge_link(link, allowlist)
        if reason is None:
            kept.append(link.raw)
        else:
            cleaned = cleaned.replace(link.raw, "")
            removed.append({"link": link.raw, "reason": reason})
    if not removed:
        return LinkFilterResult(text, removed, kept)
    tidied = _RUNS_OF_BLANKS.sub(" ", cleaned)
    tidied = _BLANKS_BEFORE_PUNCTUATION.sub(r"\1", tidied)
    tidied = _EMPTY_BRACKETS.sub("", tidied)
    return LinkFilterResult(tidied.strip(), removed, kept)
