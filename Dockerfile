# syntax=docker/dockerfile:1.7
#
# The spine image: teeny-spine (OpenHuman's host, built here from the
# vendored library, with tinywallet linked in), OpenHuman's
# pinned module releases, Chromium for the browser, and the tools bin/ needs. Teeny's scripts and brain are not baked in; deploy/compose.yaml
# mounts the checkout at /srv/teeny, so a job or cron.toml change is a pull and
# a restart, not a rebuild.
#
#   git submodule update --init --recursive vendor/openhuman
#   docker build -t teeny-spine .

# --- build teeny-spine --------------------------------------------------------
FROM rust:1.96.1-bookworm AS builder
ARG CARGO_BUILD_JOBS=4
ENV DEBIAN_FRONTEND=noninteractive
# What OpenHuman links on Linux (X11, xdo, evdev, ALSA), as its own image has,
# plus what the linked TinyComputer needs (Xi, udev), as CI installs.
RUN apt-get update && apt-get install -y --no-install-recommends \
      build-essential cmake pkg-config libssl-dev libasound2-dev libxdo-dev \
      libxtst-dev libx11-dev libxi-dev libevdev-dev libudev-dev clang mold git ca-certificates \
 && rm -rf /var/lib/apt/lists/*
WORKDIR /build
COPY Cargo.toml Cargo.lock rust-toolchain.toml ./
COPY spine/ spine/
COPY vendor/ vendor/
RUN --mount=type=cache,target=/usr/local/cargo/registry \
    --mount=type=cache,target=/usr/local/cargo/git \
    --mount=type=cache,target=/build/target \
    cargo build --release --locked -p teeny-spine \
 && cp target/release/teeny-spine /teeny-spine

# --- stage the pinned, digest-checked module releases -------------------------
# OpenHuman's own staging script, from the vendored commit, so the modules are
# exactly the ones that commit pins (vendor/openhuman/Dockerfile does the same).
FROM node:24-bookworm-slim AS modules
WORKDIR /repo
COPY vendor/openhuman/scripts/lib/module-pins.mjs vendor/openhuman/scripts/lib/git-env.mjs scripts/lib/
COPY vendor/openhuman/scripts/ci/self-hosted/test-module-assets.mjs scripts/ci/self-hosted/test-module-assets.mjs
COPY vendor/openhuman/scripts/release/stage-modules.mjs scripts/release/stage-modules.mjs
COPY vendor/openhuman/crates/openhuman-core/src/modules/registry.rs crates/openhuman-core/src/modules/registry.rs
COPY vendor/openhuman/crates/openhuman-core/src/modules/registry/ crates/openhuman-core/src/modules/registry/
RUN node scripts/release/stage-modules.mjs --output /bundled-modules

# --- runtime ----------------------------------------------------------------------
FROM debian:bookworm-slim
ENV DEBIAN_FRONTEND=noninteractive
# The core's runtime libraries, curl for the health check and bin/oh, jq and
# sqlite3 for bin/, python3 for bin/seed-cron's TOML and the hands, git for
# hands/gh's pushes.
#
# The browser (docs/gitbooks/en/developing/tinycomputer-docker.md in
# vendor/openhuman): Chromium at /usr/bin/chromium, which
# bin/lib/core_sections/automation.py writes as `[browser] chrome_path`
# (TEENY_CHROME_PATH below), with fonts so pages render. TinyComputer itself
# is its pinned release (bin/lib/core_sections/automation.py says why it is
# not served linked), staged with the others below. agent-browser adds --no-sandbox and --disable-dev-shm-usage itself
# inside a container. bubblewrap is TinyBox's namespace sandbox, which the
# builder's shell commands run under (brain/agents/builder.toml).
# python3-cryptography is the one library the hands use beyond the standard
# library: AES-256-GCM for hands/keychain's sealed passwords.
RUN apt-get update && apt-get install -y --no-install-recommends \
      ca-certificates libssl3 libasound2 libxdo3 libxtst6 libx11-6 libxi6 libevdev2 libudev1 \
      curl jq sqlite3 python3 python3-cryptography git \
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
COPY --from=builder /teeny-spine /usr/local/bin/teeny-spine
COPY --from=modules /bundled-modules /opt/openhuman/bundled-modules
ENV OPENHUMAN_BUNDLED_MODULES=/opt/openhuman/bundled-modules \
    OPENHUMAN_CORE_BIN=/usr/local/bin/teeny-spine \
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
