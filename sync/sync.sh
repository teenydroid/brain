#!/bin/sh
# Commits the brain after every run and pushes it to GitHub.
#
# The checkout at $BRAIN_DIR is the OpenCompany host's live data root, and
# its .gitignore is a deny-by-default allowlist: `git add -A` stages the
# record (definition, events, journal, runs, board, ledgers, notes) and
# nothing the host writes that must stay private (secrets, keys, sessions,
# config, the MCP registry, the agent journal, sandboxes). This script does
# not decide what is public; the allowlist does. What this script adds is the
# gate: nothing is committed until gitleaks and a grep for the box's actual
# secret values have both said no.
#
# Every tick, in order:
#   1. pull --rebase, so a person editing the definition on GitHub is merged
#      rather than overwritten. A pulled change to the definition or the
#      shared skills asks the host to reload by touching .reload-requested,
#      the same file the curator agent uses.
#   2. stage everything the allowlist permits; nothing staged means nothing
#      to do.
#   3. gitleaks over the staged tree, then the literal-value grep. Any hit
#      unstages, logs the path, and leaves the working tree exactly as it
#      was so the next tick sees the same thing and a person can look.
#   4. commit, push. A push that fails (network, a rejected key) keeps the
#      commit local and is retried next tick.
#
# `sync-now` (docker exec) runs one tick immediately by signalling this loop.
set -eu

BRAIN_DIR="${BRAIN_DIR:-/data}"
INTERVAL="${BRAIN_SYNC_INTERVAL:-60}"
REMOTE="${BRAIN_REMOTE:-}"
BRANCH="${BRAIN_BRANCH:-main}"
KEY="${BRAIN_SSH_KEY:-/run/secrets/brain_key}"
# Files whose VALUES must never appear in a commit. One KEY=VALUE per line,
# the .env format; read with sed, never sourced.
SECRET_FILES="${BRAIN_SECRET_FILES:-/run/secrets/deploy.env /run/secrets/bot.env}"

log() { printf '%s brain-sync: %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$*"; }
git_() { git -C "$BRAIN_DIR" "$@"; }

# --- one-time setup ----------------------------------------------------------
[ -d "$BRAIN_DIR/.git" ] || { log "no git repository at $BRAIN_DIR; clone the brain there first"; exit 1; }
[ -r "$KEY" ] || { log "no deploy key at $KEY (BRAIN_SSH_KEY)"; exit 1; }

# git wants a writable HOME for its config and the SSH client wants a
# known_hosts. Both are per-container scratch.
export HOME="${HOME:-/tmp}"
mkdir -p "$HOME/.ssh" && chmod 700 "$HOME/.ssh"
# GitHub's published host keys, so the first push cannot be answered by an
# impostor and there is no interactive prompt to hang on.
cat > "$HOME/.ssh/known_hosts" <<'KEYS'
github.com ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIOMqqnkVzrm0SdG6UOoqKLsabgH5C9okWi0dh2l9GKJl
github.com ecdsa-sha2-nistp256 AAAAE2VjZHNhLXNoYTItbmlzdHAyNTYAAAAIbmlzdHAyNTYAAABBBEmKSENjQEezOmxkZMy7opKgwFB9nkt5YRrYMjNuG5N87uRgg6CLrbo5wAdT/y6v0mKV0U2w0WZ2YB/++Tpockg=
github.com ssh-rsa AAAAB3NzaC1yc2EAAAADAQABAAABgQCj7ndNxQowgcQnjshcLrqPEiiphnt+VTTvDP6mHBL9j1aNUkY4Ue1gvwnGLVlOhGeYrnZaMgRK6+PKCUXaDbC7qtbW8gIkhL7aGCsOr/C56SJMy/BCZfxd1nWzAOxSDPgVsmerOBYfNqltV9/hWCqBywINIR+5dIg6JTJ72pcEpEjcYgXkE2YEFXV1JHnsKgbLWNlhScqb2UmyRkQyytRLtL+38TGxkxCflmO+5Z8CSSNY7GidjMIZ7Q4zMjA2n1nGrlTDkzwDCsw+wqFPGQA179cnfGWOWRVruj16z6XyvxvjJwbz0wQZ75XK5tKSb7FNyeIEs4TT4jk+S4dhPeAUC5y+bDYirYgM4GC7uEnztnZyaVWQ7B381AK4Qdrwt51ZqExKbQpTUNn+EjqoTwvqNj4kqx5QUCI0ThS/YkOxJCXmPUWZbhjpCg56i+2aB6CmK2JGhn57K5mj0MNdBXA4/WnwH6XoPWJzK5Nyu2zB3nAZp+S5hpQs+p1vN1/wsjk=
KEYS
export GIT_SSH_COMMAND="ssh -i $KEY -o IdentitiesOnly=yes -o BatchMode=yes -o UserKnownHostsFile=$HOME/.ssh/known_hosts"
git_ config user.name  "${GIT_AUTHOR_NAME:-teeny brain}"
git_ config user.email "${GIT_AUTHOR_EMAIL:-brain@teeny.tinyhumans.ai}"
# The host writes the checkout as the same uid this runs as, but say so
# explicitly for the `docker compose run` case.
git config --global --add safe.directory "$BRAIN_DIR"
[ -n "$REMOTE" ] && git_ remote set-url origin "$REMOTE"

# The values that must never be committed, collected once. Only non-trivial
# values count: a port number or `false` would match everything.
secret_values() {
  for f in $SECRET_FILES; do
    [ -r "$f" ] || continue
    sed -n 's/^[A-Za-z_][A-Za-z0-9_]*=//p' "$f" \
      | sed 's/^"\(.*\)"$/\1/; s/^'"'"'\(.*\)'"'"'$/\1/' \
      | awk 'length($0) >= 12'
  done | sort -u
}

# --- one tick ----------------------------------------------------------------
tick() {
  # 1. bring in what a person pushed. --autostash carries any dirty runtime
  #    state across the rebase and puts it back.
  before="$(git_ rev-parse HEAD)"
  if git_ fetch -q origin "$BRANCH" 2>/dev/null; then
    if ! git_ pull -q --rebase --autostash origin "$BRANCH" 2>/dev/null; then
      log "pull --rebase failed (a conflict with an edit on GitHub?); aborting and retrying next tick"
      git_ rebase --abort 2>/dev/null || true
      git_ stash list | grep -q autostash && log "an autostash is holding local state: $(git_ stash list | head -1)"
      return 0
    fi
    after="$(git_ rev-parse HEAD)"
    if [ "$before" != "$after" ] && git_ diff --name-only "$before" "$after" -- company skills | grep -q .; then
      log "definition changed upstream ($before..$after); asking the host to reload"
      touch "$BRAIN_DIR/.reload-requested"
    fi
  else
    log "fetch failed; working offline this tick"
  fi

  # 2. stage what the allowlist permits.
  git_ add -A
  if git_ diff --cached --quiet; then
    return 0
  fi
  n="$(git_ diff --cached --name-only | wc -l | tr -d ' ')"

  # 3. the gates. Either one aborts the commit and leaves the tree as it was.
  # --verbose is what prints the findings; only the rule, file and line are
  # echoed here -- never the `Secret:` line, which is the value itself.
  if ! gitleaks git --staged --no-banner --verbose --exit-code 1 \
        --config "$BRAIN_DIR/.gitleaks.toml" "$BRAIN_DIR" >/tmp/gitleaks.out 2>&1; then
    log "REFUSED: gitleaks found something in the staged tree; nothing committed"
    grep -E "^(RuleID|File|Line):" /tmp/gitleaks.out | sed 's/^/  /' | head -30
    grep -q "^RuleID:" /tmp/gitleaks.out || { log "  (gitleaks did not report a finding; its output follows)"; sed 's/^/  /' /tmp/gitleaks.out; }
    git_ reset -q
    return 0
  fi
  secret_values > /tmp/secret-values
  if [ -s /tmp/secret-values ]; then
    hit="$(git_ diff --cached | grep -F -f /tmp/secret-values | head -1 || true)"
    if [ -n "$hit" ]; then
      log "REFUSED: a configured secret value appears in the staged diff; nothing committed"
      git_ diff --cached --name-only | sed 's/^/  staged: /'
      git_ reset -q
      rm -f /tmp/secret-values
      return 0
    fi
  fi
  rm -f /tmp/secret-values

  # 4. commit and push.
  git_ commit -q -m "brain: $(date -u +%Y-%m-%dT%H:%M:%SZ), $n file(s)"
  log "committed $(git_ rev-parse --short HEAD): $n file(s)"
  if git_ push -q origin "HEAD:$BRANCH" 2>/tmp/push.err; then
    log "pushed to origin/$BRANCH"
  else
    log "push failed; keeping the commit and retrying next tick: $(tr '\n' ' ' </tmp/push.err)"
  fi
}

# --- the loop ----------------------------------------------------------------
# `sync-now` sends USR1; the sleep is interrupted and a tick runs at once.
# TERM/INT (docker stop) end the loop between ticks; a tick in flight is not
# interrupted, so a commit is never half-written.
trap 'log "sync-now"; tick' USR1
trap 'log "stopping"; kill "${sleeper:-}" 2>/dev/null; exit 0' TERM INT
log "syncing $BRAIN_DIR to $(git_ remote get-url origin) every ${INTERVAL}s"
while :; do
  tick || log "tick failed: exit $?"
  sleep "$INTERVAL" &
  sleeper=$!
  wait "$sleeper" || true
done
