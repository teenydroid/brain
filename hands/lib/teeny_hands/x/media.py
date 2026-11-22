"""Media: the upload verb, the chunked upload to X, and a Giphy GIF fetched
by its id. Ported from crates/teeny-x/src/media.rs and the attach step of
crates/teeny-agent/src/tools/post.rs (over teeny-media's giphy.rs).

Only two things ever become an attachment: a file under `state/media/` or
`brain/assets/` that this hand uploaded and recorded (its id in
`x_media`), or a GIF Giphy names by id. Never a URL a caller passed: a model
must not be able to make the account post an arbitrary picture, and a path
must not reach anything else on the box (the secrets sit one folder over).

A still over X's 5 MiB image limit is refused before anything is sent, with
the reason, rather than failing at X after INIT (hands/image and hands/meme
keep their pictures under it). Alt text (`--alt`) goes to X's media metadata
route once the upload is processed; it is screened by the guard's lexicon
first (no model, no hold) and may carry no link, since anyone can read it.

The upload takes the chunked route for every type, as the legacy client did:
a still fits in one APPEND, so for an image this is the simple upload with
two extra calls. An animated GIF or a video is transcoded after FINALIZE and
only attachable once processing reports `succeeded`; attaching it earlier
fails the post, not the upload.
"""

from __future__ import annotations

import base64
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Mapping

from .. import hand
from . import open_db
from . import posting_rules as rules
from .client import XApi, XError, percent_encode

#: X takes up to 5 MB per APPEND; this stays well inside it.
UPLOAD_CHUNK_BYTES = 4 * 1024 * 1024
UPLOAD_POLL_ATTEMPTS = 20
#: The longest single wait between STATUS polls, whatever X suggests.
MAX_POLL_WAIT_SECONDS = 10

#: X sorts uploads into categories, not by MIME type, and rejects the post
#: (not the upload) when one lands in the wrong pipeline, so the category is
#: tied to the type here.
CATEGORIES = {
    "image/png": "tweet_image",
    "image/jpeg": "tweet_image",
    "image/webp": "tweet_image",
    "image/gif": "tweet_gif",
    "video/mp4": "tweet_video",
}

#: The folders an upload may come from, relative to the checkout.
UPLOAD_ROOTS = ("state/media", "brain/assets")

#: X's limit for a still image (tweet_image): 5 MB, which X means as 5 MiB.
#: 4,980,488 bytes uploaded on 2026-11-15; 5,263,141 failed on 2026-11-14.
#: The media hands keep what they make under 5,000,000 for a margin
#: (media/fit.py); this refuses only what X itself would.
IMAGE_MAX_BYTES = 5 * 1024 * 1024
#: X's longest alt text, in characters.
ALT_MAX_CHARS = 1000

GIPHY_API_BASE = "https://api.giphy.com"
#: X rejects an animated GIF over 15 MB, so the largest usable rendition wins.
GIF_MAX_BYTES = 15 * 1024 * 1024
RENDITIONS = ("downsized_medium", "downsized", "original", "fixed_height")
GIPHY_TIMEOUT_SECONDS = 30.0


# --- the upload verb ------------------------------------------------------------------------


def resolve_upload_path(path: str) -> Path:
    """The real file behind `path` (relative to the checkout unless
    absolute), when it is a regular file inside one of the upload folders.
    Symlinks and `..` are resolved first, so neither can point it elsewhere."""
    root = hand.root().resolve()
    candidate = Path(path)
    if not candidate.is_absolute():
        candidate = root / candidate
    refusal = hand.Refused(
        "upload takes only a file under state/media/ or brain/assets/", rule="path", allowed=list(UPLOAD_ROOTS)
    )
    try:
        resolved = candidate.resolve(strict=True)
    except (OSError, RuntimeError):
        raise refusal from None
    allowed = [(root / folder).resolve() for folder in UPLOAD_ROOTS]
    if not resolved.is_file() or not any(resolved.is_relative_to(folder) for folder in allowed):
        raise refusal
    return resolved


def sniff(data: bytes) -> str | None:
    """The file's type from its first bytes, not its name: a name is
    whatever the writer chose."""
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if data.startswith((b"GIF87a", b"GIF89a")):
        return "image/gif"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    if data[4:8] == b"ftyp":
        return "video/mp4"
    return None


def check_alt(alt: str | None) -> str | None:
    """Alt text as X gets it, whitespace folded: at most ALT_MAX_CHARS, no
    link, and nothing the guard's lexicon holds (the outbound screen, no
    model, no hold: a refusal here is a rewrite, never a hold to release)."""
    if alt is None:
        return None
    text = " ".join(alt.split())
    if not text:
        raise hand.Refused("--alt must not be empty", rule="alt")
    if len(text) > ALT_MAX_CHARS:
        raise hand.Refused(f"alt text is at most {ALT_MAX_CHARS} characters", rule="alt")
    if rules.has_link(text):
        raise hand.Refused("alt text may not carry a link", rule="alt")
    try:
        from ..guard.screen import OUTBOUND, screen_text
    except ImportError:
        raise hand.HandError("the content guard (hands/guard) is not installed, so no alt text is sent") from None
    found = screen_text(text, OUTBOUND)
    if found is not None:
        raise hand.Refused(f"the guard would hold this alt text ({found.category}); say it another way", rule="alt",
                           category=found.category)
    return text


def upload(path: str, x: XApi | None = None, alt: str | None = None) -> dict[str, Any]:
    """`upload --file PATH [--alt TEXT]`: the file to X, its id recorded as
    minted here, and its alt text set when given. Under DRY_RUN nothing
    reaches X and the id is a rehearsal's, which a rehearsed post accepts
    and a live one refuses."""
    resolved = resolve_upload_path(path)
    data = resolved.read_bytes()
    mime = sniff(data)
    if mime is None:
        raise hand.Refused("upload takes a PNG, JPEG, WebP, GIF or MP4 file", rule="type")
    if CATEGORIES[mime] == "tweet_image" and len(data) > IMAGE_MAX_BYTES:
        raise hand.Refused(f"X takes a still of at most {IMAGE_MAX_BYTES:,} bytes and this one is {len(data):,}",
                           rule="size", bytes=len(data), limit=IMAGE_MAX_BYTES)
    alt = check_alt(alt)
    if alt is not None and mime == "video/mp4":
        raise hand.Refused("alt text goes on a picture or a GIF, not a video", rule="alt")
    source = str(resolved.relative_to(hand.root().resolve()))
    conn = open_db(rules.SCHEMA)
    extra: dict[str, Any] = {"alt": alt} if alt is not None else {}
    if hand.dry_run():
        media_id = f"dry-run-{os.urandom(6).hex()}"
        rules.record_media(conn, media_id, source, mime, len(data), dry_run=True)
        return {"dry_run": True, "media_id": media_id, "mime": mime, "bytes": len(data), **extra}
    x = x or XApi.from_settings()
    media_id = upload_media(x, data, mime)
    rules.record_media(conn, media_id, source, mime, len(data))
    if alt is not None:
        # The media is X's now and recorded, so a refused description is
        # reported beside the id, never raised: the picture can still go out.
        try:
            set_alt(x, media_id, alt)
            extra["alt_set"] = True
        except XError as exc:
            extra.update(alt_set=False, alt_error=str(exc)[:200])
    return {"media_id": media_id, "mime": mime, "bytes": len(data), **extra}


def set_alt(x: XApi, media_id: str, alt: str) -> None:
    """X's media metadata route: the alt text for an uploaded media id."""
    x.call("POST", "/2/media/metadata", body={"id": media_id, "metadata": {"alt_text": {"text": alt}}}, user=True)


# --- the chunked upload -----------------------------------------------------------------------


def upload_media(x: XApi, data: bytes, mime: str) -> str:
    """INIT, one APPEND per 4 MB chunk, FINALIZE, then STATUS polls until X
    has processed it; the media id, ready for a post."""
    initialized = x.call(
        "POST",
        "/2/media/upload/initialize",
        body={"media_type": mime, "media_category": CATEGORIES[mime], "total_bytes": len(data)},
        user=True,
    )
    media_id = _data(initialized).get("id")
    if not isinstance(media_id, str) or not media_id:
        raise XError("X did not return a media id")
    encoded = percent_encode(media_id)
    for segment, start in enumerate(range(0, len(data), UPLOAD_CHUNK_BYTES)):
        chunk = data[start : start + UPLOAD_CHUNK_BYTES]
        x.call(
            "POST",
            f"/2/media/upload/{encoded}/append",
            body={"media": base64.b64encode(chunk).decode(), "segment_index": segment},
            user=True,
        )
    finalized = x.call("POST", f"/2/media/upload/{encoded}/finalize", user=True)
    _await_processing(x, media_id, _data(finalized).get("processing_info"))
    return media_id


def _data(response: Mapping[str, Any]) -> Mapping[str, Any]:
    data = response.get("data")
    return data if isinstance(data, Mapping) else {}


def _await_processing(x: XApi, media_id: str, info: Any) -> None:
    info = info if isinstance(info, Mapping) else {}
    state = info.get("state")
    wait = info.get("check_after_secs") if isinstance(info.get("check_after_secs"), int) else 1
    for _ in range(UPLOAD_POLL_ATTEMPTS):
        if state in (None, "succeeded"):
            return
        if state == "failed":
            raise XError(f"X failed to process media {media_id}")
        time.sleep(min(max(wait, 0), MAX_POLL_WAIT_SECONDS))
        status = x.call("GET", "/2/media/upload", [("media_id", media_id), ("command", "STATUS")], user=True)
        info = _data(status).get("processing_info")
        info = info if isinstance(info, Mapping) else {}
        state = info.get("state")
        if isinstance(info.get("check_after_secs"), int):
            wait = info["check_after_secs"]
    raise XError(f"X is still processing media {media_id} after {UPLOAD_POLL_ATTEMPTS} checks")


# --- Giphy ----------------------------------------------------------------------------------------


class _NoRedirects(urllib.request.HTTPRedirectHandler):
    """A rendition is fetched from where Giphy named, or not at all: a
    redirect could lead anywhere."""

    def redirect_request(self, *args: Any, **kwargs: Any) -> None:
        return None


_OPENER = urllib.request.build_opener(_NoRedirects)


def _giphy_get(url: str, limit: int) -> tuple[int, bytes]:
    """One GET; the status and up to `limit` bytes (one more, to tell an
    oversize answer). The URL carries the key, so no error names it."""
    failure: hand.HandError | None = None
    try:
        with _OPENER.open(urllib.request.Request(url, headers={"user-agent": "teeny-hands-x"}), timeout=GIPHY_TIMEOUT_SECONDS) as response:
            return response.status, response.read(limit + 1)
    except urllib.error.HTTPError as exc:
        return exc.code, b""
    except (urllib.error.URLError, OSError, ValueError) as exc:
        failure = hand.HandError(f"could not reach Giphy ({type(getattr(exc, 'reason', exc)).__name__})")
    raise failure


def _rendition(gif: Mapping[str, Any]) -> str | None:
    """The first rendition, largest first, that X will take."""
    images = gif.get("images") if isinstance(gif.get("images"), Mapping) else {}
    for name in RENDITIONS:
        image = images.get(name)
        if not isinstance(image, Mapping) or not isinstance(image.get("url"), str) or not image["url"]:
            continue
        try:
            size = float(image.get("size") or 0)
        except (TypeError, ValueError):
            continue
        if 0 < size <= GIF_MAX_BYTES:
            return image["url"]
    return None


def _from_giphy(url: str, base: str) -> bool:
    """Giphy's own media hosts over https, or the configured Giphy base
    itself (a proxy in front of it, or the tests' fake)."""
    parts, origin = urllib.parse.urlsplit(url), urllib.parse.urlsplit(base)
    host = (parts.hostname or "").lower()
    if parts.scheme == "https" and (host == "giphy.com" or host.endswith(".giphy.com")):
        return True
    return (parts.scheme, parts.netloc) == (origin.scheme, origin.netloc)


def attach_gif(x: XApi, gif_id: str) -> str:
    """The GIF Giphy has under `gif_id`, uploaded to X; its media id, recorded
    as minted. Called only for a live post, after the guard and the rules."""
    key = hand.setting("GIPHY_API_KEY")
    if not key:
        raise hand.Refused("no Giphy key is configured (GIPHY_API_KEY), so gif_id cannot be attached", rule="attachments")
    base = (hand.setting("GIPHY_API_BASE", GIPHY_API_BASE) or GIPHY_API_BASE).rstrip("/")
    status, body = _giphy_get(f"{base}/v1/gifs/{percent_encode(gif_id)}?api_key={percent_encode(key)}", 1 << 20)
    if status == 404:
        raise hand.Refused(f"Giphy has no GIF with id {gif_id}; use an id the GIF search returned", rule="attachments")
    if status != 200:
        raise hand.HandError(f"the GIF could not be fetched (Giphy answered {status})")
    try:
        gif = json.loads(body).get("data")
    except (ValueError, AttributeError):
        gif = None
    url = _rendition(gif) if isinstance(gif, Mapping) else None
    if url is None:
        raise hand.Refused(f"GIF {gif_id} has no rendition X will take (15 MB at most)", rule="attachments")
    if not _from_giphy(url, base):
        raise hand.Refused(f"GIF {gif_id} is not served from Giphy, so it is not attached", rule="attachments")
    status, data = _giphy_get(url, GIF_MAX_BYTES)
    if status != 200:
        raise hand.HandError(f"the GIF could not be downloaded (Giphy answered {status})")
    if len(data) > GIF_MAX_BYTES:
        raise hand.Refused(f"GIF {gif_id} is over the X upload limit", rule="attachments")
    media_id = upload_media(x, data, "image/gif")
    rules.record_media(open_db(rules.SCHEMA), media_id, f"giphy:{gif_id}", "image/gif", len(data))
    return media_id
