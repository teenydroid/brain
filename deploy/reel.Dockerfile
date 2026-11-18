# syntax=docker/dockerfile:1.7
#
# The `reel` image: MoneyPrinterTurbo (vendor/moneyprinter, the upstream repo
# itself as a submodule, pinned to a release tag, never edited here) as an
# API-only service for hands/reel (docs/hands.md, "reel").
#
#   git submodule update --init vendor/moneyprinter
#   docker compose -f deploy/compose.yaml --profile reel build reel
#
# or, without compose:
#
#   docker build -f deploy/reel.Dockerfile --build-context teeny=deploy/reel \
#     -t teeny-reel:current vendor/moneyprinter
#
# The build context is the submodule. deploy/reel.Dockerfile.dockerignore
# (BuildKit reads it in place of the submodule's own .dockerignore) leaves out
# resource/songs (the README says those tracks came from YouTube), the web
# UI, the tests and the docs, so none of them is in the image. What runs is
# deploy/reel/serve.py (the `teeny` build context): upstream's FastAPI app
# behind Teeny's one route and a path allowlist. Streamlit is installed with
# the rest of upstream's requirements, as they are, and never started, so the
# web UI's update check never runs.
#
# Upstream's own Dockerfile tries Chinese mirrors first; this one uses the
# default Debian and PyPI indexes.

FROM python:3.11-slim-trixie

COPY --from=ghcr.io/astral-sh/uv:0.12.12 /uv /usr/local/bin/uv

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/MoneyPrinterTurbo

# ffmpeg renders; curl is the health check. Fonts are upstream's own
# (resource/fonts), copied with the code below.
RUN apt-get update \
 && apt-get install -y --no-install-recommends ffmpeg curl ca-certificates \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /MoneyPrinterTurbo

# Upstream's requirements as they are, with its own security overrides.
COPY requirements.txt requirements-security.txt ./
RUN UV_HTTP_TIMEOUT=60 UV_HTTP_RETRIES=3 uv pip install --system --no-cache \
      --overrides requirements-security.txt -r requirements.txt 'setuptools>=82.0.1' 'wheel>=0.46.2'

COPY . .
COPY --from=teeny serve.py /opt/teeny/serve.py

# A user of its own. storage/ is the reel-storage volume (a fresh volume
# takes this ownership); storage/local_videos/{media,assets} and
# storage/bgm/media are Teeny's media, mounted read-only over it. An empty
# resource/songs, so upstream's song folder exists and holds nothing.
RUN useradd --system --uid 10001 --home-dir /MoneyPrinterTurbo --no-create-home reel \
 && rm -rf resource/songs && mkdir -p resource/songs \
 && mkdir -p storage/local_videos/media storage/local_videos/assets storage/bgm/media storage/tasks \
 && chown -R reel:reel storage \
 && chmod 755 /MoneyPrinterTurbo

USER reel
EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
    CMD curl -fsS http://127.0.0.1:8080/ping || exit 1
ENTRYPOINT ["python3", "/opt/teeny/serve.py"]
