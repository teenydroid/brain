"""hands/remake: popular short videos to study and re-create with public
figures and Teeny in them (docs/hands.md, "remake").

The finding and fetching is the `ingest` compose service's (deploy/ingest/:
a pinned yt-dlp and ffmpeg behind a small HTTP service); this package is
the hand's side of it.

  service  the HTTP client of the ingest service, and the address allowlist
           the hand checks before it asks
  ledger   `remake_sources` (videos already made, for package B and C) and
           `remake_studies` (what each study's vision call spent, and the
           day's cap)
  charts   what is popular: TikTok accounts' recent videos and YouTube's
           most-popular chart, filtered, ranked, cached a slot
  source   one video fetched through the service, its files pulled and
           checked and kept under state/media/<root>/ (shared with
           hands/khaby dissect)
  study    one video: fetched (source), one vision call on its shots, read
           strictly
  cli      the verbs
"""
