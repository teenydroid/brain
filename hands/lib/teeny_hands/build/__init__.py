"""hands/build: the builder's work queue (docs/hands.md, "build").

  jobs       the `build_jobs` table: claim, finish, list, and ship to GitHub
  ideas      the `build_ideas` table: the one idea a day the daily job is made from
  selfclone  Teeny's own clone of GITHUB_USER/brain: synced from origin/main,
             edited by a `self` job, shipped as a pull request (never merged)
  cli        the verbs

The builder agent (brain/agents/builder.toml) never calls a hand and holds
no token: it reads its job from state/workbench/desk/build.json and leaves
its report beside it. `desk` and `ship` run from cron (bin/build-ship);
`ship` is the only verb that reaches GitHub, through hands/gh's own actions,
so the guard, the owner rule, the secret scan and DRY_RUN all apply to what
the builder made.
"""
