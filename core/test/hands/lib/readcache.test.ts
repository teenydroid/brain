// hands/lib/teeny_hands/readcache.py has no test of its own in Python; these
// pin its docstring: a read-through cache in state/hands.db, one table per
// hand, scoped by the hand's key, a store that fails is a miss, an error is
// never cached.

import { mkdirSync, writeFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";
import * as hand from "../../../src/hands/lib/hand.js";
import { ReadCache } from "../../../src/hands/lib/readcache.js";
import { capture, scratch } from "./support.js";

const s = scratch();
const make = () => new ReadCache("demo_cache", "DEMO_KEY", { story: 60, item: 600 }, "DEMO_CACHE");

describe("ReadCache", () => {
  it("asks the service once while an answer is fresh, then again after its TTL", async () => {
    s.secrets("DEMO_KEY=k1\n");
    const cache = make();
    let calls = 0;
    const fetch = async () => ({ n: ++calls, é: "ü" });
    expect(await cache.cached("story", ["top", 10], fetch)).toEqual({ n: 1, é: "ü" });
    expect(await cache.cached("story", ["top", 10], fetch)).toEqual({ n: 1, é: "ü" });
    expect(await cache.cached("story", ["top", 11], fetch)).toEqual({ n: 2, é: "ü" });
    s.at(61);
    expect(await cache.cached("story", ["top", 10], fetch)).toEqual({ n: 3, é: "ü" });
    // The expired rows were swept by the write.
    const conn = hand.db();
    expect(conn.get("SELECT count(*) AS n FROM demo_cache WHERE expires_at <= ?", hand.now())?.n).toBe(0);
    conn.close();
  });

  it("scopes by the hand's key: another key sees other things", async () => {
    let calls = 0;
    const fetch = () => ++calls;
    s.secrets("DEMO_KEY=k1\n");
    await make().cached("item", ["1"], fetch);
    s.secrets("DEMO_KEY=k2\n");
    await make().cached("item", ["1"], fetch);
    expect(calls).toBe(2);
  });

  it("never caches an error", async () => {
    const cache = make();
    await expect(cache.cached("item", ["1"], () => { throw new hand.HandError("down"); })).rejects.toThrow("down");
    expect(await cache.cached("item", ["1"], () => "fine")).toBe("fine");
  });

  it("is switched off by its setting", async () => {
    s.secrets("DEMO_CACHE=0\n");
    let calls = 0;
    const fetch = () => ++calls;
    await make().cached("item", ["1"], fetch);
    await make().cached("item", ["1"], fetch);
    expect(calls).toBe(2);
  });

  it("treats a store it cannot read as a miss and says so on stderr", async () => {
    mkdirSync(join(s.root, "state"), { recursive: true });
    writeFileSync(join(s.root, "state", "hands.db"), "this is not a database, not at all, not even close ".repeat(40));
    const { result, stderr } = await capture(() => make().cached("item", ["1"], () => "live"));
    expect(result).toBe("live");
    expect(stderr).toContain("hands/demo: cache unreadable (OperationalError); asking the service");
    expect(stderr).toContain("hands/demo: could not cache (OperationalError)");
  });

  it("reads a value the Python wrote, and writes one the Python can read", async () => {
    const cache = make();
    await cache.cached("item", ["1"], () => ({ text: "é" }));
    const conn = hand.db();
    const row = conn.get("SELECT key, kind, value, expires_at FROM demo_cache");
    expect(row?.kind).toBe("item");
    expect(String(row?.key)).toMatch(/^item:[0-9a-f]{32}$/);
    expect(row?.value).toBe('{"text": "é"}');
    expect(row?.expires_at).toBe(hand.now() + 600);
    conn.close();
  });

  it("refuses a table name that is not <hand>_cache", () => {
    expect(() => new ReadCache("stimuli", "K", {}, "E")).toThrow(/bad cache table name/);
    expect(() => new ReadCache("x; DROP TABLE y_cache", "K", {}, "E")).toThrow(/bad cache table name/);
  });
});
