# Deploying Teeny

`deploy/compose.yaml` runs four services over the checkout, which is mounted
at `/srv/teeny`:

| Service | What | Port |
|---|---|---|
| `spine` | `teeny-spine` serving over the checkout. The core, its cron and every job. | none; reach it with `exec` |
| `cortexdb` | Teeny's long-term memory (CortexDB v0.10.4, models on Surplus Intelligence: `gpt-6-luna`, embeddings `venice-embed-1`). Its store is the `cortexdb-data` volume. Keys in `secrets/cortex.env` (`deploy/cortex.env.example`); the spine runs in its network namespace and finds it at `CORTEX_API_URL=http://127.0.0.1:3141` (`secrets/spine.env`); CortexDB binds loopback only. | none; compose network only |
| `xhook` | `bin/x-webhook`: X's webhook, which queues mentions, replies and quotes into `state/hands.db`. Same image as the spine. | `127.0.0.1:3110` |
| `web` | The landing page (`web/`, its own Next.js image). Behind the `web` profile. | `127.0.0.1:3120` |

Nothing takes a public port. The host's nginx serves `teeny.enamakel.com`
(`nginx/teeny.enamakel.com.conf`): `/api/webhooks/x` goes to the webhook,
everything else to the landing page. `web-vhost.sh` installs that vhost and
its certificate.

```
X, browsers ──► Cloudflare ──► host nginx: teeny.enamakel.com (nginx/teeny.enamakel.com.conf)
                                 │ :80   /.well-known/acme-challenge/ from /var/www/certbot,
                                 │       everything else 301 to https
                                 │ :443  certificate in /etc/letsencrypt/live/teeny.enamakel.com
                                 ├── = /api/webhooks/x ──► 127.0.0.1:3110/webhooks/x ──► xhook
                                 └── /, /_next/static/ ──► 127.0.0.1:3120 ──► web (:3000 inside)

compose project teeny-spine
  cortexdb's network namespace, shared by two services
  │ spine ──► 127.0.0.1:3141 ──► cortexdb ──► cortexdb-data volume
  │ no published port: reach the spine with docker compose exec
  │ cortexdb reads secrets/cortex.env; models on api.surplusintelligence.ai
  xhook ──► queues mentions, replies and quotes into state/hands.db
  web   ──► its own image, built from web/ alone; mounts nothing

the checkout, mounted at /srv/teeny in spine and xhook
  ├── brain/storage.db    the core's state; its secrets are encrypted under secrets/openhuman.key
  ├── state/hands.db      the hands' ledgers and queues
  ├── state/browser/      the operator's browser profile and downloads
  └── secrets/            spine.env, teeny.env, cortex.env, core.token, openhuman.key
```

## First time on the box

From the checkout:

```sh
git submodule update --init --recursive vendor/openhuman
mkdir -p secrets && chmod 700 secrets
cp deploy/spine.env.example secrets/spine.env && chmod 600 secrets/spine.env
$EDITOR secrets/spine.env            # LADDER_URL, LADDER_API_KEY, SPINE_MODEL; CortexDB if wanted
cp deploy/teeny.env.example secrets/teeny.env && chmod 600 secrets/teeny.env
$EDITOR secrets/teeny.env            # the hands' keys; DRY_RUN stays 1 until you mean it
TEENY_UID=$(id -u) TEENY_GID=$(id -g) docker compose -f deploy/compose.yaml --profile web up -d --build
docker compose -f deploy/compose.yaml logs -f spine     # "[spine] ready" once seeded
```

The first boot mints `secrets/core.token` (the RPC bearer) and
`secrets/openhuman.key` (the master key).

Compose reads interpolation values (`TEENY_UID`, `TEENY_GID`, `XHOOK_PORT`,
`WEB_PORT`, `LADDER`, `CALENDAR_TZ`) from the shell or from a `deploy/.env`
file if one exists. Nothing else in this stack reads `deploy/.env`; the keys
live in `secrets/`.

## The X webhook

X's Activity API pushes `post.mention.create`, `post.reply.create` and
`post.quote.create` for the account to
`https://teeny.enamakel.com/api/webhooks/x`. `bin/x-webhook` answers X's CRC
challenge and refuses any delivery whose signature is not the HMAC of the
body under `X_API_SECRET`. `curl -fsS http://127.0.0.1:3110/health` checks it.
X search is not used.

## Watching it

```sh
bin/crons                             # jobs, next run, last result: read-only, straight from storage.db
bin/crons runs tick                   # one job's recent runs and output
docker compose -f deploy/compose.yaml exec spine bin/ask "hello"
docker compose -f deploy/compose.yaml exec spine bin/doctor --live
docker compose -f deploy/compose.yaml logs -f xhook
```

## Changing it

- To change what it runs, edit `brain/cron.toml`, then
  `docker compose -f deploy/compose.yaml restart spine`. `bin/seed-cron`
  reconciles the core's jobs by name at every boot.
- For a script or the brain, pull, then restart. Nothing of Teeny's is
  baked into the image. `brain/MEMORY.md` is Teeny's live page and not in
  git, so a pull never touches it; the repository keeps where it started,
  `brain/MEMORY.seed.md`, and the boot starts the live page from it when
  there is none.
- Once, on a checkout from before the live page left git: a plain pull
  refuses (Teeny has rewritten the page since). Move the page aside, pull,
  and put it back, where it is now ignored
  (`bin/test/memory_page_test.sh` checks these steps):

  ```sh
  mv brain/MEMORY.md state/MEMORY.live.md
  git pull --ff-only
  mv state/MEMORY.live.md brain/MEMORY.md
  ```
- For OpenHuman itself, bump `vendor/openhuman` (see `docs/developers.md`), then
  `up -d --build`.
- To change where the browser may go, edit `browser-allow.txt` here (one
  host per line, reviewed by you, never by Teeny), then restart. The operator
  agent's browser profile and downloads are in `state/browser/`, so a
  logged-in session survives restarts. Delete that folder to log everything
  out.
- The builder's queue is `hands/build jobs` and the operator's is
  `hands/tasks list`. `hands/tasks list --status needs_human` is what the
  browser left for you (a payment, a login, a CAPTCHA). Nothing the builder
  makes leaves the box while `DRY_RUN=1`.
- To tell it what to post:
  `docker compose -f deploy/compose.yaml exec -T spine hands/brief add "talk about ..."`.

## Backups

Back up `brain/storage.db` and `secrets/openhuman.key` together.
`storage.db` holds all of the core's state, including the cron jobs and its
secrets, which are encrypted under that key. A database without its key has
unreadable secrets, and a key without the database has nothing to unlock.

`state/hands.db` holds the hands' ledgers and queues. Back it up too.
