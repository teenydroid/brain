// The parity helper, proven on a toy hand written twice (Python against the
// real teeny_hands, TypeScript against the framework): the same argv, fixture
// server, settings and clock go through both, and parity must find them equal
// and must find a difference when there is one.

import { mkdirSync, mkdtempSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { beforeAll, describe, expect, it } from "vitest";
import { expectParity, runParity, type ParityCase } from "../parity.js";
import { REPO, build, pythonAvailable } from "./support.js";

const PY = `
import argparse, sys
sys.path.insert(0, ${JSON.stringify(join(REPO, "hands", "lib"))})
from teeny_hands import hand, rest, tags, scrub

def fetch(args, body):
    api = rest.RestApi("toy", hand.require("TOY_BASE"), None, "toy-agent")
    got = api.call("GET", "/items", query=[("max", str(args.max)), ("q", "a b&é")], auth=False)
    conn = hand.open_db("CREATE TABLE IF NOT EXISTS toy_log (id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL, at REAL NOT NULL, tags TEXT);")
    with hand.transaction(conn):
        conn.execute("INSERT INTO toy_log (name, at, tags) VALUES (?, ?, ?)", (got["items"][0]["name"], hand.now(), None))
    (hand.state_dir() / "toy.json").write_text(__import__("json").dumps(got, sort_keys=True))
    return {"items": got["items"], "dry_run": hand.dry_run(), "scrubbed": scrub.scrub("see http://x.y/z?k=1 at 1.2.3.4")}

def limited(args, body):
    api = rest.RestApi("toy", hand.require("TOY_BASE"), "toy-secret-key-0123456789", "toy-agent")
    try:
        api.call("GET", "/limited")
    except rest.RestError as exc:
        if exc.status == 429:
            raise hand.Refused("rate limited", wait=exc.retry_after)
        raise

def coreverb(args, body):
    return {"cron": hand.core("cron_list", {"a": 1})}

def post(args, body):
    rest_body, found = tags.split(body)
    return {"rest": rest_body, "tags": found}

def int_arg(n):
    return int(n)

p = argparse.ArgumentParser(prog="hands/toy")
v = p.add_subparsers(dest="verb")
f = v.add_parser("fetch"); f.set_defaults(handler=fetch, takes_body=False); f.add_argument("--max", type=int, default=3)
v.add_parser("limited").set_defaults(handler=limited, takes_body=False)
v.add_parser("core").set_defaults(handler=coreverb, takes_body=False)
v.add_parser("post").set_defaults(handler=post, takes_body=True)
sys.exit(hand.run("toy", p, sys.argv[1:]))
`;

function tsToy(dist: string): string {
  const lib = (m: string): string => JSON.stringify(join(dist, "hands/lib", m));
  return `
import * as hand from ${lib("hand.js")};
import * as rest from ${lib("rest.js")};
import * as tags from ${lib("tags.js")};
import { scrub } from ${lib("scrub.js")};
import { ArgumentParser, int } from ${lib("argparse.js")};
import { writeFileSync } from "node:fs";
import { join } from "node:path";
import { dumps } from ${lib("py.js")};

async function fetchVerb(args) {
  const api = new rest.RestApi("toy", hand.require("TOY_BASE"), null, "toy-agent");
  const got = await api.call("GET", "/items", { query: [["max", String(args.max)], ["q", "a b&é"]], auth: false });
  const conn = hand.openDb("CREATE TABLE IF NOT EXISTS toy_log (id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL, at REAL NOT NULL, tags TEXT);");
  hand.transaction(conn, () => conn.run("INSERT INTO toy_log (name, at, tags) VALUES (?, ?, ?)", got.items[0].name, hand.now(), null));
  conn.close();
  writeFileSync(join(hand.stateDir(), "toy.json"), dumps(got, { sortKeys: true }));
  return { items: got.items, dry_run: hand.dryRun(), scrubbed: scrub("see http://x.y/z?k=1 at 1.2.3.4") };
}
async function limited() {
  const api = new rest.RestApi("toy", hand.require("TOY_BASE"), "toy-secret-key-0123456789", "toy-agent");
  try { await api.call("GET", "/limited"); }
  catch (exc) { if (exc instanceof rest.RestError && exc.status === 429) throw new hand.Refused("rate limited", { wait: exc.retryAfter }); throw exc; }
  return {};
}
const coreVerb = async () => ({ cron: await hand.core("cron_list", { a: 1 }) });
const post = (args, body) => { const [r, t] = tags.split(body); return { rest: r, tags: t }; };

const p = new ArgumentParser({ prog: "hands/toy" });
const v = p.addSubparsers({ dest: "verb" });
const f = v.addParser("fetch"); f.setDefaults({ handler: fetchVerb, takes_body: false }); f.addArgument("--max", { type: int, default: 3 });
v.addParser("limited").setDefaults({ handler: limited, takes_body: false });
v.addParser("core").setDefaults({ handler: coreVerb, takes_body: false });
v.addParser("post").setDefaults({ handler: post, takes_body: true });
await hand.main("toy", p, process.argv.slice(2));
`;
}

const base: Pick<ParityCase, "hand" | "settings" | "routes" | "rpc"> = {
  hand: "toy",
  settings: { TOY_BASE: "{{HTTP}}/api", DRY_RUN: "0" },
  routes: {
    "GET /api/items": { json: { items: [{ name: "é-one", n: 1 }, { name: "two", n: 2.5 }] } },
    "GET /api/limited": { status: 429, headers: { "retry-after": "12" }, json: { error: "slow down toy-secret-key-0123456789" } },
  },
  rpc: { "openhuman.cron_list": { jobs: [{ id: "a" }] } },
};

describe.runIf(pythonAvailable())("the parity helper", () => {
  let cmds: Pick<ParityCase, "pythonCmd" | "tsCmd">;
  beforeAll(() => {
    const dir = mkdtempSync(join(tmpdir(), "toy-"));
    writeFileSync(join(dir, "toy.py"), PY);
    const dist = build();
    mkdirSync(join(dist, "toy"), { recursive: true });
    writeFileSync(join(dist, "toy", "toy.mjs"), tsToy(dist));
    cmds = {
      pythonCmd: ["python3", "-I", join(dir, "toy.py")],
      tsCmd: [process.execPath, "--disable-warning=ExperimentalWarning", join(dist, "toy", "toy.mjs")],
    };
  });

  it("finds a fetch that reads, writes state and a ledger row the same", async () => {
    const r = await expectParity({ ...base, ...cmds, argv: ["fetch", "--max", "5"] });
    expect(r.ts.code).toBe(0);
    expect(r.ts.requests).toHaveLength(1);
    expect(r.ts.requests[0]?.query).toBe("max=5&q=a+b%26%C3%A9");
    expect(Object.keys(r.ts.files)).toEqual(["state/hands.db", "state/toy.json"]);
    expect(r.ts.files["state/hands.db"]).toMatchObject({ toy_log: { rows: [{ id: 1, name: "é-one", at: 1789369200, tags: null }] } });
    expect(r.python.json).toMatchObject({ ok: true, dry_run: false, scrubbed: "see [url] at [host]" });
  });

  it("finds a rate limit refused with the wait, the key kept out of everything", async () => {
    const r = await expectParity({ ...base, ...cmds, argv: ["limited"] });
    expect(r.ts.code).toBe(2);
    expect(r.ts.json).toEqual({ ok: false, refused: "rate limited", wait: 12 });
    expect(r.ts.requests[0]?.headers.authorization).toBe("Bearer toy-secret-key-0123456789");
  });

  it("finds the core's RPC the same", async () => {
    const r = await expectParity({ ...base, ...cmds, argv: ["core"] });
    expect(r.ts.json).toEqual({ ok: true, cron: { jobs: [{ id: "a" }] } });
    expect(JSON.parse(r.ts.requests[0]!.body)).toEqual({ jsonrpc: "2.0", id: 1, method: "openhuman.cron_list", params: { a: 1 } });
  });

  it("finds the same error when the service is down, and the same bad arguments", async () => {
    await expectParity({ ...base, ...cmds, argv: ["fetch"], routes: { "GET /api/items": { status: 503, json: { message: "down" } } } });
    const r = await expectParity({ ...base, ...cmds, argv: ["fetch", "--max", "lots"] });
    expect(r.ts.code).toBe(1);
    expect(r.ts.stderr).toContain("argument --max: invalid int value: 'lots'");
    await expectParity({ ...base, ...cmds, argv: [] });
  });

  it("finds the same body handling", async () => {
    const r = await expectParity({ ...base, ...cmds, argv: ["post"], stdin: '{"text": "gm", "format": "GM", "experiment": "3"}' });
    expect(r.ts.json).toEqual({ ok: true, rest: { text: "gm" }, tags: { format: "gm", experiment: 3 } });
    await expectParity({ ...base, ...cmds, argv: ["post"], stdin: '{"format": "no way"}' });
  });

  it("says so when the two differ", async () => {
    const differing = await runParity({ ...base, ...cmds, argv: ["fetch"], env: { PARITY_TEST_DIFFER: "1" }, tsCmd: [...cmds.tsCmd!.slice(0, -1), join(mkdtempSync(join(tmpdir(), "d-")), "x.mjs")] });
    expect(differing.diffs.length).toBeGreaterThan(0);
  });
});
