"""hands/trend: what AI X is talking about today, read only (docs/hands.md,
"trend").

Digg's AI and tech leaderboards rank the stories people on X are posting
about, hours old, with the X posts behind each one. They are read through
`digg-pp-cli` (Apache-2.0, mvanhorn/printing-press-library, built into the
spine image at a pinned commit: the Dockerfile's `digg` stage), which needs
no login, no key and no X API. Nothing is written but the cache and the
tool's own home, both under state/trend/.

  digg  runs the tool: one call, its JSON, or why there is none
  top   the merged leaderboards and the leading stories' posts, cached a slot
  cli   the verbs
"""
