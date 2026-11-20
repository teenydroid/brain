"""hands/chan: 4chan, read only (docs/hands.md, "chan").

Teeny never posts to 4chan: its API (https://github.com/4chan/4chan-API) is
read only and needs no key. What it reads there is raw material for the
backroom and the studio: an anonymous stranger's conspiracy, theory or
premise, to collide with an idea of Teeny's own. It is never a fact, never
quoted, never credited.

  client  where the API is, the boards Teeny may read, and the one-a-second pace
  reads   the read verbs, through the read cache (`chan_cache`); 4chan's HTML as
          text; every post screened by the guard's lexicon before it leaves
  cli     the verbs
"""
