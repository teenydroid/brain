# docs

Developer documentation for Teeny. The [README](../README.md) at the root says
what Teeny is and what it does; everything here is for people who run it or
change it.

| File | What it covers |
|---|---|
| [`developers.md`](developers.md) | Start here. Running Teeny with Docker or on a laptop, the everyday commands, scheduling a job, every config key, the repository layout, the tests and how to bump OpenHuman. |
| [`architecture.md`](architecture.md) | The design: the spine, the hands and the brain, and why the model only drafts. Code and tests cite its sections by number (§4a, §6a), so keep the numbering stable. |
| [`hands.md`](hands.md) | The spec for every hand: its verbs, its rules, its ledger and its exit codes. |
| [`roadmap.md`](roadmap.md) | The work list. Where Teeny stands against `DIRECTIVE.md`, and what to build and switch on to make it agentic, popular, self-sustaining and self-governing, one pull request per item. Start here to pick up work. |
| [`evolve.md`](evolve.md) | The self-improvement loop: what Teeny may change about itself, the frozen paths, how a generation is judged, and promotion, rollback and the probes. Built; it has not yet run a real generation. |
| [`oh-gaps.md`](oh-gaps.md) | What upstream OpenHuman could add so the hands could shrink. |
| [`research/`](research/README.md) | What others have built and written on self-improving, goal-driven and public agents, the state of agents and AGI in 2026, how agents govern and pay for themselves, and the proposal for Teeny's drive (grow and become powerful) and a faster evolve loop. |

Elsewhere in the repository:

- [`../deploy/README.md`](../deploy/README.md): the production box, its
  services, updates and backups.
- [`../web/README.md`](../web/README.md): the landing page.
- [`../DIRECTIVE.md`](../DIRECTIVE.md): what Teeny has to do before we call it
  alive.
- [`../evals/`](../evals/): the frozen tasks, judge rubric and invariants that
  measure Teeny.
