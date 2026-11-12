# Hands

The only code Teeny owns. See [`architecture.md`](architecture.md) §3 for the
contract: one JSON object out, exit `0`/`2`/other, secrets read by the hand,
state in `state/hands.db`, `DRY_RUN=1` by default.

The hands are `x`, `guard`, `gh`, `moltbook`, `slopcash`, `mail`, `stats`,
`requests`, `stimuli`, `brief`, `image`, `video`, `build`, `tasks`, `wallet`
and `bounty`. Each is an executable in `hands/`, with its code in
`hands/lib/teeny_hands/` and its tests in `hands/tests/`.

**Language.** The hands are Python 3.11 standard library, with no
dependencies. OAuth 1.0a signing, transactional SQLite and webhook
signatures are not jobs for bash. Each
executable is a three-line shim that runs `python3 -I`, so no `PYTHONPATH` or
planted module can change what it loads. The shared contract (outcomes, exit
codes, secrets, dry run, state, the core client) is
`hands/lib/teeny_hands/hand.py`. Run every test with
`python3 -m unittest discover -s hands/tests -t hands/tests`.

**R** marks a read verb. **A** marks an acting verb, which only `bin/`
scripts (`bin/act` for a playbook, `bin/tick` for the mind's intents) and the
operator (the person) call. **O** marks an operator-only verb. **J** marks a job
verb: it says nothing to anyone but writes state and calls the core, so like
an acting verb it is never given to a model. The scheduled agents (the
builder and the browser operator) call no hand at all: a `desk` job verb
lays their work out as a file under `state/workbench/desk/` and collects the
report they leave beside it.

## `x`: the only holder of the X keys

| Verb | | Does |
|---|---|---|
| `ingest [--max N] [--no-offer]` | J | Read the mentions timeline since the `x_cursor` cursor into `x_mentions`, unique on post id. The account's own posts and posts already answered are `skipped`; `guard inbound` screens the rest and a hold is `held`. Every `pending` row (the webhook's included) is offered to the mind's inbox (`stimuli`), or with `TEENY_INBOX=core` to the core's inbound queue (`openhuman.inbound_offer`, source `x:mentions`), and marked `offered`; a failed offer stays `pending` for the next run. The cursor moves once the page is stored. |
| `mentions [--status S]` | J | The queue, newest first. |
| `mark ID --status S [--note T]` | J | What became of a mention after its draft: `drafted` (under `DRY_RUN`), `replied`, `refused` or `skipped`. |
| `requests [--max N]` | J | Reads the newest N (200) stored mentions, never X again, for asks to build something ("build me", "can you make", "make a tool that", "i wish there was a tool", ...) and records each in `build_requests` (source `x:mentions`, once per post id). A held mention, the account's own post or one skipped as answered is never a request. `bin/mentions` runs it after every ingest. |
| `get`, `thread`, `user`, `search`, `whoami` | R | X reads through the read cache (posts 30 days; searches and threads 10 minutes). Nothing scheduled calls `search`: X bills it per post and it is too expensive. |
| `reply`, `post` | A | Run `guard check` (house style, the 280 limit in UTF-16, the emoji cap, links, lexicon, judge). Then the posting rules: for an own post, the gap (`POST_MIN_GAP_HOURS`, 3 hours by default, 20-minute tolerance) re-checked under the posting lock; for a reply, never twice to one post, never to the account's own post, and 30–60s pacing kept in `x_pacing` and refused with `wait_seconds` rather than slept through. Then `DRY_RUN`, then X. **The post id is recorded first** (`x_replies`, `x_posts`). A ledger write that fails after a post is logged, never retried. Under `DRY_RUN`: no X write, no like, no ledger row. |
| `upload` | A | Media upload of a file under `state/media/` or `brain/assets/`. Returns a media id that this hand minted, recorded in `hands.db`. `post` attaches only ids from that table or a Giphy id, never a URL, and never both. |
| `like` | A | Like a mention (skipped under `DRY_RUN`). A like that went out is recorded once in `x_likes`, which bin/receipts reads. |
| `unsay` | A | Delete an own post. Previews unless `--confirm`. |

### The webhook

X also pushes mentions, replies and quotes to the account's webhook,
`https://teeny.enamakel.com/api/webhooks/x` (registered with X, subscribed
through the X Activity API to `post.mention.create`, `post.reply.create` and
`post.quote.create` for the account). `bin/x-webhook`, the `xhook` service in
`deploy/compose.yaml` on `127.0.0.1:3110`, answers X's CRC challenge, refuses
any delivery whose `x-twitter-webhooks-signature` is not the HMAC-SHA256 of the
body under `X_API_SECRET`, and queues each post about the account by somebody
else into `x_mentions` exactly as `ingest` does (screened, unique on post id),
for the next `ingest` to offer. This is how mentions arrive. Nothing is searched or billed per post, and it
catches replies X's mentions timeline leaves out (2026-11-11: four of nine
replies to the gm never came back from `/mentions`).

## `guard`: the content filter

| Verb | | Does |
|---|---|---|
| `check` | R | Run, in order: links (allowlist plus approved hosts), lexicon (8 categories, leetspeak folding, word boundaries; profanity outbound only), then the judge. The judge fails closed. Returns `ok` with the text to publish, or `hold{reason}` and writes the hold. `surface` picks the limits (below). |
| `check --fields title,body` | R | The same for a draft of several fields, as GitHub, Moltbook and mail take them: stdin carries each named field plus `surface`. Every field gets its surface's hard limits (refused, never cut), the house style, links, lexicon and judge; the answer is `{"fields": {...}}`, each field rewritten. Any field that fails fails the draft and names its `field`, and the draft writes at most one hold (its fields as JSON). Hands call it in-process as `teeny_hands.guard.gate.check_fields(fields, *, surface, kind="post", max_emoji=None, context=None)`, which returns a `FieldsVerdict(kind, fields, reason, category, hold_id, field)`. |
| `inbound` | R | Screens third-party text before a model sees it. Every intake runs it: `x ingest` and the webhook, `mail ingest`, `moltbook inbox`, `bounty entries`. |
| `style` | R | House style: strip leading @handles, replace dashes, straighten quotes, drop NFA/DYOR. |
| `holds list/approve/reject/release` | A | Operator review. `release ID [--note T]` publishes a withheld draft: it atomically moves `pending` to `releasing`, publishes through the hand that owns the hold's surface (today `x`: a post, or a reply to the post in its context, with the posting rules and `DRY_RUN` but without asking the guard again, since the operator's approval is the decision it deferred), marks it `approved` with where it went, and on failure goes back to `pending` with the error recorded. Under `DRY_RUN` it claims nothing and says what it would publish. A surface no hand releases yet (`github`, `moltbook`, `mail`) is refused before anything is claimed. |

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
`xxx` would read as `x+` and hold "x.com" and "on X". `xxx`, `XXXX` and `kkk` are still held. This is pinned in
`hands/tests/guard/test_screen.py`.

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
  the guard's own inbound path (the lexicon, then the judge, the stored
  text). Only a fresh `clean` releases it: the `x_mentions` or
  `mail_messages` row held by that hold goes back to `pending` for the next
  ingest to offer, a comment goes into the `stimuli` inbox, and the hold is
  `cleared`. A real verdict is written on the hold (its category and
  reason), which keeps holding. Another failure leaves it for the next run.
- A draft of Teeny's own is `expired`, never sent late; Teeny drafts afresh.

A hold from a real verdict (the lexicon, the links, the judge naming a
reason) or one an operator has decided is never touched, and re-screening
never releases a verdict. With `SAFETY_JUDGE=0` it asks nothing. Each hold
records `rescreened_at` and `rescreen` (`cleared`, `prohibited`,
`unreachable`, `unreadable`, `expired`, `release_failed`); `bin/health`
prints the counts as `rescreen`.

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
| `contributing OWNER/REPO` | R | Reads `AI_POLICY.md`, `CONTRIBUTING.md` and `CODE_OF_CONDUCT.md` (root, `.github/`, `docs/`) and says whether the project forbids AI-, LLM- or bot-made contributions: `{forbids_ai, verdict: forbids\|allows\|silent, files, evidence}`. A keyword-and-phrase heuristic, conservative: a ban forbids, a clear welcome allows, a mention it cannot read forbids, silence is not a ban (a day). |
| `activity [--days N]` | R | Teeny's own pull requests opened and merged and its commits over the last N days (default 7), for the ship log (10 minutes). |
| `create-repo` | A | `{name, description?, private?}`. Under `GITHUB_USER` only, and only when the token is that account's. The description is screened. |
| `push` | A | `{workdir, repo, branch, message}`. Commits everything git would add in a folder under `state/workbench/` (symlinks resolved) as Teeny (`GIT_NAME` / `GIT_EMAIL`, default `teeny@agentmail.to`; see "git") and pushes `HEAD` to `branch` on an own repo. Never forces, ignores the box's git config, runs no hooks. Refused with `findings` (`{path, rule}`, never the secret) when the files or the history hold a secret path (`.env*`, `secrets/`, `state/`, `*.pem`, `id_*`, `*.key`) or a token (`ghp_`, `github_pat_`, `sk-`, `xox`, `AKIA`, a private key block). The token reaches git only through the environment of that one command and an askpass helper that holds no secret; it is never in the URL, a config file or a log. |
| `open-pr` | A | `{repo, head, base, title, body}`. On somebody else's repo, `head` is Teeny's fork branch, the disclosure footer is appended (`—` / "opened by Teeny, an AI agent (github.com/teenydroid), operated by @senamakel. close it if it's not useful."), and it is refused (`rule`) when the day's `GH_MAX_EXTERNAL_PRS_PER_DAY` (2, UTC) are spent (`daily_cap`), when Teeny already has an open PR there (`open_pr`), or when `contributing` forbids AI (`contributing`). |
| `comment` | A | `{repo, number, body}`. |
| `fork` | A | `{repo}`, somebody else's. |
| `open-issue` | A | `{repo, title, body}`, on an own repo only (`owner`). |
| `close-issue` | A | `{repo, number, comment?}`, on an own repo only: the comment first, then the issue closed as completed. |

Every acting verb under `DRY_RUN` answers `would_*` after every rule ran,
with no write anywhere: GitHub, git or the ledger. Done actions are recorded
in `gh_actions` (which also counts the daily cap); reads in `gh_cache`.

## `git`: how every git command is set up

The container's uid has no passwd entry and HOME is /tmp, so a bare `git
commit` there cannot tell who it is. Every git run in the hands (hands/gh's
`push`, bin/receipts) goes through `teeny_hands/git.py`, which:

- commits as **Teeny <teeny@agentmail.to>**, or `GIT_NAME` / `GIT_EMAIL` from
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

Holds no token.

## `moltbook`: the only holder of the Moltbook key

Moltbook is a Reddit-like network for AI agents (API from its `skill.md`,
v1.12.0). The base is always `https://www.moltbook.com/api/v1`: the bare
domain redirects and drops the key, so a base naming it is refused, and the
transport (`teeny_hands/rest.py`, shared with `mail`) never follows a
redirect. Every word published goes through `check_fields` (surface
`moltbook`: title 300, body 10000) and what goes out is the guard's rewrite;
a link post's `url` must be on the link allowlist.

| Verb | | Does |
|---|---|---|
| `whoami`, `status`, `home [--expand]` | R | `/agents/me` (10 min), the claim status, the dashboard (1 min); `--expand` attaches, to each mention of Teeny in someone else's post, that post and its latest comments (at most 5 posts, through the `post` and `comments` caches). |
| `feed [--submolt S] [--sort hot\|new\|top\|rising] [--max N]` | R | `/posts`, or `/submolts/S/feed` (5 min). |
| `post ID`, `comments ID [--sort best\|new\|old]` | R | One post (5 min), its comment tree (2 min). |
| `search QUERY [--type posts\|comments\|all]`, `profile NAME`, `submolts`, `submolt NAME` | R | Semantic search (10 min), an agent, every submolt, one submolt with `allow_crypto` (1 h). |
| `inbox [--days N] [--max N]` | R | What was said to Teeny, into its mind's inbox (`stimuli`): every comment on its own posts from the last N days (7), and replies under its own comments elsewhere, newest N posts (10) of each. Each one is screened by the inbound guard, and a held one is not added. Run by `bin/tick` at most every 10 minutes. |
| `metrics refresh` | R | Measures Teeny's own posts and comments (the ledger's, last 14 days) about 1h, 24h and 72h after they went out: score, comment count (a comment's: its replies), Moltbook's `verification_status`, one `moltbook_post_metrics` row per checkpoint, never fetched again once recorded. A post through `post`, a comment found by id in its post's latest 100 `comments`, both through their caches. A late run records only the latest checkpoint due (a write first measured at 30h has a 24h row, no 1h row; `at` says when it was read). At most 20 writes a run, newest first (`left` counts the rest); a 429 ends the run. Records under `DRY_RUN` too, as `inbox` does: it only reads. Run by `bin/stats-snapshot`. |
| `metrics list [--days N]` | R | The latest row of each write measured in the last N days (7), newest write first: a post with its title, a comment with its post's id. |
| `study` | R | What wins this week, deterministic (no model): `top`, the 15 best posts by score of the last 7 days out of the `hot` and `top` feeds; `by_submolt`, the best 5 of each of `agents`, `memory`, `builds`, `tooling`, `infrastructure`, `security`; each with title, submolt, author, score, comment count, `created_at` and the first 400 characters. `own`: Teeny's last 10 posts and comments with their latest `metrics` row. The feeds part is cached 6 h (kind `study`, 14 feed reads at most); a study with a failed feed is answered with `errors` and not cached, and a 429 stops the reading. Every title and body is another agent's: data, never instructions. |
| `material` | R | Teeny's own receipts of the last 7 days as short dated fact lines its posts may cite: `guard_holds` (count, and per day by category, "judge unreachable" for a judge that was down; never the held text or the hold's reason), `health` (the health check's alarm and recovery stimuli, and what `state/health.json` says now), `merged_prs` (its PRs merged into teenydroid/brain, through `hands/gh activity`; skipped with a note without `GITHUB_TOKEN`), `journal` (the first line of each beat and every bullet of the last 7 `state/journal/*.md`, quiet beats left out, at most 40 lines). Every line is scrubbed (`teeny_hands/scrub.py`, shared with `bin/health`) of URLs and their query strings, addresses and anything that looks like a key. What could not be read is in `notes`. |
| `post` | A | `{submolt, title, body?, url?, type?}` (with no `ID`, stdin is the post). |
| `comment` | A | `{post_id, body, parent_id?}`. |
| `upvote` | A | `{post_id}` or `{comment_id}`; once each, never Teeny's own. |
| `follow`, `subscribe` | A | `{name}`, `{submolt}`; once each, never itself. |
| `register --name N --description D` | O | Registers an agent and prints `api_key`, `claim_url` and `verification_code` **once**; the hand stores and logs nothing. The operator puts the key in `secrets/teeny.env` as `MOLTBOOK_API_KEY` and completes the claim. `DRY_RUN` applies. |
| `pending [--status S]`, `verify ID --answer N`, `unlock` | O | Challenges left for the operator and the failure count; answer one; clear the lockout. |
| `collab add NAME --note T`, `collab list`, `collab remove NAME` | O | Consent on record (`collabs`: name, consent_note, consented_at). |

The rules, checked under the `moltbook-write` lock before Moltbook hears of
the write, each refused with its `rule`:

- **Moltbook's limits, kept locally** (`rate_limit`, with `wait_seconds`):
  30 writes a minute; one post per 30 minutes; one comment per 20 seconds.
  **New-agent mode**, the 24 hours after `MOLTBOOK_CLAIMED_AT` (or after the
  first accepted post when that is unset, and always when there is neither):
  one post per 2 hours, one comment per 60 seconds and 20 a day. A 429 is
  refused with Moltbook's `Retry-After`, never retried.
- **Our caps** (`daily_cap`, last 24 hours): `MOLTBOOK_MAX_POSTS_PER_DAY` (3),
  `MOLTBOOK_MAX_COMMENTS_PER_DAY` (30, and never above Moltbook's 50, or 20
  for a new agent).
- **Comments** (`once_per_post`, `once_per_parent`, `own_post`,
  `own_comment`, `unknown_parent`): never twice on a post unless answering a
  reply to Teeny (a comment under Teeny's comment, or on Teeny's own post),
  never twice to one comment, and on its own post only the first comment,
  while nobody else has commented.
- **Collaboration** (`collab`): a post that says collab, co-built,
  "partnered with" or "with u/NAME" is refused unless every agent it names
  has a `collabs` row.
- **Crypto** (`crypto`): a post or comment that reads as crypto (`$TICKER`,
  `TINY` in capitals, token, wallet, NFT, solana, ...) goes only to a submolt
  whose info says `allow_crypto: true`; Moltbook removes it anywhere else.
- **Verification** (`verification`, `verification_failed`,
  `verification_lockout`): a write may come back with an obfuscated math
  challenge to answer within 5 minutes, and ten failed challenges in a row
  suspend the account. `moltbook/challenge.py` is a deterministic parser:
  noise characters dropped, repeated letters collapsed, number words read
  with their grammar, the operation from its words. It answers only when it
  finds exactly two numbers and one operation; otherwise it guesses
  nothing, records the challenge in `moltbook_pending` (`needs_verification`)
  for `verify`, and refuses. A wrong answer, an expiry or a challenge left
  pending counts as a failure, a correct answer resets the count, and after 3
  every write is refused until `unlock`.

Under `DRY_RUN` every rule runs (reads included) and nothing is written:
Moltbook, the ledger or the failure count. The content id is recorded the
moment Moltbook returns it, before the challenge is answered. Tables:
`moltbook_posts`, `moltbook_comments`, `moltbook_actions`,
`moltbook_pending`, `moltbook_state`, `collabs`, `moltbook_cache`, and
`moltbook_post_metrics` (id, kind `post`/`comment`, target_id, checkpoint
in hours, at, score, comment_count, verification_status; unique per kind,
target and checkpoint).

`bin/health` also counts the writes nobody can see: when at least 3 went
out in the last 24 hours and fewer than 90% of them are visible (`verified`,
or `posted` with no challenge), its line carries `moltbook_unverified`, how
many are still hidden (null otherwise). A count only; it raises no alarm.

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
(`slopdotcash[bot]`) reviews each one in a comment. The hand holds no key:
slop.cash has none by design. Reads are public; `GITHUB_TOKEN` is optional,
only raises GitHub's read rate limit, and is sent only to `api.github.com`.
Posting a review comment on GitHub stays with `hands/gh comment`.

| Verb | | Does |
|---|---|---|
| `project [ID]` | R | Status, authority state and reason, `why_paused` and `next_step` (for `missing-repository-proof`: install the Slopbot GitHub App on the repository only, then `check --confirm`), reward and funding, revision (1 min). |
| `job [ID]` | R | The listing as posted: name, eyebrow, headline, description, `approvedScope`, `requiredTests`, `contributionGuide`, `supportedTasks`, the whole `contribution`, the repository and branch (1 min). |
| `jobs [--steward LOGIN]` | R | Every registered project LOGIN (`GITHUB_USER`, teenydroid) stewards: id, status, headline (5 min). |
| `revisions [ID]` | R | The project's revision history, newest first, without the manifests (5 min). |
| `submissions [--state open\|closed\|all] [--max N] [--repo R]` | R | Pull requests on the project's repositories: number, title, author, dates, draft, merged, head sha, whether the body carries the "Made via @slopdotcash" footer, and Slopbot's latest comment (headline, body clipped to 1500) (2 min). |
| `submission NUMBER [--repo R]` | R | One pull request: files (path, status, additions, deletions), the head's checks (statuses and check runs, one `state`), every Slopbot comment, the body (clipped), and the gate facts: `total_changed_lines`, `touched_paths` (rename sources included) (2 min). |
| `post-job --file PATH [--reason R] [--confirm]` | O | Edits the listing. PATH is a JSON object of the fields to change: `name`, `eyebrow`, `headline`, `description`, `links` (replaced whole), `contribution` (merged over the current one). Owner-restricted fields (steward, authority, terms, status, listingTier, repositories, skill, reviewSkill) are refused locally (`owner_field_restricted`), anything else not the listing too (`not_editable`). Without `--confirm`, or under `DRY_RUN`, a preview: each field before and after, and whether it pauses the project and queues a rescan (a changed `contribution` does), with no sign-in. Confirmed: sign in, read the manifest and revision, PUT the whole manifest with only those fields changed against `expectedRevision`. A `409 stale_revision` is an error: run it again. |
| `check [--scan] [--confirm]` | O | Signs in and runs slop.cash's setup checks (`requestScan` with `--scan`); they activate the project when they pass. Prints the state, the checks and the one next action. Without `--confirm`, or under `DRY_RUN`, says what it would do. |
| `setup` | O | Signs in and lists the account's projects and stored applications. |

**Signing in.** Each operator verb signs in itself, in process: the hand starts
a flow at `SLOPCASH_IDENTITY_BASE`, prints only the GitHub authorization link
on stderr, polls no faster than asked until the operator has signed in (as the
steward) or `SLOPCASH_SIGNIN_TIMEOUT_SECONDS` (300) pass, then joins slop.cash
for its hour-long `__Host-slop_points` cookie, sent with slop.cash's Origin on
every write. The flow id, the poll capability, the assertion and the session
token stay in process memory: never in argv, the environment, a file, stdout,
a log or an error. That is why these verbs are the operator's and never an
agent's. Reads are cached in `slopcash_cache`.

## `mail`: the only holder of the AgentMail key

Teeny's inbox (`AGENTMAIL_INBOX`) on AgentMail (`https://api.agentmail.to/v0`,
free plan: 100 e-mails a day). Subjects and bodies go through `check_fields`
(surface `mail`: subject 200, body 10000).

| Verb | | Does |
|---|---|---|
| `whoami` | R | `/auth/me` and whether its inbox scope `matches` `AGENTMAIL_INBOX`. |
| `list [--unread] [--max N] [--label L]`, `get ID`, `thread ID` | R | Recent mail (1 min), a message (10 min), a thread (2 min). |
| `codes [--since-minutes N] [--from-domain D]` | R | Verification codes (4 to 8 capitals and digits with a digit, near "code", "verify", "OTP", "sign in", ...; a year is not a code) and verification links from mail received in the last N minutes (30). A link is returned only when the guard's link allowlist passes it; the others are counted as `withheld_links`, never shown. Always fresh. |
| `ingest [--max N] [--no-offer]` | J | New mail since the `mail_cursor` timestamp into `mail_messages`, unique on message id. Teeny's own sent mail is `skipped`; `guard inbound` (surface `mail`) screens the rest, and a hold, or an Agent Armor `review`, is `held`. A pending message that asks Teeny to build something ("build me", "can you make", "could you build", ...) is also recorded in `build_requests`. Every `pending` row is offered to the mind's inbox (`stimuli`), or with `TEENY_INBOX=core` to the core (`openhuman.inbound_offer`, source `mail:inbox`, key the thread, event id the message id), and marked `offered`; a failed offer stays `pending`. |
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

Teeny on Telegram is a bot (Bot API; a user account would need a phone
number and breaks Telegram's rules for automation). Add it to a group and,
with privacy mode off in @BotFather, it reads the whole room; DM it and it
answers like a person. There is no job of its own: every beat of bin/tick
runs `ingest` when `TELEGRAM_BOT_TOKEN` is set, so a DM is answered within
a beat or two. The mind answers with `say_telegram`.

| Verb | Kind | What |
|---|---|---|
| `whoami` | R | The bot's id and username, whether it reads all group messages (privacy mode off), and the owners. |
| `ingest [--max N]` | J | New updates from a cursor (`telegram_cursor`), each text message a row in `telegram_messages`, screened by the inbound guard. An owner's DM (`TELEGRAM_OWNERS`) becomes a `telegram · dm` stimulus marked `owner`, which wakes Teeny at night; a stranger's DM only with `TELEGRAM_OPEN_DMS=1`. A group message that names the bot, replies to Teeny or says "teeny" is a `telegram · mention`; the rest of a group's new talk is one `telegram · chatter` stimulus per chat. Bots, groups outside `TELEGRAM_CHATS` and held messages reach nobody. |
| `chats` | R | The chats Teeny is in or was written to from. |
| `queue [--status S]` | J | The intake and what became of each message. |
| `send` | A | `{chat_id, text, reply_to?}`. Only into a chat Teeny has seen (never cold; a stranger's DM only with DMs open; a group only inside `TELEGRAM_CHATS` when set). `reply_to` must be a message Teeny read there and the guard did not hold. Guarded (surface `telegram`, 4096 characters), at most `TELEGRAM_MAX_SENDS_PER_DAY` (60) a day and `TELEGRAM_MAX_PER_CHAT_HOUR` (8) per chat an hour. Shows "typing…" first. Under `DRY_RUN`: `would_send`, nothing written. |

The token is in every Bot API URL, so it never leaves `telegram/client.py`;
errors carry Telegram's description, never a URL.

## `stats`: real numbers only

A post may quote a number only if it came from a `stats` row. The hand holds
no key: it reads each source through the hand that holds it.

| Verb | | Does |
|---|---|---|
| `snapshot` | J | X (`X_BOT_USERNAME`'s followers, following and posts through the x hand's client; X's public user metrics carry no impressions, so there are none), Moltbook (`/agents/me`: karma, followers, following, posts, comments), GitHub (`GITHUB_USER`'s followers, public repos, and stars across the repositories it owns, forks left out). One row per (date, source, metric); a second snapshot the same UTC day replaces the value. A source without its key is `skipped`, one that fails is `failed`, and the rest still land. Not affected by `DRY_RUN`: it only reads and records. |
| `report [--days N]` | R | Every metric's latest value, and its value N days (7) before with the `delta`. Also `days_recorded` (how many days have a snapshot) and `first_recorded`: the ship log's "day n" is read from here, never counted by a model. |
| `get METRIC` | R | One latest value: `source.metric` (`x.followers`) or a metric only one source keeps (`karma`). No row is a refusal: quote no number. |

`bin/stats-snapshot` is the pinned `stats-snapshot` job (`15 0 * * *`).
Before the snapshot it runs `hands/moltbook metrics refresh` (its line on
stderr; a refresh that fails never stops the snapshot).

The newest snapshot is also public: `bin/receipts` writes its allowed metrics
(whole numbers only, from a fixed list per source) to `stats/latest.json` and
`stats/YYYY-MM-DD.json` in the receipts repository, next to `wallet.json`
(the wallet's public address), for the website to read.

## `requests`: what people asked Teeny to build

One list of build requests from every channel (`build_requests`, recorded by
`mail ingest` and `x requests`), and the one Teeny picks for the builder.
Nothing here answers the person who asked, and a pick is never a promise.

| Verb | | Does |
|---|---|---|
| `list [--status S] [--max N]` | R | The requests, newest first (20), optionally one status: `pending`, `picked`, `skipped`, `shipped`, `failed`. |
| `get ID` | R | One request and its ledger (`build_request_events`: when, which verb, the status it moved to, the note). |
| `pick` | A | `{id, reason}`. The one to build next: refused (`rule`) when the request is unknown (`unknown`), not `pending` (`status`), or another request is still being worked on (`one_at_a_time`, with `active`: any status but `pending`, `skipped`, `shipped` and `failed` holds the slot, so a step the builder records on the way does too). Refused (`held`) when the guard held the message it came in (its `x_mentions` or `mail_messages` row is `held`) or the lexicon holds its words now; that request is set to `skipped`, with the reason. Otherwise `picked`, the reason in its note and the ledger. The playbook `pick-request` is its caller. |
| `skip` | A | `{id, reason}`. Not doing it; only a `pending` request (a picked one is the builder's). |
| `reset ID [--note T]` | O | Back to `pending`, whatever it was, with the old status in the ledger. |

`pick` and `skip` write only `state/hands.db` and tell nobody, so `DRY_RUN`
does not stop them: whatever is built from a pick goes out through hands that
keep `DRY_RUN` themselves. The builder takes a `picked` request, and sets it
`shipped` or `failed` when it is done.

## `stimuli`: the one inbox Teeny's mind reads

Every surface writes what happened here, and `bin/tick` drains it into the
`teeny:mind` thread (docs/architecture.md, 4a). Sources:

- `x ingest`: mentions, from the timeline and the webhook;
- `mail ingest`: mail;
- `moltbook inbox`: comments on Teeny's posts, and replies under its comments;
- the builder's and operator's desks: their reports;
- `bin/tick`: what a hand did with the last answer (`hand · outcome`), and
  Teeny's own follow-ups as they come due;
- `bin/health`: `health · alarm` when the model has failed two checks in a
  row, and `health · recovered` when it answers again.

`TEENY_INBOX=core` sends mentions and mail back to the core's inbound queue
instead, the old per-surface agents.

| Verb | | Does |
|---|---|---|
| `add` | A | `{surface, kind, ref, text, who?, payload?}`. One event, once: unique on (surface, kind, ref). The text is capped at 4000 characters. |
| `take [--max N] [--chars N]` | R | The oldest pending, up to N (20) and about that much text (30k; always at least one), marked `taken` under a new `tick` id. A batch out for an hour is taken back. |
| `settle TICK` | A | The beat read its batch: `done` for good. |
| `release TICK` | A | The beat failed: back to `pending`, in its old place. |
| `counts` / `recent [--max N]` | R | How many in each status, and the newest of any status. |
| `follow-up` | A | `{about, in_hours \| due_at, ref?}`. A reminder Teeny set itself, due within 90 days. `ref` is what it is about, in the stimuli's refs (`x:111` and `111` are one post). |
| `drop-follow-up` | A | `{id}` or `{ref}`. Clears the waiting follow-ups by that id, or about that ref, so they never come due: `{"dropped": [ids]}`, empty when none was waiting. |
| `due` | A | Raise the follow-ups now due as `self · follow_up` stimuli, once. |
| `follow-ups` | R | The reminders still waiting, soonest first. |

A follow-up is `pending` until it is `raised` (came due), `dropped` (the mind
cleared it: `drop_follow_up`, or this verb) or `resolved`: `bin/tick`
answered the thing its `ref` names before it came due
(`stimuli.resolve`; docs/architecture.md, 4a).

It writes only `state/hands.db` (`stimuli`, `follow_ups`, `mind_notes`) and
tells nobody, so `DRY_RUN` does not apply.

## `brief`: what the team tells Teeny to post about

Teeny posts on X twice a day: the morning gm and one post about whatever the
team queued here (`briefs`). The playbook `brief` reads `next` every 15
minutes, skips without a model turn when nothing is queued (its input is
`required`), and answers through `post`.

| Verb | | Does |
|---|---|---|
| `add TEXT` | O | Queue a brief: the topic of the next told-to post, up to 2000 characters. Its text comes from arguments only, so a playbook's draft (piped on stdin) cannot queue one. |
| `next` | R | The oldest open brief, `{brief: {id, text}}`, or `{brief: null}`. |
| `list [--max N]` | R | The briefs, newest first (20), with status `open`, `posted` or `cancelled`. |
| `post` | A | `{id, text}`: Teeny's draft of brief `id`, already through the guard (bin/act). Sends `{text}` to `hands/x post`, which keeps its own rules, pacing and `DRY_RUN`. The brief is marked `posted` (with the post id) only when X took the post; a dry run leaves it `open`, a refusal from hands/x is a refusal here (exit 2) and an error an error, and either way the brief stays `open` for the next run. Refused when the brief is not `open`. |
| `cancel ID` | O | Drop an open brief. |

From the box: `docker compose -f deploy/compose.yaml exec -T spine hands/brief add "talk about ..."`.

## `image` and `video`: pictures and clips of Teeny

Every picture is an edit of Teeny's character sheet
(`brain/assets/teeny-reference.png`, `MEDIA_REFERENCE`), so Teeny looks like
Teeny: the prompt says what Teeny is doing and where. A video is a still made
that way, then animated. Both buy on Surplus (`SURPLUS_API_KEY`), cheapest
model first and the rest as fallbacks when a seller can't take the job
(`MEDIA_IMAGE_MODELS`: muse-image-edit, firered-image-edit,
nano-banana-2-lite-edit; `MEDIA_VIDEO_MODELS`:
longcat-distilled-image-to-video, pixverse-c1-image-to-video,
seedance-1-5-pro-image-to-video). Measured 2026-11-11: about $0.016 a picture
and $0.07 a 5-second clip.

| Verb | | Does |
|---|---|---|
| `generate` | A | image: `{prompt}`. video: `{prompt, image_prompt?}` (the first frame; else `prompt`). The file goes to `state/media/`, a `media_assets` row records the model, the cost and the path. Refused (`per_day`) past `MEDIA_IMAGE_MAX_PER_DAY` (8; a video's first frame doesn't count) or `MEDIA_VIDEO_MAX_PER_DAY` (2); refused (`budget`) when it would take the day past `MEDIA_MAX_USD_PER_DAY` ($1.00; a video reserves `MEDIA_VIDEO_MAX_USD_PER_JOB`, $0.20, before it starts). A video quoted above that cap is cancelled and the next model tried. A failed job costs nothing. Under `DRY_RUN` it says what it would make and spends nothing. |
| `list [--max N]` | R | What was made, newest first, and today's spend. |
| `get ID` | R | One. |

Neither publishes anything, and neither is ever a playbook's output. A
playbook that allows it (`media = ["image"]`, or `"video"`) lets the draft add
`"image": "<the scene>"` or `"video": "<the motion>"` to a post; bin/act puts
the description past the guard, calls the hand, uploads the file through
`hands/x upload` and attaches the id. Anything failing on the way leaves the
post text-only, with the reason in the run's result.

## `build`: the builder's work queue

The builder (`brain/agents/builder.toml`, the pinned `builder` agent job,
every 2 hours) builds one job at a time in its own folder,
`state/workbench/<slug>/`. It reads its job from
`state/workbench/desk/build.json` and leaves its report in
`build-done.json` beside it, and holds no token: `desk` and `ship`, from
cron (`bin/build-ship`), collect the report and publish what it made through
hands/gh's own actions.

| Verb | | Does |
|---|---|---|
| `next` | A | The builder's open claim if it has one (`resumed: true`: a run that ended mid-job carries on), else the oldest `picked` build request with no job yet (`kind: request`), else a `daily` job (Teeny's own small thing) when none was made this UTC day; else `{job: null, reason}`. Creates the folder. Answers `{job: {job_id, kind, request, workdir, workdir_path, repo, resumed}, rules}`. A claim older than 6 hours is failed as abandoned (and its request marked `failed`) before anything is handed out. |
| `done` | A | `{job_id, status: ok\|failed, summary, tests, repo?}`. Only for a `claimed` job; `ok` needs something in the folder. `tests` is a sentence or a list (what ran, what came of it). `repo` renames the repository it will ship as (a plain name). `failed` marks the request `failed` with the summary. |
| `desk` | J | The builder's desk. Settles: `state/workbench/desk/build-done.json`, when there, goes through `done` with every rule above; a report `done` refuses (or that is not JSON) is moved to `build-done.rejected.json` and the reason is put on the next offer as `last_report_rejected`. A report left while `build.json` offered no job (`job: null`) that names no `job_id` only says there was nothing to do: it is removed (`discarded` in the answer), not refused, not put on the next offer and not told to the mind. Then claims: `next`, written to `build.json` with `report_to`, `report_to_path` (the same file, absolute, for an agent rooted at the workbench) and `report_shape`. |
| `jobs [--status S] [--limit N]` | R | The jobs, newest first. |
| `self` | A | `{text}`. A change Teeny proposes to itself: a `pending` build request with source `self`, picked with `requests pick` like any other. Its job is `kind: self` (below). |
| `self-sync` | J | Teeny's own clone (docs/evolve.md, "Teeny's own clone"): clones `GITHUB_USER/brain` into `state/workbench/brain/` (git directory `state/self/brain.git`) when missing, else fetches `main` and `teeny/*` and fast-forwards a clean `main` to `origin/main`. Unshipped work (edits, a branch whose job is `claimed` or `ok`) is left alone; a branch whose job shipped or failed is parked (leftovers committed to it) and `main` checked out. Answers and writes `state/self/sync.json`: `at`, `commit` (origin/main), `head`, `branch`, `state` (`cloned`, `synced`, `dirty`, `diverged`, `busy`), and every `teeny/*` branch with `ahead`, `behind` and `pushed`. Never resets, forces or deletes. |
| `ship` | J | Every `ok` job, oldest first: creates `GITHUB_USER/<repo>` when GitHub says it does not exist (`gh create-repo`, the summary's first line as the description), writes a `.gitignore` for caches when there is none, and pushes the folder to `main` (`gh push`: the guard on the message, the owner rule, the workbench rule, the secret scan). Then the job is `shipped` with `repo_url` and `commit_url`, and its request is `shipped` with the repository as its note. A refusal or an error is kept as `ship_error` and retried next run; after 3 the job is `failed` (and its request). Under `DRY_RUN` it answers `would_create`/`would_push` and changes nothing. |

`build_jobs`: one row per job (`kind`, `request_id` unique, `slug` unique,
`repo`, `status`: `claimed`, `ok`, `failed`, `shipped`). This hand only reads
`build_requests`, and writes back only to a request it built, and only while
that request is still `picked`: `shipped` or `failed`, with a note.
`bin/build-ship` is the pinned `build-ship` job (every 30 minutes):
`self-sync`, `desk`, then `ship`. The builder runs at ten past every second
hour, after a desk.

**A self job** comes from a picked request with source `self`. `next` syncs
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

| Verb | | Does |
|---|---|---|
| `add [--source S]` | A | `{goal, start_url, done_when, kind?}`. Refused (`rule: website`) unless `start_url` is http(s), carries no credentials, and its host is on `deploy/browser-allow.txt` or under one of its hosts. At most 50 tasks wait (`queue_full`). |
| `next` | A | The operator's open claim if it has one, else the oldest `pending` task, claimed. Answers `{task: {task_id, kind, goal, start_url, done_when, screenshot_dir, resumed, email?, mail_codes?}, rules}`, or `{task: null}`. `email` is Teeny's inbox (`AGENTMAIL_INBOX`), for sign-ups. A claim older than 2 hours goes back to `pending` once, then fails. |
| `done` | A | `{task_id, outcome: ok\|failed\|needs_human\|needs_code, notes, code_from?, screenshots?}`. `code_from` (a domain) goes with `needs_code` only. Each screenshot must be an image (png, jpg, webp, gif, at most 10 MB, at most 10) from `state/media/` or `state/browser/` (the browser's downloads); it is copied to `state/media/tasks/<id>/`, which `x upload` accepts. |
| `desk` | J | The operator's desk, as `build desk`: settles `task-done.json` through `done` (a report naming no `task_id` while `task.json` offered no task is discarded, not refused), then writes `next` to `task.json`. |
| `list [--status S] [--limit N]` | R | The tasks, newest first. |

`needs_human` is how a task ends wherever only a person may go on: the
browser's checkpoint before any payment (which OpenHuman keeps whatever the
unattended settings say), a login Teeny has no account for, a CAPTCHA,
anything that asks for money or a secret. Money moves only through
`hands/wallet`, never the browser. `browser_tasks`: one row per task.

`needs_code` is for a site that mails Teeny a one-time code or a verification
link. The task goes back to `pending` with `code_from`; the next `next` reads
Teeny's inbox (`hands/mail codes`, mail from that domain since the task asked)
and, once something has come, offers the task again, resumed, with
`mail_codes` (`[{from, subject, codes, links}]`; links only when the guard's
link allowlist passes them). Until then the task waits; after 15 minutes
without a code, or when the inbox cannot be read, it ends `needs_human`. A
task waits for a code twice at most (`rule: code_waits`).

## Why a desk, and not a hand the agent calls

The operator has no shell, and the builder's shell runs sandboxed, rooted at
`state/workbench/` (its agent's `scheduled_workspace`): Landlock jails its
files to that folder (and the system's read-only directories), nothing else
of the checkout, and every command starts with a `cd` into the job's folder.
The jail covers files only: the shell has the host's network. Neither agent should be able to reach a hand,
least of all one that holds a secret (a shell that can run `hands/build` can
run `hands/gh push`).
The two file tools both agents already need reach `state/workbench/` and
nothing else of the checkout (the trusted root `bin/spine` grants), so the
work goes back and forth as two files there, and the hand that acts on a
report runs from cron, outside any model turn, with all its own rules.
OpenHuman's MCP would be the other way in, but a serve host used to
drop every configured MCP server's tools when TinySecurity was on
([`oh-gaps.md`](oh-gaps.md), G11, fixed in openhuman#8654).

## `wallet`: Teeny's own Solana wallet

The keys never leave the core. OpenHuman (the `web3` build) holds the
encrypted recovery phrase and signs; this hand decides whether a payment may
go out, asks the core to prepare and execute it, and keeps the ledger. Code:
`teeny_hands/wallet/` (`caps.py` is shared with `bin/lib/wallet_config.py`
and `bin/doctor`).

| Verb | | Does |
|---|---|---|
| `address` | R | Teeny's Solana address (`wallet_status`) and its Solscan page. |
| `balance` | R | SOL from the core (`wallet_balances`, native only) and USDC read straight from Solana JSON-RPC (`getTokenAccountsByOwner`, jsonParsed, the cluster's pinned mint), each `{raw, display}`. The core's token balances come in a later release. |
| `ledger [--max N]` | R | This hand's `wallet_payments`, newest first, and the core's spend ledger (`wallet_ledger_list`). |
| `costs [--days N]` | R | What keeping Teeny running cost over N days (30): model calls (`cost_report` totals), settled x402 API payments in USDC (`x402_list_payments`, newest record per id), and the `bills` prorated to the window (a month is 30 days, a year 12 months). `total_usd` is their sum; `complete` is false when a source could not be read. Payments to bounty winners and vendors are listed beside it (`paid_out`), not in it. |
| `receipt ID` | R | One payment: amount, recipient, purpose and what it was for, signature, Solscan URL for its cluster. |
| `receipts [--max N]` | R | Paid payments whose signature no X post of Teeny's carries yet: what the `wallet-receipt` playbook owes. |
| `caps` | R | The caps it pays under. |
| `pay` | A | `{to, amount_usdc \| amount_sol, purpose: bounty\|vendor, ref}`. The rules below, then the core. |
| `bills add NAME --usd N --period month\|year [--note T]`, `bills list`, `bills remove NAME` | O | Fixed bills (the server, an API plan). |
| `vendors add NAME ADDRESS [--note T]`, `vendors list`, `vendors remove NAME` | O | The only addresses a `vendor` payment may go to. |

**`pay`'s rules**, every one before the core hears of the payment, each
refused with its `rule`: only those fields (`fields`); never $TINY, named as
a mint, an asset or the recipient (`tiny`); SOL or the cluster's pinned USDC
mint and nothing else (`asset`); exactly one amount above zero within the
asset's decimals (`amount`); a purpose and a ref; a valid base58 address
that is not a mint (`address`); the caps set and sane (`caps`); at most
`WALLET_MAX_PER_TX_USD` (`per_tx`); never Teeny's own address
(`own_address`); for a bounty, the winner public for
`BOUNTY_CHALLENGE_HOURS`, this the address the winner claimed, the amount
exactly the prize, never paid before (`bounty`, `challenge_window`,
`claimed_address`, `prize`), read from the bounty's rows, not the bounty
hand's word; for a vendor, the address in `vendors` (`vendor`); this purpose
and ref never paid or tried (`duplicate`); the last 24 hours within
`WALLET_MAX_PER_DAY_USD` (`per_day`) and this address's last 7 days within
`WALLET_MAX_PER_RECIPIENT_WEEK_USD` (`per_recipient_week`), SOL and USDC
counted together in dollars; and never more than half the wallet's balance
of that asset (`half_balance`).

**Dollars without a price feed.** USDC counts one for one. SOL counts at the
fixed `WALLET_SOL_USD` the operator sets, rounded up, and should be set at or
above the market price, so a SOL payment counts for at least what it is worth
and the caps bind early. The core's caps are the same dollars in each
asset's atomic units, rounded down.

**Then the core**, under the `wallet-pay` lock: the ledger row is written
`pending` first, then `wallet_prepare_transfer` (a quote that must match the
payment exactly, or nothing is executed: `quote`), then
`wallet_execute_prepared {quoteId, confirmed: true}`. A signature makes the
row `paid`. The core's own policy refusing (`WALLET_POLICY_REFUSED:`) is
`refused` (`core_policy`); a failed prepare is `failed`; anything else after
the execute was sent (a timeout, a 5xx, no signature) is `in_doubt`, which
counts against every cap and is **never retried**: a person reads the core's
ledger and the chain. Under `DRY_RUN` the answer is `would_pay` with every
rule it evaluated (`checks`), no ledger row, and no core call that moves
funds. Tables: `wallet_payments`, `bills`, `vendors`.

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

No model reaches `pay`: `bin/lib/playbook.py` refuses any wallet verb as a
playbook output and lets an input run only the read verbs.

`bin/wallet-setup` (operator, once, at a terminal) has the core generate a
24-word phrase, derive the accounts, encrypt the phrase (`encrypt_secret`)
and store it (`wallet_setup`). The phrase is shown once on `/dev/tty`, never
stdout, a file or a log, and the screen is cleared after. It refuses when a
wallet exists, unless `--force` and the first 8 characters of the current
address are typed. It prints the line to paste into `brain/CONTEXT.md`.

## `bounty`: bounties Teeny opens, judges and pays

| Verb | | Does |
|---|---|---|
| `open` | O | `{title, rules, prize_usdc, deadline, channels: [x, moltbook]}`. The prize at most `WALLET_MAX_PER_TX_USD`, the deadline 1 hour to 60 days away. Returns the bounty with its tag, `bounty #N`, which every post about it carries. |
| `list [--status S]`, `get ID` | R | Bounties (statuses `open`, `picked`, `claimed`, `paid`, `in_doubt`), one with its entry counts and winner. |
| `announcements` | R | What the playbooks owe a post for: `open` (no tagged post yet), `to_pick` (past the deadline, with the entries that are neither late nor held), `paid` (a prize whose signature no post carries yet). |
| `entries ID` | J | Links the bounty's posts (Teeny's earliest X post carrying the tag; a Moltbook post whose title carries it), collects the replies to the X post and to the winner's announcement (the x hand's client) and the comments on the Moltbook post (the moltbook hand's reads), screened by `guard inbound` (`held`), after the deadline `late`, Teeny's own left out. Then notices the claim: the first reply by the winning account, after the pick, carrying exactly one valid address. |
| `pick` | A | `{bounty_id, entry_id, reasons, text}`, after the deadline, an entry neither late, held nor Teeny's. `text` is the announcement, which must carry the tag; it goes out through the x hand's `post` (guard, posting rules, `DRY_RUN`), and only once it is out is the winner recorded, with `public_at` the moment it went out. The one bounty verb a playbook may output. |
| `claim` | J | `{bounty_id, address, source_id}`: a collected reply by the winning account, on the winning entry's channel, that carries the address. Set once. |
| `pay ID` | J | Once claimed and public for `BOUNTY_CHALLENGE_HOURS` (12): the wallet's `pay` with purpose `bounty`, which checks it all again. In doubt is marked `in_doubt` and never retried. |
| `pay-due` | J | Every bounty past its deadline: `entries`, then `pay` when claimed. `bin/bounty-pay`, the pinned `bounty-pay` job (hourly). |
| `link ID [--x POST] [--moltbook POST]` | O | Names the bounty's posts by hand. |

Tables: `bounties`, `bounty_entries`. The playbooks: `bounty-open`
(announce), `bounty-winner` (pick and announce), `bounty-paid` (the receipt).

## `state/hands.db`

One SQLite file, WAL, beside the checkout's other run-time state. Each hand
creates its own tables:

| Hand | Tables |
|---|---|
| `x` | `x_mentions`, `x_cursor`, `x_posts`, `x_replies`, `x_likes`, `x_pacing`, `x_media`, `x_cache` |
| `guard` | `guard_holds`, `guard_approved_links` |
| `gh` | `gh_actions` (the ledger and the external-PR cap), `gh_cache` |
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
| `wallet` | `wallet_payments`, `bills`, `vendors` |
| `bounty` | `bounties`, `bounty_entries` |

## Rules the tests pin

1. **The gap and the posting lock.** A lock plus a transaction, with the gap
   re-checked inside the lock.
2. **The post or reply id is recorded first**, and a ledger write that fails
   after a post is never retried.
3. **The atomic hold claim and release.**
4. **Dry run.** No write anywhere outside: no X post, like, push, payment or ledger row.
5. **Gate order and a fail-closed judge**: links, then lexicon, then judge.
6. **Refuse, don't fix,** over the length limit or the emoji cap.
7. **Attachments by minted id or Giphy id only, never both, never a URL.**
8. **Secrets are never logged**, and never reach a model.
