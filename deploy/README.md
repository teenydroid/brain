# Deploying Teeny

`deploy/compose.yaml` runs these services over the checkout, which is mounted
at `/srv/teeny`:

| Service | What | Port |
|---|---|---|
| `spine` | `teeny-spine` serving over the checkout. The core, its cron and every job. | none; reach it with `exec` |
| `cortexdb` | Teeny's long-term memory (CortexDB v0.10.4, models on Surplus Intelligence: `glm-5.3-flash` for writing memories, `gpt-6-luna` for recall, embeddings `venice-embed-1`). Its store is the `cortexdb-data` volume. Keys in `secrets/cortex.env` (`deploy/cortex.env.example`); the spine runs in its network namespace and finds it at `CORTEX_API_URL=http://127.0.0.1:3141` (`secrets/spine.env`); CortexDB binds loopback only. | none; compose network only |
| `xhook` | `bin/x-webhook`: X's webhook, which queues mentions, replies and quotes into `state/hands.db`. Same image as the spine. | `127.0.0.1:3110` |
| `web` | The landing page (`web/`, its own Next.js image). Behind the `web` profile. | `127.0.0.1:3120` |
| `reel` | Clips with voice and captions, and songs, for `hands/reel`: MoneyPrinterTurbo (`vendor/moneyprinter`, a release tag) behind `deploy/reel/serve.py`. Behind the `reel` profile. | none; compose network only (`http://reel:8080`) |

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
$EDITOR secrets/spine.env            # LADDER_URL, LADDER_API_KEY, SPINE_MODEL; CortexDB and SURPLUS_API_KEY if wanted
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

## The reel service

`hands/reel` renders clips and songs on the `reel` service
(docs/hands.md, "reel"). It is opt-in, behind the `reel` profile, and posts
nothing. It needs about 4 cores and 4 GB while rendering (capped at that in
compose; one render at a time). To start it on the box:

```sh
git submodule update --init vendor/moneyprinter        # the pinned release tag
docker compose -f deploy/compose.yaml --profile reel build reel
docker compose -f deploy/compose.yaml --profile reel up -d reel
docker compose -f deploy/compose.yaml exec -T spine curl -fsS http://reel:8080/ping   # "pong"
```

`up -d reel` starts that one service; it does not recreate the spine. Then
check what a clip would be, with nothing rendered:

```sh
echo '{"kind":"clip","script":"...","materials":["brain/assets/tiny/tiny-love.jpg"]}' |
  docker compose -f deploy/compose.yaml exec -T spine env DRY_RUN=1 hands/reel make
```

`REEL_URL` defaults to `http://reel:8080`. The caps (`REEL_MAX_PER_DAY`,
`REEL_SONGS_MAX_PER_WEEK`) and the music switch (`REEL_MUSIC`, off) are in
`secrets/teeny.env` (`deploy/teeny.env.example`). Renders wait in the
`reel-storage` volume until the hand copies them into `state/media/` and
deletes the task; a restart of the service forgets its tasks, and a folder
left by a task in flight then stays in the volume
(`docker compose -f deploy/compose.yaml --profile reel down reel` and
`docker volume rm teeny-spine_reel-storage` clear it).

Egress: the only outbound call the service makes is upstream's Edge TTS
request (`speech.platform.bing.com`, Microsoft's unofficial endpoint, over
https). No model, stock library, upload service or update check is
configured or run. Compose does not restrict it today: the service sits on
the stack's default network, which reaches the internet. To hold it to that
one host, either put `reel` on its own network with an egress proxy that
allows only `speech.platform.bing.com:443` (set `HTTPS_PROXY` on the
service; edge-tts honours it), or add a host firewall rule on the
`DOCKER-USER` chain that drops the reel container's traffic to anything but
that host. Either way the spine still reaches it on the compose network.

To move the pin: check out the new release tag in `vendor/moneyprinter`, run
`python3 -m unittest discover -s hands/tests -t hands/tests -p 'test_reel.py'`
(its schema check reads the new job model), rebuild, render one clip, and
land it as a pull request. Never edit the submodule; a fix upstream needs is
a pull request to `harry0703/MoneyPrinterTurbo`.

## The X webhook

X's Activity API pushes `post.mention.create`, `post.reply.create`,
`post.quote.create`, `follow.follow` and `follow.unfollow` (inbound) for the
account to `https://teeny.enamakel.com/api/webhooks/x`. The subscriptions are
made, or refreshed after an event type is added, with
`hands/x subscribe --webhook-id ID` (a preview) and then `--confirm`. `bin/x-webhook` answers X's CRC
challenge and refuses any delivery whose signature is not the HMAC of the
body under `X_API_SECRET`. `curl -fsS http://127.0.0.1:3110/health` checks it.
X search is not used.

## The studio cutover

Merging and deploying the studio changes what Teeny posts in public: from the
first slot after the restart, every original post on X is written by
`bin/studio` (docs/architecture.md, §4b) and goes out with no per-post
approval. Replies are unchanged. To deploy it:

1. Pull, with the submodules:

   ```sh
   git -C /home/ubuntu/teeny pull --ff-only
   git -C /home/ubuntu/teeny submodule update --init --recursive
   ```
2. Add these lines to `secrets/teeny.env` (the rest of the `STUDIO_*`
   switches can stay at their defaults; `deploy/teeny.env.example` shows
   these commented out, so a copy of it stays off). `STUDIO` takes only
   `1/true/yes/on` or `0/false/no/off`: anything else posts nothing on either
   path and fails `bin/doctor`:

   ```sh
   STUDIO=1
   STUDIO_WRITER_MODEL=claude-fable-5.2
   STUDIO_EDITOR_MODEL=claude-opus-5.5
   SAFETY_JUDGE_MODEL=claude-opus-5.5
   ```

   `SAFETY_JUDGE_MODEL` is what the truth judge, the cold reader, the anchor
   judge and the guard's content judge ask, so no judge is the writer. It is
   also asked for every reply and every Moltbook write the guard screens
   (and with it unset, or equal to the writer's model, a live slot posts
   nothing: `bin/doctor` warns):
   expect the judge's share of the model bill to grow. Leave `STUDIO_DRY`
   unset (0): set to 1, every slot runs its gates and posts nothing.
3. Check no generation is running, since a restart kills it:

   ```sh
   docker compose -f /home/ubuntu/teeny/deploy/compose.yaml exec -T spine bin/evolve status | jq .running
   ```

   Go on only when it prints `false`.
4. Rebuild the spine image and start it (the cron jobs are reconciled at
   boot):

   ```sh
   docker compose -f /home/ubuntu/teeny/deploy/compose.yaml up -d --build spine
   ```
5. Verify, after boot:

   ```sh
   docker compose -f /home/ubuntu/teeny/deploy/compose.yaml logs spine | grep "the studio's"
   ```

   must show `model: the studio's claude-fable-5.2 has a N-token window` and
   the same for `claude-opus-5.5` (bin/lib/model_windows.sh). A line saying
   "no window" or "refused a window" means its turns may come back empty: fix
   that before the next slot. Then `bin/crons` lists `teeny.studio-0430`,
   `-0930`, `-1500`, `-sunday` and `-2300` and no `teeny.x-quote`,
   `teeny.morning`, `teeny.take`, `teeny.show` or `teeny.growth`; `bin/doctor
   --live` is green; and a preview that posts nothing:

   ```sh
   docker compose -f /home/ubuntu/teeny/deploy/compose.yaml exec -T spine bin/studio --dry-run --slot check
   ```

   After the first slot: `tail state/studio/<slot>.log` (the detached run's
   output), `hands/story runs --limit 4` (every run, its pick,
   its gates and what came of it) and `sqlite3 state/hands.db "select id,
   producer, storyline, shape from x_posts order by created_at desc limit 5"`.

**To roll back**, set `STUDIO=0` in `secrets/teeny.env` and restart the
spine (step 3 first). Nothing else changes: each studio job then runs its
slot's old playbook (x-quote, morning, take, growth on Sundays, show),
`shiplog` runs again at 23:10, the mind's `post_x` posts, and the brief and
the peer quote post without the editor. The schedule is the same, except
that the brief runs at 14:55 and 22:55 (five minutes before the slot, not
after).

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
  agent's browser profile is in `state/browser/profile/` and its downloads in
  `state/browser/downloads/`, so a logged-in session survives restarts.
  Delete `state/browser/profile/` (with the spine stopped) to log everything
  out.
- The builder's queue is `hands/build jobs` and the operator's is
  `hands/tasks list`. `hands/tasks list --status needs_human` is what the
  browser left for you (a payment, a login, a CAPTCHA its own tries did not
  pass). Nothing the builder
  makes leaves the box while `DRY_RUN=1`.
- To tell it what to post:
  `docker compose -f deploy/compose.yaml exec -T spine hands/brief add "talk about ..."`.

## Backups

Back up `brain/storage.db` and `secrets/openhuman.key` together.
`storage.db` holds all of the core's state, including the cron jobs and its
secrets, which are encrypted under that key. A database without its key has
unreadable secrets, and a key without the database has nothing to unlock.

`state/hands.db` holds the hands' ledgers and queues. Back it up too.
