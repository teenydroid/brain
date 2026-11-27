# syntax=docker/dockerfile:1.7
#
# The spine image: teeny-core (the TypeScript core, built here, which hosts the
# OpenClaw Gateway its agent turns run on), Node 24, Chromium for the browser,
# Digg's reader for hands/trend, and the tools bin/ needs. Teeny's scripts and
# brain are not baked in; deploy/compose.yaml mounts the checkout at /srv/teeny,
# so a job or cron.toml change is a pull and a restart, not a rebuild. The core
# itself lives in /opt/teeny-core, because that mount hides /srv/teeny/core.
#
#   docker build -t teeny-spine .

# --- build the core -------------------------------------------------------------
FROM node:24-bookworm-slim AS core
WORKDIR /build/core
COPY core/package.json core/package-lock.json ./
RUN --mount=type=cache,target=/root/.npm npm ci
COPY core/tsconfig.json core/tsconfig.build.json ./
COPY core/src/ src/
RUN npm run build && npm prune --omit=dev

# --- digg-pp-cli: what AI X is talking about (hands/trend) -------------------
# Digg's leaderboard reader (Apache-2.0), from mvanhorn/printing-press-library
# at a pinned commit, built unmodified. The pin here is the vendoring: that
# repository is about 500 MB, so it is not a submodule; a new pin is a PR that
# passes hands/tests/trend. Pure Go (its SQLite is modernc's), so it is built
# static and does not care which libc the runtime has.
FROM golang:1.26 AS digg
ARG DIGG_COMMIT=c51ca2c9232719ee6a82f2316bf6af6df9ddc05e
ENV CGO_ENABLED=0 GOBIN=/out GOFLAGS=-trimpath
RUN --mount=type=cache,target=/go/pkg/mod \
    --mount=type=cache,target=/root/.cache/go-build \
    go install github.com/mvanhorn/printing-press-library/library/media-and-entertainment/digg/cmd/digg-pp-cli@${DIGG_COMMIT} \
 && /out/digg-pp-cli version

# --- runtime ----------------------------------------------------------------------
FROM node:24-bookworm-slim
ENV DEBIAN_FRONTEND=noninteractive
# Node 24 comes with the base image (OpenClaw needs it, and so does the core's
# node:sqlite). curl for the health check and bin/oh, jq and sqlite3 for bin/,
# python3 for bin/seed-cron's TOML and the hands, git for hands/gh's pushes.
#
# The browser: Chromium at /usr/bin/chromium (TEENY_CHROME_PATH below, which
# OpenClaw's browser is pointed at), with fonts so pages render. bubblewrap is
# the namespace sandbox the builder's shell commands run under.
# python3-cryptography and python3-pil are the libraries the hands use beyond
# the standard library: AES-256-GCM for hands/keychain's sealed passwords, and
# Pillow for hands/meme's captions, the 5 MB shrink of hands/image and the
# viewer check (hands/guard view).
RUN apt-get update && apt-get install -y --no-install-recommends \
      ca-certificates \
      curl jq sqlite3 python3 python3-cryptography python3-pil git \
      chromium fonts-liberation bubblewrap \
 && rm -rf /var/lib/apt/lists/*
# git for whoever runs it in the container without the hands (an operator's
# `exec`, a job's shell): the uid has no passwd entry and HOME is /tmp, so
# without this `git commit` cannot work out who it is, and a checkout owned
# by another uid is refused as unsafe. The hands ignore this file and set
# the same identity themselves (hands/lib/teeny_hands/git.py; GIT_NAME and
# GIT_EMAIL in secrets/teeny.env move both). `hands/git check` proves it.
RUN git config --system user.name "Teeny" \
 && git config --system user.email "teeny@agentmail.to" \
 && git config --system init.defaultBranch main \
 && git config --system safe.directory /srv/teeny \
 && git config --system --add safe.directory '/srv/teeny/*' \
 && git config --system advice.detachedHead false
COPY --from=core /build/core/dist /opt/teeny-core/dist
COPY --from=core /build/core/node_modules /opt/teeny-core/node_modules
COPY --from=core /build/core/package.json /opt/teeny-core/package.json
COPY bin/teeny-core /usr/local/bin/teeny-core
# hands/trend runs it with a home of its own under state/trend/home.
COPY --from=digg /out/digg-pp-cli /usr/local/bin/digg-pp-cli
ENV OPENHUMAN_CORE_BIN=/usr/local/bin/teeny-core \
    OPENHUMAN_DOCKER=1 \
    OPENHUMAN_CORE_HOST=127.0.0.1 \
    OPENHUMAN_CORE_PORT=7788 \
    TEENY_CHROME_PATH=/usr/bin/chromium
WORKDIR /srv/teeny
# The core answers on loopback inside the container only; operators use
# `docker compose exec spine bin/ask ...`.
HEALTHCHECK --interval=30s --timeout=5s --start-period=60s --retries=3 \
    CMD curl -fsS http://127.0.0.1:7788/health || exit 1
ENTRYPOINT ["/srv/teeny/bin/spine"]
