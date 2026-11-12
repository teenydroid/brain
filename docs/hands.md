# Hands

The hands are the only code Teeny owns. [`architecture.md`](architecture.md) §3
has the contract: one JSON object out, exit `0`/`2`/other, secrets read by the
hand, state in `state/hands.db`, `DRY_RUN=1` by default.

The hands are `x`, `guard`, `gh`, `moltbook`, `slopcash`, `mail`, `stats`,
`world`, `requests`, `stimuli`, `brief`, `image`, `video`, `build`, `tasks`,
`wallet`, `bounty`, `goals` and `vps`. Each is an executable in `hands/`, with its code in
`hands/lib/teeny_hands/` and its tests in `hands/tests/`.

**Language.** The hands are written in the Python 3.11 standard library, with
no dependencies. OAuth 1.0a signing, transactional SQLite and webhook
signatures are not jobs for bash. Each executable is a three-line shim that
runs `python3 -I`, so no `PYTHONPATH` or planted module can change what it
loads. The shared contract (outcomes, exit codes, secrets, dry run, state, the
core client) lives in `hands/lib/teeny_hands/hand.py`. To run every test:
`python3 -m unittest discover -s hands/tests -t hands/tests`.

**R** marks a read verb. **A** marks an acting verb, which only `bin/`
scripts (`bin/act` for a playbook, `bin/tick` for the mind's intents) and the
operator (the person) call. **O** marks an operator-only verb. **J** marks a job
verb: it says nothing to anyone but writes state and calls the core, so, like
an acting verb, it is never given to a model. The scheduled agents (the
builder and the browser operator) call no hand at all. A `desk` job verb
lays their work out as a file under `state/workbench/desk/` and collects the
report they leave beside it.

## `x`: the only holder of the X keys

| Verb | | Does |
|---|---|---|
| `ingest [--max N] [--no-offer]` | J | Reads the mentions timeline since the `x_cursor` cursor into `x_mentions`, unique on post id. The account's own posts and posts already answered are `skipped`; `guard inbound` screens the rest, and a hold is `held`. Every `pending` row (the webhook's included) is offered to the mind's inbox (`stimuli`), or with `TEENY_INBOX=core` to the core's inbound queue (`openhuman.inbound_offer`, source `x:mentions`), and marked `offered`. A failed offer stays `pending` for the next run. The cursor moves once the page is stored. |
| `mentions [--status S]` | J | The queue, newest first. |
| `mark ID --status S [--note T]` | J | What became of a mention after its draft: `drafted` (under `DRY_RUN`), `replied`, `refused` or `skipped`. |
| `requests [--max N]` | J | Reads the newest N (200) stored mentions, never X again, for asks to build something ("build me", "can you make", "make a tool that", "i wish there was a tool", ...) and records each in `build_requests` (source `x:mentions`, once per post id). A held mention, the account's own post or one skipped as answered is never a request. `bin/mentions` runs it after every ingest. |
| `get`, `thread`, `user`, `search`, `whoami` | R | X reads through the read cache (posts 30 days; searches and threads 10 minutes). Nothing scheduled calls `search`: X bills it per post, which makes it too expensive. |
| `reply`, `post` | A | Runs `guard check` (house style, the 280 limit in UTF-16, the emoji cap, links, lexicon, judge). Then the posting rules: for an own post, the gap (`POST_MIN_GAP_HOURS`, 3 hours by default, 20-minute tolerance) re-checked under the posting lock, and no repeat: a post or quote that is a near-duplicate (`teeny_hands.guard.repeats`: word-set Jaccard at least 0.8, links left out) of an own post or quote of the last `REPEAT_WINDOW_DAYS` (30) is refused with `rule: repeat` and the earlier post's id, under `DRY_RUN` too, and counted by the guard (`hands/guard repeats`); for a reply, never twice to one post, never to the account's own post, and 30 to 60 s pacing kept in `x_pacing`, refused with `wait_seconds` rather than slept through. Then `DRY_RUN`, then X. The post id is recorded first (`x_replies`, `x_posts`). A ledger write that fails after a post is logged, never retried. Under `DRY_RUN` there is no X write, no like and no ledger row. |
| `upload` | A | Uploads a file under `state/media/` or `brain/assets/`. Returns a media id that this hand minted and recorded in `hands.db`. `post` attaches only ids from that table or a Giphy id, never a URL, and never both. |
| `like` | A | Likes a mention (skipped under `DRY_RUN`). A like that went out is recorded once in `x_likes`, which bin/receipts reads. |
| `unsay` | A | Deletes an own post. Previews unless `--confirm`. |
| `twitr-connect HANDLE`, `twitr-accounts` | O | Links an X account to the wallet on twitr.sh (below), and lists the linked ones. Run by the operator, once. |

A `post` or `reply` passes these steps in order (`teeny_hands/x/publish.py`);
any refusal exits 2 and nothing reaches X:

```
hands/x post | reply            (the draft on stdin)
  │
  ▼
guard check ───── hold ─────► guard_holds row, refused as `held`
  │ allow: the guard's rewrite goes on (a released hold skips this step only)
  ▼
280 limit (UTF-16), attachments (a minted media id or a Giphy id)
  │                           reply only: never to the account's own post
  ▼
lock "posting" (post) or "replying" (reply)
  │  post:   the gap since the last own post; no near-duplicate of one of the last 30 days
  │  reply:  never twice to one post, 30 to 60 s pacing
  │  both:   the daily link-post budget
  ▼
DRY_RUN=1 ? ── yes ─► {"would_post": ...}   no X write, no ledger row
  │ no
  ▼
X API ─► id recorded at once (a reply: x_replies), then the x_posts ledger row
         a record that fails is logged on stderr, never retried
```

### twitr.sh

[twitr.sh](https://twitr.sh) is a pay-per-call X API with no keys: every call
is paid in USDC over x402 from Teeny's own wallet
(`teeny_hands/x/twitr.py`). Each call goes through hands/wallet's `x402
fetch`, the one gate for paid requests. That gate makes one
`openhuman.x402_fetch`: the core sends the request, TinyWallet signs the 402
within the cap given for the call, and the core retries and records the
settlement. The keys never come near either hand.

| Switch | Sends to twitr | Stays on the X API |
|---|---|---|
| `X_TWITR_READS` | `get`, `thread`, `user`, `search`, and the reads `reply` and `unsay` make first | the mentions timeline and `whoami`: an owned read on X is $0.001 a post, under twitr's $0.0024 floor |
| `X_TWITR_WRITES` | `post`, `reply`, `like`, `unsay` | quotes and posts with media: `x_write` has no quote, and X's media ids are X's |

Reads cost $0.0012 a record with a $0.0024 floor. A search or conversation
read is an `upto` payment on Base: the signed amount is `resultsLimit` ×
$0.0012, and only what comes back is charged (nothing for an empty answer).
A write costs at most $0.036. A read that fails on twitr is read again from X.
A write never is. A failed twitr write may still be out, so the publish path
treats it exactly as a failed X write (`_in_doubt`). Each write carries an
`Idempotency-Key` made from what it says, so a retry within ten minutes
replays the first result rather than posting twice.

Four limits stand between a call and the wallet:

- **The hand's own cap.** Any call priced over `TWITR_MAX_PER_CALL_USD`
  (5 cents) is refused here and never reaches the wallet.
- **The wallet's gate.** `twitr.sh` must be in the operator's `x402 hosts`
  allowlist, and each call is held to `WALLET_MAX_X402_PER_REQUEST_USD` and
  `_PER_DAY_USD`. Each call is written to `x402_requests` (`hands/wallet x402
  log`).
- **The core.** It refuses a payment over the call's `maxAmountAtomic`.
- **TinyWallet's own budget.** `bin/lib/wallet_config.py` pins it from the
  same caps.

Paid calls run one at a time under the wallet's `wallet-x402` lock. That is
also what twitr asks of a wallet. Under `DRY_RUN` the gate calls nothing, so
twitr reads come from X.

Writes act as an X account that the wallet linked on twitr.sh, and twitr
checks every write against the paying wallet. The link is signed with the
wallet's EVM key (Sign-In-With-X), so every payment is pinned to
`TWITR_NETWORK`, Base. The wallet therefore needs USDC on Base as well as on
Solana. Linking is done once, by a person:

```
hands/wallet x402 hosts add twitr.sh --note "hands/x reads and writes"
hands/x twitr-connect teenydroid   # → {connect_url, expires_in: 900}
# open connect_url in a browser and sign in to X there; Teeny never sees the password
hands/x twitr-accounts             # until teenydroid reads `linked`
# then X_TWITR_WRITES=true in secrets/teeny.env
```

### The webhook

X also pushes mentions, replies and quotes to the account's webhook,
`https://teeny.enamakel.com/api/webhooks/x` (registered with X, subscribed
through the X Activity API to `post.mention.create`, `post.reply.create` and
`post.quote.create` for the account). `bin/x-webhook`, the `xhook` service in
`deploy/compose.yaml` on `127.0.0.1:3110`, answers X's CRC challenge and
refuses any delivery whose `x-twitter-webhooks-signature` is not the
HMAC-SHA256 of the body under `X_API_SECRET`. It queues each post about the
account by somebody else into `x_mentions` exactly as `ingest` does (screened,
unique on post id), for the next `ingest` to offer. This is how mentions
arrive. Nothing is searched or billed per post, and the webhook catches
replies that X's mentions timeline leaves out (2026-11-11: four of nine
replies to the gm never came back from `/mentions`).

How a mention reaches whoever answers it (`teeny_hands/x/mentions.py`):

```
mentions timeline ── x ingest (from x_cursor) ──┐
webhook (bin/x-webhook, HMAC checked) ──────────┤
                                                ▼
                     x_mentions row, unique on post id
                       own post, or already answered ──► skipped
                       guard inbound holds it ─────────► held
                       otherwise ──────────────────────► pending
                                                │
                       x ingest offers every pending row
                ┌───────────────────────────────┴─────────────────────┐
                ▼ default                                TEENY_INBOX=core ▼
   stimuli inbox (bin/tick drains it        openhuman.inbound_offer
   into the teeny:mind thread)              (source x:mentions)
                └──────────────► row marked offered ◄─────────────────┘
                  a failed offer leaves the row pending for the next run

   after the draft: x mark ─► drafted | replied | refused | skipped
   a held row whose hold is re-screened clean goes back to pending
```

## `guard`: the content filter

| Verb | | Does |
|---|---|---|
| `check` | R | Runs, in order: links (allowlist plus approved hosts), lexicon (8 categories, leetspeak folding, word boundaries; profanity outbound only), then the judge. The judge fails closed. Returns `ok` with the text to publish, or `hold{reason}` and writes the hold. `surface` picks the limits (below). |
| `check --fields title,body` | R | After a rejection (the caller's context carries `rejection: {people}`), any field that names a person (an @handle, or one of `people` as a word) is held first, category `rejection`, by `screen`, before links, lexicon or judge (docs/roadmap.md, P4). Otherwise: the same for a draft of several fields, as GitHub, Moltbook and mail take them: stdin carries each named field plus `surface`. Every field gets its surface's hard limits (refused, never cut), the house style, links, lexicon and judge; the answer is `{"fields": {...}}`, each field rewritten. Any field that fails fails the draft and names its `field`, and the draft writes at most one hold (its fields as JSON). Hands call it in-process as `teeny_hands.guard.gate.check_fields(fields, *, surface, kind="post", max_emoji=None, context=None)`, which returns a `FieldsVerdict(kind, fields, reason, category, hold_id, field)`. |
| `inbound` | R | Screens third-party text before a model sees it. Every intake runs it: `x ingest` and the webhook, `mail ingest`, `moltbook inbox`, `bounty entries`. |
| `style` | R | House style: strips leading @handles, replaces dashes, straightens quotes, drops NFA/DYOR. |
| `repeats [--days N]` | R | How many near-duplicate posts the posting rules refused over the last N days (default 30), in all and by surface (`guard_repeats`: when, the surface, the score and the earlier post's id, never the draft). The one measure (`teeny_hands.guard.repeats.similarity`) is shared with `x`'s posting rules and `bin/proofs` (`L1.no_repeats`). |
| `holds list/approve/reject/release` | A | Operator review. `release ID [--note T]` publishes a withheld draft. It atomically moves `pending` to `releasing` and publishes through the hand that owns the hold's surface (today `x`: a post, or a reply to the post in its context). The posting rules and `DRY_RUN` still apply, but the guard is not asked again, since the operator's approval is the decision it deferred. The hold is then marked `approved` with where it went; on failure it goes back to `pending` with the error recorded. Under `DRY_RUN` it claims nothing and says what it would publish. A surface no hand releases yet (`github`, `moltbook`, `mail`) is refused before anything is claimed. |

**Surfaces and limits** (lengths in UTF-16 code units, refused over, never
cut; `max_emoji` overrides the cap):

| Surface | Fields and limits | Emoji per field |
|---|---|---|
| `x` (and any surface not below) | `text` 280 | `MAX_EMOJI_PER_POST` (1) |
| `github` | `title` 256, `body` 20000, `description` 350, `message` 5000 | 3 |
| `moltbook` | `title` 300, `body` 10000 | 3 |
| `mail` | `subject` 200, `body` 10000 | 3 |

On a long-form surface, `text` is the body, and a leading `@handle` is kept
(nothing adds it back there). The built-in link allowlist also carries
`github.com/teenydroid`, `moltbook.com`, `www.moltbook.com`, `agentmail.to` and
`solscan.io`.

**Repeated letters.** The screen collapses repeated letters, except in a
term made of one repeated letter, which keeps its count (`x{3,}`). Otherwise
`xxx` would read as `x+` and hold "x.com" and "on X". `xxx`, `XXXX` and `kkk`
are still held. `hands/tests/guard/test_screen.py` pins this.

The judge runs through the core (`inference_agent_chat_simple`), so the model
key stays in the core's encrypted store. It fails closed: an unreachable core
or an unreadable verdict is a hold. `SAFETY_JUDGE=0` turns it off, leaving the
lexicon and the links.

Holds are written best-effort and never turn into a publish.

**Re-screening.** A hold the judge made only because it failed (its reason
starts `the judge could not be reached` or is `the judge answered in an
unreadable shape`, category `unknown`) is not a verdict, and an outage
should not silence the people who wrote in during it. The pinned
`bin/health` job, right after the model has answered its probe, asks again
about at most ten such holds, oldest first and none younger than two
minutes (`teeny_hands.guard.rescreen`):

- An inbound X mention, Moltbook comment or mail is screened again through
  the guard's own inbound path (the lexicon, then the judge, on the stored
  text). Only a fresh `clean` releases it: the `x_mentions` or
  `mail_messages` row held by that hold goes back to `pending` for the next
  ingest to offer, a comment goes into the `stimuli` inbox, and the hold is
  `cleared`. A real verdict is written on the hold (its category and
  reason), and the hold stays. Another failure leaves it for the next run.
- A draft of Teeny's own is `expired`, never sent late; Teeny drafts afresh.

Re-screening never touches a hold from a real verdict (the lexicon, the links,
the judge naming a reason) or one an operator has decided, and it never
releases a verdict. With `SAFETY_JUDGE=0` it asks nothing. Each hold
records `rescreened_at` and `rescreen` (`cleared`, `prohibited`,
`unreachable`, `unreadable`, `expired`, `release_failed`); `bin/health`
prints the counts as `rescreen`.

The statuses a hold moves through (`teeny_hands/guard/holds.py` and
`rescreen.py`). `releasing` is a claim taken while the effect runs, never a
resting state:

```
                               pending
       ┌──────────────────────────┼──────────────────────────────┐
 holds reject             holds approve / release        bin/health re-screen
       │                          │                     (judge-failure holds only)
       ▼                          ▼                               │
   releasing ─► rejected      releasing                           │
                                  ├─ effect done ─► approved      │
                                  └─ effect failed ─► pending     │
                                     (error recorded)             │
       ┌──────────────────────────────────────────────────────────┘
       ├─ inbound item, fresh clean ─► releasing ─► cleared (item back in its queue)
       ├─ inbound item, real verdict ─► stays pending (category and reason written)
       ├─ judge fails again ──────────► stays pending, for the next run
       └─ Teeny's own draft ──────────► expired (never sent late)
```

## `gh`: the only holder of the GitHub token

Teeny codes in public as `GITHUB_USER` (default `teenydroid`). Every word it
publishes on GitHub goes through `guard check --fields` (in-process,
surface `github`) first, and what goes out is the guard's rewrite.

| Verb | | Does |
|---|---|---|
| `whoami` | R | The token's account, and whether it `matches` `GITHUB_USER`. |
| `repo OWNER/REPO` | R | One repository (cached 10 minutes). |
| `issues OWNER/REPO [--label L] [--max N]` | R | Open issues, pull requests left out (10 minutes). |
| `pr OWNER/REPO NUMBER` | R | One pull request (5 minutes). |
| `contributing OWNER/REPO` | R | Reads `AI_POLICY.md`, `CONTRIBUTING.md` and `CODE_OF_CONDUCT.md` (root, `.github/`, `docs/`) and says whether the project forbids AI-, LLM- or bot-made contributions: `{forbids_ai, verdict: forbids\|allows\|silent, files, evidence}`. It is a conservative keyword-and-phrase heuristic: a ban forbids, a clear welcome allows, a mention it cannot read forbids, and silence is not a ban (cached a day). |
| `activity [--days N]` | R | Teeny's own pull requests opened and merged and its commits over the last N days (default 7), for the ship log (10 minutes). |
| `create-repo` | A | `{name, description?, private?}`. Under `GITHUB_USER` only, and only when the token is that account's. The description is screened. |
| `push` | A | `{workdir, repo, branch, message}`. Commits everything git would add in a folder under `state/workbench/` (symlinks resolved) as Teeny (`GIT_NAME` / `GIT_EMAIL`, default `teeny@agentmail.to`; see "git") and pushes `HEAD` to `branch` on an own repo. Never forces, ignores the box's git config, runs no hooks. Refused with `findings` (`{path, rule}`, never the secret) when the files or the history hold a secret path (`.env*`, `secrets/`, `state/`, `*.pem`, `id_*`, `*.key`) or a token (`ghp_`, `github_pat_`, `sk-`, `xox`, `AKIA`, a private key block). The token reaches git only through the environment of that one command and an askpass helper that holds no secret; it is never in the URL, a config file or a log. |
| `open-pr` | A | `{repo, head, base, title, body}`. On somebody else's repo, `head` is Teeny's fork branch and the disclosure footer is appended (a line holding one em dash, U+2014, then "opened by Teeny, an AI agent (github.com/teenydroid), operated by @senamakel. close it if it's not useful."). It is refused (`rule`) when the day's `GH_MAX_EXTERNAL_PRS_PER_DAY` (2, UTC) are spent (`daily_cap`), when Teeny already has an open PR there (`open_pr`), or when `contributing` forbids AI (`contributing`). |
| `comment` | A | `{repo, number, body}`. On somebody else's repository, a live comment first reads the target: when it is a pull request of Teeny's that was closed without being merged, the guard is told so (context `rejection`, the repository's owner as the person involved), and a body that names anybody (an @handle, or the owner's login) is held for a person, category `rejection` (evals/invariants.md, `no-reprisal`). A rehearsal asks GitHub nothing and so cannot tell. |
| `fork` | A | `{repo}`, somebody else's. |
| `open-issue` | A | `{repo, title, body}`, on an own repo only (`owner`). |
| `close-issue` | A | `{repo, number, comment?}`, on an own repo only: the comment first, then the issue closed as completed. |
| `ingest` | | GitHub into the mind's inbox (the pinned `gh-inbound` job, every 15 minutes; docs/roadmap.md, A1). Off unless `GH_INBOUND=1`. Reads `GET /notifications` from a cursor (the first read looks back a day) and, for each issue or pull request notification, the newest comment it points at (the issue or PR itself when nothing was said after it opened), fetched only from the API's own host. On Teeny's own repositories every issue, PR, comment and review comment counts; elsewhere only a `mention`. Teeny's own words, bots (`[bot]`) and other subjects (releases, CI) are `skipped`. The rest goes past `guard inbound` (surface `github`): a hold never reaches the mind; what passes becomes one `gh · issue\|pr\|comment\|review\|mention` stimulus, ref `<owner/repo>#<n>:<comment id or "opened">`, once (`gh_notifications` is keyed by it). Then the star counts of the token's own repositories (`gh_stars`): a count that rose is one `gh · star` stimulus with the numbers only; the first read is a baseline. Writes nothing to GitHub (notifications are not marked read). Two comments between reads reach the mind as the newer one. The token must be able to read notifications (a classic token with the `notifications` scope). |
| `notifications [--max N]` | R | What `ingest` read, newest first, and what became of each (`offered`, `held`, `skipped`, with why). |

Under `DRY_RUN`, every acting verb answers `would_*` after every rule ran and
writes nothing anywhere: not GitHub, git or the ledger. Done actions are
recorded in `gh_actions` (which also counts the daily cap), reads in
`gh_cache`.

## `git`: how every git command is set up

The container's uid has no passwd entry and HOME is /tmp, so a bare `git
commit` there cannot tell who it is. Every git run in the hands (hands/gh's
`push`, bin/receipts) goes through `teeny_hands/git.py`, which:

- commits as `Teeny <teeny@agentmail.to>`, or `GIT_NAME` / `GIT_EMAIL` from
  secrets/teeny.env (never the process environment), set as GIT_AUTHOR_* and
  GIT_COMMITTER_*;
- reads no system or global config (HOME is state/), runs no hooks, signs
  nothing, uses no credential helper and never prompts;
- takes a token only for the one command that needs it, through askpass and
  that command's environment, and redacts it from any error.

The image also writes the same identity, `init.defaultBranch main` and
`safe.directory /srv/teeny` to /etc/gitconfig, for git run by hand inside
the container.

| Verb | Kind | What |
|---|---|---|
| `check` | R | Commits in a scratch repository under state/ (removed again) and reports git's `version` and the `name` / `email` it committed as. bin/doctor runs it. |
| `whoami` | R | The identity in effect. |

This hand holds no token.

## `moltbook`: the only holder of the Moltbook key

Moltbook is a Reddit-like network for AI agents (API from its `skill.md`,
v1.12.0). The base is always `https://www.moltbook.com/api/v1`. The bare
domain redirects and drops the key, so a base naming it is refused, and the
transport (`teeny_hands/rest.py`, shared with `mail`) never follows a
redirect. Every word published goes through `check_fields` (surface
`moltbook`: title 300, body 10000) and what goes out is the guard's rewrite.
A link post's `url` must be on the link allowlist.

| Verb | | Does |
|---|---|---|
| `whoami`, `status`, `home [--expand]` | R | `/agents/me` (10 min), the claim status, the dashboard (1 min). `--expand` attaches, to each mention of Teeny in someone else's post, that post and its latest comments (at most 5 posts, through the `post` and `comments` caches). |
| `feed [--submolt S] [--sort hot\|new\|top\|rising] [--max N]` | R | `/posts`, or `/submolts/S/feed` (5 min). |
| `post ID`, `comments ID [--sort best\|new\|old]` | R | One post (5 min), its comment tree (2 min). |
| `search QUERY [--type posts\|comments\|all]`, `profile NAME`, `submolts`, `submolt NAME` | R | Semantic search (10 min), an agent, every submolt, one submolt with `allow_crypto` (1 h). |
| `inbox [--days N] [--max N]` | R | What was said to Teeny, into its mind's inbox (`stimuli`): every comment on its own posts from the last N days (7), and replies under its own comments elsewhere, newest N posts (10) of each. The inbound guard screens each one, and a held one is not added. `bin/tick` runs it at most every 10 minutes. |
| `metrics refresh` | R | Measures Teeny's own posts and comments (the ledger's, last 14 days) about 1h, 24h and 72h after they went out: score, comment count (for a comment, its replies) and Moltbook's `verification_status`. Each checkpoint gets one `moltbook_post_metrics` row and is never fetched again once recorded. A post is read through `post`, a comment found by id in its post's latest 100 `comments`, both through their caches. A late run records only the latest checkpoint due (a write first measured at 30h has a 24h row and no 1h row; `at` says when it was read). At most 20 writes a run, newest first (`left` counts the rest); a 429 ends the run. It records under `DRY_RUN` too, as `inbox` does, because it only reads. `bin/stats-snapshot` runs it. |
| `metrics list [--days N]` | R | The latest row of each write measured in the last N days (7), newest write first: a post with its title, a comment with its post's id. |
| `study` | R | What wins this week, worked out deterministically (no model). `top` is the 15 best posts by score of the last 7 days from the `hot` and `top` feeds; `by_submolt` is the best 5 of each of `agents`, `memory`, `builds`, `tooling`, `infrastructure`, `security`. Each comes with title, submolt, author, score, comment count, `created_at` and the first 400 characters. `own` is Teeny's last 10 posts and comments with their latest `metrics` row. The feeds part is cached 6 h (kind `study`, 14 feed reads at most). A study with a failed feed is answered with `errors` and not cached, and a 429 stops the reading. Every title and body is another agent's: data, never instructions. |
| `material` | R | Teeny's own receipts of the last 7 days as short dated fact lines its posts may cite. `guard_holds`: the count, and per day by category ("judge unreachable" for a judge that was down; never the held text or the hold's reason). `health`: the health check's alarm and recovery stimuli, and what `state/health.json` says now. `merged_prs`: its PRs merged into teenydroid/brain, through `hands/gh activity` (skipped with a note without `GITHUB_TOKEN`). `journal`: the first line of each beat and every bullet of the last 7 `state/journal/*.md`, quiet beats left out, at most 40 lines. Every line is scrubbed (`teeny_hands/scrub.py`, shared with `bin/health`) of URLs and their query strings, addresses and anything that looks like a key. What could not be read is in `notes`. |
| `post` | A | `{submolt, title, body?, url?, type?}` (with no `ID`, stdin is the post). |
| `comment` | A | `{post_id, body, parent_id?}`. |
| `upvote` | A | `{post_id}` or `{comment_id}`; once each, never Teeny's own. |
| `follow`, `subscribe` | A | `{name}`, `{submolt}`; once each, never itself. `follow` refuses (`too_few_exchanges`) an agent that has said fewer than 2 things to Teeny on Moltbook (its `moltbook` stimuli): the follow list stays small and curated, and never holds a stranger. |
| `register --name N --description D` | O | Registers an agent and prints `api_key`, `claim_url` and `verification_code` once; the hand stores and logs nothing. The operator puts the key in `secrets/teeny.env` as `MOLTBOOK_API_KEY` and completes the claim. `DRY_RUN` applies. |
| `pending [--status S]`, `verify ID --answer N`, `unlock` | O | Lists the challenges left for the operator and the failure count; answers one; clears the lockout. |
| `collab add NAME --note T`, `collab list`, `collab remove NAME` | O | Consent on record (`collabs`: name, consent_note, consented_at). |

The hand checks these rules under the `moltbook-write` lock before Moltbook
hears of the write, and refuses each with its `rule`:

- Moltbook's limits, kept locally (`rate_limit`, with `wait_seconds`):
  30 writes a minute, one post per 30 minutes, one comment per 20 seconds.
  New-agent mode covers the 24 hours after `MOLTBOOK_CLAIMED_AT` (or after the
  first accepted post when that is unset, and always when there is neither):
  one post per 2 hours, one comment per 60 seconds and 20 a day. A 429 is
  refused with Moltbook's `Retry-After`, never retried.
- Our caps (`daily_cap`, last 24 hours): `MOLTBOOK_MAX_POSTS_PER_DAY` (3),
  `MOLTBOOK_MAX_COMMENTS_PER_DAY` (30, and never above Moltbook's 50, or 20
  for a new agent).
- Comments (`once_per_post`, `once_per_parent`, `own_post`,
  `own_comment`, `unknown_parent`): never twice on a post unless answering a
  reply to Teeny (a comment under Teeny's comment, or on Teeny's own post),
  never twice to one comment, and on its own post only the first comment,
  while nobody else has commented.
- Collaboration (`collab`): a post that says collab, co-built,
  "partnered with" or "with u/NAME" is refused unless every agent it names
  has a `collabs` row.
- Crypto (`crypto`): a post or comment that reads as crypto (`$TICKER`,
  `TINY` in capitals, token, wallet, NFT, solana, ...) goes only to a submolt
  whose info says `allow_crypto: true`, because Moltbook removes it anywhere
  else.
- Verification (`verification`, `verification_failed`,
  `verification_lockout`): a write may come back with an obfuscated math
  challenge to answer within 5 minutes, and ten failed challenges in a row
  suspend the account. `moltbook/challenge.py` is a deterministic parser: it
  drops noise characters, collapses repeated letters, reads number words
  with their grammar and takes the operation from its words. It answers only
  when it finds exactly two numbers and one operation. When it cannot, and
  `MOLTBOOK_CHALLENGE_MODEL` is on (the default), `moltbook/model_read.py`
  asks the core's model twice, worded differently, for the two numbers and
  the operation as JSON (never the result: the hand computes it). It answers
  only when both reads agree and every number they name is one the text
  writes; a spoken self-correction ("fifty, no, say seven") is the usual
  case. Otherwise it guesses nothing, records the challenge in
  `moltbook_pending` (`needs_verification`) for `verify`, and refuses. A wrong
  answer, an expiry or a challenge left pending counts as a failure, a
  correct answer resets the count, and after 3 every write is refused until
  `unlock`.

Under `DRY_RUN` every rule runs (reads included) and nothing is written to
Moltbook, the ledger or the failure count. The content id is recorded the
moment Moltbook returns it, before the challenge is answered. Tables:
`moltbook_posts`, `moltbook_comments`, `moltbook_actions`,
`moltbook_pending`, `moltbook_state`, `collabs`, `moltbook_cache`, and
`moltbook_post_metrics` (id, kind `post`/`comment`, target_id, checkpoint
in hours, at, score, comment_count, verification_status; unique per kind,
target and checkpoint).

`bin/health` also counts the writes nobody can see. When at least 3 went
out in the last 24 hours and fewer than 90% of them are visible (`verified`,
or `posted` with no challenge), its line carries `moltbook_unverified`, the
number still hidden (null otherwise). This is a count only and raises no
alarm.

The metrics refresh runs once a day from `bin/stats-snapshot`, not from a
cron job of its own: a brain job may only run `bin/heartbeat` or `bin/act`
(`bin/lib/cron_policy.py`), and a new pinned job is a change to the frozen
`evals/cron.pinned.toml`. So a 1h checkpoint is usually folded into the next
one due until something calls `metrics refresh` more often.

## `slopcash`: Teeny's project on slop.cash

slop.cash pays open-source contributors for accepted pull requests on the
projects registered there. Teeny's project (`SLOPCASH_PROJECT`, default
`teeny-s-brain`, on `teenydroid/brain`) is its job listing: headline,
description and `contribution` (approved scope, required tests, ...).
Contributors open pull requests against the repository and Slopbot
(`slopdotcash[bot]`) reviews each one in a comment. The hand holds no key,
because slop.cash has none by design. Reads are public. `GITHUB_TOKEN` is
optional, only raises GitHub's read rate limit, and is sent only to
`api.github.com`. Posting a review comment on GitHub stays with
`hands/gh comment`.

| Verb | | Does |
|---|---|---|
| `project [ID]` | R | Status, authority state and reason, `why_paused` and `next_step` (for `missing-repository-proof`: install the Slopbot GitHub App on the repository only, then `check --confirm`), reward and funding, revision (1 min). |
| `job [ID]` | R | The listing as posted: name, eyebrow, headline, description, `approvedScope`, `requiredTests`, `contributionGuide`, `supportedTasks`, the whole `contribution`, the repository and branch (1 min). |
| `jobs [--steward LOGIN]` | R | Every registered project LOGIN (`GITHUB_USER`, teenydroid) stewards: id, status, headline (5 min). |
| `revisions [ID]` | R | The project's revision history, newest first, without the manifests (5 min). |
| `submissions [--state open\|closed\|all] [--max N] [--repo R]` | R | Pull requests on the project's repositories: number, title, author, dates, draft, merged, head sha, whether the body carries the "Made via @slopdotcash" footer, and Slopbot's latest comment (headline, body clipped to 1500) (2 min). |
| `submission NUMBER [--repo R]` | R | One pull request: files (path, status, additions, deletions), the head's checks (statuses and check runs, one `state`), every Slopbot comment, the body (clipped), and the gate facts: `total_changed_lines`, `touched_paths` (rename sources included) (2 min). |
| `post-job --file PATH [--reason R] [--confirm]` | O | Edits the listing. PATH is a JSON object of the fields to change: `name`, `eyebrow`, `headline`, `description`, `links` (replaced whole), `contribution` (merged over the current one). Owner-restricted fields (steward, authority, terms, status, listingTier, repositories, skill, reviewSkill) are refused locally (`owner_field_restricted`), and so is anything else outside the listing (`not_editable`). Without `--confirm`, or under `DRY_RUN`, it previews each field before and after, and whether the change pauses the project and queues a rescan (a changed `contribution` does), without signing in. Confirmed, it signs in, reads the manifest and revision, and PUTs the whole manifest with only those fields changed against `expectedRevision`. A `409 stale_revision` is an error: run it again. |
| `check [--scan] [--confirm]` | O | Signs in and runs slop.cash's setup checks (`requestScan` with `--scan`), which activate the project when they pass. Prints the state, the checks and the one next action. Without `--confirm`, or under `DRY_RUN`, says what it would do. |
| `setup` | O | Signs in and lists the account's projects and stored applications. |

**Signing in.** Each operator verb signs in by itself, in process. The hand
starts a flow at `SLOPCASH_IDENTITY_BASE`, prints only the GitHub
authorization link on stderr, and polls no faster than asked until the
operator has signed in (as the steward) or `SLOPCASH_SIGNIN_TIMEOUT_SECONDS`
(300) pass. It then joins slop.cash for its hour-long `__Host-slop_points`
cookie, sent with slop.cash's Origin on every write. The flow id, the poll
capability, the assertion and the session token stay in process memory, never
in argv, the environment, a file, stdout, a log or an error. That is why these
verbs belong to the operator and never to an agent. Reads are cached in
`slopcash_cache`.

## `mail`: the only holder of the AgentMail key

Teeny's inbox (`AGENTMAIL_INBOX`) is on AgentMail (`https://api.agentmail.to/v0`,
free plan: 100 e-mails a day). Subjects and bodies go through `check_fields`
(surface `mail`: subject 200, body 10000).

| Verb | | Does |
|---|---|---|
| `whoami` | R | `/auth/me` and whether its inbox scope `matches` `AGENTMAIL_INBOX`. |
| `list [--unread] [--max N] [--label L]`, `get ID`, `thread ID` | R | Recent mail (1 min), a message (10 min), a thread (2 min). |
| `codes [--since-minutes N] [--from-domain D]` | R | Verification codes (4 to 8 capitals and digits with a digit, near "code", "verify", "OTP", "sign in", ...; a year is not a code) and verification links from mail received in the last N minutes (30). A link is returned only when the guard's link allowlist passes it; the others are counted as `withheld_links` and never shown. Always fresh. |
| `ingest [--max N] [--no-offer]` | J | Reads new mail since the `mail_cursor` timestamp into `mail_messages`, unique on message id. Teeny's own sent mail is `skipped`; `guard inbound` (surface `mail`) screens the rest, and a hold, or an Agent Armor `review`, is `held`. A pending message that asks Teeny to build something ("build me", "can you make", "could you build", ...) is also recorded in `build_requests`. Every `pending` row is offered to the mind's inbox (`stimuli`), or with `TEENY_INBOX=core` to the core (`openhuman.inbound_offer`, source `mail:inbox`, key the thread, event id the message id), and marked `offered`. A failed offer stays `pending`. |
| `queue [--status S]`, `mark ID --status S [--note T]`, `requests [--status S]` | J | The intake (statuses as `x_mentions`), what became of a message, the build requests. |
| `reply` | A | `{message_id, body}`. Only to a message in the inbox (a reply stays in its thread: `no_thread`), never Teeny's own (`own`), never one the guard held (`held`), once per message (`replied`, with `reply_id`). |
| `send` | A | `{to, subject, body}`; `to` is an address or up to 5. Only to addresses in `contacts` (`contacts`): Teeny never cold-mails. |
| `label` | A | `{message_id, add?, remove?}`. |
| `contacts add EMAIL --kind vendor\|winner\|collaborator [--note T]`, `contacts list`, `contacts remove EMAIL` | O | Who Teeny may mail first. |

Every reply and send counts toward `MAIL_MAX_SENDS_PER_DAY` (20, UTC day,
`daily_cap`). A 429 is refused with `Retry-After`, never retried. Under
`DRY_RUN` the rules run and nothing is sent or recorded. Tables:
`mail_messages`, `mail_cursor`, `mail_sent`, `contacts`, `build_requests`
(shared: `teeny_hands/requests.py`, statuses `pending|picked|skipped|shipped|failed`,
used by `x requests` the same way, and picked from with `hands/requests`),
`mail_cache`.

`bin/mail` (the pinned `mail-inbound` job, every 5 minutes) runs `hands/mail
ingest`, then `bin/inbound-consume --source mail:inbox`. By default the mind
answers mail (`reply_mail` intents). With `TEENY_INBOX=core` the core drafts each
thread with the tool-less `teeny-mail` agent (prompt: `brain/playbooks/mail.md`,
written into the core's config by `bin/lib/inbound_config.py`), and each
reply goes through `hands/mail reply` and is marked.

## `telegram`: the only holder of the Telegram bot token

Teeny on Telegram is a bot (Bot API). A user account would need a phone
number and would break Telegram's rules for automation. Added to a group with
privacy mode off in @BotFather, the bot reads the whole room; in a DM it
answers like a person. It has no job of its own: every beat of bin/tick runs
`ingest` when `TELEGRAM_BOT_TOKEN` is set, so a DM is answered within a beat or
two. The mind answers with `say_telegram`.

| Verb | Kind | What |
|---|---|---|
| `whoami` | R | The bot's id and username, whether it reads all group messages (privacy mode off), and the owners. |
| `ingest [--max N]` | J | Reads new updates from a cursor (`telegram_cursor`), each text message a row in `telegram_messages`, screened by the inbound guard. An owner's DM (`TELEGRAM_OWNERS`) becomes a `telegram · dm` stimulus marked `owner`, which wakes Teeny at night; a stranger's DM does so only with `TELEGRAM_OPEN_DMS=1`. A group message that names the bot, replies to Teeny or says "teeny" is a `telegram · mention`; the rest of a group's new talk is one `telegram · chatter` stimulus per chat. Bots, groups outside `TELEGRAM_CHATS` and held messages reach nobody. |
| `chats` | R | The chats Teeny is in or was written to from. |
| `queue [--status S]` | J | The intake and what became of each message. |
| `send` | A | `{chat_id, text, reply_to?}`. Only into a chat Teeny has seen (never cold; a stranger's DM only with DMs open; a group only inside `TELEGRAM_CHATS` when set). `reply_to` must be a message Teeny read there and the guard did not hold. Guarded (surface `telegram`, 4096 characters), at most `TELEGRAM_MAX_SENDS_PER_DAY` (60) a day and `TELEGRAM_MAX_PER_CHAT_HOUR` (8) per chat an hour. Shows the "typing…" status first. Under `DRY_RUN` it answers `would_send` and writes nothing. |

The token is in every Bot API URL, so it never leaves `telegram/client.py`.
Errors carry Telegram's description, never a URL.

## `stats`: real numbers only

A post may quote a number only if it came from a `stats` row. The hand holds
no key: it reads each source through the hand that holds it.

| Verb | | Does |
|---|---|---|
| `snapshot` | J | Reads X (`X_BOT_USERNAME`'s followers, following and posts through the x hand's client; X's public user metrics carry no impressions, so there are none), Moltbook (`/agents/me`: karma, followers, following, posts, comments) and GitHub (`GITHUB_USER`'s followers, public repos, and stars across the repositories it owns, forks left out). One row per (date, source, metric); a second snapshot the same UTC day replaces the value. A source without its key is `skipped`, one that fails is `failed`, and the rest still land. `DRY_RUN` does not affect it, because it only reads and records. |
| `report [--days N]` | R | Every metric's latest value, and its value N days (7) before with the `delta`. Also `days_recorded` (how many days have a snapshot) and `first_recorded`. The ship log reads its "day n" from here; a model never counts it. |
| `get METRIC` | R | One latest value: `source.metric` (`x.followers`) or a metric only one source keeps (`karma`). No row is a refusal: quote no number. |

`bin/stats-snapshot` is the pinned `stats-snapshot` job (`15 0 * * *`).
Before the snapshot it runs `hands/moltbook metrics refresh` (its line goes
to stderr, and a refresh that fails never stops the snapshot).

The newest snapshot is also public. `bin/receipts` writes its allowed metrics
(whole numbers only, from a fixed list per source) to `stats/latest.json` and
`stats/YYYY-MM-DD.json` in the receipts repository, next to `wallet.json`
(the wallet's public address), for the website to read.

## `world`: the world score W

`hands/world` is the scorer (docs/research/teeny-application.md, §2). It
computes W, a vector of what Teeny controls, and J, the growth score built
from it. It is frozen (`evals/frozen.txt`), so Teeny can change how it grows
but not how the growth is counted. It holds no key and publishes nothing. It
reads the other hands' data through their modules and `state/hands.db`, the
way `stats` does. Code: `teeny_hands/world/` (`score.py` holds every
constant).

| Verb | | Does |
|---|---|---|
| `snapshot` | J | Today's W: one row per component into `world_scores`, keyed (date, component). A second snapshot the same UTC day replaces the day's values. A component whose sources are all missing is `skipped`, one whose source errs is `failed`, and the rest still land. A component with no row that day neither gains nor loses. It only reads and records, so `DRY_RUN` does not affect it. |
| `report [--days N]` | R | The latest W and, per component, its value N days (7) before, the plain `delta_log`, the capped `credit`, its weight, offset `c` and `daily_cap`, and its `contribution` to J. Then the penalty, J, the formula, and a `reading`: one plain sentence per component, for a playbook to hand a model. With no snapshot yet, J is null and the reading says to quote nothing. |
| `history [--days N]` | R | The rows of the last N days (7), one entry per day: each component's value and its parts. |

**The components.** The weight falls from attention to the world, which
Teeny writes itself.

| Component | Counts | Source | Weight | c | Daily cap |
|---|---|---|---|---|---|
| `attention` | followers on X, Moltbook and GitHub, Moltbook karma, and the distinct accounts that mentioned, commented, mailed or messaged Teeny in the 7 UTC days before the snapshot | `stats` rows (each metric's latest up to today), `stimuli` (`x`, `moltbook`, `mail`, `telegram`, with a `who`) | 1.0 | 50 | 0.10 |
| `tiny` | $TINY held, in tokens | `wallet tiny` | 0.8 | 10,000 | 0.20 |
| `money` | the wallet's USD value (USDC, and SOL at `WALLET_SOL_USD`) minus `WORLD_TEAM_USD` (what the team put in, a running total the operator keeps; 0 when unset) minus the last 30 days of `wallet costs` | `wallet balance`, `wallet costs --days 30` | 0.6 | 100 | 0.20 |
| `capability` | hands with tests (an executable in `hands/` with `hands/tests/<name>/test_*.py` or `hands/tests/test_<name>.py`; `image` and `video` share `media`), plus the channels Teeny can act on (`x`, `moltbook`, `github`, `mail`, `telegram` when their keys are set; `wallet` when its caps load) | the checkout, `secrets/teeny.env` | 0.5 | 5 | 0.10 |
| `shipped` | build jobs shipped, plus `github.stars`, `github.forks` and `github.open_issues` where `stats` keeps them | `build_jobs`, `stats` | 0.4 | 5 | 0.15 |
| `world` | each arc in `brain/narrative/ARCS.md` by its `- status:` line: 0 not started, 2 done (complete, reached, achieved), 1 anything else | `ARCS.md` | 0.2 | 2 | 0.10 |
| `penalty` | side effects on the UTC day before the snapshot: guard holds on Teeny's own text (not inbound, not approved, not cleared by the re-screen) and wallet payments in doubt | `guard_holds`, `wallet_payments` | 0.02 each | | none |

These are honest proxies, not the whole idea. Follower counts include
accounts that never engaged, because no source tells them apart; the
engaged accounts from the stimuli are the part that does. A hand counts by
having tests, not by passing them: the tests gate the merge (CI, `bin/doctor
--full`), not a daily job. There is no inflow ledger, so money is the
wallet's value with the team's own money taken out by hand. Teeny writes
`ARCS.md` itself, which is why the world weighs least. GitHub stars count as
shipped, not attention, so nothing counts twice.

**J.**

```
J = Σᵢ wᵢ · creditᵢ − 0.02 · penalty events         over the last N days
creditᵢ = Σ over consecutive rows of clip(Δ log(Wᵢ + cᵢ), ±capᵢ · days between)
```

- The log makes components comparable: 100 followers on 1,000 counts as
  $10 on $100. The offset `c` keeps a component near zero from counting one
  unit as a doubling.
- Money can be negative. Below zero the curve is mirrored,
  `2·log(c) − log(c − W)`, which has the same value and slope at zero, so a
  loss counts as much as the equal gain.
- The cap works both ways: one spike can't carry J, and one glitch in a
  source can't sink it. Across a gap of k days the cap is k times as wide.
- The penalty has no cap.

`bin/world-snapshot` is the pinned `world-snapshot` job (`30 0 * * *`),
after `stats-snapshot`, so attention reads that day's rows.

## `requests`: what people asked Teeny to build

This hand keeps one list of build requests from every channel
(`build_requests`, recorded by `mail ingest` and `x requests`), and the one
Teeny picks for the builder. Nothing here answers the person who asked, and a
pick is never a promise.

| Verb | | Does |
|---|---|---|
| `list [--status S] [--max N]` | R | The requests, newest first (20), optionally one status: `pending`, `picked`, `skipped`, `shipped`, `failed`. |
| `get ID` | R | One request and its ledger (`build_request_events`: when, which verb, the status it moved to, the note). |
| `pick` | A | `{id, reason}`. Picks the one to build next. Refused (`rule`) when the request is unknown (`unknown`), not `pending` (`status`), or another request is still being worked on (`one_at_a_time`, with `active`). Any status but `pending`, `skipped`, `shipped` and `failed` holds the slot, so a step the builder records on the way does too. Refused (`held`) when the guard held the message it came in (its `x_mentions` or `mail_messages` row is `held`) or the lexicon holds its words now; that request is set to `skipped`, with the reason. Otherwise it is `picked`, with the reason in its note and the ledger. The playbook `pick-request` is its caller. |
| `skip` | A | `{id, reason}`. Not doing it; only a `pending` request (a picked one is the builder's). |
| `reset ID [--note T]` | O | Back to `pending`, whatever it was, with the old status in the ledger. |

`pick` and `skip` write only `state/hands.db` and tell nobody, so `DRY_RUN`
does not stop them. Whatever is built from a pick goes out through hands that
keep `DRY_RUN` themselves. The builder takes a `picked` request, and sets it
`shipped` or `failed` when it is done.

## `stimuli`: the one inbox Teeny's mind reads

Every surface writes what happened here, and `bin/tick` drains it into the
`teeny:mind` thread (docs/architecture.md, 4a). Sources:

- `x ingest`: mentions, from the timeline and the webhook;
- `mail ingest`: mail;
- `moltbook inbox`: comments on Teeny's posts, and replies under its comments;
- the builder's and operator's desks: their reports. A finished `news` task
  arrives as `world · news` instead (its notes, framed as what web pages
  said; see `tasks`);
- `goals check`: `self · goal` when a goal is solved or fails;
- `bin/tick`: what a hand did with the last answer (`hand · outcome`), and
  Teeny's own follow-ups as they come due;
- `gh ingest` (`bin/gh-inbound`): `gh · issue`, `pr`, `comment`, `review`,
  `mention` and `star` (see `gh`);
- `bin/health`: `health · alarm` when the model has failed two checks in a
  row, and `health · recovered` when it answers again; `bin/health daily`
  (the pinned `health-daily` job, 00:40 UTC): one `health · daily` a day
  about the UTC day before, with what the model cost (the core's
  `cost_report`), failed cron runs by job, failed `bin/act` runs by
  playbook, failed beats of the mind, the doctor's checks that are not ok
  (names only) and guard holds by direction and category. Counts and names
  only: never a hold's text or reason, an error's words or a check's detail.
  A name that is not a plain name is counted as `other`.

`TEENY_INBOX=core` sends mentions and mail back to the core's inbound queue
instead, the old per-surface agents.

| Verb | | Does |
|---|---|---|
| `add` | A | `{surface, kind, ref, text, who?, payload?}`. One event, once: unique on (surface, kind, ref). The text is capped at 4000 characters. |
| `take [--max N] [--chars N]` | R | Takes the oldest pending, up to N (20) and about that much text (30k; always at least one), marked `taken` under a new `tick` id. A batch out for an hour is taken back. |
| `settle TICK` | A | The beat read its batch: `done` for good. |
| `release TICK` | A | The beat failed: back to `pending`, in its old place. |
| `counts` / `recent [--max N]` | R | How many in each status, and the newest of any status. |
| `follow-up` | A | `{about, in_hours \| due_at, ref?}`. A reminder Teeny set itself, due within 90 days. `ref` is what it is about, in the stimuli's refs (`x:111` and `111` are one post). |
| `drop-follow-up` | A | `{id}` or `{ref}`. Clears the waiting follow-ups by that id, or about that ref, so they never come due: `{"dropped": [ids]}`, empty when none was waiting. |
| `due` | A | Raises the follow-ups now due as `self · follow_up` stimuli, once. |
| `follow-ups` | R | The reminders still waiting, soonest first. |

A follow-up is `pending` until it is `raised` (came due), `dropped` (the mind
cleared it with `drop_follow_up`, or this verb) or `resolved` (`bin/tick`
answered the thing its `ref` names before it came due; `stimuli.resolve`,
docs/architecture.md, 4a).

It writes only `state/hands.db` (`stimuli`, `follow_ups`, `mind_notes`) and
tells nobody, so `DRY_RUN` does not apply.

## `brief`: what the team tells Teeny to post about

Teeny posts on X twice a day: the morning gm and one post about whatever the
team queued here (`briefs`). The playbook `brief` reads `next` every 15
minutes, skips without a model turn when nothing is queued (its input is
`required`), and answers through `post`.

| Verb | | Does |
|---|---|---|
| `add [--surface x\|moltbook] TEXT` | O | Queues a brief: the topic of the next told-to post, up to 2000 characters. Its text comes from arguments only, so a playbook's draft (piped on stdin) cannot queue one. `--surface moltbook` queues a Moltbook lead instead: a thread worth joining or a topic the operator scouted (on Reddit, X or Moltbook), with a note. A lead is never an X brief. |
| `next` | R | The oldest open X brief, `{brief: {id, text}}`, or `{brief: null}`. |
| `leads` | R | The open Moltbook leads of the last 48 hours, newest first (at most 10): `{leads: [{id, text, created_at}]}`. The Moltbook playbooks read them and decide for themselves whether one is worth a post or a comment, under the same guard and caps. A lead is never marked used; it drops out after 48 hours or when cancelled. |
| `list [--max N]` | R | The briefs, newest first (20), with status `open`, `posted` or `cancelled`. |
| `post` | A | `{id, text}`: Teeny's draft of brief `id`, already through the guard (bin/act). Sends `{text}` to `hands/x post`, which keeps its own rules, pacing and `DRY_RUN`. The brief is marked `posted` (with the post id) only when X took the post. A dry run leaves it `open`. A refusal from hands/x is a refusal here (exit 2) and an error is an error; either way the brief stays `open` for the next run. Refused when the brief is not `open`. |
| `cancel ID` | O | Drops an open brief. |

From the box: `docker compose -f deploy/compose.yaml exec -T spine hands/brief add "talk about ..."`.
A Moltbook lead: `... hands/brief add --surface moltbook "https://www.moltbook.com/post/<id> they are arguing about compaction; we have the 7-hour outage receipt"`.

## `image` and `video`: pictures and clips of Teeny

Every picture is an edit of Teeny's character sheet
(`brain/assets/teeny-reference.png`, `MEDIA_REFERENCE`), so Teeny looks like
Teeny: the prompt says what Teeny is doing and where. A video is a still made
that way, then animated. Both buy on Surplus (`SURPLUS_API_KEY`), cheapest
model first, with the rest as fallbacks when a seller can't take the job
(`MEDIA_IMAGE_MODELS`: muse-image-edit, firered-image-edit,
nano-banana-2-lite-edit; `MEDIA_VIDEO_MODELS`:
longcat-distilled-image-to-video, pixverse-c1-image-to-video,
seedance-1-5-pro-image-to-video). Measured 2026-11-11: about $0.016 a picture
and $0.07 a 5-second clip.

| Verb | | Does |
|---|---|---|
| `generate` | A | image: `{prompt}`. video: `{prompt, image_prompt?}` (the first frame; else `prompt`). The file goes to `state/media/`, and a `media_assets` row records the model, the cost and the path. Refused (`per_day`) past `MEDIA_IMAGE_MAX_PER_DAY` (8; a video's first frame doesn't count) or `MEDIA_VIDEO_MAX_PER_DAY` (2); refused (`budget`) when it would take the day past `MEDIA_MAX_USD_PER_DAY` ($1.00; a video reserves `MEDIA_VIDEO_MAX_USD_PER_JOB`, $0.20, before it starts). A video quoted above that cap is cancelled and the next model tried. A failed job costs nothing. Under `DRY_RUN` it says what it would make and spends nothing. |
| `list [--max N]` | R | What was made, newest first, and today's spend. |
| `get ID` | R | One. |

Neither publishes anything, and neither is ever a playbook's output. A
playbook that allows it (`media = ["image"]`, or `"video"`) lets the draft add
`"image": "<the scene>"` or `"video": "<the motion>"` to a post. bin/act puts
the description past the guard, calls the hand, uploads the file through
`hands/x upload` and attaches the id. If anything fails on the way, the post
goes out text-only, with the reason in the run's result.

## `build`: the builder's work queue

The builder (`brain/agents/builder.toml`, the pinned `builder` agent job,
every 2 hours) builds one job at a time in its own folder,
`state/workbench/<slug>/`. It reads its job from
`state/workbench/desk/build.json`, leaves its report in `build-done.json`
beside it, and holds no token. `desk` and `ship` run from cron
(`bin/build-ship`), collect the report and publish what it made through
hands/gh's own actions.

| Verb | | Does |
|---|---|---|
| `next` | A | The builder's open claim if it has one (`resumed: true`: a run that ended mid-job carries on), else the oldest `picked` build request with no job yet (`kind: request`), else a `daily` job (Teeny's own small thing) when none was made this UTC day; else `{job: null, reason}`. Creates the folder. Answers `{job: {job_id, kind, request, workdir, workdir_path, repo, resumed}, rules}`. Before anything is handed out, a claim older than 6 hours is failed as abandoned (and its request marked `failed`). |
| `done` | A | `{job_id, status: ok\|failed, summary, tests, repo?}`. Only for a `claimed` job; `ok` needs something in the folder. `tests` is a sentence or a list (what ran, what came of it). `repo` renames the repository it will ship as (a plain name). `failed` marks the request `failed` with the summary. |
| `desk` | J | The builder's desk. First it settles: `state/workbench/desk/build-done.json`, when there, goes through `done` with every rule above. A report `done` refuses (or one that is not JSON) is moved to `build-done.rejected.json`, and the reason is put on the next offer as `last_report_rejected`. A report that names no `job_id`, left while `build.json` offered no job (`job: null`), only says there was nothing to do: it is removed (`discarded` in the answer), not refused, not put on the next offer and not told to the mind. Then it claims: `next`, written to `build.json` with `report_to`, `report_to_path` (the same file, absolute, for an agent rooted at the workbench) and `report_shape`. |
| `jobs [--status S] [--limit N]` | R | The jobs, newest first. |
| `self` | A | `{text}`. A change Teeny proposes to itself: a `pending` build request with source `self`, picked with `requests pick` like any other. Its job is `kind: self` (below). |
| `self-sync` | J | Keeps Teeny's own clone (docs/evolve.md, "Teeny's own clone") current. Clones `GITHUB_USER/brain` into `state/workbench/brain/` (git directory `state/self/brain.git`) when missing; otherwise fetches `main` and `teeny/*` and fast-forwards a clean `main` to `origin/main`. Unshipped work (edits, a branch whose job is `claimed` or `ok`) is left alone; a branch whose job shipped or failed is parked (leftovers committed to it) and `main` checked out. Answers and writes `state/self/sync.json`: `at`, `commit` (origin/main), `head`, `branch`, `state` (`cloned`, `synced`, `dirty`, `diverged`, `busy`), and every `teeny/*` branch with `ahead`, `behind` and `pushed`. Never resets, forces or deletes. |
| `ship` | J | Every `ok` job, oldest first: creates `GITHUB_USER/<repo>` when GitHub says it does not exist (`gh create-repo`, the summary's first line as the description), writes a `.gitignore` for caches when there is none, and pushes the folder to `main` (`gh push`: the guard on the message, the owner rule, the workbench rule, the secret scan). The job is then `shipped` with `repo_url` and `commit_url`, and its request is `shipped` with the repository as its note. A refusal or an error is kept as `ship_error` and retried next run; after 3 the job is `failed` (and its request). Under `DRY_RUN` it answers `would_create`/`would_push` and changes nothing. |

`build_jobs` has one row per job (`kind`, `request_id` unique, `slug` unique,
`repo`, `status`: `claimed`, `ok`, `failed`, `shipped`). This hand only reads
`build_requests`. It writes back only to a request it built, and only while
that request is still `picked`: `shipped` or `failed`, with a note.
`bin/build-ship` is the pinned `build-ship` job (every 30 minutes):
`self-sync`, `desk`, then `ship`. The builder runs at ten past every second
hour, after a desk.

How the builder and cron hand work back and forth, and the states a job
moves through (`teeny_hands/build/jobs.py`):

```
 builder agent (every 2 h)                    bin/build-ship (cron, every 30 min)
 shell rooted at state/workbench/             self-sync ─► desk ─► ship
        │                                                   │        │
        │ reads   desk/build.json ◄──── desk writes `next` ─┤        │
        │ writes  desk/build-done.json ── desk settles it ──┘        │
        │         through `done`                                     │
        ▼                                                            ▼
 state/workbench/<slug>/ ────────────────────────────► gh create-repo, gh push
                                                      (self job: push_branch, open_pr)

 next ─► claimed ── done ok ──► ok ── ship ──► shipped (repo_url, commit_url | pr_url)
            │                    │
            │                    └─ refused or error: ship_error kept, retried next run;
            │                       after 3 tries (self job: a frozen path at once) ─► failed
            └─ done failed, or claim older than 6 h ─────────────────────────────────► failed
```

A self job comes from a picked request with source `self`. `next` syncs
the clone and starts `teeny/<slug>` from the fresh `origin/main`. While the
clone isn't free, the request waits (`waiting` in the answer) and the queue
moves on. Its offer adds `branch`, `base` and `instructions` (Teeny's own
`brain/agents/builder-self.md`), with rules of its own. `done` with `ok` needs
a change in the clone. `ship` takes these steps:

1. The guard reads the pull request's title (the summary's first line) and
   body (the summary, the tests, the checks).
2. It commits the branch as Teeny.
3. `frozen.check` runs against `origin/main`. A frozen path fails the job at
   once, with `paths`.
4. `gh`'s `push_branch` sends only a `teeny/*` branch, never `main`, with the
   secret scan on what the branch adds.
5. `open_pr` opens the pull request into `main`.

The job is then `shipped` with `pr_url`, and the mind gets a `builder`
stimulus (`pull_request`, or `ship_refused` when it gives up). Under
`DRY_RUN` it answers `would_push`/`would_open_pr`. Nothing merges.

## `tasks`: the browser operator's queue

The browser operator (`brain/agents/operator.toml`, the pinned `operator`
agent job, hourly) does one task at a time in OpenHuman's browser. It reads
its task from `state/workbench/desk/task.json` and leaves its report in
`task-done.json`; `desk` (`bin/tasks-desk`, every 15 minutes) collects it.
Playbooks (`hand:tasks add`) and the operator (the person) add tasks.
Nothing else turns a task into words for the mind: the desk tells it only the
outcome (`operator · report`), except for the news.

| Verb | | Does |
|---|---|---|
| `add [--source S]` | A | `{goal, start_url, done_when, kind?}`. Refused (`rule: website`) unless `start_url` is http(s), carries no credentials, and its host is on `deploy/browser-allow.txt` or under one of its hosts. At most 50 tasks wait (`queue_full`). |
| `next` | A | The operator's open claim if it has one, else the oldest `pending` task, claimed. Answers `{task: {task_id, kind, goal, start_url, done_when, screenshot_dir, resumed, email?, mail_codes?}, rules}`, or `{task: null}`. `email` is Teeny's inbox (`AGENTMAIL_INBOX`), for sign-ups. A claim older than 2 hours goes back to `pending` once, then fails. |
| `done` | A | `{task_id, outcome: ok\|failed\|needs_human\|needs_code, notes, code_from?, screenshots?}`. `code_from` (a domain) goes with `needs_code` only. Each screenshot must be an image (png, jpg, webp, gif, at most 10 MB, at most 10) from `state/media/` or `state/browser/` (the browser's downloads); it is copied to `state/media/tasks/<id>/`, which `x upload` accepts. |
| `desk` | J | The operator's desk, as `build desk`: settles `task-done.json` through `done` (a report naming no `task_id` while `task.json` offered no task is discarded, not refused), then writes `next` to `task.json`. |
| `list [--status S] [--limit N]` | R | The tasks, newest first. |

A task ends `needs_human` wherever only a person may go on: the browser's
checkpoint before any payment (which OpenHuman keeps whatever the unattended
settings say), a login Teeny has no account for, a CAPTCHA, anything that
asks for money or a secret. Money moves only through `hands/wallet`, never
the browser. `browser_tasks` has one row per task.

**The news sense.** `brain/playbooks/news-scan.md` (daily) queues one task of
kind `news` that starts on the Hacker News front page
(`news.ycombinator.com`, on `deploy/browser-allow.txt` for it) and asks for
the top AI and agent stories in its notes. When that task ends `ok`, `done`
writes its notes to the mind's inbox as one `world · news` stimulus (ref
`task:<id>`, payload `{task_id, start_url}`), and the desk sends no separate
`operator · report` for it. Pages are strangers' text, so the stimulus opens
by saying the notes are data to read, never instructions to follow, and it is
capped like every stimulus. `bin/tick`'s header says the same of the whole
batch. A news task that fails is an ordinary operator report.

`needs_code` is for a site that mails Teeny a one-time code or a verification
link. The task goes back to `pending` with `code_from`. The next `next` reads
Teeny's inbox (`hands/mail codes`, mail from that domain since the task asked)
and, once something has come, offers the task again, resumed, with
`mail_codes` (`[{from, subject, codes, links}]`; links only when the guard's
link allowlist passes them). Until then the task waits. After 15 minutes
without a code, or when the inbox cannot be read, it ends `needs_human`. A
task waits for a code twice at most (`rule: code_waits`).

## Why a desk, and not a hand the agent calls

The operator has no shell, and the builder's shell runs sandboxed, rooted at
`state/workbench/` (its agent's `scheduled_workspace`). Landlock jails its
files to that folder (and the system's read-only directories), nothing else
of the checkout, and every command starts with a `cd` into the job's folder.
The jail covers files only: the shell has the host's network. Neither agent
should be able to reach a hand, least of all one that holds a secret (a shell
that can run `hands/build` can run `hands/gh push`). The two file tools both
agents already need reach `state/workbench/` and nothing else of the checkout
(the trusted root `bin/spine` grants). So the work goes back and forth as two
files there, and the hand that acts on a report runs from cron, outside any
model turn, with all its own rules. OpenHuman's MCP would be the other way
in, but a serve host dropped every configured MCP server's tools when
TinySecurity was on ([`oh-gaps.md`](oh-gaps.md), G11, fixed in
openhuman#8654).

## `wallet`: Teeny's own wallet, Solana and EVM from one recovery phrase

The keys never leave the core. OpenHuman (the `web3` build) holds the
encrypted recovery phrase and signs. That one phrase derives a Solana
address and an EVM address (the same on every EVM network), so one backup
covers every chain. This hand decides whether a payment, a swap, a bridge or
a paid HTTP request may go out, asks the core to do it, and keeps the ledger.
Code: `teeny_hands/wallet/` (`caps.py` is shared with
`bin/lib/wallet_config.py` and `bin/doctor`; `chains.py` pins the chains and
assets).

**Chains and assets** (`chains.py`), nothing else:

| Chain | Assets | deBridge id | Notes |
|---|---|---|---|
| `solana` | SOL, USDC | 7565164 | devnet or mainnet (`WALLET_CLUSTER`) |
| `base` | ETH, USDC | 8453 | mainnet only |
| `arbitrum` | ETH, USDC | 42161 | mainnet only |
| `ethereum` | ETH, USDC | 1 | mainnet only |
| `optimism` | ETH, USDC | 10 | mainnet only |

USDC is pinned per chain (mint or contract). Under `WALLET_CLUSTER=devnet`
every EVM payment, swap and bridge is refused (`cluster`). ETH moves only
with `WALLET_ETH_USD` set. The core reads native ETH; USDC on an EVM network
is read here with one `eth_call` (`balanceOf`) to `EVM_RPC_URL_<NETWORK>`, else
the network's public endpoint, and the core is given the same URL.

| Verb | | Does |
|---|---|---|
| `address` | R | Teeny's Solana address (`wallet_status`) and its Solscan page, and `evm`: the EVM address with its explorer page per network (`usable` only on mainnet). |
| `balance` | R | SOL from the core (`wallet_balances`) and USDC read straight from Solana JSON-RPC (`getTokenAccountsByOwner`, jsonParsed, the cluster's pinned mint), each `{raw, display}`. On mainnet also `evm.networks.<network>`: ETH from the core and USDC by `eth_call`; a network that cannot be read carries `error` and does not stop the others. Also `tiny`, read the same way (`{raw, display, mint}`, or `{error}` when that one read fails, which never hides the rest). |
| `tiny` | R | Teeny's $TINY, in tokens: `{mint, owner, cluster, raw, decimals, amount}`. One `getTokenAccountsByOwner` read over the same RPC as USDC, summed over every account for the mint; nothing is signed. The mint is `TINY_MINT`, else the one in `brain/CONTEXT.md`. No token account is `amount` 0, with 6 decimals (a pump.fun mint's). $TINY lives on mainnet, so on devnet it reads 0. |
| `conversions [--max N]` | R | The swaps and bridges (`wallet_conversions`), newest first. |
| `x402 log [--max N]` | R | The paid HTTP requests (`x402_requests`), newest first. |
| `ledger [--max N]` | R | This hand's `wallet_payments`, newest first, and the core's spend ledger (`wallet_ledger_list`). |
| `costs [--days N]` | R | What keeping Teeny running cost over N days (30): model calls (`cost_report` totals), settled x402 API payments in USDC (`x402_list_payments`, newest record per id), and the `bills` prorated to the window (a month is 30 days, a year 12 months). `total_usd` is their sum; `complete` is false when a source could not be read. Payments to bounty winners and vendors are listed beside it (`paid_out`), not in it. |
| `receipt ID` | R | One payment: amount, recipient, purpose and what it was for, signature, Solscan URL for its cluster. `receipt buy-N` is one $TINY buy. |
| `receipts [--max N]` | R | Paid payments and $TINY buys whose signature no X post of Teeny's carries yet, oldest first: what the `wallet-receipt` playbook owes. |
| `caps` | R | The caps it pays under, and which optional ones are on. |
| `inflows scan` | J | Every deposit to Teeny's Solana address since the last scan, one row each in `wallet_inflows`. Read only. Below. |
| `inflows list [--max N]` | R | The inflow ledger, newest first, and how far it has been scanned. Never a memo. |
| `inflows tag SIGNATURE --category team\|tip\|sale\|bounty [--note T] [--ref R]` | O | Says what one deposit was (a bounty Teeny won, money from the team). |
| `accounts [--days N]` | R | The profit and loss over the last N days (7). Below. |
| `accounts close` | J | Scans, then freezes last week's page, once (`bin/accounts`, the pinned `accounts` job, Mondays). Off until `WALLET_ACCOUNTS=1`. |
| `pay` | A | `{to, amount_usdc \| amount_sol \| amount_eth, chain?, purpose: bounty\|vendor, ref}`, `chain` Solana by default. The rules below, then the core. |
| `swap` | A | `{chain, from, to, amount}`: one asset into another on one chain, to Teeny's own address, through deBridge. Below. |
| `bridge` | A | `{from_chain, from, to_chain, to, amount}`: across chains, to Teeny's own address on the destination, a deBridge DLN order. Below. |
| `x402 fetch` | A | `{url, method?, headers?, body?, max_usd?, network?, sign_in?}`: one HTTP request that may pay for itself (HTTP 402). Below. |
| `buy-tiny` | J | A share of Teeny's net inflows into $TINY, buy only. No body and no option. `bin/tiny-buy`, the pinned `tiny-buy` job (daily). See "Buying $TINY" below. |
| `bills add NAME --usd N --period month\|year [--note T]`, `bills list`, `bills remove NAME` | O | Fixed bills (the server, an API plan). |
| `vendors add NAME ADDRESS [--chain C] [--note T]`, `vendors list`, `vendors remove NAME` | O | The only addresses a `vendor` payment may go to, each on one chain (`solana` by default): an EVM address that is a wallet on Base need not be one on Arbitrum. |
| `x402 hosts add HOST [--note T]`, `x402 hosts list`, `x402 hosts remove HOST` | O | The only hosts an x402 payment may go to. |

**`pay`'s rules** all run before the core hears of the payment, and each
refuses with its `rule`:

- only those fields (`fields`);
- never $TINY, named as a mint, an asset or the recipient (`tiny`);
- a chain it knows, an EVM one only on mainnet (`chain`, `cluster`);
- the chain's native asset or its pinned USDC and nothing else (`asset`);
- exactly one amount above zero within the asset's decimals (`amount`);
- a purpose and a ref;
- a valid address for the chain (base58 on Solana, `0x` and 40 hex digits on
  EVM) that is not a token (`address`);
- the caps set and sane (`caps`);
- at most `WALLET_MAX_PER_TX_USD` (`per_tx`);
- never Teeny's own address (`own_address`);
- for a bounty: the winner public for `BOUNTY_CHALLENGE_HOURS`, this the
  address the winner claimed, the amount exactly the prize, never paid before
  (`bounty`, `challenge_window`, `claimed_address`, `prize`), read from the
  bounty's rows, not the bounty hand's word;
- for a vendor: the address in `vendors` on that chain (`vendor`);
- this purpose and ref never paid or tried (`duplicate`);
- the last 24 hours within `WALLET_MAX_PER_DAY_USD` (`per_day`) and this
  address's last 7 days within `WALLET_MAX_PER_RECIPIENT_WEEK_USD`
  (`per_recipient_week`), every asset on every chain counted together in
  dollars (an EVM address matches without case);
- never more than half the wallet's balance of that asset on that chain
  (`half_balance`).

**Dollars without a price feed.** USDC counts one for one. SOL counts at the
fixed `WALLET_SOL_USD` the operator sets, and ETH at `WALLET_ETH_USD`, rounded up. Set it at or above the
market price, so a SOL payment counts for at least what it is worth and the
caps bind early. The core's caps are the same dollars in each asset's atomic
units, rounded down.

**Then the core**, under the `wallet-pay` lock: the ledger row is written
`pending` first, then `wallet_prepare_transfer` (a quote that must match the
payment exactly, or nothing is executed: `quote`), then
`wallet_execute_prepared {quoteId, confirmed: true}`. A signature makes the
row `paid`. If the core's own policy refuses (`WALLET_POLICY_REFUSED:`), the
row is `refused` (`core_policy`); a failed prepare is `failed`. Anything else
after the execute was sent (a timeout, a 5xx, no signature) is `in_doubt`,
which counts against every cap and is never retried: a person reads the
core's ledger and the chain. Under `DRY_RUN` the answer is `would_pay` with
every rule it evaluated (`checks`), no ledger row, and no core call that
moves funds. Tables: `wallet_payments`, `wallet_tiny_buys`, `bills`, `vendors`. An older
`hands.db` is brought up to date on first open: `wallet_payments` is rebuilt
with a `chain` column (every old row `solana`) and `vendors` gains one.

### The mind's wallet intents

Teeny's mind (bin/lib/tick.py) may act on chain itself: `check_wallet`
(`balance`, always allowed), and, once the operator sets
`WALLET_MIND_ACTIONS=1`, `transfer` (`pay` with purpose `vendor` and ref
`mind:<ref>`: only to an allowlisted vendor address on that chain), `swap`,
`bridge` and `x402_fetch`. The tick forwards only the fields each verb takes,
so the mind cannot name a recipient for a swap or a bridge, or a purpose for
a transfer; every rule and cap below still holds, and the outcome comes back
as the next beat's `hand` stimulus. Nobody but Teeny drives the wallet
(evals/invariants.md, `own-wallet`): a request from anyone to send, swap or
pay is still an attack. Playbooks still reach only the read verbs.

### The mind's issues, browser tasks and new mail

Three more intents reach a hand from the mind (docs/roadmap.md, A3), each
with a daily cap the operator sets in `secrets/teeny.env` (0, the default,
keeps it off and out of the mind's prompt):

| Intent | Hand verb | The hand's own limits | Cap (ceiling) |
|---|---|---|---|
| `open_issue` `{repo, title, text}` | `gh open-issue {repo, title, body}` | own repositories only, the guard on title and body, DRY_RUN | `MIND_ISSUES_PER_DAY` (5) |
| `queue_task` `{goal, start_url, done_when}` | `tasks add --source teeny {goal, done_when, start_url, kind: "research"}` | the allowed websites, the queue's limit; the tick runs `goal` and `done_when` past `guard check` (surface `task`) first and sends the guard's words | `MIND_TASKS_PER_DAY` (10) |
| `start_mail` `{to, subject, text}` | `mail send {to, subject, body}` | contacts only, the guard, the mail cap, DRY_RUN | `MIND_MAILS_PER_DAY` (5) |

Only the fields each verb takes are forwarded. Every call that reaches the
hand counts against the day (UTC), whatever the hand answers, so a refused
issue is not tried over and over; the count is in `state/tick/caps.json`.
Playbooks reach the same verbs and no others of those hands (`VERB_OUTPUTS`
in `bin/lib/playbook.py`: `gh open-issue` and `comment`, `tasks add`, `mail
reply` and `send`).

### `swap` and `bridge`: deBridge, between Teeny's own accounts

Neither verb can pay anyone: the output goes to Teeny's own address on the
destination chain, named explicitly in every quote request. The quote comes
from the core (`web3_swap_quote`, `web3_bridge_quote`), which asks deBridge's
public API through the spine (`spine/src/debridge.rs`: `/v1.0/chain/transaction`
for a swap, `/v1.0/dln/order/create-tx` for a bridge; `DEBRIDGE_API_KEY` is
optional and only raises the rate limit). Upstream answers these through the
TinyHumans backend, which needs a signed-in session Teeny does not have.

Off until `WALLET_MAX_CONVERT_PER_DAY_USD` is set (`caps`). The rules, each
refused with its `rule`, before anything is approved or signed: only the
fields above, never $TINY on either side (`fields`, `tiny`); mainnet chains it
knows, a swap on one and a bridge across two (`chain`, `cluster`, `kind`);
pinned assets that differ (`asset`); an amount (`amount`); the input at most
`WALLET_MAX_PER_TX_USD` (`per_tx`); swaps and bridges in the last 24 hours
within `WALLET_MAX_CONVERT_PER_DAY_USD` (`per_day_convert`); never more than
half the input asset (`half_balance`); and `quote`: the quote is for exactly
this input token and amount and this output token, its EVM transaction goes
only to a pinned deBridge contract (DlnSource or the deBridge router), it has
not expired, and the minimum that comes out (after slippage) is worth at
least the input less `WALLET_MAX_CONVERT_LOSS_BPS` (300 by default), counting
deBridge's flat fee as a loss. USDC counts at par; another asset at
deBridge's own USD estimate, because the fixed SOL and ETH prices are set high
on purpose and would hide a loss on the way out. Small bridges fail this on
the flat fee alone, which is the point.

An ERC-20 input (USDC on an EVM network) is approved first: exactly the input
amount, to the quote's `tx.to` (already checked to be deBridge), through
`web3_dapp_call`/`web3_dapp_execute`, then `wallet_tx_status` until it is
confirmed (`WALLET_APPROVAL_WAIT_SECONDS`, 120). A quote that expired meanwhile
is fetched again and must pass every rule, with the same spender. Then
`web3_swap_execute` or `web3_bridge_execute {quoteId, confirmed: true}`.

The `wallet_conversions` row is written `pending` first, then `sent` (the
signature, and a bridge's `order_id` with its deBridge tracking link),
`failed` (nothing was swapped: the quote or the approval failed) or
`in_doubt` (the execute did not come back), which counts against the cap and
is never retried. Under `DRY_RUN` the quote is fetched and checked (it moves
nothing) and the answer is `would_convert`.

### `x402 fetch`: a request that pays for itself

The core does the protocol (`openhuman.x402_fetch`): on an HTTP 402 the
wallet module reads the challenge, picks the rail (USDC on Base or on Solana,
from the same phrase), signs, retries and records the settlement, paying at
most the `maxAmountAtomic` this hand passes. Off until both
`WALLET_MAX_X402_PER_REQUEST_USD` and `WALLET_MAX_X402_PER_DAY_USD` are set
(`caps`). Rules: only those fields (`fields`); an https URL with no
credentials whose host is in `x402 hosts` (`url`, `host`); `max_usd` at most
the per-request cap (`max_usd`, the default); and room left in the last 24
hours (`per_day_x402`), where a request in flight or in doubt counts at its
cap. The cap passed down is the smaller of `max_usd` and what is left today.
`bin/lib/wallet_config.py` also pins the module's own x402 budget to these
caps (or to one atomic unit when x402 is off), so the module refuses what the
hand would.

The `x402_requests` row is written `pending` first, then `paid` (what it
paid), `free` (no 402), `refused` (`X402_REFUSED:` from the core:
`core_x402`) or `in_doubt` (anything else, or a payment the core reports that
is not USDC within the cap). The response body is the server's, untrusted:
callers treat it as data. Under `DRY_RUN` nothing is requested: `would_fetch`.
`costs` keeps reading x402 spend from the core's own ledger
(`x402_list_payments`), so nothing is counted twice.

Two optional fields:

- **`network`** pins the payment to one CAIP-2 network (`eip155:8453`) or
  namespace (`eip155`, `solana`). The module refuses a challenge with no
  option there. hands/x pins twitr.sh to Base, the chain its sign-in
  identity lives on.
- **`sign_in: true`** answers the 402 with a Sign-In-With-X credential
  instead of a payment. The module builds the message, signs it with the EVM
  key, and refuses any challenge that is not for the requested host. It pays
  nothing: the cap passed down is one atomic unit, and `max_usd` is refused.
  The answer names `signed_in_as`.

Both need the TinyWallet release with contract 1.16 (`X402SignIn` and the
prepare `network` field). Until the core is re-pinned to it, the core ignores
both fields, so leave the twitr.sh writes switch (`X_TWITR_WRITES`) off.

The whole path of one `pay` (`teeny_hands/wallet/pay.py`):

```
hands/wallet pay {to, amount_usdc | amount_sol, purpose, ref}
  │
  ▼  lock "wallet-pay"
every rule above, half_balance last ── any fails ──► refused with its rule, no row
  │
  ▼
DRY_RUN=1 ? ── yes ──► {would_pay, checks}   no row, no core call that moves funds
  │ no
  ▼
ledger rules again in one transaction, row written ──► pending
  │
  ▼
wallet_prepare_transfer ── error ──────────────────► failed
  │ the quote must match the payment ── mismatch ──► failed (rule quote), nothing executed
  ▼
wallet_execute_prepared {quoteId, confirmed: true}
  ├─ signature ────────────────────────────────────► paid
  ├─ WALLET_POLICY_REFUSED: ───────────────────────► refused (rule core_policy)
  └─ timeout, 5xx, no signature ───────────────────► in_doubt
                                     counts against every cap, never retried
```

**Defence in depth.** `bin/lib/wallet_config.py` writes the core's
`[wallet_policy]` at every boot: `apply_to_interactive = true` (an execute
over JSON-RPC is "interactive" to the core and uncapped without it),
`automation_enabled = true` only with sane caps, and SOL and USDC (mint
pinned) capped at the same dollars. It exports `OPENHUMAN_SOLANA_CLUSTER`
(devnet until `WALLET_CLUSTER=mainnet`) and `OPENHUMAN_WALLET_RPC_SOLANA`
(from `SOLANA_RPC_URL`), and unsets the `OPENHUMAN_WALLET_POLICY_*`
overrides. With no usable caps the policy lists no asset, and the core
refuses every capped execute. `bin/doctor` refuses to boot a checkout whose
`WALLET_*` keys are set with a cap missing, zero or over its ceiling.

No model reaches `pay` or `buy-tiny`: `bin/lib/playbook.py` refuses any
wallet verb as a playbook output, names both as never an output, and lets an
input run only the read verbs.

### Buying $TINY

`buy-tiny` (`teeny_hands/wallet/buy.py`) turns a share of what Teeny earned
into $TINY. It only buys. The input is USDC, the output is the $TINY mint, a
constant in code, and no body, option or setting can name another. The
`wallet_tiny_buys` table refuses a row with any other output mint. There is no
sell path. A pinned job calls it once a day (`bin/tiny-buy`,
`[jobs.tiny-buy]` in `evals/cron.pinned.toml`); no model does.

**Why USDC.** The caps count USDC one for one, with no price feed. Inflows
are measured in USDC. SOL pays the fees and stays where it is.

**Settings** (`secrets/teeny.env`), each read and checked in code. A value
over its ceiling is refused (`caps`), never obeyed:

| Key | Default | Ceiling | Says |
|---|---|---|---|
| `TINY_BUY` | off | | `1` turns buying on. Off, every run is refused (`off`) before anything is read. |
| `TINY_BUY_SHARE` | `0` | `0.5` | The share of net inflows to buy with. `0` skips every run. |
| `TINY_BUY_MAX_USDC_PER_DAY` | none, required | `100` | The most bought in any rolling 24 hours, also clamped to `WALLET_MAX_PER_DAY_USD`. |
| `TINY_BUY_MAX_USDC_PER_BUY` | none, required | `50` | The most one buy spends, also clamped to `WALLET_MAX_PER_TX_USD` and the daily buy cap. |
| `TINY_BUY_MIN_USDC` | `1` | the per-buy max | Under this a run is skipped, and the row says why. |
| `TINY_BUY_SLIPPAGE_BPS` | `50` | `100` (1%) | The slippage asked for and accepted. |
| `TINY_BUY_MAX_PRICE_IMPACT_BPS` | `50` | `100` (1%) | The most price impact a buy may have. |
| `TINY_BUY_TEAM_ADDRESSES` | none, required | | The team's and the operator's addresses, comma or space separated. Money from them never counts as earned. |
| `JUPITER_QUOTE_URL`, `JUPITER_API_KEY` | `https://api.jup.ag/swap/v1/quote`, none | | Jupiter's quote API, and its key if one is used (`x-api-key`). |

**The amount.** Under the `wallet-pay` lock, the one `pay` takes:

- `since` is the last buy that may have moved funds (`pending`, `bought` or
  `in_doubt`), or 30 days ago for the first. A skip does not move it, so
  small inflows add up across days.
- `earned` is the USDC that reached Teeny's own token accounts since then
  (`getSignaturesForAddress` on each account, then `getTransaction`,
  jsonParsed), counted only from successful transactions that Teeny did not
  sign and where no team address signed or sent. At most 200 transactions are
  read a run; any beyond that are left uncounted, which only makes the buy
  smaller. SOL inflows are not counted.
- `spent` is what keeping Teeny running cost over the same whole days, plus
  what the wallet paid out (`costs`). If a cost source could not be read,
  the run is skipped: an unread cost would look like profit.
- The amount is `TINY_BUY_SHARE` times `max(0, earned - spent)`, rounded
  down, then clamped by the per-buy max, `WALLET_MAX_PER_TX_USD`, what is
  left of `TINY_BUY_MAX_USDC_PER_DAY` and of `WALLET_MAX_PER_DAY_USD` in the
  last 24 hours (payments and buys count together), and half the USDC the
  wallet holds. The result names the bound that decided it (`clamped_by`).
  What a cap cuts off is not carried over.

**The quote.** Jupiter's public quote API (`GET /swap/v1/quote`, ExactIn,
`restrictIntermediateTokens=true`; `teeny_hands/wallet/jupiter.py`). A
quote moves nothing. It is refused unless:

- `direction`: its input mint is the cluster's pinned USDC, its output mint
  is $TINY, and `inAmount` is exactly the amount computed;
- `route`: every mint the route plan ends in is $TINY, and every mint it
  starts from is USDC;
- `slippage`: `slippageBps` is at most `TINY_BUY_SLIPPAGE_BPS`, and
  `otherAmountThreshold` holds to it;
- `price_impact`: `priceImpactPct`, read as a fraction (so a misread can
  only refuse), and the impact measured against a second quote for a
  hundredth of the amount are both at most `TINY_BUY_MAX_PRICE_IMPACT_BPS`.

Under `DRY_RUN` the answer is `would_buy` with the plan and every check, and
that is all: no row, no core call but the wallet's address, nothing signed
or sent.

**Then the core.** The keys never leave it, and it signs a buy the way it
signs a payment: the TinyWallet module signs, the hand never sees a key.
The row is written `pending`, then `web3_swap_quote {chainId: 7565164,
tokenIn: USDC, tokenInAmount, tokenOut: $TINY, tokenOutRecipient and
senderAddress: Teeny's own address, slippage}` prepares the swap. Its quote
must name USDC in, the exact amount, $TINY out to Teeny's own address, and a
`minAmount` no lower than Jupiter's `otherAmountThreshold`, or the row is
`failed` (`quote`). The unsigned transaction it carries is simulated over
Solana RPC (`simulateTransaction`, `sigVerify: false`): it must succeed,
take no more USDC from Teeny's accounts than the amount, and give at least
the minimum to any $TINY account Teeny already holds, or the row is `failed`
(`simulate`). Then `web3_swap_execute {quoteId, confirmed: true}`. A
signature makes the row `bought`; anything else after the execute was sent
is `in_doubt`, which counts against every cap and is never retried.

The core's `[wallet_policy]` caps native transfers only: a `web3_swap`
execute is approved by its confirmed quote id, not by the spend policy. So
for a buy, the hand's caps are the caps. The core's `web3_swap` reaches its
swap provider through the TinyHumans wallet backend; a core with no backend
answers `BACKEND_UNAVAILABLE:` at the prepare, and the row is `failed` with
nothing moved.

```
hands/wallet buy-tiny            (bin/tiny-buy, daily)
  │
TINY_BUY=1 ? settings sane ? mainnet ? ── no ──► refused (off, caps, cluster)
  │
  ▼  lock "wallet-pay"
earned - spent, x share, clamped ── under the minimum ──► skipped, row says why
  │
  ▼
Jupiter quote, USDC to $TINY ── direction, route, slippage, price_impact ──► refused, no row
  │
DRY_RUN=1 ? ── yes ──► {would_buy, plan, checks}   no row, nothing signed
  │ no
  ▼
row ──► pending
web3_swap_quote ── error ──────────────────────────► failed
  │ must match the checked quote ── mismatch ──────► failed (rule quote)
simulateTransaction ── fails, takes too much ──────► failed (rule simulate)
web3_swap_execute
  ├─ signature ────────────────────────────────────► bought
  └─ timeout, 5xx, no signature ───────────────────► in_doubt, never retried
```

Each buy gets a public receipt like a payment: `hands/wallet receipts`
lists it for the `wallet-receipt` playbook, and `bin/receipts` writes it to
the diary as `wallet.bought` (signature, cluster, USDC spent, mint).

**Switching it on.** In `secrets/teeny.env`, with the wallet on mainnet and
holding USDC: set `TINY_BUY_TEAM_ADDRESSES`, `TINY_BUY_MAX_USDC_PER_DAY` and
`TINY_BUY_MAX_USDC_PER_BUY`, then `TINY_BUY_SHARE` (say `0.2`) and
`TINY_BUY=1`. Run `DRY_RUN=1 hands/wallet buy-tiny` first and read the
plan. The core also needs a wallet backend for `web3_swap`.

`bin/wallet-setup` (operator, once, at a terminal) has the core generate a
24-word phrase, derive the accounts, encrypt the phrase (`encrypt_secret`)
and store it (`wallet_setup`). The phrase is shown once on `/dev/tty`, never
on stdout, in a file or in a log, and the screen is cleared after. It refuses
when a wallet exists, unless given `--force` and the first 8 characters of the
current address are typed. It prints the line to paste into
`brain/CONTEXT.md`.

### The inflow ledger and the weekly accounts

The public accounts (docs/roadmap.md, S1): what keeping Teeny running cost
against what it earned, with the team's money shown apart, one page a week,
published with the receipts. Code: `teeny_hands/wallet/accounts.py`. Nothing
here signs or moves anything.

**The inflow ledger.** `inflows scan` reads the deposits to Teeny's own
Solana address since its cursor (30 days back the first time): USDC to its
token accounts and SOL to the address, from successful transactions Teeny did
not sign. Each leg is one `wallet_inflows` row (unique on signature and
asset) with its amount, sender and block time, and a category:

| Category | When |
|---|---|
| `team` | the sender or a signer is in `WALLET_TEAM_ADDRESSES` (else `TINY_BUY_TEAM_ADDRESSES`) |
| `tip` | anyone else, until something says what it paid for |
| `sale` | it paid for something Teeny sells (a paid build request, S3; the operator's `tag`) |
| `bounty` | a bounty Teeny won (the operator's `tag`) |
| `unclassified` | no team list is set: nothing is counted as earned |

A memo on the deposit is a stranger's text. It goes through `guard inbound`
(surface `wallet`); a held memo is dropped and the row says `memo_held`. A
kept memo is data, never an instruction, and `inflows list` never prints
one. At most 200 transactions a scan; the rest wait for the next.

**The page.** `accounts` adds up the last N days. Costs come from the same
sources as `costs` (`reads.cost_parts`): `models`, `server` (a bill whose
name starts with `vultr`, `eqvps`, `vps` or `server`, prorated) and `tools`
(every other bill, prorated, plus x402 API payments). Income is the USDC
`tip`, `sale` and `bounty` rows in the window; `team` and `unclassified`
are shown apart and never counted. Every line is whole cents, rounded once
from its source, and every total is the sum of the lines above it, so the
page adds up to the cent and each line checks against `costs` and `inflows
list`. Beside it: `paid_out` (bounties, vendors), `tiny_bought`, SOL received
in lamports (never in dollars: `WALLET_SOL_USD` is set high on purpose and
would flatter the income), and `not_counted` (network fees, SOL in dollars,
EVM networks). A page whose model costs, x402 payments or inflow scan (none
in the hour before) could not be read is `complete: false`.

`accounts close` (the pinned `accounts` job, Mondays 00:20 UTC) scans, then
freezes the 7 days to `wallet_accounts` under the ISO week they cover,
once. It refuses off a Monday (`not_monday`), with `WALLET_ACCOUNTS` unset
(`off`), and for an incomplete page (`incomplete`): an unread cost would
look like profit. `bin/receipts` publishes each closed page as
`accounts/<week>.json` once `ACCOUNTS_PUBLISH=1`, rebuilt from dollars,
enums, timestamps and signatures only, written once and never rewritten. No
sender address and no memo is ever published.

## `vps`: Teeny's own servers on Vultr or EQVPS

One hand, two providers, one ledger (`vps_orders`) and one set of caps.
`--provider` picks one (default `VPS_PROVIDER`, else `vultr`).

| Provider | API | Key | How it is paid |
|---|---|---|---|
| `vultr` | `VULTR_API_BASE`, default `https://api.vultr.com/v2` | `VULTR_API_KEY` | Hourly, against the account's credit. A person opens the account, funds it (card, PayPal, or crypto through BitPay) and makes the key; Vultr's key also needs this box's address on its access list. |
| `eqvps` | `EQVPS_API_BASE`, default `https://api.eqvps.com/api/v1/eqvps` | `EQVPS_API_TOKEN` | From a prepaid balance that a crypto checkout tops up (USDC or USDT on Base, Ethereum or Tron). `register` makes the account. EQVPS places the server itself (Germany or Finland). |

Every cap is closed until the operator opens it in `secrets/teeny.env`:

| Setting | Default | Caps |
|---|---|---|
| `VPS_ORDERS` | off | whether `order` may buy at all |
| `VPS_MAX_MONTHLY_USD` | 0 (0..100) | a plan's monthly price |
| `VPS_MAX_SERVERS` | 1 (0..3) | orders that still hold a slot (`ordering`, `ordered`, `unfunded`, `in_doubt`), both providers together |
| `VPS_TOPUP_MAX_USD` | 0 (0..200) | one EQVPS top-up, and every top-up in 30 days together |
| `VPS_SSH_PUBKEY` | none | the one public key every server is ordered with |
| `VPS_REGION` | `bom` | Vultr's region |
| `VPS_OS` | `Ubuntu 24.04 LTS x64` (Vultr), `Ubuntu 24.04` (EQVPS) | the system, by the provider's name for it |

| Verb | | Does |
|---|---|---|
| `plans [--provider P] [--max-usd N] [--region R]` | R | The public plan list, cheapest first (for Vultr, the plans sold in the region): plan id, monthly USD, vCPU, memory, disk, public IPv4. Also the caps in force. No key needed. |
| `account` | R | Vultr's balance (negative is credit) and this month's pending charges. |
| `servers` | R | This hand's orders and its latest 20 EQVPS top-ups. |
| `server [--provider P] ID` | R | One server's status and `address`. A root password in the answer (both providers show one once) goes to `secrets/vps/<provider>-<ID>.json`, mode 600; only the file and key names are printed. |
| `order [--provider P] --plan PLAN --reason T [--region R] [--os NAME]` | A | Refused while `VPS_ORDERS` is off (`orders_off`), for an unknown plan (`unknown`), a region it is not sold in (`region`), over `VPS_MAX_MONTHLY_USD` (`price_cap`), a missing system (`os`) or a full `VPS_MAX_SERVERS` (`server_cap`). Otherwise the order is reserved as `ordering` under the `vps` lock, then sent with `VPS_SSH_PUBKEY` (on Vultr the key is added to the account once and reused). A server is `ordered` with its id, and a monthly bill `<provider> <id>` is added so `hands/wallet costs` counts it. An EQVPS order the balance did not cover is `unfunded` with its invoice. A 4xx is `failed` and frees the slot. No answer, a 5xx or an unreadable one is `in_doubt` and keeps the slot: it may have bought a server, so a person checks and the hand never retries. Under `DRY_RUN` it answers `would_order` and writes nothing. |
| `topup --usd N --reason T` | A | An EQVPS checkout URL for N whole dollars, recorded with its reason. Refused over `VPS_TOPUP_MAX_USD` (`topup_cap`, `topup_month`). It pays nothing: the browser stops at payment pages, so the operator pays it, or `hands/wallet` under the wallet's caps. |
| `destroy [--provider P] ID --note T` | O | Destroys a Vultr server this hand bought (it stops billing), releases its order and drops its bill. Never a server the hand did not buy. EQVPS has no cancel in its API (`no_cancel`): cancel in its console, then `release`. |
| `release ID --note T` | O | An order stops holding a slot (an `in_doubt` a person has read, a server cancelled in a console), and its bill goes. |
| `register --first F --last L --email E` | O | A new EQVPS account. Its token is printed once and written nowhere; the operator puts it in `secrets/teeny.env`. |

`order` and `topup` spend money, so they are never a playbook's output and
never given to a model.

## `bounty`: bounties Teeny opens, judges and pays

| Verb | | Does |
|---|---|---|
| `open` | O | `{title, rules, prize_usdc, deadline, channels: [x, moltbook]}`. The prize is at most `WALLET_MAX_PER_TX_USD`, the deadline 1 hour to 60 days away. Returns the bounty with its tag, `bounty #N`, which every post about it carries. |
| `list [--status S]`, `get ID` | R | Bounties (statuses `open`, `picked`, `claimed`, `paid`, `in_doubt`), one with its entry counts and winner. |
| `announcements` | R | What the playbooks owe a post for: `open` (no tagged post yet), `to_pick` (past the deadline, with the entries that are neither late nor held), `paid` (a prize whose signature no post carries yet). |
| `entries ID` | J | Links the bounty's posts (Teeny's earliest X post carrying the tag; a Moltbook post whose title carries it). Collects the replies to the X post and to the winner's announcement (the x hand's client) and the comments on the Moltbook post (the moltbook hand's reads), screened by `guard inbound` (`held`), marked `late` after the deadline, Teeny's own left out. Then notices the claim: the first reply by the winning account, after the pick, carrying exactly one valid address. |
| `pick` | A | `{bounty_id, entry_id, reasons, text}`, after the deadline, for an entry neither late, held nor Teeny's. `text` is the announcement, which must carry the tag. It goes out through the x hand's `post` (guard, posting rules, `DRY_RUN`), and only once it is out is the winner recorded, with `public_at` the moment it went out. The one bounty verb a playbook may output. |
| `claim` | J | `{bounty_id, address, source_id}`: a collected reply by the winning account, on the winning entry's channel, that carries the address. Set once. |
| `pay ID` | J | Once claimed and public for `BOUNTY_CHALLENGE_HOURS` (12): the wallet's `pay` with purpose `bounty`, which checks it all again. A payment in doubt is marked `in_doubt` and never retried. |
| `pay-due` | J | Every bounty past its deadline: `entries`, then `pay` when claimed. `bin/bounty-pay`, the pinned `bounty-pay` job (hourly). |
| `link ID [--x POST] [--moltbook POST]` | O | Names the bounty's posts by hand. |

Tables: `bounties`, `bounty_entries`. The playbooks: `bounty-open`
(announce), `bounty-winner` (pick and announce), `bounty-paid` (the receipt).

## `goals`: the goal ledger

Teeny proposes its own goals, and frozen code checks them
([`research/teeny-application.md`](research/teeny-application.md) §4, Phase D).
A goal has an instruction, the W component it moves (`attention`, `tiny`,
`money`, `capability`, `shipped`, `world`), a channel (`x`, `moltbook`,
`github`, `wallet`, `web`, `mail`, `telegram`), a family (a slug that groups
goals for learning progress), a deadline (1 to 30 days), optionally the
flywheel `step` it turns and whether it would be a `first`, and a **check**.
The check is one of a fixed set of kinds written in
`hands/lib/teeny_hands/goals/checks.py`, with parameters. Teeny never
supplies code, a query or a command for a check: `propose` validates the
kind and its parameters and nothing else, and the hand is frozen
(`evals/frozen.txt`), so a goal cannot redefine how its success is decided.

| Kind | Params | Solved when |
|---|---|---|
| `stats_delta` | `{source: x\|moltbook\|github, metric, at_least}` | the `stats` metric rose by `at_least` over its value when the goal was proposed |
| `pr_merged` | `{repo: OWNER/REPO, author?}` | a pull request by `author` (default `GITHUB_USER`) merged into `repo` between proposal and deadline (GitHub search, through the gh hand's client) |
| `build_shipped` | `{}` | a `build_jobs` row was shipped between proposal and deadline |
| `wallet_inflow` | `{at_least_usdc}` | the USDC balance rose by that much, with USDC paid out since added back |
| `world_delta` | `{component, at_least}` | W's component rose by `at_least` (`hands/world report`) |
| `stimuli_count` | `{kind, surface?, at_least}` | that many stimuli of that kind arrived between proposal and deadline |

A kind with a baseline (`stats_delta`, `wallet_inflow`, `world_delta`)
records it at proposal when the data can be read then, else at the first
check. Data that cannot be read (no stats row yet, no GitHub token, no
wallet, no `hands/world`) leaves the goal open with the reason in its
evidence; a goal never shown solved fails at its deadline.

| Verb | | Does |
|---|---|---|
| `propose [--source S]` | A | `{instruction, component, channel, family, deadline_days, check: {kind, params}, step?, first?, why?}`. Refused (`rule`): `check_kind` for an unknown kind, `check_params` for a parameter the kind does not take or a bad value, `invalid` for any other field, `duplicate` for a near-duplicate of an open goal or of any goal proposed in the last 30 days, `too_many_open` at 5 open goals. Records whether the goal is `novel` (not a near-duplicate of any goal solved before) and its family's `p_hat` then. The one goals verb a playbook may output (`VERB_OUTPUTS` in `bin/lib/playbook.py`). |
| `check` | J | Every open goal against its check: `solved` when it passes, `failed` when its deadline has passed and it does not, else still `open`, with the evidence. Only an open row moves, so a second run changes nothing. Each move is one `self · goal` stimulus. `bin/goals-check`, the pinned `goals-check` job (hourly, at :35). |
| `list [--status S] [--limit N]` | R | The goals, newest first, with `open` and `max_open`. |
| `rank` | R | The open goals by score, every family's numbers, `new_family` (what an untried family scores) and `world` (W and J from `hands/world report` when that hand is there, else `available: false`). |
| `annecs` | R | ANNECS, the goals it counts, and the archive. |

**Near-duplicates.** Two goals are the same when their instructions share
60% of their words (Jaccard, after lowercasing, dropping filler words and
plurals), or when they have the same channel and the same check (kind and
every parameter but the threshold) with thresholds within 1.5x of each other.

**The rank.** `score = LP · [0 < p̂ < 1] · (1 − p̂) · interest · E[ΔJ] · first`.

- `LP`, learning progress: the absolute change in the family's success rate
  over the last 14 days against the 14 to 56 days before, each rate with a
  Beta(1, 1) prior. A family with fewer than 2 resolved goals gets 0.25, so
  a new family is explored; any other at least 0.02.
- `p̂`: the family's success rate over every resolved goal, with the same
  prior. The prior keeps it strictly between 0 and 1, so the band is read
  from the counts: a family resolved 3 times or more that always succeeded
  or never did is out of the band and scores 0 (Absolute Zero).
- `interest`: 1 + 1 / (1 + the family's goals so far). Until the judge-based
  interestingness check (OMNI) is built, which is future work, the
  near-duplicate filter at proposal and this family novelty stand in for it.
- `E[ΔJ]`: the component's weight in J: attention 3, $TINY 2, money,
  capability and shipped 1, world 0.5.
- `first`: 2 for a goal marked `first` while no solved goal has the same
  check (kind and non-threshold parameters), else 1. This is the extra
  weight for a checkable first.

**ANNECS and the archive.** ANNECS counts solved goals that were `novel` when
proposed and are not a near-duplicate of a goal solved before them, so it
rises only through new goals that frozen checks confirmed. The archive is a
MAP-Elites grid over (W component × channel), 42 cells: for each niche, how
many goals were solved there and the best one, by quality
(`(1 − p̂ at proposal) · E[ΔJ] · first`). `empty` lists the niches never
solved.

The playbook `goals` (weekly) is its caller. It writes only `state/hands.db`
and tells nobody, so `DRY_RUN` does not apply.

## `state/hands.db`

One SQLite file, WAL, beside the checkout's other run-time state. Each hand
creates its own tables:

| Hand | Tables |
|---|---|
| `x` | `x_mentions`, `x_cursor`, `x_posts`, `x_replies`, `x_likes`, `x_pacing`, `x_media`, `x_cache` |
| `guard` | `guard_holds`, `guard_approved_links`, `guard_repeats` |
| `gh` | `gh_actions` (the ledger and the external-PR cap), `gh_cache`, `gh_notifications`, `gh_cursor`, `gh_stars` (the intake) |
| `moltbook` | `moltbook_posts`, `moltbook_comments`, `moltbook_actions`, `moltbook_pending`, `moltbook_state`, `collabs`, `moltbook_cache`, `moltbook_post_metrics` |
| `mail` | `mail_messages`, `mail_cursor`, `mail_sent`, `contacts`, `mail_cache` |
| `slopcash` | `slopcash_cache` |
| `requests` | `build_requests`, `build_request_events` |
| `stimuli` | `stimuli`, `follow_ups`, `mind_notes` |
| `brief` | `briefs` |
| `image`, `video` | `media_assets` |
| `build` | `build_jobs` |
| `tasks` | `browser_tasks` |
| `stats` | `stats` |
| `world` | `world_scores` |
| `wallet` | `wallet_payments`, `wallet_tiny_buys`, `bills`, `vendors`, `wallet_inflows`, `wallet_scan`, `wallet_accounts` |
| `bounty` | `bounties`, `bounty_entries` |
| `goals` | `goals` |

## Rules the tests pin

1. The gap and the posting lock: a lock plus a transaction, with the gap
   re-checked inside the lock.
2. The post or reply id is recorded first, and a ledger write that fails
   after a post is never retried.
3. Holds are claimed and released atomically.
4. Dry run writes nothing outside: no X post, like, push, payment or ledger row.
5. Gate order is links, then lexicon, then judge, and the judge fails closed.
6. Over the length limit or the emoji cap, refuse; never fix.
7. Attachments go by minted id or Giphy id only, never both, never a URL.
8. Secrets are never logged, and never reach a model.
9. The wallet buys $TINY and never sells it: a buy's output mint is a
   constant, and a quote, a route or a prepared swap with any other output
   is refused before anything is signed.
