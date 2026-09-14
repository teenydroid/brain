# The sidecar that commits the brain after every run and pushes it to GitHub.
# git and an SSH client for the push; gitleaks for the gate in front of every
# commit. Pinned, and verified against the checksum the release publishes, so
# the gate cannot be swapped out from under the stack by a moved tag.
FROM alpine:3.21

ARG GITLEAKS_VERSION=8.30.1
ARG GITLEAKS_SHA256=551f6fc83ea457d62a0d98237cbad105af8d557003051f41f3e7ca7b3f2470eb

RUN apk add --no-cache git openssh-client ca-certificates curl \
  && curl -fsSL -o /tmp/gitleaks.tar.gz \
       "https://github.com/gitleaks/gitleaks/releases/download/v${GITLEAKS_VERSION}/gitleaks_${GITLEAKS_VERSION}_linux_x64.tar.gz" \
  && echo "${GITLEAKS_SHA256}  /tmp/gitleaks.tar.gz" | sha256sum -c - \
  && tar -xzf /tmp/gitleaks.tar.gz -C /usr/local/bin gitleaks \
  && rm /tmp/gitleaks.tar.gz \
  && gitleaks version

COPY sync.sh /usr/local/bin/sync.sh
COPY sync-now /usr/local/bin/sync-now
RUN chmod +x /usr/local/bin/sync.sh /usr/local/bin/sync-now

# The container runs as the box user's uid so what it writes into the
# bind-mounted checkout is the checkout owner's. OpenSSH refuses to run as a
# uid with no passwd entry ("No user exists for uid 1000"), so that uid gets
# a name here; compose passes the real value as a build arg.
ARG SYNC_UID=1000
ARG SYNC_GID=1000
RUN grp="$(getent group "${SYNC_GID}" | cut -d: -f1)"; \
    if [ -z "$grp" ]; then addgroup -g "${SYNC_GID}" brain && grp=brain; fi; \
    adduser -D -u "${SYNC_UID}" -G "$grp" -h /tmp brain

ENV BRAIN_DIR=/data
ENTRYPOINT ["/usr/local/bin/sync.sh"]
