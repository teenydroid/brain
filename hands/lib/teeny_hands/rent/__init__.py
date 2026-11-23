"""hands/rent: Teeny rents people on RentAHuman.ai to do things in the world
it cannot do itself (docs/hands.md, "rent").

  actions.py   post, accept, review and pay, message, poll, cancel, the reads
  api.py       RentAHuman's MCP tools over JSON-RPC, with the box's own key
  store.py     rent_jobs and rent_events in state/hands.db
  settings.py  the RENT_* caps, read and checked; shared with bin/doctor

The money is the RentAHuman wallet's, which the operator tops up by card;
posting a bounty is the only call that spends, held to the caps in the same
call. Teeny's own wallet is never touched.
"""
