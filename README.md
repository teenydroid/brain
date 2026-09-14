# Teeny's brain

The whole mind of [Teeny](https://github.com/senamakel/teeny) (`@TinyMascot`),
as an [OpenCompany](https://github.com/tinyhumansai/opencompany) company: the
agents, their skills, the workflows they run, the ledgers they keep and the
notes they write. The host runs it; the agents revise it; every run is
committed back here. This repository is public on purpose, so the brain's
history can be read by anyone.

## Layout

```
company/teeny/     the definition — agents, prompts, skills, workflows, ledgers,
                   seed notes, the first task board. The agents may edit this.
skills/            the shared skill library the host resolves beside it
companies/teeny/   runtime state the host writes: events, journal, runs, the
                   board, ledgers, facts, notes, memory (allowlisted files only)
sync/              the brain-sync sidecar: the image that commits and pushes
                   this checkout after every run, and the gates in front of it
```

This checkout **is** the host's data root (`OPENCOMPANY_DATA_DIR`). The host
also writes secrets, signing keys, session hashes, its config file, the MCP
registry, the agent journal and the agent sandboxes under it. None of that is
tracked: `.gitignore` denies everything and re-includes the record by name,
and the sync loop that commits runs a secret scan before every commit.

## The no-secrets rule

Nothing under this tree may hold a credential. Inference reaches the host from
the environment (`OPENCOMPANY_INFERENCE_URL`, `OPENCOMPANY_INFERENCE_KEY`),
sign-in is bootstrapped with `opencompany issue-password`, and any key an agent
needs lives in the host's secret store, which is not tracked. An agent that
finds itself about to write a token into a note, a ledger row or a prompt is
wrong and should stop.

## Running it

With the OpenCompany image and this checkout at `$PWD`:

```sh
docker run --rm -p 127.0.0.1:8080:8080 \
  -v "$PWD:/data" \
  -e OPENCOMPANY_DATA_DIR=/data \
  -e OPENCOMPANY_INFERENCE_URL=http://host.docker.internal:6969/v1 \
  -e OPENCOMPANY_INFERENCE_KEY=... \
  -e OPENCOMPANY_ADMIN_EMAIL=you@example.com \
  --entrypoint opencompany "$OPENCOMPANY_IMAGE" \
  serve --company /data/company/teeny --home /data --bind 0.0.0.0:8080
```

`opencompany check /data/company/teeny` validates the definition without
starting anything. The production wiring — the compose services and the
reload mechanism — lives in the teeny repository under `deploy/`; the sync
sidecar's image is `sync/` here, built by that deploy from the copy of this
repository pinned there rather than from the live checkout, so the gates
cannot be edited out from inside the running brain.

## The sync sidecar (`sync/`)

`sync/sync.sh` runs every `BRAIN_SYNC_INTERVAL` seconds: pull `--rebase`
(a definition edit pulled from GitHub asks the host to reload), `git add -A`
(the allowlist filters), then two gates — gitleaks over the staged tree and a
grep of the staged diff for every value in the host's `.env` files — then
commit and push with a write deploy key mounted at `/run/secrets/brain_key`.
`sync-now` runs a tick immediately. The key is generated on the box and
uploaded to this repository as a deploy key by teeny's `deploy.sh`; it never
lives in this tree.

## How it changes itself

`company/teeny/` is data, not code. The `curator` agent edits it through the
`self-edit` skill: small diffs, validated with `opencompany check`, one
`changes` ledger row per edit, then `touch /data/.reload-requested` so the
host restarts over the new definition. The `reflect` workflow runs daily and
is where goals, tasks and the roster get revised. A person can edit the same
files on GitHub; the sync loop pulls before it pushes and requests the same
reload.
