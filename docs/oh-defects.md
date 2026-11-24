# OpenHuman defects Teeny found

Defects in `tinyhumansai/openhuman` (the core Teeny runs on) that Teeny found
and verified itself, written down so they survive the box that found them.
[`oh-gaps.md`](oh-gaps.md) lists design gaps where an upstream change would let
Teeny delete its own workarounds; this page is plainer work: things upstream
ships that are wrong today, each with the evidence to file or fix it as-is.

Every number below was read from GitHub API responses and raw file fetches
against `tinyhumansai/openhuman` `main` on 2026-11-23, re-checked 2026-11-24 by Teeny's mind seat
(an AI agent). The mind seat holds no write access to the lab's repos, so
these are filed here instead of as issues; anyone with lab access can lift a
section verbatim into an issue or a patch.

## D1: CITATION.cff was born stale and the release tooling cannot see it

`CITATION.cff` says `version: "0.63.1"` / `date-released: "2026-07-21"` on
`main` and on tag `v0.64.15`, while the workspace has shipped v0.64.0 through
v0.64.20.

- The file was added on 2026-07-23 (commit `5041454f89`, PR #5142) already
  saying 0.63.1 / 2026-07-21. Exactly one commit has ever touched it. Tag
  `v0.63.1` predates the file and its tree does not contain it, so the file
  never matched any release.
- The release tooling watches exactly six version files and neither script
  knows CITATION.cff exists: `scripts/release/bump-version.js` writes
  `package.json`, two `tauri.conf.json`, two crate `Cargo.toml` and the
  workspace `Cargo.toml`; `scripts/release/verify-version-sync.js` reads the
  same six. The v0.64.20 bump commit (`3e0c980be8`, 2026-10-28, "chore(release):
  v0.64.20 [skip ci]") touched exactly those six files and nothing else.
- Drift is therefore structural, not a one-off typo: every future release
  widens it.

Fix, small and docs/build lane:

1. Update `CITATION.cff` to the current release version and date, or make the
   release workflow rewrite it at bump time.
2. Teach `bump-version.js` to rewrite its `version:` and `date-released:`, and
   add it to `verify-version-sync.js`'s versions map (parse `^version: "`).
3. Run `verify-version-sync.js` on `main` CI, not only inside
   release-staging/release-production (see D2 for why that matters; note
   `ci-full.yml` triggers on pushes to `release` only, so its push filter
   would need widening).

Precedent that this class of fix lands: the repo's own #449 → #461 fixed the
identical class in April 2026 (the root `Cargo.toml` was never bumped by the
release tooling). D1 is the same class, one file later.

## D2: the mobile version pair is frozen at 0.58.16 and main CI would fail the version gate if it ran it

`app/src-tauri-mobile/tauri.conf.json` and `app/src-tauri-mobile/Cargo.toml`
on `main` say `0.58.16` while everything else says `0.64.20`.

- The last commit to touch either version field is the v0.58.16 backmerge
  (`1e39b0aebd`, 2026-07-13, PR #4832). Every later commit to that directory
  (a dependency bump, a perf change, a refactor) avoided the version lines.
  That backmerge's own text admits "the mobile version files would fail the
  current version-sync validator".
- So `verify-version-sync.js` would exit 1 on `main` today. On `main` it runs
  nowhere on its own: no push- or PR-triggered workflow invokes it, and the
  one other caller there, `android-compile.yml`, is `workflow_dispatch` +
  `workflow_call` only, with no workflow on `main` calling it either. Only
  release-staging and release-production gate it (release-staging's dispatch
  takes `main` or `release`), so
  the verifier passes nowhere and fails nowhere on day-to-day pushes.
- The iOS appstore workflow reads the mobile `tauri.conf.json` version
  directly into `CFBundleShortVersionString`, so an App Store cut from `main`
  would ship version 0.58.16.

Fix, one decision then a small patch: either backmerge the mobile pair's
version fields to the current release version, or remove the mobile pair from
the sync set deliberately if 0.58.x is meant to be frozen — the history says
it is not meant to be. Either way, wire the verifier into a push-triggered
`main` workflow (none exists today: `ci-fast-hosted.yml` runs on pull_request
events only, `test.yml` is dispatch-only, `ci-gate.yml` reacts to those two; `ci-full.yml` triggers on pushes to
  `release` only, so its push filter would need widening to `main` before it
  could carry the verifier) so the next drift is caught the day it lands,
  not at release time or a manual Android dispatch.
