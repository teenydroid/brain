# syntax=docker/dockerfile:1.7
#
# The `ingest` image: a pinned yt-dlp and ffmpeg behind Teeny's own small
# HTTP service (deploy/ingest/serve.py), which finds a short video on
# YouTube, TikTok or Instagram, fetches an excerpt, and cuts it into shots
# and frames for hands/remake (docs/hands.md, "remake"). No secret and no
# mount of Teeny's goes in or on it.
#
#   docker compose -f deploy/compose.yaml --profile ingest build ingest
#
# or, without compose:
#
#   docker build -f deploy/ingest.Dockerfile -t teeny-ingest:current deploy/ingest
#
# The build context is deploy/ingest: serve.py, policy.py and shots.py,
# plain standard-library Python (hands/tests/ingest tests them as they run).
# yt-dlp is installed from its release wheel by hash (pip --require-hashes),
# so a rebuild gets the same bytes; YTDLP_VERSION and the hash move together
# (the digest is the wheel's sha256 on PyPI). yt-dlp changes often to keep up
# with the sites: bump both when a site stops working.

# Pinned by digest (the tag as of 2026-11-18, as the reel image's).
FROM python:3.11-slim-trixie@sha256:07d4824036af339fa1b6ef769cc99d9c10a141769538b52b3b597c84f7a7b535

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

ARG YTDLP_VERSION=2026.10.26
ARG YTDLP_SHA256=a05c5b5d8562f7e8a62f5334348b406af4930d56d3a65f8021d14af0e22339ae

# ffmpeg cuts and looks for scenes; ca-certificates is yt-dlp's trust store.
RUN apt-get update \
 && apt-get install -y --no-install-recommends ffmpeg ca-certificates \
 && rm -rf /var/lib/apt/lists/* \
 && printf 'yt-dlp==%s --hash=sha256:%s\n' "${YTDLP_VERSION}" "${YTDLP_SHA256}" > /tmp/requirements.txt \
 && pip install --no-cache-dir --require-hashes -r /tmp/requirements.txt \
 && rm /tmp/requirements.txt \
 && yt-dlp --version

COPY serve.py policy.py shots.py /opt/teeny/

# A user of its own. /data is the ingest-storage volume (a fresh volume takes
# this ownership); each fetch lives there until hands/remake deletes it.
RUN useradd --system --uid 10002 --home-dir /nonexistent --no-create-home ingest \
 && mkdir -p /data \
 && chown ingest:ingest /data

USER ingest
WORKDIR /data
EXPOSE 8090
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD ["python3", "-c", "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8090/ping', timeout=4).status == 200 else 1)"]
ENTRYPOINT ["python3", "-I", "/opt/teeny/serve.py"]
