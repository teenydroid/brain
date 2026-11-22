# hands/x

The only holder of Teeny's X keys. Everything that reads from or writes to X
goes through `hands/x <verb>`. Verbs are either reads (safe to give a drafting
agent, served through a cache) or acts, and every act goes through
`publish.py`. Verbs and flags are in [`docs/hands.md`](../../../../docs/hands.md), "x".

```
X webhook ──► webhook.py ──► mentions.py ──► state/hands.db (mentions queue) ──► offered to the core
bin/act, bin/tick ──► cli.py ──► publish.py ──► guard ──► posting_rules.py ──► DRY_RUN ──► client.py ──► X
```

| File | What it does |
|---|---|
| `cli.py` | The verbs. Reads go through the read cache; acting verbs go only through `publish` and `media`. |
| `publish.py` | The one way to publish: guard, the 280 limit, the posting rules, then `DRY_RUN` or X, in that order and in one call. |
| `posting_rules.py` | The rules persisted in `state/hands.db` (gaps, daily limits, repeats) that `publish` applies after the guard. |
| `client.py` | The X v2 transport, its two ways of authenticating, the reads and the raw writes. |
| `oauth.py` | OAuth 1.0a request signing for user-context calls. |
| `model.py` | The plain JSON shapes the hand returns, built from X's snake_case answers. |
| `cache.py` | A read-through cache in front of the API (`x_cache` table). |
| `media.py` | Chunked media upload to X and Giphy GIF fetch. |
| `mentions.py` | The mentions queue: what people said to the account, screened and offered to the core as inbound events. |
| `webhook.py` | X's webhook: mentions, replies and quotes pushed as they happen. Served by `bin/x-webhook`. |
| `follows.py` | Who follows the account, as the webhook reported it. |
| `peers.py` | The peer-quote loop: Teeny's quotes of other people's posts, the one outward lane X's API allows. |
| `metrics.py` | How Teeny's own posts and replies landed, one row per checkpoint. |
| `profile.py` | The account's own profile as last read, and whether the bio says what the account is. |
| `recall.py` | What a beat needs to answer a mention without contradicting itself: the post it answers and that person's history with Teeny. |
| `twitr.py` | A pay-per-call X API paid from Teeny's wallet over x402, through `hands/wallet x402 fetch`; no X keys involved. |

Tests are in `hands/tests/` (X is faked). `hands/x` is on the frozen list
(`evals/frozen.txt`), so Teeny's own pull requests cannot change it.
