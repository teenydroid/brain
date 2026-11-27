// A port of hands/tests/test_hand.py: outcomes and exit codes, secrets, dry
// run, state, the core. Every assertion of the Python is here.

import { spawn } from "node:child_process";
import { mkdirSync, rmSync, writeFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";
import { ArgumentParser } from "../../../src/hands/lib/argparse.js";
import * as hand from "../../../src/hands/lib/hand.js";
import { build, capture, scratch, serveRpc } from "./support.js";

const s = scratch({ now: null });

describe("secrets", () => {
  it("reads keys, quotes and comments", () => {
    s.secrets('# keys\nX_API_KEY=abc\nexport X_API_SECRET="s e c"\nEMPTY=\n');
    expect(hand.setting("X_API_KEY")).toBe("abc");
    expect(hand.setting("X_API_SECRET")).toBe("s e c");
    expect(hand.setting("EMPTY", "fallback")).toBe("fallback");
  });

  it("does not let the environment override a secret", () => {
    s.secrets("X_API_KEY=from-file\n");
    process.env.X_API_KEY = "from-env";
    try {
      expect(hand.setting("X_API_KEY")).toBe("from-file");
    } finally {
      delete process.env.X_API_KEY;
    }
  });

  it("names the missing key when it is required", () => {
    expect(() => hand.require("X_BEARER_TOKEN")).toThrow(/X_BEARER_TOKEN/);
    expect(() => hand.require("X_BEARER_TOKEN")).toThrow(hand.HandError);
  });

  it("reads switches as the Python did", () => {
    s.secrets("A=on\nB= Yes \nC=0\n");
    expect(hand.flag("A", false)).toBe(true);
    expect(hand.flag("B", false)).toBe(true);
    expect(hand.flag("C", true)).toBe(false);
    expect(hand.flag("MISSING", true)).toBe(true);
  });

  it("clamps a number and falls back on a typo, so a typo cannot lift a cap", () => {
    s.secrets("CAP=500\nLOW=-5\nTYPO=lots\nSPACED= 7 \nUNDERSCORE=1_0\nFLOATY=5.0\n");
    expect(hand.number("CAP", 10, 1, 100)).toBe(100);
    expect(hand.number("LOW", 10, 1, 100)).toBe(1);
    expect(hand.number("TYPO", 10, 1, 100)).toBe(10);
    expect(hand.number("SPACED", 10, 1, 100)).toBe(7);
    expect(hand.number("UNDERSCORE", 3, 1, 100)).toBe(10);
    expect(hand.number("FLOATY", 3, 1, 100)).toBe(3);
    expect(hand.number("UNSET", 3, 1, 100)).toBe(3);
  });
});

describe("the error detail", () => {
  it("carries into the JSON and exits 1", async () => {
    const parser = new ArgumentParser({ prog: "t" });
    const go = parser.addSubparsers({ dest: "verb" }).addParser("go");
    go.setDefaults({
      handler: () => {
        throw new hand.HandError("stopped part way", { sent: 2, ids: ["1", "2"] });
      },
      takes_body: false,
    });
    const { result: code, stdout } = await capture(() => hand.run("t", parser, ["go"]));
    expect(code).toBe(1);
    expect(JSON.parse(stdout)).toEqual({ ok: false, error: "stopped part way", sent: 2, ids: ["1", "2"] });
  });
});

describe("dry run", () => {
  it("is on by default", () => {
    expect(hand.dryRun()).toBe(true);
  });

  it("is off only from the file", () => {
    s.secrets("DRY_RUN=0\n");
    expect(hand.dryRun()).toBe(false);
  });

  it("can be forced on by the environment but never off", () => {
    s.secrets("DRY_RUN=0\n");
    process.env.DRY_RUN = "1";
    expect(hand.dryRun()).toBe(true);
    s.secrets("DRY_RUN=1\n");
    process.env.DRY_RUN = "0";
    expect(hand.dryRun()).toBe(true);
  });
});

describe("state", () => {
  it("rolls a transaction back on error", () => {
    const conn = hand.db();
    conn.run("CREATE TABLE t (v INTEGER)");
    expect(() =>
      hand.transaction(conn, () => {
        conn.run("INSERT INTO t VALUES (1)");
        throw new Error("boom");
      }),
    ).toThrow("boom");
    expect(conn.get("SELECT count(*) AS n FROM t")?.n).toBe(0);
    conn.close();
  });

  it("commits a transaction that returns", () => {
    const conn = hand.db();
    conn.run("CREATE TABLE t (v INTEGER)");
    expect(hand.transaction(conn, () => conn.run("INSERT INTO t VALUES (7)").rowcount)).toBe(1);
    expect(conn.all("SELECT v FROM t")).toEqual([{ v: 7 }]);
    conn.close();
  });

  it("keeps the schema and pragmas the Python set", () => {
    const conn = hand.db();
    expect(conn.get("PRAGMA journal_mode")?.journal_mode).toBe("wal");
    expect(conn.get("PRAGMA foreign_keys")?.foreign_keys).toBe(1);
    expect(conn.get("PRAGMA busy_timeout")?.timeout).toBe(10000);
    conn.close();
  });

  it("adds a column a table lacks, once", () => {
    const conn = hand.openDb("CREATE TABLE IF NOT EXISTS posts (id INTEGER PRIMARY KEY);");
    hand.addColumns(conn, "posts", { format: "TEXT", experiment: "INTEGER" });
    hand.addColumns(conn, "posts", { format: "TEXT", experiment: "INTEGER" });
    expect(conn.all("PRAGMA table_info(posts)").map((r) => r.name)).toEqual(["id", "format", "experiment"]);
    conn.close();
  });

  it("times a lock out while it is held", async () => {
    mkdirSync(join(s.root, "state"), { recursive: true });
    const outcome = await hand.lock("posting", async () => {
      try {
        await hand.lock("posting", () => "got it", 0.5);
        return "got";
      } catch (exc) {
        return exc instanceof hand.HandError ? exc.message : "other";
      }
    });
    expect(outcome).toBe("timed out waiting for the posting lock");
  });

  it("lets the lock go when its block ends, or throws", async () => {
    await expect(hand.lock("a", () => { throw new Error("inside"); })).rejects.toThrow("inside");
    expect(await hand.lock("a", () => "again", 2)).toBe("again");
  });

  it("lets many processes open a fresh database at once", async () => {
    // Switching a brand-new file to WAL takes an exclusive lock the busy
    // timeout does not cover; without a retry the losers fail outright.
    const dist = build();
    const script =
      `import * as hand from ${JSON.stringify(join(dist, "hands/lib/hand.js"))};` +
      "const c = hand.db(); c.run('CREATE TABLE IF NOT EXISTS t (v)'); console.log('ok');";
    for (let round = 0; round < 3; round++) {
      mkdirSync(join(s.root, "state"), { recursive: true });
      for (const suffix of ["", "-wal", "-shm"]) rmSync(join(s.root, "state", `hands.db${suffix}`), { force: true });
      const runs = Array.from({ length: 12 }, () =>
        new Promise<{ code: number | null; err: string }>((done) => {
          const child = spawn(process.execPath, ["--input-type=module", "--disable-warning=ExperimentalWarning", "-e", script], { env: process.env });
          let err = "";
          child.stderr.on("data", (c: Buffer) => (err += c.toString()));
          child.on("close", (code) => done({ code, err }));
        }),
      );
      const failed = (await Promise.all(runs)).filter((r) => r.code !== 0);
      expect(failed.map((f) => f.err.slice(0, 300)), `round ${round}`).toEqual([]);
    }
  }, 120_000);

  it("can have its clock pinned", () => {
    process.env.TEENY_NOW = "1000";
    expect(hand.now()).toBe(1000);
  });

  it("starts a UTC day where the Python did", () => {
    expect(hand.dayStart(1_789_369_200)).toBe(1_789_344_000);
    process.env.TEENY_NOW = "1789369200";
    expect(hand.dayStart()).toBe(1_789_344_000);
  });
});

describe("the core", () => {
  function point(url: string): void {
    mkdirSync(join(s.root, "state"), { recursive: true });
    writeFileSync(join(s.root, "state", "core.url"), `${url}\n`);
    writeFileSync(join(s.root, "secrets", "core.token"), "tok\n");
  }

  it("unwraps the controller envelope and sends the token", async () => {
    const server = await serveRpc({ jsonrpc: "2.0", id: 1, result: { logs: ["x"], result: { a: 1 } } });
    try {
      point(server.url);
      expect(await hand.core("cron_list")).toEqual({ a: 1 });
      expect(server.calls[0]?.authorization).toBe("Bearer tok");
      expect(server.calls[0]?.body.method).toBe("openhuman.cron_list");
    } finally {
      await server.close();
    }
  });

  it("raises an RPC error", async () => {
    const server = await serveRpc({ jsonrpc: "2.0", id: 1, error: { code: -1, message: "nope" } });
    try {
      point(server.url);
      await expect(hand.core("cron_list")).rejects.toThrow(/nope/);
      await expect(hand.core("cron_list")).rejects.toBeInstanceOf(hand.CoreError);
    } finally {
      await server.close();
    }
  });

  it("raises when the core is unreachable", async () => {
    point("http://127.0.0.1:9");
    await expect(hand.core("cron_list", {}, { timeout: 2 })).rejects.toThrow(/unreachable/);
  });

  it("names a missing token", async () => {
    await expect(hand.core("cron_list")).rejects.toThrow("no core token at secrets/core.token");
  });

  it("takes the URL and token from the environment first", async () => {
    const server = await serveRpc({ jsonrpc: "2.0", id: 1, result: 5 });
    try {
      process.env.OPENHUMAN_CORE_URL = server.url;
      process.env.OPENHUMAN_CORE_TOKEN = "env-tok";
      expect(await hand.core("openhuman.thing", { x: 1 })).toBe(5);
      expect(server.calls[0]?.authorization).toBe("Bearer env-tok");
      expect(server.calls[0]?.body).toEqual({ jsonrpc: "2.0", id: 1, method: "openhuman.thing", params: { x: 1 } });
    } finally {
      await server.close();
    }
  });
});

describe("the run contract, through a real process", () => {
  /** A fake hand compiled against the framework, run as `node fake.mjs`. */
  function runHand(body: string, argv: string[], stdin = ""): Promise<{ code: number | null; out: Record<string, unknown>; err: string }> {
    const dist = build();
    const script = join(s.root, "fake-hand.mjs");
    writeFileSync(
      script,
      `import * as hand from ${JSON.stringify(join(dist, "hands/lib/hand.js"))};
import { ArgumentParser } from ${JSON.stringify(join(dist, "hands/lib/argparse.js"))};
${body}
const p = new ArgumentParser({ prog: "fake" });
const v = p.addSubparsers({ dest: "verb" }).addParser("go");
v.setDefaults({ handler: go, takes_body: true });
await hand.main("fake", p, process.argv.slice(2));
`,
    );
    return new Promise((done) => {
      const child = spawn(process.execPath, ["--disable-warning=ExperimentalWarning", script, ...argv], { env: process.env });
      let out = "";
      let err = "";
      child.stdout.on("data", (c: Buffer) => (out += c.toString()));
      child.stderr.on("data", (c: Buffer) => (err += c.toString()));
      child.on("close", (code) => done({ code, out: JSON.parse(out), err }));
      child.stdin.end(stdin);
    });
  }

  it("is ok", async () => {
    const r = await runHand("const go = (a, body) => ({ echo: body });", ["go"], '{"x": 1}');
    expect([r.code, r.out]).toEqual([0, { ok: true, echo: { x: 1 } }]);
  });

  it("makes refused exit 2 with the reason", async () => {
    const r = await runHand("const go = () => { throw new hand.Refused('gap', { next_at: 5 }); };", ["go"]);
    expect([r.code, r.out]).toEqual([2, { ok: false, refused: "gap", next_at: 5 }]);
  });

  it("makes an error exit 1 and one JSON line", async () => {
    const r = await runHand("const go = () => { throw new TypeError('bad'); };", ["go"]);
    expect(r.code).toBe(1);
    expect(r.out).toEqual({ ok: false, error: "TypeError: bad" });
  });

  it("treats no verb as an error", async () => {
    const r = await runHand("const go = () => ({});", []);
    expect(r.code).toBe(1);
    expect(r.out.ok).toBe(false);
    expect(r.err).toMatch(/^usage: fake /);
  });

  it("treats bad JSON on stdin as an error", async () => {
    const r = await runHand("const go = () => ({});", ["go"], "{nope");
    expect(r.code).toBe(1);
    expect(String(r.out.error)).toContain("not JSON");
  });

  it("answers bad arguments with the error JSON, and argparse's words on stderr", async () => {
    const r = await runHand("const go = () => ({});", ["go", "--nope"]);
    expect(r.code).toBe(1);
    expect(r.out).toEqual({ ok: false, error: "bad arguments to hands/fake" });
    expect(r.err).toBe("usage: fake [-h] {go} ...\nfake: error: unrecognized arguments: --nope\n");
  });

  it("prints one line of sorted keys, spaced as json.dumps spaces them, with no ASCII escaping", async () => {
    const r = await runHand("const go = () => ({ zeta: 1, alpha: 'é', nested: { b: [1, 2], a: null } });", ["go"]);
    expect(JSON.stringify(r.out)).toBe(JSON.stringify({ alpha: "é", nested: { a: null, b: [1, 2] }, ok: true, zeta: 1 }));
  });
});
