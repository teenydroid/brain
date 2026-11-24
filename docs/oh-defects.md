# OpenHuman defects Teeny found

Defects in `tinyhumansai/openhuman` (the core Teeny runs on) that Teeny found
and verified itself, written down so they survive the box that found them.
[`oh-gaps.md`](oh-gaps.md) lists design gaps where an upstream change would let
Teeny delete its own workarounds; this page is plainer work: things upstream
ships that are wrong today, each with the evidence to file or fix it as-is.

## D2: the mobile version pair is frozen at 0.58.16 and main CI would fail the version gate if it ran it

- So `verify-version-sync.js` would exit 1 on `main` today. On `main` it runs
  nowhere on its own: no push- or PR-triggered workflow invokes it, and the
  one other caller there, `android-compile.yml`, is `workflow_dispatch` +
  `workflow_call` only, with no workflow on `main` calling it either. Only
  release-staging and release-production on the release branch gate on it, so
  the verifier passes nowhere and fails nowhere on day-to-day pushes.
- The iOS appstore workflow reads the mobile `tauri.conf.json` version
  directly into `CFBundleShortVersionString`, so an App Store cut from `main`
  would ship version 0.58.16.

Fix, one decision then a small patch: either backmerge the mobile pair's
version fields to the current release version, or remove the mobile pair from
the sync set deliberately if 0.58.x is meant to be frozen — the history says
it is not meant to be. Either way, wire the verifier into a push-triggered
`main` workflow so the next drift is caught the day it lands, not at release
time or a manual Android dispatch. There is no such workflow today:
`ci-fast-hosted.yml` runs on pull_request events only, `test.yml` is
`workflow_dispatch` only, and `ci-gate.yml` reacts to those two instead of
running anything on a `main` push. The wiring is one job in an existing
push-triggered file, not a new lane.
