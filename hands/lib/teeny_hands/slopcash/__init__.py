"""hands/slopcash: Teeny's project on slop.cash (docs/hands.md, "slopcash").

slop.cash pays open-source contributors for accepted pull requests on the
projects registered there. Teeny's project (`SLOPCASH_PROJECT`, default
teeny-s-brain, on teenydroid/brain) is its job listing; contributors open pull
requests against the repository and Slopbot (`slopdotcash[bot]`) reviews each.

  client   where slop.cash, its identity service and GitHub are, and the names
           that may reach a path
  reads    the read verbs (public; through the read cache `slopcash_cache`)
  signin   the GitHub sign-in to slop.cash, held in process memory only
  actions  the operator's writes: edit the listing, run the setup check
  cli      the verbs
"""
