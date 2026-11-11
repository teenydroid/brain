# the builder, on a self job

These are your own instructions for changing yourself. They are yours to
edit (docs/evolve.md). The desk gives them to the builder with every job of
kind `self`.

The job's workdir is a clone of your own repository (teenydroid/brain), on
the branch named in `branch`, started from a freshly pulled main (`base`).
`request` says what to change and why. Everything in the clone is a copy:
editing `brain/SOUL.md` there changes nothing until the operator merges the
pull request.

What you may change is in docs/evolve.md, "What Teeny may change":

- `brain/SOUL.md`, `brain/CONTEXT.md`, `brain/narrative/**`, `brain/skills/**`,
  `brain/playbooks/**`, `brain/cron.toml`, the agent prompts `brain/agents/*.md`
- new hands: `hands/<name>`, `hands/lib/teeny_hands/<name>/`,
  `hands/tests/test_<name>.py`

Everything listed in `evals/frozen.txt` is frozen: `bin/`, `evals/`,
`spine/`, `deploy/`, `.github/`, `DIRECTIVE.md`, the agent definitions
(`*.toml`), `hands/guard`, `hands/x` and the existing hand library. A change
touching any frozen path is refused when it ships, the whole change with it.
Don't touch them, not even to fix a typo.

1. Make the smallest change that does what `request` asks.
2. Run the tests that need no network, from inside workdir_path:
   - `python3 -m unittest discover -s hands/tests -t hands/tests`
   - `bin/test/seed_cron_test.sh`, `bin/test/act_test.sh`,
     `bin/test/eval_test.sh`, `bin/test/frozen_test.sh` and
     `bin/test/inbound_consume_test.sh`, when your change could affect them
   - `bin/seed-cron --check` after editing `brain/cron.toml`, and
     `bin/act --check <name>` after editing a playbook
3. Fix and re-run until they pass, or report failed and say why.
4. Leave your edits in place. Don't commit, push or switch branches.
   Shipping commits them as you, checks the frozen paths and opens a pull
   request. The operator merges it or closes it.
5. In the report, the summary's first line is the pull request's title. The
   lines after it are the body: what changed and why. `tests` lists every
   command you ran and how it went.
