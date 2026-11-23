"""One GET of a public web page, for the broker's /fetch.

Teeny's jailed code (a `home/tools/` tool in bin/box, the mind) reads the
web through this, outside its jail: the box gives a tool no network but the
broker's port. It is a read and nothing more, so it can carry nothing of
Teeny's out:

  the request   GET, no body, no cookies, no key, no Referer, a fixed
                User-Agent, `Accept-Encoding: identity` (what is read is
                what is counted), never through a proxy
  the URL       https only, port 443, no credentials, at most MAX_URL
                characters of printable ASCII; a public host name, never an
                address or a local name (teeny_hands.websites.open_web), and
                never a host on the operator's deploy/browser-deny.txt or
                under one (websites.denied); no deny list, no fetch
  the address   the name is resolved once, here, and every address it
                answers must be public: not loopback, private, link-local
                (the cloud metadata address 169.254.169.254 among them),
                CGNAT, multicast, reserved or unspecified, not IPv4-mapped,
                6to4 or Teredo, and a NAT64 address only for a public IPv4
                one. The connection goes to the address that was checked,
                with the name as TLS's SNI and the Host header, so a second
                DNS answer (a rebinding) is never asked for
  redirects     at most MAX_REDIRECTS, each new URL held to every rule above;
                one that breaks a rule ends the fetch
  the answer    the final URL, the HTTP status, the content type and the
                text: only text types (text/*, application/json,
                application/xml, application/*+json, application/*+xml),
                decoded by their charset with bad bytes replaced. At most
                MAX_BYTES are read; past that the text is cut and
                `truncated` is true. TOTAL_SECS for everything, redirects
                included

`fetch` calls `on_send` just before each request leaves (the broker's day
count, which may refuse); nothing is counted for a URL refused before that.
`_resolve` and `_connect` are the two seams the tests replace.
"""

from __future__ import annotations

import codecs
import contextlib
import http.client
import ipaddress
import socket
import ssl
import sys
import threading
import time
from pathlib import Path
from typing import Any, Callable, Iterator
from urllib.parse import urljoin, urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "hands" / "lib"))

from teeny_hands import websites  # noqa: E402

MAX_URL = 2000
MAX_BYTES = 2 * 1024 * 1024
TOTAL_SECS = 30
MAX_REDIRECTS = 5
USER_AGENT = "TeenyFetch/1.0 (a read-only page reader; https://github.com/teenydroid/brain)"
REDIRECTS = (301, 302, 303, 307, 308)
TEXT_TYPES = ("application/json", "application/xml")
ACCEPT = "text/*, application/json, application/xml, application/*+json, application/*+xml"
_NAT64 = ipaddress.ip_network("64:ff9b::/96")


class Refused(Exception):
    """A URL, an address or an answer a rule will not read; says which."""


class Failed(Exception):
    """The page could not be read: the network, TLS, or the clock."""


def _resolve(host: str) -> list[str]:
    """Every address the name answers for a TCP connection."""
    return sorted({info[4][0] for info in socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)})


def _connect(ip: str, host: str, timeout: float) -> socket.socket:
    """TLS to IP on 443, checked against HOST's certificate."""
    raw = socket.create_connection((ip, 443), timeout=timeout)
    try:
        return ssl.create_default_context().wrap_socket(raw, server_hostname=host)
    except BaseException:
        raw.close()
        raise


def public(address: str) -> bool:
    """Whether ADDRESS is one the open internet routes to someone else."""
    try:
        ip = ipaddress.ip_address(address.split("%", 1)[0])
    except ValueError:
        return False
    if isinstance(ip, ipaddress.IPv6Address):
        if ip.ipv4_mapped is not None or ip.sixtofour is not None or ip.teredo is not None:
            return False
        if ip in _NAT64:
            return public(str(ipaddress.IPv4Address(int(ip) & 0xFFFFFFFF)))
    return ip.is_global and not ip.is_multicast


def _deny_list() -> list[str]:
    try:
        return websites.load_deny()
    except (OSError, ValueError) as exc:
        raise Refused(f"the deny list deploy/browser-deny.txt cannot be read ({exc}): nothing is fetched") from None


def check_url(url: Any, deny: list[str]) -> tuple[str, str]:
    """The host and the request target of URL, or Refused."""
    if not isinstance(url, str) or not url:
        raise Refused("url is an https URL")
    if len(url) > MAX_URL:
        raise Refused(f"the URL is {len(url)} characters; the most is {MAX_URL}")
    if any(not 0x20 < ord(c) < 0x7F for c in url):
        raise Refused("the URL holds spaces, control or non-ASCII characters: percent-encode them")
    why = websites.open_web(url)
    if why:
        raise Refused(f"{why}: {url[:200]}")
    parts = urlsplit(url)
    if "@" in parts.netloc:
        raise Refused("no credentials in the URL")
    try:
        port = parts.port
    except ValueError:
        port = -1
    if port not in (None, 443):
        raise Refused("only port 443")
    host = websites.host_of(url)
    if websites.denied(url, deny):
        raise Refused(f"{host} is on the operator's deny list (deploy/browser-deny.txt)")
    target = (parts.path or "/") + (f"?{parts.query}" if parts.query else "")
    return host, target


def address_of(host: str) -> str:
    """The one address to connect to: HOST resolved once, every answer public."""
    try:
        addresses = _resolve(host)
    except (OSError, UnicodeError) as exc:
        raise Refused(f"{host} does not resolve ({exc})") from None
    if not addresses:
        raise Refused(f"{host} does not resolve")
    for address in addresses:
        if not public(address):
            raise Refused(f"{host} resolves to {address}, which is not a public address")
    return addresses[0]


class _Connection(http.client.HTTPConnection):
    """http.client over a socket already open to the checked address."""

    def __init__(self, sock: socket.socket, timeout: float) -> None:
        super().__init__("fetch.invalid", timeout=timeout)
        self.sock = sock

    def connect(self) -> None:
        raise Failed("the connection closed")


def _text_type(kind: str) -> bool:
    main, _, sub = kind.partition("/")
    return main == "text" or kind in TEXT_TYPES or (main == "application" and sub.endswith(("+json", "+xml")))


def _read(resp: http.client.HTTPResponse) -> tuple[bytes, bool]:
    """At most MAX_BYTES of the body, and whether there was more."""
    chunks, size = [], 0
    while size <= MAX_BYTES:
        chunk = resp.read1(min(65536, MAX_BYTES + 1 - size))
        if not chunk:
            break
        chunks.append(chunk)
        size += len(chunk)
    data = b"".join(chunks)
    return data[:MAX_BYTES], len(data) > MAX_BYTES


def _decode(data: bytes, charset: str | None) -> str:
    try:
        codec = codecs.lookup(charset or "utf-8").name
    except LookupError:
        codec = "utf-8"
    return data.decode(codec, "replace")


@contextlib.contextmanager
def _get(host: str, target: str, address: str, deadline: float) -> Iterator[tuple[http.client.HTTPResponse,
                                                                                Callable[[], bool]]]:
    """One GET of TARGET from HOST at ADDRESS, its response, and whether the
    deadline cut it short. Everything is closed on the way out."""
    left = deadline - time.monotonic()
    if left <= 0:
        raise _late()
    sock = _connect(address, host, left)
    expired = threading.Event()

    def cut() -> None:
        expired.set()
        try:
            # The plain socket's shutdown, under TLS too: whatever is
            # blocked reading it returns.
            socket.socket.shutdown(sock, socket.SHUT_RDWR)
        except OSError:
            pass

    timer = threading.Timer(left, cut)
    timer.daemon = True
    timer.start()
    conn = _Connection(sock, left)
    try:
        conn.putrequest("GET", target, skip_host=True, skip_accept_encoding=True)
        for name, value in (("Host", host), ("User-Agent", USER_AGENT), ("Accept", ACCEPT),
                            ("Accept-Encoding", "identity"), ("Connection", "close")):
            conn.putheader(name, value)
        conn.endheaders()
        yield conn.getresponse(), expired.is_set
    finally:
        timer.cancel()
        conn.close()


def _late() -> Failed:
    return Failed(f"the fetch took longer than {TOTAL_SECS} s")


def fetch(url: Any, *, on_send: Callable[[], None] = lambda: None, deny: list[str] | None = None) -> dict[str, Any]:
    """URL read by every rule in this module's docstring: `http`, `url`
    (the last one), `content_type`, `text`, `truncated`."""
    deny = _deny_list() if deny is None else deny
    deadline = time.monotonic() + TOTAL_SECS
    hops = 0
    while True:
        host, target = check_url(url, deny)
        address = address_of(host)
        on_send()
        try:
            with _get(host, target, address, deadline) as (resp, expired):
                location = resp.getheader("Location")
                if resp.status in REDIRECTS and location:
                    hops += 1
                    if hops > MAX_REDIRECTS:
                        raise Refused(f"more than {MAX_REDIRECTS} redirects")
                    url = urljoin(url, location.strip())
                    continue
                raw_type = resp.getheader("Content-Type")
                if not raw_type:
                    raise Refused("the page names no content type")
                kind = resp.headers.get_content_type()
                if not _text_type(kind):
                    raise Refused(f"the page is {kind[:100]}, not text: only text/*, application/json, "
                                  "application/xml and application/*+json or *+xml are read")
                data, truncated = _read(resp)
                if expired():
                    raise _late()
                return {"http": resp.status, "url": url, "content_type": raw_type[:200],
                        "text": _decode(data, resp.headers.get_content_charset()), "truncated": truncated}
        except (OSError, http.client.HTTPException) as exc:
            if time.monotonic() >= deadline:
                raise _late() from None
            raise Failed(f"{host}: {type(exc).__name__}: {exc}"[:300]) from None
