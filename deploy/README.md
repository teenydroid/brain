# Deploying the spine

The spine is one container: `teeny-spine` serving over the checkout, which
is mounted at `/srv/teeny`. It publishes no port and shares no database with
anything else on the box.

## First time on the box

From the checkout:

```sh
git submodule update --init --recursive vendor/openhuman
mkdir -p secrets && cp deploy/spine.env.example secrets/spine.env && chmod 600 secrets/spine.env
$EDITOR secrets/spine.env            # LADDER_API_KEY, SPINE_MODEL; CortexDB if wanted
cp deploy/teeny.env.example secrets/teeny.env && chmod 600 secrets/teeny.env
$EDITOR secrets/teeny.env             # the X keys; DRY_RUN stays 1 until you mean it
docker compose -f deploy/compose.yaml up -d --build
docker compose -f deploy/compose.yaml logs -f spine     # "[spine] ready" once seeded
```

The first boot mints `secrets/core.token` (the RPC bearer) and
`secrets/openhuman.key` (the master key).

## Watching it

```sh
bin/crons                             # jobs, next run, last result: read-only, straight from storage.db
bin/crons runs heartbeat              # one job's recent runs and output
docker compose -f deploy/compose.yaml exec spine bin/ask "hello"
```

## Changing it

- **What it runs:** edit `brain/cron.toml`, then
  `docker compose -f deploy/compose.yaml restart spine`. `bin/seed-cron`
  reconciles the core's jobs by name at every boot.
- **A script or the brain:** pull, then restart. Nothing of Teeny's is
  baked into the image.
- **OpenHuman itself:** bump `vendor/openhuman` (see the root README), then
  `up -d --build`.
- **Where the browser may go:** edit `browser-allow.txt` here (one host per
  line, reviewed by you, never by Teeny), then restart. The operator agent's
  browser profile and downloads are in `state/browser/`; a logged-in session
  survives restarts. Delete that folder to log everything out.
- **The builder and the operator:** their queues are `hands/build jobs` and
  `hands/tasks list`. `hands/tasks list --status needs_human` is what the
  browser left for you (a payment, a login, a CAPTCHA). Nothing the builder
  makes leaves the box while `DRY_RUN=1`.

## Backups

Back up `brain/storage.db` and `secrets/openhuman.key` **together**:
- `storage.db` holds all of the core's state, including the cron jobs and its
  secrets, which are encrypted under that key.
- A database without its key has unreadable secrets.
- A key without the database has nothing to unlock.

## The landing page

`web/` is its own Next.js image, served by the host's nginx through
`nginx/teeny.enamakel.com.conf`. `web-vhost.sh` installs that vhost.
