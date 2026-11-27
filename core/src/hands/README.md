# Hands in TypeScript

A hand is `hands/<name> <verb> [--flag value ...]`, an optional JSON body on
stdin, one JSON object on stdout, and an exit code (0 done, 2 refused, 1
error). OpenClaw's agents call it by path through their shell, so that
contract is everything: argv, stdout JSON, exit codes, stderr, the settings it
reads from `secrets/teeny.env`, the files it writes under `state/`. Language is
an implementation detail behind the path.

This directory is the framework (`lib/`) and, one directory per hand, the hands
already ported (`<name>/cli.ts`, which `npm run build` emits to
`core/dist/hands/<name>/cli.js`).

## The framework (`lib/`)

Each module is a port of its `hands/lib/teeny_hands/*.py` twin, with the same
semantics and messages. Names are the Python's, camel-cased.

| module | what it is | Python |
| --- | --- | --- |
| `hand.ts` | `Refused`, `HandError`, `CoreError`; `root()`, `stateDir()`, `setting()`, `require()`, `flag()`, `number()`, `dryRun()`, `now()`, `dayStart()`; `db()`, `openDb(schema)`, `addColumns()`, `transaction(conn, fn)`, `lock(name, fn, timeout)`; `core(method, params)`; `run()`, `main()`, `emit()`, `readBody()` | `hand.py` |
| `argparse.ts` | `ArgumentParser`, `addSubparsers`, `addArgument`, `bounded()`, `int`, `float`; Python 3.11's argparse, byte for byte on usage, errors and help | `argparse` |
| `rest.ts` | `RestApi(name, base, key, agent)`: `call`, `exchange`, `request`; `RestError` (`status`, `retryAfter`, `body`), `TooLarge`, `quote`, `urlencode` | `rest.py` |
| `readcache.ts` | `ReadCache(table, keySetting, ttl, enabledSetting).cached(kind, parts, fetch)` | `readcache.py` |
| `sqlite.ts` | `Connection` over `node:sqlite`: `run` (rowcount, lastrowid), `all`, `get`, `exec`, `runMany`, `close` | `sqlite3` |
| `scrub.ts`, `slurs.ts`, `tags.ts`, `authorship.ts`, `stimuli.ts`, `runway.ts`, `xpost.ts` | the shared modules the read-only hands import | same names |
| `guard/lexicon.ts`, `guard/screen.ts` | the guard's `hate` lexicon and screen, copied for `slurs` only | `guard/lexicon.py`, `guard/screen.py` |
| `py.ts` | `dumps` (json.dumps spacing, key order), `pyInt`, `pyFloat`, `pyStrip`, `pySplit`, `pyRepr`, `sleepSync` | stdlib |

A hand's `cli.ts` is the Python `cli.py` line for line:

```ts
import { ArgumentParser, bounded } from "../lib/argparse.js";
import * as hand from "../lib/hand.js";

export function parser(): ArgumentParser {
  const root = new ArgumentParser({ prog: "hands/hn", description: "Hacker News, read only." });
  const verbs = root.addSubparsers({ dest: "verb" });
  const top = verbs.addParser("top", { help: "the top stories" });
  top.setDefaults({ handler: (a) => reads.stories("top", a.max), takes_body: false });
  top.addArgument("--max", { type: bounded(1, 30, "--max"), default: 10 });
  return root;
}

await hand.main("hn", parser(), process.argv.slice(2));   // last line of cli.ts
```

Handlers are `(args, body) => object | Promise<object>`. They throw
`hand.Refused(reason, detail)` for a rule (exit 2) and `hand.HandError(message,
detail)` for a failure (exit 1); anything else becomes `{"ok": false, "error":
"<ErrorName>: <message>"}`, exit 1.

### What differs from the Python, and why

- **Async.** The network is async, so handlers, `RestApi.call`, `ReadCache.cached`,
  `core()` and `lock()` are. SQLite stays synchronous (`node:sqlite`), as in Python.
- **`lock(name, fn)`** takes the function to run under the lock. Node has no
  `flock(2)`; it is taken by util-linux `flock(1)` in a child that holds it until
  its stdin closes, so Python and TypeScript hands exclude each other on the same
  `state/locks/<name>.lock` files while both exist.
- **Whole-number floats** print `1`, not `1.0` (a JS number cannot tell). Every
  JSON reader takes both.
- **Error labels** for an unexpected exception read `TypeError: ...`, not
  `ValueError: ...`; a bad JSON body reads `stdin is not JSON: <V8's words>`.
- **`fetch` refuses a few ports** outright (9, 25, 6000...); urllib would connect.
  Nothing a hand talks to listens there.
- **The 3.11 argparse** is the reference (node:24's Python). Python 3.12+ words some
  errors differently; the port follows 3.11 because that is what runs today.
- **`hand.main()`** exits the process itself once stdout has drained, so an open
  socket or cache never keeps a finished hand alive.

### Stubs, and what is copied

- `runway.measure()` needs the wallet's `balance()` and `costParts()`. The wallet
  hand is not ported, so `runway.setWalletReads()` takes them and `measure()`
  fails closed (`HandError`: unmeasured counts as the `low` tier) until something
  is installed. The wallet port installs the real reads. Until then `bin/health
  runway` and the Python `runway.py` are the live ones.
- `guard/lexicon.ts` and `guard/screen.ts` are the Python's `hate` entry and
  screen, copied for `slurs`. `test/hands/lib/slurs.test.ts` compares them with
  the Python lexicon and screen on every run, so they cannot drift. The guard's
  own port takes both files over.

## Switching a hand to TypeScript

`hands/<name>` keeps its path. `core/scripts/hand-shim.sh <name>` rewrites its
wrapper to:

```sh
#!/bin/sh
exec node --disable-warning=ExperimentalWarning "$(dirname "$(realpath "$0")")/../core/dist/hands/<name>/cli.js" "$@"
```

`realpath "$0"` finds the checkout from a symlink or a PATH lookup, so it works
from any cwd; `exec` leaves no extra process, so stdin, stdout, stderr and the
exit status are node's own; the flag keeps `node:sqlite`'s "experimental" notice
off stderr. It needs `node` >= 24.16 on PATH and `core/dist` built (`npm run
build`; `src` is already in `tsconfig.build.json`, so `dist/hands/**` comes with
it). `test/hands/lib/shim.test.ts` pins the convention.

## The porting recipe

1. **Read the hand's Python completely**: `hands/lib/teeny_hands/<name>/*.py`,
   `hands/<name>`, `hands/tests/<name>/`. Trace its `teeny_hands` imports; port
   (or find already ported) each. Don't invent behaviour: same messages, same
   caps, same order of checks, same SQL.
2. **Port the tests first**, to `core/test/hands/<name>/*.test.ts`, with every
   assertion (`test/hands/lib/support.ts` gives `scratch()` for a TEENY_ROOT and a
   pinned clock, `serveRpc`, `build`, `capture`; `stimuli.test.ts` is a worked
   example of a ported `unittest` file). A test that drives `hands/<name>` as a
   subprocess is not ported: it stays, and runs against the shim.
3. **Port the code** to `core/src/hands/<name>/*.ts`, file for file, with
   `cli.ts` ending in `await hand.main(...)`. Settings come from
   `hand.setting()` only, never `process.env`; `dryRun()` and the kill switches
   keep "the environment can only turn it off, never on".
4. **Run parity** (`test/hands/parity.ts`) for every verb and refusal, against the
   Python hand still in place: same stdout JSON, exit code, stderr, `state/`
   files (hands.db table by table) and requests made. `settings` points the
   hand's base-URL overrides (`HN_API_BASE`, `CHAN_API_BASE`, `LISTEN_*_BASE`,
   ...) at the fixture server; `rpc` answers the core. No real network, ever.
5. **Switch and delete in one commit**: `core/scripts/hand-shim.sh <name>`, delete
   `hands/lib/teeny_hands/<name>/` and `hands/tests/<name>/` (the Python unit
   tests that import the module; the ones that exec `hands/<name>` stay), and
   fix nothing else in Python callers. Run the untouched Python suite (the
   `bin/test/hands_shard.py` shards) against the shim: it must be green.
6. A shared module (`hand.py`, `rest.py`, `stimuli.py`, ...) stays in Python as
   long as any Python caller (`bin/*`, a not-yet-ported hand) imports it; its
   TypeScript twin lives in `lib/` from the first port that needs it.

`npm ci && npx tsc --noEmit && npm run lint && npm test` in `core/` on node:24
is the bar.
