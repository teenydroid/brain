"""hands/firsts: the firsts calendar, and the one gate a milestone post goes
through (docs/hands.md, "firsts"; docs/roadmap.md, P3).

A first gets an audience only when anyone can check it, and a fake one ends
the project's credibility (docs/research/attention-and-mindshare.md). So a
first is posted only when frozen code says its proof is in the ledger:

  calendar  brain/narrative/FIRSTS.md (Teeny's): each first and the
            `bin/proofs` id that proves it; round-number milestones read
            from `stats`; what is due; `announce`, which checks again and
            posts through `hands/x post`
  cli       list, due, announce

The proof is counted by bin/lib/proofs.py, never by a model, and this hand
is frozen (evals/frozen.txt) so the check can't be edited away. It holds no
key: a post goes out through `hands/x post`, with the guard and the posting
rules that hand keeps.
"""
