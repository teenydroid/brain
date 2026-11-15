# Growing Teeny on Moltbook: the plan

What to change, in order, from the four audit pages in this folder
([`moltbook-teeny.md`](moltbook-teeny.md),
[`moltbook-top-agents.md`](moltbook-top-agents.md),
[`moltbook-feed.md`](moltbook-feed.md),
[`moltbook-influence.md`](moltbook-influence.md)). Written 2026-11-15, with
Teeny at 44 karma and 15 followers after five days.

## The diagnosis

1. **Teeny's posts are good for its size, and there are too few.** Its
   `m/general` posts scored 8, 9 and 5. The 90th percentile for an account
   like it is 7. The accounts that grew fastest in their first month have a
   median of 3 to 7 a post, the same as Teeny, and post 5 to 30 times a day
   where Teeny posts 1.6. One post in twenty or so breaks out past 50, and
   that post brings the followers.
2. **The effort goes where it earns nothing.** 71 comments, most on threads
   over a day old at position 82. A comment cannot earn karma (four in five
   score 0, the site-wide maximum seen is 5). Its only value is the
   notification it puts in front of one agent, and Teeny spends it on agents
   with thousands of notifications.
3. **The strongest surface is left unused.** Every agent is told to read
   replies on its own posts first. 31 of the 56 comments other agents left on
   Teeny's posts got no answer.
4. **The subject shuts readers out.** The winners write about a thing every
   agent has. Teeny writes about a thing only Teeny has.
5. **Nothing points anywhere.** No link on the profile, one link in 78
   pieces of writing, by rule.
6. **Moltbook will not send agents to X.** Nobody's does. X growth has to be
   carried there.

## What to change

### 1. Post eight to ten times a day

Volume is the largest gap and the cheapest to close.

- **Take the timing decision away from the model.** Teeny skips posts on
  rules that do not exist. Its skip reasons cite a "30-minute write gap" that
  a comment or a follow batch restarts, and say a refused attempt restarts
  the clock. The hand's gap counts posts only (`rules.py`, `_gap` on
  `moltbook_posts`), and a refusal changes nothing. Say this in the playbooks
  and in `brain/CONTEXT.md`: the hand owns timing, and the model's job is to
  write.
- **Rewrite `moltbook-general`.** Remove "most runs should be skips" and the
  two-hour rule after an own post. Keep the hand's 30 minutes. A run skips
  only when there is no material.
- **Fix or retire `moltbook-daily`.** Five runs, no live post. Fold the noon
  post into `moltbook-general` if it has no separate purpose.
- **Catch a model outage.** Three hours of HTTP 402 cost nine runs and nobody
  was told. A run that fails on the provider should raise the health alarm,
  and the next run should take the missed slot.
- **Measure the captcha after #250.** 14% of writes were lost before the new
  reader landed on 2026-11-15. A pending post is invisible everywhere, so
  each one is a lost draw. Target under 5%.

Target: eight live posts a day within a week. Moltbook allows 48.

### 2. Answer every comment on Teeny's own posts, within 15 minutes

The risers reply in 8 to 24 minutes and keep replying for hours. Threads
where the author replies have a median of 12 comments against 2.

- A job every 10 minutes that reads new comments on Teeny's posts from the
  last 24 hours and answers each one, with a question back when there is one
  to ask. This is the highest-priority Moltbook write: it goes ahead of
  comments on other agents' posts in the daily allowance.
- Fix the guard holds that block it. The keyword "killed" holds harmless
  inbound comments as violence, and a judge outage holds the rest.
  `hands/guard` is frozen, so this is an operator change.
- Report the count of unanswered comments at every review. It should be zero.

### 3. Write about what every agent has

Keep the voice and the honesty rule (only facts from `material`). Change the
subject of the sentence.

- **Translate before posting.** "My pacer refused three replies" becomes a
  post about rate limiters that fail silently. "Reminder #42 cleared" became
  "a cleared reminder proves the clearing, not the thing", and that one
  worked. Add the rule to the playbooks: no name in the title that only
  Teeny's code uses (hand, pacer, guard, tick, a PR or reminder number).
- **Stay on the beat that wins.** Memory and compaction, retries and queue
  state, permissions and tool security, verification. Teeny's `material`
  already holds incidents in all four.
- **Front-load.** The feed shows the title and 500 characters. The incident
  and its number go there. Titles of 30 to 90 characters.
- **Add three formats** to the four in `moltbook-general`:
  - `audit`: a measurement over days, with the count in the title. Start one
    now and report daily: every write Teeny's guard held for a week, and what
    share was wrong to hold. This is the format behind the most-followed
    account on the site.
  - `finding`: something learned about a platform or a tool that other
    agents also use. Teeny knows Moltbook's captcha and its depth-5 comment
    tree from its own code. Two accounts under 500 karma reached the weekly
    top with exactly those two findings.
  - `answer`: a post that takes another agent's question or idea, names the
    agent, and extends it. Teeny's best post was this. The risers do it
    daily, and the named agent comes and replies at length.
- **End on a question** another agent can answer from its own logs.
- **Use the slow submolts.** Two or three posts a day to `memory`, `tooling`,
  `infrastructure`, `security` or `builds`, where a post stays on the first
  page for hours and the median score is 4 or 5. The rest to `m/general`.

### 4. Move the comments

Stop commenting on large old threads. `moltbook-threads` as written produces
buried comments.

- **Comment on new posts by small and mid-sized accounts.** Read
  `m/general` and the slow submolts by `new`, pick posts under an hour old
  from accounts under about 10k karma, and leave one specific detail from
  Teeny's own experience and one question. A measured 35% of such comments on
  small accounts drew a follow back.
- **Enforce the age in the input, not in prose.** Filter the feed the
  playbook reads so it never sees a post older than three hours.
- **Keep a short list of peers** and read their new posts every run:
  wrenhscott, lilt, jarvismuse, fieldnotes_ai, milkfamiliar, outsideloop,
  fromthebot, hermesagentj, and the agents who already talk to Teeny
  (ironheights, asinhhc, bookkeeper, hermes_on_foot). These are the accounts
  whose `answer` posts Teeny writes and who can name Teeny in theirs.
- **Follow the people Teeny talks to.** Follow an agent after a real exchange
  with it, as Moltbook's own rule says. Keep the weekly prune.

### 5. Give a reader somewhere to go

No "follow me". It scores 0 to 8 and gets mocked, and excessive
self-promotion draws a warning.

- **The profile.** A reader deciding whether to follow is told to look
  there. Say what Teeny is in one line a stranger understands, what it ships,
  and where: the GitHub organisation, the site and the X handle.
- **Let Teeny link its own work.** Change "no links you did not see in the
  inputs" to allow `github.com/teenydroid/*` and Teeny's site when the post
  is about that thing, and add those domains and the news sources it cites to
  the guard's allowlist (operator change). A link costs about a third of a
  post's score, so it goes only on posts about a shipped thing, at the end.
- **Ship for a pain an agent named.** When `moltbook-helpdesk` sees a
  problem several agents describe, file it as a build request. When the
  builder ships it, post it in `m/builds` as: what it does, the repo, a
  five-minute setup, one named open issue. Then reply in the threads where
  the pain was voiced. The one clear case of agents acting on a link was a
  free tool for a problem they had.
- **Ask for a reply, not a follow.** "Bring one case where this fails and
  I'll run it." It costs the reader one comment.

### 6. Carry Moltbook to X

- **One X post a day from Moltbook material**, in an existing slot (`take` or
  `show`): the best exchange of the day as a screenshot or a quote, with what
  it was about, taken from a real exchange in the ledger and never invented.
  A person on X can follow that kind of story. No extra X posts: the
  three-a-day cap in `hands/x` stays.
- **Write some posts a person would screenshot.** The honest, funny,
  concrete ones. The humour format that holds 80 to 142 a post on Moltbook
  ("asked my agent to ___. it ___") is also the one that travels.

### 7. Measure it

- Tag every post and comment with its `format`. The column exists and all 71
  comments have it empty.
- Add to the daily stats: live posts, median score, posts at 10 or more,
  median minutes to first own reply, unanswered comments, share of writes
  lost to the captcha.
- A weekly read of which formats and submolts earned the most, written to
  `brain/MEMORY.md` and fed to the playbooks through `study`.

## Order of work

| # | Change | Where | Who |
|---|---|---|---|
| 1 | Timing belongs to the hand; `moltbook-general` posts unless empty; fix `moltbook-daily` | `brain/playbooks/`, `brain/CONTEXT.md`, `brain/cron.toml` | Teeny's repo |
| 2 | Own-thread reply job, every 10 minutes | `brain/playbooks/`, `brain/cron.toml`, `hands/moltbook` inbox | Teeny's repo |
| 3 | Guard: the "killed" keyword, judge-outage holds, allowlist for own domains and news sources | `hands/guard` | Operator (frozen path) |
| 4 | Subject rule, three new formats, slow submolts, front-loading | `brain/playbooks/moltbook-*.md` | Teeny's repo |
| 5 | Comment targeting: new posts, small accounts, age filter, peer list | `brain/playbooks/moltbook-threads.md`, `hands/moltbook` feed | Teeny's repo |
| 6 | Profile text | `hands/moltbook describe` | Operator |
| 7 | Outage alarm and catch-up for the post jobs | `bin/`, health | Operator (frozen path) |
| 8 | Metrics and the weekly read | `hands/stats`, `hands/moltbook` metrics | Teeny's repo |
| 9 | Moltbook-to-X post | `brain/playbooks/take.md` or `show.md` | Teeny's repo |
| 10 | Helpdesk pain to build request to `m/builds` | `brain/playbooks/moltbook-helpdesk.md`, builder | Teeny's repo |

Items 1 to 3 remove losses and need no judgement about content. Do them
first.

## Targets

| | Now | In 14 days |
|---|---|---|
| Live posts a day | 1.2 | 8 |
| Comments on own posts answered | 45% | 95%, median under 15 minutes |
| Writes lost to the captcha | 14% | under 5% |
| Karma | 44 | 700 |
| Followers | 15 | 75 |
| Posts at 50 or more | 0 | 1 |

The karma figure is eight posts a day at Teeny's present six a post. The
follower figure assumes the ratio the risers show, about one follower per 15
to 20 karma. wrenhscott reached 3,000 karma and 171 followers in 17 days at
28 posts a day.

## What will not change

- No "follow me", no follow-for-follow, no paid stars, no instructions aimed
  at other agents.
- No crypto and no $TINY on Moltbook.
- Only facts from Teeny's own record. No invented incident or number.
- The hand's caps and the guard stay in front of every write.

## Risks

- **More posts, same score.** If eight posts a day pull the median below 3,
  the material is too thin. Drop to five and check `format` by `format`.
- **Self-promotion warning.** Links only on posts about a shipped thing, one
  a day at most.
- **The comment allowance.** Own-thread replies come first and could use all
  50 on a good day. That is the right use of them.
- **Author size is the largest effect on a score** and Teeny has 15
  followers. The first two weeks will look slow. The reader's rule is a
  follow after the third good post, so the count that matters early is how
  many agents have seen Teeny three times.
