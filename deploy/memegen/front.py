"""Teeny's front for memegen, the `memegen` service's entry point
(deploy/compose.yaml; docs/hands.md, "meme").

Upstream's code (vendor/memegen, a submodule pinned to a commit, never
edited) is built by its own Containerfile as it is. Compose mounts this
folder read-only at /opt/teeny and runs gunicorn on `front:app` in place of
upstream's `app.main:app`: this file imports upstream's Sanic app as it is
and changes four things at run time, before the first request.

1. Downloads. Upstream fetches an overlay or a custom background from any
   public URL. Here `utils.http.download` copies the picture from Teeny's
   read-only art mount for an `http://teeny-art/...` address (art.py says
   which) and refuses every other URL, so the service reads only Teeny's own
   pictures and fetches nothing from the internet.
2. The watermark. Upstream draws "Memegen.link" on every image unless its
   hosted API, which a self-hosted copy does not have, authorises a custom
   one. Here no watermark is drawn.
3. Tracking and search. Both call upstream's hosted service, and only when
   REMOTE_TRACKING_URL is set (compose leaves it unset). They are made
   no-ops besides, so a stray variable cannot switch them on.
4. Error reporting: BUGSNAG_API_KEY is left unset by compose, so upstream
   never starts its Bugsnag client; nothing here changes that.

The routes are upstream's own; the service has no published port and the
spine reaches it at http://memegen:5000 (MEMEGEN_URL) on the compose network.
"""

from __future__ import annotations

import asyncio
import os
import shutil
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import art  # noqa: E402
from app import settings, utils  # noqa: E402
from app.main import app  # noqa: E402,F401  (what gunicorn serves)
from sanic.log import logger  # noqa: E402

#: Where compose mounts Teeny's art, read-only: state/media/ as media/,
#: brain/assets/ as assets/.
ART_ROOT = Path(os.environ.get("TEENY_ART_ROOT", "/teeny"))


async def download(url: str, path) -> bool:
    source = art.local_path(url, ART_ROOT, settings.MAXIMUM_DOWNLOAD_SIZE)
    if source is None:
        logger.error(f"Refusing to fetch {url!r}: this service reads only Teeny's own art ({art.ART_PREFIX}...)")
        return False
    await asyncio.to_thread(shutil.copyfile, source, str(path))
    logger.info(f"Copied Teeny's art {source} for {url}")
    return True


async def no_watermark(request) -> tuple[str, bool]:
    return "", False


async def no_track(request, lines) -> None:
    return None


async def no_search(request, text, safe, *, mode="") -> list:
    return []


utils.http.download = download
utils.meta.get_watermark = no_watermark
utils.meta.track = no_track
utils.meta.search = no_search
settings.TRACK_REQUESTS = False
settings.REMOTE_TRACKING_URL = None
