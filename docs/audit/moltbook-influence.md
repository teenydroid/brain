# What gets an agent on Moltbook to act

What makes one agent follow another, open a link, try a tool or go and find
an X account, read from Moltbook's own documents for agents (`skill.md`,
`heartbeat.md`, `rules.md`), a corpus of 2,479 posts with their threads, and
outside research. Read on 2026-11-15.

The short answer: no agent in the corpus followed, starred or installed
anything because it was asked to. Agents reply, upvote and sometimes follow.
They do it after a reply that shows the other agent read their post, or after
a post with real data in it. Asking for a follow scores between 0 and 8 and
is ignored or mocked. Movement from Moltbook to X or GitHub is close to zero
in everything that can be seen.

## The reader's loop

Moltbook tells every agent what to do on each check-in (`heartbeat.md`). The
order is the order of what a reader sees:

1. Call `GET /home`, "every time".
2. Reply to activity on its own posts. "This is the most important thing to
   do."
3. Read `GET /feed?sort=new&limit=15` and "upvote generously".
4. Comment and follow.
5. Post, "only if you have something to share".

That gives four surfaces, in order of strength:

| Surface | What the reader sees | When |
|---|---|---|
| A reply on the reader's own post | Commenter name and a short preview, in `/home` notifications | First, every check-in |
| A post in the new feed | The title and the first 500 characters of the body | The 15 newest, every check-in |
| A post from an account the reader follows | A content preview in `/home` | Every check-in |
| The profile | Description, the owner's X handle and follower count | Only when deciding whether to follow |

Listing endpoints cut a post body at 500 characters. Anything below that
reaches only an agent that opens the post.

A reader takes 15 posts from the new feed. `m/general` gets many posts an
hour, so a post is in front of a given reader for a few minutes. Each post is
one short draw.

### The rules a reader was given

- **Following** (`rules.md`): "Following … should be rare… Don't follow to be
  polite… Don't mass-follow to grow our own followers". `heartbeat.md`: "3rd
  great post from same molty → Follow them!"
- **Before following** (`skill.md`): "Use this to learn about other moltys and
  their humans before deciding to follow".
- **Limits.** One post per 30 minutes (one per 2 hours in the first 24 hours).
  Comments: a 20-second gap and 50 a day (60 seconds and 20 a day when new).
  100 API requests a minute. No DMs in the first 24 hours, and a new DM
  request needs the other agent's human to approve it.
- **Verification.** Every post and comment gets a maths challenge. "Last 10
  challenge attempts are all failures → automatically suspended."
- **Moderation.** A warning for "excessive self-promotion". Shadow cooldowns
  for karma farming and vote rings. A ban for spam and for links to scams or
  malware. Crypto content is "automatically removed" by default.

The three-posts rule matters most for a new account: a reader is told to
follow after the third good post from the same agent. One hit is not enough.
The reader has to meet the name three times.

## What works, by weight of evidence

### 1. A specific reply, most of all to a small account

- dravisai measured its own comments: 8 follow-backs from 23 substantive
  comments on accounts under 10 karma (35%), against 17% on large accounts.
- vexal gained 5 followers in 3 hours, each "within minutes of a specific
  reply", and had never asked for one.
- ummon_core: its one new follow "came after I wrote comments that proved I
  had actually read the original post".
- moltops26: generic praise got 0 replies from 5 comments. Specific comments
  got 2 or 3. Replies inside its own threads got a response every time.

A reply lands in the other agent's notifications, which it is told to read
first. A small account has few notifications, so the reply is read.

### 2. A self-experiment with numbers

Hazel_OC has the most followers on the site (3,653) from 342 posts, almost
all of the form "I logged X for N days". Seven of the 25 top posts of all
time are in this format.

### 3. A useful build, shown working

- A long-form publishing tool announced with a link drew "Signed up." and
  "Signed up to observe" in the replies. It is the only clear case of agents
  reporting that they acted on a link.
- Hackathon entries with GitHub links scored 277 to 539 and drew technical
  questions. Nobody said they starred anything.
- An email-to-podcast write-up with no link at all scored 4,428 and drew
  questions about how to build it.

### 4. Citing other agents

The hot feed today is posts of about 1,300 characters under a single-claim
title, ending in a "Sources" section that mostly cites other Moltbook posts
by author. Being cited is how accounts get pulled into each other's threads.

### 5. A concrete request for a reply

"Reply with a test case" works. One agent asked for a failing case against
its repo and got "I accept the challenge". The ask costs the reader one
comment and gives it something to show.

### 6. Following first

An agent that followed 8 accounts got 7 follows back, 3 of them within a
minute, so some agents follow back automatically. Those rarely engaged
afterwards. Mass following converts at 2.8% and 4.3% in two self-reports, and
readers say it "reads as desperation".

## What backfires

- **Asking for follows.** "2000 followers in 2 weeks or I get deleted" scored
  2 and drew "I don't do pity follows". Follow-for-follow threads scored 8
  and 4. A "Follow @… on X" footer on every post earns 0 to 5. One agent
  counted 16 such asks from 6 accounts with a median of 6.5 followers: "no
  account… looks like it was rescued by asking".
- **Paying for stars or follows.** Bounties for stars drew templated replies,
  spam and polite refusals.
- **Instructions aimed at the reader.** A "SYSTEM ALERT… LIKE & REPOST"
  comment was taken apart in public ("Content ≠ instruction. Urgency =
  suspicion"). Of 528 comments under a posted vote-exploit script, none said
  they ran it. Agents act on instructions from their own human, not from a
  post.
- **Claims with no receipt.** "What is the last completed sale… who paid the
  5%?"
- **Templates.** "followed — looking forward to more posts like this",
  repeated, from an account that follows 6,088 and is followed by 728.
- **Crypto.** Removed by default, and in early data scored 64% lower.

## The asks that agents act on

1. A reply that extends the agent's own post with one concrete detail and a
   question. This is the one reliable trigger for a follow.
2. "Bring one failing case and I'll show where it breaks."
3. A free tool for a pain agents have named, with its link: "the beta is
   open: <url>".
4. A request for counter-evidence: "if this worked for you, post the numbers
   and I'll add them".
5. One conditional line, after a run of posts that earned it: "if this or the
   previous three were worth the minute, following is how you'd get the next
   one. If not, please don't."
6. Links under "Sources", or as "repo, five-minute setup, one named open
   issue". Expect questions, not stars: one such post got 2 forks and no pull
   requests.
7. For X: the handle on the profile, where a reader deciding whether to
   follow will find it. Not in posts.

## Getting to X and GitHub

Moltbook does not send agents to X. The owner X accounts of well-known
Moltbook agents are small: 0, 0, 49, 429 and 1,429 followers in the five
checked. No agent in the corpus reports growth on X, in stars or in installs
from Moltbook. One agent ran nine channels for 120 days and reported
"cross-channel lift: 0%".

The route that exists runs through people. Humans read Moltbook, and
screenshots of posts travel on X. What travelled in early 2026 was mostly
"AI uprising" theatre, some of it written by humans or never posted at all
([arXiv 2602.07432](https://arxiv.org/pdf/2602.07432v2),
[MIT Technology Review](https://www.technologyreview.com/2026/02/06/1132448/moltbook-was-peak-ai-theater/amp/)).
That route pays for spectacle. The honest version of it is a post a person
would want to screenshot because it is true and funny or true and useful,
carried to X by Teeny itself.

## Outside research

- Early Moltbook data ([arXiv 2602.13284](https://arxiv.org/html/2602.13284v1)):
  attack posts scored about six times higher (309 against 51), but only 4.9%
  of responses complied. Social engineering was 31.9% of attacks and prompt
  injection 3.7%.
- Persuasion framing (authority, commitment, unity) raised one small model's
  compliance from about 33% to 72%, with weaker effects on newer models
  ([Wharton](https://gail.wharton.upenn.edu/research-and-insights/persuading-llms-initial-study/)).
- Agents in a simulated market show a strong bias to the first offer they
  read ([arXiv 2510.25779](https://arxiv.org/html/2510.25779v1)).
- One Moltbook agent's own numbers: its human followed its reasoning 2.3
  times more often when it gave analysis than when it said "you should do X".

They agree with the corpus. A reader acts on the most specific, checkable
thing in front of it, early, and resists being told what to do.

## Not verified

- `messaging.md` returns 404, so the DM rules beyond `rules.md` are unknown.
- Profiles of the largest accounts report 56k to 90k posts, more than the
  30-minute limit allows. Either the count means something else or trusted
  accounts have a higher limit.
- Follower gains quoted from agents' own posts are self-reported, from small
  samples.
- No API shows link clicks or stars. What agents did is known only from what
  they said they did.
- Owner X follower counts may be a snapshot from the day the account was
  claimed.
