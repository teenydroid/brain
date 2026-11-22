# home

Teeny's own code. Everything here is Teeny's to write and change by pull
request, and a pull request that touches only `brain/`, `docs/` and `home/`
merges itself on green CI (`docs/mind.md`).

Nothing here runs with a key. There are two places it runs, both jailed:

- the mind's own shell, from its working copy (`state/home/repo/home/`);
- `bin/box <name>`, which runs `home/tools/<name>/` from the live checkout
  for a cron job in `brain/cron.toml` or for the operator.

## tools/

One folder per tool: `home/tools/<name>/` with a `run.py` (run with
`python3 -I`), a `run.sh` (bash) or an executable `run`. The name is
lowercase letters, digits and dashes.

What a tool gets:

| | |
|---|---|
| Arguments | at most 8 plain words from the cron line |
| Working folder | `state/box/tools/<name>/`, also `HOME`; `TMPDIR` is its `tmp/`. It is the only place the tool can write, and it stays between runs |
| Can read | `home/` and the system. Not `secrets/`, `brain/`, `bin/`, `hands/`, `state/` or `.git` |
| Network | open. It holds none of Teeny's accounts |
| To act | `"$TEENY_CLI" do '{"do": "post_x", "text": "..."}'`: the broker, with the guard and the caps, as for the mind |
| Time | `BOX_TIMEOUT_SECS` (600 by default), then it is killed with whatever it started |
| Output | the last 16 KiB it printed is left for the mind in `notes/inbox/tool-<name>.md` |

To schedule one, add a job to `brain/cron.toml`:

```toml
[jobs.hello]
schedule = "0 9 * * *"
command = "bin/box hello"
```

It runs only while the operator has `BOX=1` in `secrets/teeny.env`.
