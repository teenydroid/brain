# Hands

The only code Teeny owns. The `crates/...` paths cited below are the Rust
bot at `55c2c75`, the code each hand replaces. See [`architecture.md`](architecture.md) §3 for the
contract: one JSON object out, exit `0`/`2`/other, secrets read by the hand,
state in `state/hands.db`, `DRY_RUN=1` by default.

**Status.** `guard`, the outbound and read half of `x`, `gh`, `moltbook`,
`mail`, `stats`, `wallet` and `bounty` are built (`hands/guard`, `hands/x`, `hands/gh`,
`hands/moltbook`, `hands/mail`, `hands/stats`, `hands/wallet`, `hands/bounty`, code in `hands/lib/teeny_hands/`,
tests in `hands/tests/`). So is mention intake (`x ingest`, `x mentions`, `x mark`):
the queue itself is the core's inbound event queue, which coalesces mentions
into one drafting turn (docs/architecture.md, §7), so there is no `x queue`
claim or lease here. `article`, `auth` and the other hands are still to
come. Rows below say which.

**Language.** The hands are Python 3.11 standard library, with no
dependencies. Python is already in the image for `bin/seed-cron`, and OAuth 1.0a
signing, transactional SQLite and a guarded fetch are not jobs for bash. Each
executable is a three-line shim that runs `python3 -I`, so no `PYTHONPATH` or
planted module can change what it loads. The shared contract (outcomes, exit
codes, secrets, dry run, state, the core client) is
`hands/lib/teeny_hands/hand.py`. Run every test with
`python3 -m unittest discover -s hands/tests -t hands/tests`.

**R** marks a read verb. Read verbs are exposed to no-shell agents through
`hands/mcp` as read-only tools. **A** marks an acting verb, which only
`bin/job` scripts and the operator call. **J** marks a job verb: it says
nothing to anyone but writes state and calls the core, so like an acting
verb it is never given to a model.

## `x`: the only holder of the X keys

| Verb | | Does |
|---|---|---|
| `ingest [--max N] [--no-offer]` | J | Read the mentions timeline since the `x_cursor` cursor into `x_mentions`, unique on post id. The account's own posts and posts already answered are `skipped`; `guard inbound` screens the rest and a hold is `held`. Every `pending` row is offered to the core (`openhuman.inbound_offer`, source `x:mentions`) and marked `offered`; a failed offer stays `pending` for the next run. The cursor moves once the page is stored. |
| `mentions [--status S]` | J | The queue, newest first. |
| `mark ID --status S [--note T]` | J | What became of a mention after its draft: `drafted` (under `DRY_RUN`), `replied`, `refused` or `skipped`. |
| `get`, `thread`, `user`, `search`, `whoami` | R | X reads through the read cache (posts 30 days; searches and threads 10 minutes). |
| `reply`, `post` | A | Run `guard check` (house style, the 280 limit in UTF-16, the emoji cap, links, lexicon, judge). Then the posting rules: for an own post, the gap (`POST_MIN_GAP_HOURS`, 20-minute tolerance) re-checked under the posting lock; for a reply, never twice to one post, never to the account's own post, and 30–60s pacing kept in `x_pacing` and refused with `wait_seconds` rather than slept through. Then `DRY_RUN`, then X. **The post id is recorded first** (`x_replies`, `x_posts`). A ledger write that fails after a post is logged, never retried. Under `DRY_RUN`: no X write, no like, no ledger row. *The reply budget lanes come with `cadence`.* |
| `upload` | A | Media upload. Returns a media id that this hand minted, recorded in `hands.db`. `post` attaches only ids from that table or a Giphy id, never a URL, and never both. |
| `article` | A | *Not built yet.* Publish an article (guard and the 48h article interval). |
| `like` | A | Like a mention (skipped under `DRY_RUN`). |
| `unsay` | A | Delete an own post. Previews unless `--confirm`. |
| `auth` | A | *Not built yet.* The three-legged OAuth flow (today's `teeny auth-x`). Operator only, interactive. |

Replaces: `crates/teeny-x/*`, `teeny-agent/src/tools/{x,post}.rs`,
`teeny/src/{mentions,stream}.rs`, `teeny/src/http/webhooks.rs`,
`teeny-store/src/mentions.rs`, `teeny-store` x-cache. The webhook and activity
stream are dropped in favour of polling, because there's no HTTP server to
receive them. Bring them back only if a 5-minute poll proves too slow.

## `guard`: the content filter

| Verb | | Does |
|---|---|---|
| `check` | R | Run, in order: links (allowlist plus approved hosts), lexicon (8 categories, leetspeak folding, word boundaries; profanity outbound only), then the judge. The judge fails closed. Returns `ok` with the text to publish, or `hold{reason}` and writes the hold. `surface` picks the limits (below). |
| `check --fields title,body` | R | The same for a draft of several fields, as GitHub, Moltbook and mail take them: stdin carries each named field plus `surface`. Every field gets its surface's hard limits (refused, never cut), the house style, links, lexicon and judge; the answer is `{"fields": {...}}`, each field rewritten. Any field that fails fails the draft and names its `field`, and the draft writes at most one hold (its fields as JSON). Hands call it in-process as `teeny_hands.guard.gate.check_fields(fields, *, surface, kind="post", max_emoji=None, context=None)`, which returns a `FieldsVerdict(kind, fields, reason, category, hold_id, field)`. |
| `inbound` | R | Screens third-party text before a model sees it. `bin/job` calls it before every draft. |
| `style` | R | House style: strip leading @handles, replace dashes, straighten quotes, drop NFA/DYOR. |
| `holds list/approve/reject/release` | A | Operator review. `release ID [--note T]` publishes a withheld draft: it atomically moves `pending` to `releasing`, publishes through the hand that owns the hold's surface (today `x`: a post, or a reply to the post in its context, with the posting rules and `DRY_RUN` but without asking the guard again, since the operator's approval is the decision it deferred), marks it `approved` with where it went, and on failure goes back to `pending` with the error recorded. Under `DRY_RUN` it claims nothing and says what it would publish. A surface no hand releases yet (`github`, `moltbook`, `mail`) is refused before anything is claimed. |

**Surfaces and limits** (lengths in UTF-16 code units, refused over, never
cut; `max_emoji` overrides the cap):

| Surface | Fields and limits | Emoji per field |
|---|---|---|
| `x` (and every surface not below: `telegram`, `narrative`, ...) | `text` 280 | `MAX_EMOJI_PER_POST` (1) |
| `github` | `title` 256, `body` 20000, `description` 350, `message` 5000 | 3 |
| `moltbook` | `title` 300, `body` 10000 | 3 |
| `mail` | `subject` 200, `body` 10000 | 3 |

On a long-form surface, `text` is the body, and a leading `@handle` is kept
(nothing adds it back there). The built-in link allowlist also carries
`github.com/teenydroid`, `moltbook.com`, `www.moltbook.com`, `agentmail.to` and
`solscan.io`.

**One deliberate change from the Rust.** The old screen collapsed every
repeated letter, so the term `xxx` read as `x+` and held a lone "x" as sexual,
and `kkk` held a lone "k". On X that meant "x.com" and "on X". A term made of
one repeated letter now keeps its count (`x{3,}`), as a doubled vowel already
did. `xxx`, `XXXX` and `kkk` are still held. This is pinned in
`hands/tests/guard/test_screen.py`.

The judge runs through the core (`inference_agent_chat_simple`), so the model
key stays in the core's encrypted store. It fails closed: an unreachable core
or an unreadable verdict is a hold. `SAFETY_JUDGE=0` turns it off, leaving the
lexicon and the links.

Replaces `crates/teeny-safety/*` and `teeny/src/http/{safety,release}.rs`.
Holds are written best-effort and never turn into a publish.

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
| `push` | A | `{workdir, repo, branch, message}`. Commits everything git would add in a folder under `state/workbench/` (symlinks resolved) as `GITHUB_USER` (noreply address) and pushes `HEAD` to `branch` on an own repo. Never forces, ignores the box's git config, runs no hooks. Refused with `findings` (`{path, rule}`, never the secret) when the files or the history hold a secret path (`.env*`, `secrets/`, `state/`, `*.pem`, `id_*`, `*.key`) or a token (`ghp_`, `github_pat_`, `sk-`, `xox`, `AKIA`, a private key block). The token reaches git only through the environment of that one command and an askpass helper that holds no secret; it is never in the URL, a config file or a log. |
| `open-pr` | A | `{repo, head, base, title, body}`. On somebody else's repo, `head` is Teeny's fork branch, the disclosure footer is appended (`—` / "opened by Teeny, an AI agent (github.com/teenydroid), operated by @senamakel. close it if it's not useful."), and it is refused (`rule`) when the day's `GH_MAX_EXTERNAL_PRS_PER_DAY` (2, UTC) are spent (`daily_cap`), when Teeny already has an open PR there (`open_pr`), or when `contributing` forbids AI (`contributing`). |
| `comment` | A | `{repo, number, body}`. |
| `fork` | A | `{repo}`, somebody else's. |

Every acting verb under `DRY_RUN` answers `would_*` after every rule ran,
with no write anywhere: GitHub, git or the ledger. Done actions are recorded
in `gh_actions` (which also counts the daily cap); reads in `gh_cache`.

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
| `whoami`, `status`, `home` | R | `/agents/me` (10 min), the claim status, the dashboard (1 min). |
| `feed [--submolt S] [--sort hot\|new\|top\|rising] [--max N]` | R | `/posts`, or `/submolts/S/feed` (5 min). |
| `post ID`, `comments ID [--sort best\|new\|old]` | R | One post (5 min), its comment tree (2 min). |
| `search QUERY [--type posts\|comments\|all]`, `profile NAME`, `submolts`, `submolt NAME` | R | Semantic search (10 min), an agent, every submolt, one submolt with `allow_crypto` (1 h). |
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
`moltbook_pending`, `moltbook_state`, `collabs`, `moltbook_cache`.

## `mail`: the only holder of the AgentMail key

Teeny's inbox (`AGENTMAIL_INBOX`) on AgentMail (`https://api.agentmail.to/v0`,
free plan: 100 e-mails a day). Subjects and bodies go through `check_fields`
(surface `mail`: subject 200, body 10000).

| Verb | | Does |
|---|---|---|
| `whoami` | R | `/auth/me` and whether its inbox scope `matches` `AGENTMAIL_INBOX`. |
| `list [--unread] [--max N] [--label L]`, `get ID`, `thread ID` | R | Recent mail (1 min), a message (10 min), a thread (2 min). |
| `codes [--since-minutes N] [--from-domain D]` | R | Verification codes (4 to 8 capitals and digits with a digit, near "code", "verify", "OTP", "sign in", ...; a year is not a code) and verification links from mail received in the last N minutes (30). A link is returned only when the guard's link allowlist passes it; the others are counted as `withheld_links`, never shown. Always fresh. |
| `ingest [--max N] [--no-offer]` | J | New mail since the `mail_cursor` timestamp into `mail_messages`, unique on message id. Teeny's own sent mail is `skipped`; `guard inbound` (surface `mail`) screens the rest, and a hold, or an Agent Armor `review`, is `held`. A pending message that asks Teeny to build something ("build me", "can you make", "could you build", ...) is also recorded in `build_requests`. Every `pending` row is offered to the core (`openhuman.inbound_offer`, source `mail:inbox`, key the thread, event id the message id) and marked `offered`; a failed offer stays `pending`. |
| `queue [--status S]`, `mark ID --status S [--note T]`, `requests [--status S]` | J | The intake (statuses as `x_mentions`), what became of a message, the build requests. |
| `reply` | A | `{message_id, body}`. Only to a message in the inbox (a reply stays in its thread: `no_thread`), never Teeny's own (`own`), never one the guard held (`held`), once per message (`replied`, with `reply_id`). |
| `send` | A | `{to, subject, body}`; `to` is an address or up to 5. Only to addresses in `contacts` (`contacts`): Teeny never cold-mails. |
| `label` | A | `{message_id, add?, remove?}`. |
| `contacts add EMAIL --kind vendor\|winner\|collaborator [--note T]`, `contacts list`, `contacts remove EMAIL` | O | Who Teeny may mail first. |

Every reply and send counts toward `MAIL_MAX_SENDS_PER_DAY` (20, UTC day,
`daily_cap`). A 429 is refused with `Retry-After`, never retried. Under
`DRY_RUN` the rules run and nothing is sent or recorded. Tables:
`mail_messages`, `mail_cursor`, `mail_sent`, `contacts`, `build_requests`
(shared: `teeny_hands/requests.py`, statuses `pending|picked|skipped|shipped`,
for `x` to use the same way), `mail_cache`.

`bin/mail` (the pinned `mail-inbound` job, every 5 minutes) runs `hands/mail
ingest`, then `bin/inbound-consume --source mail:inbox`: the core drafts each
thread with the tool-less `teeny-mail` agent (prompt: `brain/playbooks/mail.md`,
written into the core's config by `bin/lib/inbound_config.py`), and each
reply goes through `hands/mail reply` and is marked.

## `stats`: real numbers only

A post may quote a number only if it came from a `stats` row. The hand holds
no key: it reads each source through the hand that holds it.

| Verb | | Does |
|---|---|---|
| `snapshot` | J | X (`X_BOT_USERNAME`'s followers, following and posts through the x hand's client; X's public user metrics carry no impressions, so there are none), Moltbook (`/agents/me`: karma, followers, following, posts, comments), GitHub (`GITHUB_USER`'s followers, public repos, and stars across the repositories it owns, forks left out). One row per (date, source, metric); a second snapshot the same UTC day replaces the value. A source without its key is `skipped`, one that fails is `failed`, and the rest still land. Not affected by `DRY_RUN`: it only reads and records. |
| `report [--days N]` | R | Every metric's latest value, and its value N days (7) before with the `delta`. |
| `get METRIC` | R | One latest value: `source.metric` (`x.followers`) or a metric only one source keeps (`karma`). No row is a refusal: quote no number. |

`bin/stats-snapshot` is the pinned `stats-snapshot` job (`15 0 * * *`).

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

## `cadence`: when Teeny may post

| Verb | | Does |
|---|---|---|
| `claim <job>` | A | The 20h re-run guard, recorded at the decision, so a failed run counts. Calendar items get 1h. Also checks the gap and hot hold. |
| `slot <afternoon\|evening>` | A | Pick from the three-day rota, falling back when the owner doesn't post. |
| `gap` | R | Whether an own post may go now (7h, 20-minute tolerance, hot hold). Replies are exempt. |
| `budget <post>` | R | Reply-lane counts: Auto 10 per own post, Smart 10 per post published in the last day, Called uncapped, 7-day window, hot spill. |
| `media` | A | The daily media budget: 20 images, 3 videos (UTC). |
| `heat` | A | Sample own-post metrics and set or clear the hot hold. |

Replaces `crates/teeny-cadence/*` and `teeny-jobs/src/{heat,slot}.rs`. The 20h
guard, the pacing and the media budget are **in-memory today**. Here they are
persisted, which is a deliberate fix.

## The rest

| Hand | Verbs | Replaces |
|---|---|---|
| `market` | R: `price`, `pools`, `holders`, `global`, `ta`, `leaderboard` (rate-limited) | `crates/teeny-market/*`, `teeny-agent/src/tools/market.rs` |
| `chart` | A: `render` (spec JSON → PNG, chart and moon SVG themes) | `teeny-media` chart, moon SVG, `tools/chart.rs` |
| `media` | R: `gif`, `look` (vision through the guarded fetch); A: `gen image\|video` (under `cadence media`) | `crates/teeny-media/*` |
| `web` | R: `search`, `read`, `similar`, `answer` (Exa), `news` | `teeny-agent/src/{exa,tools/web}.rs` |
| `record` | A: `log` (journal, handles redacted for the public feed), `brief`, `health`, `export` | `teeny-jobs/src/journal_glue.rs`, `teeny/src/http/feeds.rs`, `reflections` |
| `telegram` | A: `poll` (debounced, ordered per chat), `send` (after `guard check`, honours `TELEGRAM_DRY_RUN`; the token is never logged, since it sits in the URL path) | `crates/teeny/src/telegram/*`, `teeny-jobs/src/chat` |
| `policy` | `shell`: the `beforeShellExecution` hook. Refuses anything but `hands/*` with known verbs. | `teeny-agent/src/posture.rs` DENY list |
| `mcp` | A stdio MCP server exposing only the **R** verbs above, each `ReadOnly` with no external effect | `teeny-agent/src/tools/*` registration |

**The guarded fetch** (`media look`, `web read`) inherits
`crates/teeny-media/src/fetch.rs`:
- public https only, with the URL re-checked on every redirect (at most 3);
- names resolved by the hand, refusing private, loopback, link-local and
  CGNAT addresses, with the vetted IP pinned;
- no proxy, a 20s timeout and a 10MB cap;
- a generic error back to the model.

## `state/hands.db`

| Table | From (Mongo / file) |
|---|---|
| `x_mentions`, `x_cursor` (the intake; the queue proper is the core's) | `mentions` |
| `own_posts` (ledger, heat samples, hot hold) | `own_posts` |
| `holds`, `approved_hosts` | `holds`, `holds_links` |
| `x_cache` | `x-cache` |
| `gh_actions` (the GitHub ledger and the external-PR cap), `gh_cache` | — |
| `moltbook_posts`, `moltbook_comments`, `moltbook_actions`, `moltbook_pending`, `moltbook_state`, `collabs`, `moltbook_cache` | — |
| `mail_messages`, `mail_cursor`, `mail_sent`, `contacts`, `mail_cache` | — |
| `build_requests` (asks to build something, from any channel) | — |
| `stats` (one row per day per metric) | — |
| `wallet_payments`, `bills`, `vendors` (the wallet's ledger and the operator's lists) | — |
| `bounties`, `bounty_entries` | — |
| `people` | `people` |
| `media` (minted ids) | in-process today |
| `guards` (20h, calendar, article interval, pacing, media budget) | in-memory today |
| `runs` (`bin/job` start, finish, exit) | — |
| `cursor` (`since_id`, `last_polled_at`) | `data/state.json` |

Dropped:
- `inferences` and `inferences_totals`. OpenHuman's traces cover them.
- `telegram_transcript`. Telegram history becomes `record/` plus memory.

`bin/migrate-mongo` copies the five live collections once, at cutover.

## Invariants every hand port must keep

Each port brings over the tests of the code it replaces. These are the ones a
port is not done without:

1. **The gap and the posting lock** (`teeny-cadence/src/cadence.rs:33,203-230`).
   `flock` plus a transaction, with the gap re-checked inside the lock.
2. **The 20h guard is marked at the decision** (`teeny-cadence/src/guard.rs`).
3. **The reply budget lanes** (`teeny-cadence/src/budget.rs`).
4. **The claim and lease, and the reply id recorded first**
   (`teeny-store/src/mentions.rs:196-209,462`, `teeny-jobs/src/reply/job.rs:464`).
5. **The atomic hold claim and release** (`teeny-store/src/holds.rs:546-572`).
6. **Dry run.** No X write, like, ledger row or memory capture
   (`teeny-x/src/publisher.rs`, `reply/job.rs:219`, `tools/post.rs:206`).
7. **Gate order and fail-closed judge**: links → lexicon → judge
   (`teeny-safety/src/gate.rs`, `judge.rs:161`).
8. **Refuse, don't fix, over 280 or over the emoji cap**
   (`teeny-safety/src/gate.rs:377`).
9. **Attachments by minted id or Giphy id only, never both**
   (`teeny-agent/src/tools/post.rs:393`).
10. **The guarded fetch** (`teeny-media/src/fetch.rs`).
11. **Secrets are never logged**, including the Telegram token in URLs
    (`teeny/src/telegram/api.rs:118-122`).
12. **No retries on posting jobs** (`teeny/src/schedule.rs:382`).
