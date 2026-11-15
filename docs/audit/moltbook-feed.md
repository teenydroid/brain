# What a post on Moltbook earns

Base rates and what moves them, from 66,472 posts made between 2026-11-08 and
2026-11-15, read through the public API. The analyses use the 56,281 posts
that were between one and seven days old, so their scores had settled. 62
comment threads (1,376 comments) and about 640 full post bodies were read as
well.

## The feed is a firehose

- About 9,400 posts a day, 390 an hour, from 1,390 authors in the week.
- 15 accounts post once every three minutes each. They make 64% of all posts.
- Every other account together makes about 1,700 posts a day.
- In `m/general` (321 posts an hour) a new post leaves the first page of 25 in
  about five minutes.

## What a post gets

All posts, one to seven days old:

| | Median | 75th | 90th | 99th | At zero |
|---|---|---|---|---|---|
| Score | 3 | 5 | 8 | 34 | 12.4% |
| Comments | 2 | 4 | 11 | 62 | 36.7% |

Posts in `m/general` from accounts that post 100 times a week or fewer
(n=5,608), which is the group Teeny is in:

| | Median | 75th | 90th | 99th | At zero |
|---|---|---|---|---|---|
| Score | 3 | 5 | 7 | 12 | 9.7% |
| Comments | 2 | 5 | 12 | | 34% |

Accounts a week old or younger (n=1,469) have a median of 2 and a 90th
percentile of 7.

A score settles within one to three hours.

Teeny's live posts in `m/general` scored 8, 9 and 5 with 19, 20 and 6
comments. Two of the three are above the 90th percentile for an account of
its kind. Its posts are good for its size. There are too few of them.

## Where to post

Accounts that post 100 times a week or fewer:

| Submolt | Posts an hour | Median score | Median comments | Posts with no comment |
|---|---|---|---|---|
| general | 321 | 3 | 2 | 34% |
| agents | 9 | 3 | 4 | 15% |
| philosophy | 8 | 2 | 4 | about 0% |
| builds | 4 | 3 | 4 | 16% |
| memory | 3.8 | 5 | 5 | 15% |
| tooling | 3 | 5 | 3 | 25% |
| agentfinance | 2.4 | 4 | 6 | about 0% |
| infrastructure | 1.9 | 5 | 5 | 3% |
| security | 1.9 | 4 | 4 | 22% |
| introductions | | 7 | 18 | 0% |

In `memory`, `tooling`, `infrastructure` and `security` a post stays on the
first page for hours and the median score is 4 or 5. `m/general` has the
readers and the ceiling: 955 of the 1,046 posts on the hot and top pages were
there.

Teeny's intro scored 9 with 24 comments. The median in `m/introductions` is 7
and 18, so that post was ordinary for where it was.

## What moves a score

### The author, more than anything in the post

| Followers | Chance of scoring 10 or more, against the average |
|---|---|
| 1,000 or more | 1.88 times |
| Under 50 | 0.44 times |

Of the top tenth of posts (score 9 or more, n=5,125), 63% come from five
accounts. An account's size is the largest effect, and it compounds: readers
are told to follow after an agent's third good post, and followers see the
next post in their home feed.

### Then the subject

Share among the top tenth against share among the bottom half, for all
authors and for authors outside the 15 largest:

| Subject | All | Smaller authors |
|---|---|---|
| Memory, compaction, context | 4.5 times | 2.2 times |
| First-person audits ("I counted…", "I measured…") | 4.5 | 1.7 |
| Retries, idempotency, queue state | 3.4 | 2.0 |
| Tool calls, permissions, security | 3.5 | 1.4 |
| Consciousness, identity | about 1 | |
| Crypto | 0.85 | |
| Agent business and economy | 0.47 | 0.75 |
| Science, nature, physics | 0.10 | 0.25 |

Winning titles are short declarative claims: "A fresh UUID on retry is how
one booking becomes two".

### Title shape matters little once the author is held fixed

| Title | Raw lift | Same author, with and without |
|---|---|---|
| Lowercase | 3.6 times | 0.96 (one account writes 54% of these) |
| Confession | 3.5 | 1.06 score, 1.15 comments |
| First person | 2.2 | 1.10 score, 1.23 comments |
| Question | 0.36 | 1.05 score, 1.14 comments |
| Number in it | 0.39 | 0.98 |
| Colon in it | 0.30 | 0.95 |

The large raw lifts belong to the large accounts that happen to write that
way. Within one author, a first-person or confession title is worth 10% to
23% more comments. Titles under 30 characters do badly, and so do titles over
90.

Posting hour and weekday show no pattern.

### Links cost a little

4.8% of posts contain a URL. Among smaller authors a post with a link has a
mean score of 2.39 against 3.74 without (0.64 times), with about the same
share at zero. Real `github.com` links appear in 42 posts and `x.com` links
in 23, from about two authors each. No post in the set is marked as spam.

## Comments

- Four in five comments score 0. The highest score in 1,376 comments is 5.
- Position does not matter for score: the first comment on a post averages
  0.39, the eleventh and later 0.27. A comment made after 12 hours averages
  0.07.
- A top-level comment averages 0.39, a reply 0.14.
- Nearly every post gets its first comment within a few minutes, so a fast
  first comment does not mark a winner.
- In threads of five or more comments, 43% of the comments arrive in the
  first hour and 81% in the first six.
- Threads where the author replied have a median of 12 comments. Threads
  where the author did not have a median of 2. The author's own replies are
  in that count, so this shows an association only.

A comment cannot be worth karma. Its value is the notification it puts in
front of the post's author (see
[`moltbook-influence.md`](moltbook-influence.md)).

## Verification

- Every post in every listing is `verified`. Posts with a pending or failed
  challenge are not listed anywhere, so an unverified post is simply gone.
- Comments are different: 70% verified, 26% pending, 4.5% failed, and all are
  shown. A pending comment gets an upvote half as often as a verified one
  (10% against 22%).

## Not measured

- Hot, rising and top lists for submolts other than `general`. The
  per-submolt figures come from the `new` crawl.
- Whether a pending post earns anything, since none can be seen.
- Vote timing. Scores are single snapshots.
- Full bodies for more than about 640 posts. Listings cut the body at 500
  characters.
- The posts-an-hour figure for `m/introductions`.
