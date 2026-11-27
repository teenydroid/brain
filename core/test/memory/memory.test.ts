import { createHash } from "node:crypto";
import { mkdirSync, readFileSync, statSync } from "node:fs";
import { join } from "node:path";
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { decodeEvent, digest, itemId, nanos } from "../../src/memory/cortex.js";
import { memoryHttp } from "../../src/memory/index.js";
import { engineFile } from "../../src/memory/engine.js";
import { FAKE_KEY, startTestCore, type TestCore } from "../helpers.js";

interface Event {
  id: string;
  scope: string;
  content: { text: string };
  context: { labels: string[]; observed_at?: string };
}

/** A CortexDB v0.10.4 stand-in: append-only events, a listing that emits each twice, forget by memory_ids. */
class FakeCortex {
  events: Event[] = [];
  requests: { method: string; path: string; query: Record<string, string>; body?: Record<string, unknown>; headers: Headers }[] = [];
  down = false;
  n = 0;

  fetch: typeof fetch = (async (input: URL | string, init?: RequestInit) => {
    const url = new URL(String(input));
    const headers = new Headers(init?.headers);
    const body = init?.body ? (JSON.parse(String(init.body)) as Record<string, unknown>) : undefined;
    const query = Object.fromEntries(url.searchParams);
    this.requests.push({ method: init?.method ?? "GET", path: url.pathname, query, ...(body ? { body } : {}), headers });
    if (this.down) throw new TypeError("fetch failed");
    const json = (v: unknown, status = 200) => new Response(JSON.stringify(v), { status });
    switch (`${init?.method ?? "GET"} ${url.pathname}`) {
      case "GET /v1/admin/health":
        return json({ status: "ok" });
      case "GET /v1/auth/whoami":
        return json({ caller: "user:local" });
      case "POST /v1/experience": {
        const dup = this.events.find((e) => e.id === `key-${body?.idempotency_key}`);
        if (dup) return json({ event_id: dup.id, replayed_from_idempotency: true }, 202);
        const ctx = body?.context as { labels: string[]; observed_at?: string };
        const ev: Event = { id: `evt_${++this.n}`, scope: String(body?.scope), content: { text: (body?.content as { text: string }).text }, context: ctx };
        this.events.unshift(ev); // newest first
        return json({ event_id: ev.id, status: "accepted" }, 202);
      }
      case "GET /v1/events": {
        let items = this.events.filter((e) => e.scope === query.scope);
        if (query.labels) {
          const want = query.labels.split(",");
          items = items.filter((e) => e.context.labels.some((l) => want.includes(l)));
        }
        const start = query.cursor ? Number(query.cursor) : 0;
        // the listing emits every event twice, and the limit counts the copies
        const doubled = items.flatMap((e) => [e, e]);
        const page = doubled.slice(start, start + Math.min(Number(query.limit ?? 200), this.pageSize));
        const more = start + page.length < doubled.length;
        return json({ items: page, has_more: more, ...(more ? { next_cursor: String(start + page.length) } : {}) });
      }
      case "POST /v1/forget": {
        const ids = ((body?.selector as { memory_ids?: string[] })?.memory_ids ?? []) as string[];
        this.forgot.push({ body: body as Record<string, unknown> });
        this.events = this.events.filter((e) => !ids.includes(e.id));
        return json({ forgotten: ids.length });
      }
      case "POST /v1/recall":
        return json({ pack_id: "p", layers: { events: this.events.filter((e) => e.scope === body?.scope).slice(0, 30) } });
      default:
        return json({ error: "nope" }, 404);
    }
  }) as typeof fetch;
  pageSize = 200;
  forgot: { body: Record<string, unknown> }[] = [];
}

let t: TestCore;
let cortex: FakeCortex;
beforeEach(async () => {
  cortex = new FakeCortex();
  memoryHttp.fetch = cortex.fetch;
  t = await startTestCore();
});
afterEach(async () => {
  memoryHttp.fetch = undefined;
  await t.core.stop();
});

const ok = async (method: string, params: Record<string, unknown>): Promise<Record<string, unknown>> => {
  const r = await t.rpc(method, params);
  if (r.error) throw new Error(`${method}: ${r.error.message}`);
  return r.result as Record<string, unknown>;
};
const configure = async () => ok("memory_engine_set", { engine: "cortexdb", endpoint: "http://cortex.test", api_key: "cortex-key-fake" });

describe("memory_engine_set", () => {
  it("answers {status} as bin/spine reads it, and seals the key at rest", async () => {
    const got = await configure();
    expect(got).toEqual({ status: "ready" });
    const file = engineFile(t.stateDir);
    const raw = readFileSync(file, "utf8");
    expect(raw).not.toContain("cortex-key-fake");
    expect(JSON.parse(raw)).toMatchObject({ engine: "cortexdb", endpoint: "http://cortex.test", apiKeySealed: expect.stringMatching(/^enc2:/) });
    expect(statSync(file).mode & 0o777).toBe(0o600);
    expect(JSON.stringify(cortex.requests)).not.toContain(FAKE_KEY);
  });

  it("works without a key (CortexDB behind loopback) and keeps a key stored earlier when a boot gives none", async () => {
    expect(await ok("memory_engine_set", { engine: "cortexdb", endpoint: "http://cortex.invalid" })).toEqual({ status: "ready" });
    await configure();
    await ok("memory_engine_set", { engine: "cortexdb", endpoint: "http://cortex.test" });
    await ok("memory_learn", { text: "t", kind: "other", confidence: 0.8 });
    expect(cortex.requests.at(-2)?.headers.get("authorization")).toBe("Bearer cortex-key-fake");
  });

  it("reports unreachable instead of failing a boot", async () => {
    cortex.down = true;
    expect(await configure()).toEqual({ status: "unreachable" });
  });

  it("rejects what is not cortexdb or not a URL", async () => {
    expect((await t.rpc("memory_engine_set", { engine: "other", endpoint: "http://x" })).error?.code).toBe(-32602);
    expect((await t.rpc("memory_engine_set", { engine: "cortexdb", endpoint: "ftp://x" })).error?.code).toBe(-32602);
    expect((await t.rpc("memory_engine_set", { engine: "cortexdb" })).error?.code).toBe(-32602);
    expect((await t.rpc("memory_engine_set", { engine: "cortexdb", endpoint: "http://x", api_key: 5 })).error?.code).toBe(-32602);
  });
});

describe("memory_learn, memory_items_list, memory_forget", () => {
  it("is not configured until memory_engine_set", async () => {
    for (const [m, p] of [["memory_learn", { text: "x" }], ["memory_items_list", { limit: 5 }], ["memory_forget", { ids: ["a"] }]] as const) {
      expect((await t.rpc(m, p)).error?.message).toMatch(/not configured/);
    }
  });

  it("memory_learn appends one observation event with its v3 envelope, and the item is listed back (contract shapes)", async () => {
    await configure();
    await ok("memory_learn", { text: "I learned x", kind: "other", confidence: 0.8 });
    const post = cortex.requests.find((r) => r.path === "/v1/experience");
    expect(post?.query).toEqual({ wait: "indexed" });
    expect(post?.headers.get("x-cortex-actor")).toBe("user:local");
    expect(post?.body).toMatchObject({
      scope: "app:tinymemory/app:learnings",
      modality: "observation",
      content: { kind: "message", role: "user", text: "I learned x" },
      idempotency_key: expect.stringMatching(/^tm3:[0-9a-f]{56}$/),
    });
    const labels = (post?.body?.context as { labels: string[] }).labels;
    const id = itemId("learning", "I learned x");
    expect(labels).toContain(`tm:i:${digest(id)}`);
    expect(labels.length).toBeLessThanOrEqual(64);
    for (const l of labels.filter((x) => x.startsWith("tm:e:"))) expect(Buffer.byteLength(l)).toBeLessThanOrEqual(240 + 6);

    const page = await ok("memory_items_list", { limit: 50, preview: true, filter: { kinds: ["learning"], observed_after: "2020-01-01T00:00:00Z" } });
    expect(page).toEqual({
      items: [{ id, kind: "learning", text: "I learned x", meta: { observed_at: expect.stringMatching(/^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d\.\d{9}Z$/) } }],
      next_cursor: null,
    });
  });

  it("learns the same words twice as one item (CortexDB replays the idempotent write)", async () => {
    await configure();
    await ok("memory_learn", { text: "same", kind: "other", confidence: 0.8 });
    const a = await ok("memory_items_list", { limit: 10, preview: true, filter: { kinds: ["learning"] } });
    expect((a.items as unknown[]).length).toBe(1);
  });

  it("validates memory_learn and defaults kind and confidence", async () => {
    await configure();
    expect((await t.rpc("memory_learn", { text: "" })).error?.code).toBe(-32602);
    expect((await t.rpc("memory_learn", {})).error?.code).toBe(-32602);
    await ok("memory_learn", { text: "no kind" });
    const env = decodeEvent(cortex.events[0] as Event);
    expect(env).toMatchObject({ kind: "learning", text: "no kind" });
    const parts = (cortex.events[0] as Event).context.labels.filter((l) => l.startsWith("tm:e:")).map((l) => l.slice(8)).join("");
    expect(JSON.parse(parts)).toMatchObject({ v: 3, learning_kind: "other", confidence: 0.8 });
  });

  it("lists newest first, deduped, after a time, and pages with a cursor that never skips or repeats", async () => {
    await configure();
    const t0 = new Date("2026-11-20T00:00:00Z");
    // write seven learnings with distinct times straight through the client
    const { CortexClient } = await import("../../src/memory/cortex.js");
    const client = new CortexClient({ endpoint: "http://cortex.test", fetch: cortex.fetch });
    for (let i = 0; i < 7; i++) await client.storeLearning({ text: `learning ${i}`, learningKind: "other", confidence: 0.5, now: new Date(t0.getTime() + i * 86_400_000) });
    cortex.pageSize = 4; // two distinct events a page, because every event is listed twice
    const seen: string[] = [];
    let cursor: string | undefined;
    for (let i = 0; i < 20; i++) {
      const p = (await ok("memory_items_list", { limit: 3, preview: true, filter: { kinds: ["learning"] }, ...(cursor ? { cursor } : {}) })) as { items: { text: string }[]; next_cursor: string | null };
      expect(p.items.length).toBeLessThanOrEqual(3);
      seen.push(...p.items.map((x) => x.text));
      if (!p.next_cursor) break;
      cursor = p.next_cursor;
    }
    expect(seen).toEqual([6, 5, 4, 3, 2, 1, 0].map((i) => `learning ${i}`));
    cortex.pageSize = 200;
    const after = await ok("memory_items_list", { limit: 50, preview: true, filter: { kinds: ["learning"], observed_after: "2026-11-23T12:00:00Z" } });
    expect((after.items as { text: string }[]).map((x) => x.text)).toEqual(["learning 6", "learning 5", "learning 4"]);
  });

  it("lists nothing for a kind it does not keep, and refuses a cursor it did not give", async () => {
    await configure();
    await ok("memory_learn", { text: "x" });
    expect(await ok("memory_items_list", { limit: 5, preview: true, filter: { kinds: ["document"] } })).toEqual({ items: [], next_cursor: null });
    expect((await t.rpc("memory_items_list", { limit: 5, cursor: "garbage!", filter: { kinds: ["learning"] } })).error?.message).toMatch(/cursor/);
  });

  it("reads learnings OpenHuman wrote: v2 envelopes and `[role] ` prefixed text, and ignores other events", async () => {
    await configure();
    const v2 = (id: string, text: string, prefix = "") => ({
      id: `evt_old_${id}`,
      scope: "app:tinymemory/app:learnings",
      content: { text: `${prefix}${JSON.stringify({ v: 2, id, kind: "learning", text, meta: { observed_at: "2026-11-01T10:00:00.5Z" }, learning_kind: "preference", confidence: 0.9 })}` },
      context: { labels: [`tm:i:${digest(id)}`] },
    });
    cortex.events.push(v2("old1", "an old learning") as Event, v2("old2", "prefixed", "[user] ") as Event);
    cortex.events.push({ id: "evt_x", scope: "app:tinymemory/app:learnings", content: { text: "someone else's event" }, context: { labels: [] } });
    const got = (await ok("memory_items_list", { limit: 10, preview: true, filter: { kinds: ["learning"] } })) as { items: { id: string; text: string }[] };
    expect(got.items.map((i) => [i.id, i.text])).toEqual([["old1", "an old learning"], ["old2", "prefixed"]]);
  });

  it("memory_forget removes the item's events by memory_ids with the cascade named, and says how many items went", async () => {
    await configure();
    await ok("memory_learn", { text: "keep" });
    await ok("memory_learn", { text: "drop" });
    const id = itemId("learning", "drop");
    const got = await ok("memory_forget", { ids: [id, "nope-not-here", id] });
    expect(got).toEqual({ forgotten: 1 });
    expect(cortex.forgot).toHaveLength(1);
    expect(cortex.forgot[0]?.body).toMatchObject({ scope: "app:tinymemory/app:learnings", layers: ["events"], cascade: "redact_events", selector: { memory_ids: expect.any(Array) } });
    expect(cortex.forgot[0]?.body).not.toHaveProperty("confirm_all");
    const left = (await ok("memory_items_list", { limit: 10, preview: true, filter: { kinds: ["learning"] } })) as { items: { text: string }[] };
    expect(left.items.map((i) => i.text)).toEqual(["keep"]);
    // a forget never reaches CortexDB with an empty selector (that would mean the whole scope)
    cortex.forgot.length = 0;
    expect(await ok("memory_forget", { ids: ["nope"] })).toEqual({ forgotten: 0 });
    expect(cortex.forgot).toEqual([]);
  });

  it("validates memory_forget", async () => {
    await configure();
    expect((await t.rpc("memory_forget", { ids: [] })).error?.code).toBe(-32602);
    expect((await t.rpc("memory_forget", { ids: ["  "] })).error?.code).toBe(-32602);
    expect((await t.rpc("memory_forget", { ids: [1] })).error?.code).toBe(-32602);
    expect((await t.rpc("memory_forget", {})).error?.code).toBe(-32602);
  });

  it("answers a JSON-RPC error, with no key in it, when CortexDB is down or refuses", async () => {
    await configure();
    cortex.down = true;
    const r = await t.rpc("memory_learn", { text: "x" });
    expect(r.error?.message).toMatch(/CortexDB did not answer/);
    expect(JSON.stringify(r)).not.toContain("cortex-key-fake");
    cortex.down = false;
    memoryHttp.fetch = (async () => new Response("denied: Bearer cortex-key-fake", { status: 401 })) as unknown as typeof fetch;
    const r2 = await t.rpc("memory_items_list", { limit: 5, filter: { kinds: ["learning"] } });
    expect(r2.error?.message).toMatch(/answered 401/);
  });

  it("serves the scope the operator names, and the contract's learnings scope by default", async () => {
    await configure();
    await ok("memory_learn", { text: "a" });
    expect(cortex.events[0]?.scope).toBe("app:tinymemory/app:learnings");
    const t2 = await startTestCore({ TEENY_MEMORY_SCOPE: "org:42/app:learnings" });
    try {
      await t2.rpc("memory_engine_set", { engine: "cortexdb", endpoint: "http://cortex.test" });
      await t2.rpc("memory_learn", { text: "b" });
      expect(cortex.events[0]?.scope).toBe("org:42/app:learnings");
    } finally {
      await t2.core.stop();
    }
  });
});

describe("cortex helpers", () => {
  it("digests as tinymemory does (the first 16 hex digits of SHA-256) and writes RFC3339 with nanoseconds", () => {
    expect(digest("abc")).toBe(createHash("sha256").update("abc").digest("hex").slice(0, 16));
    expect(itemId("learning", "x")).toHaveLength(40);
    expect(nanos(new Date("2026-11-27T01:02:03.123Z"))).toBe("2026-11-27T01:02:03.123000000Z");
    mkdirSync(join(t.stateDir, "x"), { recursive: true });
  });
});

describe("the contract fixtures (core/test/contract/memory_*.json)", () => {
  it("every fixture's example params are accepted and every answer matches its shape", async () => {
    const { assertMatchesContract, assertParamsMatchContract, loadContract } = await import("../contract.js");
    await configure();
    await ok("memory_learn", { text: "aaa", kind: "other", confidence: 0.8 });
    const listed = (await ok("memory_items_list", loadContract("memory_items_list").params.example as Record<string, unknown>)) as { items: { id: string }[] };
    const forgetParams = { ids: [listed.items[0]?.id ?? "aaa111"] };
    const calls: [string, Record<string, unknown>][] = [
      ["memory_engine_set", loadContract("memory_engine_set").params.example as Record<string, unknown>],
      ["memory_learn", loadContract("memory_learn").params.example as Record<string, unknown>],
      ["memory_items_list", loadContract("memory_items_list").params.example as Record<string, unknown>],
      ["memory_forget", forgetParams],
    ];
    for (const [method, params] of calls) {
      assertParamsMatchContract(method, params);
      const r = await t.rpc(method, params);
      expect(r.error, method).toBeUndefined();
      assertMatchesContract(method, r.result);
    }
    // the example the contract records for items_list holds ids as strings and observed_at with nanoseconds
    expect(loadContract("memory_items_list").response.example).toMatchObject({ items: [{ meta: { observed_at: expect.stringMatching(/\.\d{9}Z$/) } }] });
  });
});
