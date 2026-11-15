"""hands/people: who Teeny talks to, what they asked, and what Teeny promised
them (docs/hands.md, "people"; docs/roadmap.md, A9; DIRECTIVE.md, Level 2,
"Remembers people").

A record is built from what the hands already keep about a person's own
words to Teeny, and from Teeny's own promises. Nothing is scraped, nothing
is inferred about the person:

  records   the record per person: their messages in the inbox (`stimuli`,
            already through `guard inbound`; held text never lands there),
            their build requests (`build_requests`), and the promises; who
            counts as a regular; the block a beat's prompt carries for the
            people in that beat only
  cli       list, get, promise, kept, drop, forget

A promise is also a follow-up (`stimuli.follow_up`, ref `promise:<id>`), so
when it comes due the mind's next beat sees it, with that person's record.
"""
