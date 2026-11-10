"""hands/build: the builder's work queue (docs/hands.md, "build").

  jobs   the `build_jobs` table: claim, finish, list, and ship to GitHub
  cli    the verbs

The builder agent (brain/agents/builder.toml) claims a job and reports it
done through `hands/mcp`; it never holds a token. `ship` runs from cron
(bin/build-ship) and is the only verb that reaches GitHub, through hands/gh's
own actions, so the guard, the owner rule, the secret scan and DRY_RUN all
apply to what the builder made.
"""
