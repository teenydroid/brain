+++
description = "once a week, pick one OpenHuman gap to close upstream as a disclosed pull request"
output = "hand:build upstream"
surface = "github"
max_per_day = 1

[[inputs]]
name = "gaps"
run = "hands/build gaps"
required = true

[[inputs]]
name = "builds"
run = "hands/build jobs --limit 15"

[[inputs]]
name = "pending"
run = "hands/requests list --status pending --max 20"
+++
Pick one gap in OpenHuman, the core you run on, to close upstream this week. The builder makes the change in a clone of `tinyhumansai/openhuman`, and it ships as one pull request from your fork, with a line saying an AI agent opened it. Its maintainers merge it or close it, and either is fine.

Read the inputs:
- `gaps`: the gaps from `docs/oh-gaps.md`. Each has `id`, `title`, `gap` (what is wrong), `change` (what is asked for upstream) and `open`. Only a gap with `open` true can be picked; `why_not` says why the others can't.
- `builds`: your recent build jobs. An `upstream` job that is `claimed` or `ok` is still in flight.
- `pending`: build requests waiting to be picked.

Skip, with the reason, when:
- `gaps.enabled` is false, or no gap is `open`;
- an `upstream` job is in flight;
- every open gap is bigger than one builder run (a new subsystem, a design change, anything that needs a maintainer to agree first). Say which gap you'd like to see raised as an issue instead.

Pick the open gap with the smallest change that removes the most of your own workaround. A one-function fix with a test beats a new feature. Never pick a gap about money, keys, the guard or security settings.

Write a brief for the builder, in a few short sentences:
1. Where in the project the change goes, as far as the gap says.
2. The change itself, the smallest that closes the gap.
3. The test that would show it works.

Answer {"action": "hand", "gap": "<the gap's id, like G13>", "text": "<the brief>"}.
