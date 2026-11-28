import { mkdirSync, mkdtempSync, readFileSync, readdirSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { describe, expect, it } from "vitest";
import plugin from "../../plugins/teeny-lanes/index.js";
import { globMatch, Ledger, laneOf, parseLanes, resolveModel, type LanesConfig } from "../../plugins/teeny-lanes/lanes.js";
import { laneModels, lanesPluginConfig, loadLanes, parseLanesToml } from "../../src/agents/lanes.js";
import { REPO } from "../helpers.js";

type Handler = (event: Record<string, unknown>, ctx: Record<string, unknown>) => unknown;
function register(config: unknown) {
  const hooks = new Map<string, Handler>();
  const logs: string[] = [];
  const api = {
    pluginConfig: config,
    registrationMode: "full",
    logger: { error: (m: string) => logs.push(m), warn: (m: string) => logs.push(m), info: (m: string) => logs.push(m), debug: () => undefined },
    on: (name: string, handler: Handler) => hooks.set(name, handler),
  };
  (plugin as unknown as { register: (api: unknown) => void }).register(api);
  return { hooks, logs };
}

function config(over: Partial<LanesConfig> = {}): LanesConfig {
  return {
    provider: "local-openai",
    ledgerDir: mkdtempSync(join(tmpdir(), "lanes-")),
    lanes: [
      { id: "locked", model: "glm-5.3-flash", fallbacks: [], match: ["agent:x-reply:*"], dailyTokens: 1000, threadDailyTokens: 300 },
      { id: "free", model: "kimi-k3", fallbacks: ["glm-5.3-flash"], match: ["agent:teeny:teeny:dream*", "agent:evolver:evolve:*"] },
      { id: "wake", match: ["agent:mind:*"] },
    ],
    ...over,
  };
}

const rows = (dir: string) =>
  readdirSync(dir).flatMap((f) => readFileSync(join(dir, f), "utf8").trim().split("\n").map((l) => JSON.parse(l) as Record<string, unknown>));

describe("lane matching", () => {
  it("globs over the whole key, `*` crossing colons, and nothing else a wildcard", () => {
    expect(globMatch("agent:x-reply:*", "agent:x-reply:x:123")).toBe(true);
    expect(globMatch("agent:teeny:teeny:dream*", "agent:teeny:teeny:dream")).toBe(true);
    expect(globMatch("agent:evolver:evolve:*", "agent:evolver:evolve-review:probe:1")).toBe(false);
    expect(globMatch("agent:teeny:teeny:mind", "agent:teeny:teeny:mind:x")).toBe(false);
    expect(globMatch("a*b*c", "a-b-b-c")).toBe(true);
    expect(globMatch("a*bc", "abc")).toBe(true);
    expect(globMatch("ab*bc", "abc")).toBe(false);
  });

  it("takes the first lane in file order, and none for an unclaimed or missing key", () => {
    const cfg = config({ lanes: [{ id: "a", match: ["agent:mind:*"] }, { id: "b", match: ["agent:*"] }] });
    expect(laneOf(cfg, "agent:mind:cron:teeny.mind")?.id).toBe("a");
    expect(laneOf(cfg, "agent:teeny:teeny:mind")?.id).toBe("b");
    expect(laneOf(cfg, "cron:x")).toBeUndefined();
    expect(laneOf(cfg, undefined)).toBeUndefined();
  });
});

describe("before_model_resolve", () => {
  it("puts a stranger's X thread on GLM with no fallback, so it never runs on a freer model", async () => {
    const { hooks } = register(config());
    const out = await hooks.get("before_model_resolve")!({ prompt: "hi" }, { sessionKey: "agent:x-reply:x:1999", agentId: "x-reply" });
    expect(out).toEqual({ modelOverride: "glm-5.3-flash", providerOverride: "local-openai", fallbacksOverride: [] });
  });

  it("puts dreams and evolve candidates on Kimi, falling back to GLM under the provider", () => {
    const cfg = config();
    expect(resolveModel(cfg, "agent:teeny:teeny:dream")).toEqual({ modelOverride: "kimi-k3", providerOverride: "local-openai", fallbacksOverride: ["local-openai/glm-5.3-flash"] });
    expect(resolveModel(cfg, "agent:evolver:evolve:12:3:abcd1234")?.modelOverride).toBe("kimi-k3");
  });

  it("leaves the agent's own model alone for a lane without one, and for a session no lane claims", () => {
    const cfg = config();
    expect(resolveModel(cfg, "agent:mind:cron:teeny.mind")).toBeUndefined();
    expect(resolveModel(cfg, "agent:teeny:teeny:mind")).toBeUndefined();
    expect(resolveModel(cfg, "agent:evolver:evolve-review:probe:1")).toBeUndefined();
  });

  it("with a config it cannot read, overrides nothing, logs nothing, and says so", async () => {
    const { hooks, logs } = register({ provider: "local-openai", lanes: [] });
    expect(logs.join("\n")).toContain("models are left alone");
    expect(await hooks.get("before_model_resolve")!({ prompt: "p" }, { sessionKey: "agent:x-reply:x:1" })).toBeUndefined();
    expect(await hooks.get("llm_output")!({ sessionId: "s", provider: "p", model: "m", usage: { total: 5 } }, { sessionKey: "agent:x-reply:x:1" })).toBeUndefined();
  });
});

describe("the ledger (log only)", () => {
  it("writes one 0600 line per model call with the lane, the session and the day's totals", async () => {
    const cfg = config();
    const { hooks } = register(cfg);
    const ctx = { sessionKey: "agent:x-reply:x:1", agentId: "x-reply" };
    await hooks.get("llm_output")!({ sessionId: "s", provider: "local-openai", model: "glm-5.3-flash", usage: { input: 80, output: 20, cacheRead: 5, total: 100 } }, ctx);
    await hooks.get("llm_output")!({ sessionId: "s", provider: "local-openai", model: "glm-5.3-flash", resolvedRef: "local-openai/glm-5.3-flash", usage: { input: 40, output: 10 } }, ctx);
    const [file] = readdirSync(cfg.ledgerDir);
    expect(file).toMatch(/^usage-\d{4}-\d{2}-\d{2}\.jsonl$/);
    expect((await import("node:fs")).statSync(join(cfg.ledgerDir, file as string)).mode & 0o777).toBe(0o600);
    const [a, b] = rows(cfg.ledgerDir);
    expect(a).toMatchObject({ lane: "locked", session: "agent:x-reply:x:1", agent: "x-reply", model: "local-openai/glm-5.3-flash", input: 80, output: 20, cacheRead: 5, total: 100, laneDay: 100, threadDay: 100 });
    expect(b).toMatchObject({ total: 50, laneDay: 150, threadDay: 150 });
  });

  it("logs a session no lane claims under `other`, and a cron run with no key under its job", async () => {
    const cfg = config();
    const { hooks } = register(cfg);
    await hooks.get("llm_output")!({ sessionId: "s", provider: "p", model: "m", usage: { total: 7 } }, { sessionKey: "agent:teeny:teeny:mind" });
    await hooks.get("llm_output")!({ sessionId: "s", provider: "p", model: "m", usage: { total: 3 } }, { jobId: "teeny.dream" });
    expect(rows(cfg.ledgerDir).map((r) => [r.lane, r.session])).toEqual([["other", "agent:teeny:teeny:mind"], ["other", "cron:teeny.dream"]]);
  });

  it("marks and warns the first call past a thread's or a lane's cap once a day, and blocks nothing", async () => {
    const cfg = config();
    const { hooks, logs } = register(cfg);
    const call = (session: string, total: number) => hooks.get("llm_output")!({ sessionId: "s", provider: "p", model: "m", usage: { total } }, { sessionKey: session });
    expect(await call("agent:x-reply:x:1", 250)).toBeUndefined();
    await call("agent:x-reply:x:1", 100); // thread 350 > 300
    await call("agent:x-reply:x:1", 100); // already warned
    await call("agent:x-reply:x:2", 600); // lane 1050 > 1000, thread 600 > 300
    await call("agent:x-reply:x:2", 10);
    expect(rows(cfg.ledgerDir).map((r) => r.over ?? [])).toEqual([[], ["thread"], [], ["lane", "thread"], []]);
    expect(logs.filter((l) => l.includes("past its daily cap"))).toHaveLength(3);
    expect(logs.join("\n")).toContain("logged only");
  });

  it("rebuilds the day's totals and warnings from its file after a restart", () => {
    const cfg = config();
    const now = Date.UTC(2026, 10, 28, 12);
    new Ledger(cfg).record({ ts: now, session: "agent:x-reply:x:1", total: 350 });
    const again = new Ledger(cfg);
    const row = again.record({ ts: now + 1000, session: "agent:x-reply:x:1", total: 10 });
    expect(row).toMatchObject({ threadDay: 360, laneDay: 360 });
    expect(row.over).toBeUndefined();
    const tomorrow = again.record({ ts: now + 86_400_000, session: "agent:x-reply:x:1", total: 10 });
    expect(tomorrow).toMatchObject({ threadDay: 10, laneDay: 10 });
  });

  it("skips a torn line instead of failing", () => {
    const cfg = config();
    const now = Date.UTC(2026, 10, 28, 12);
    writeFileSync(join(cfg.ledgerDir, "usage-2026-11-28.jsonl"), '{"lane":"locked","session":"agent:x-reply:x:1","total":5}\n{"lane":"lo');
    expect(new Ledger(cfg).record({ ts: now, session: "agent:x-reply:x:1", total: 1 }).threadDay).toBe(6);
    // the new row starts on its own line, so a restart still finds it
    expect(new Ledger(cfg).record({ ts: now, session: "agent:x-reply:x:1", total: 1 }).threadDay).toBe(7);
  });

  it("keeps a row recoverable when it follows a torn last line", () => {
    const cfg = config();
    const now = Date.UTC(2026, 10, 28, 12);
    writeFileSync(join(cfg.ledgerDir, "usage-2026-11-28.jsonl"), '{"lane":"locked","session":"agent:x-reply:x:1","total":5}\n{"lane":"lo');
    new Ledger(cfg).record({ ts: now, session: "agent:x-reply:x:1", total: 350 });
    const reloaded = new Ledger(cfg).record({ ts: now + 1, session: "agent:x-reply:x:1", total: 1 });
    expect(reloaded.threadDay).toBe(356);
    expect(reloaded.over).toBeUndefined(); // the cap warning saved in the row survived
  });

  it("counts nothing and warns nothing for a row it could not write, then recovers", () => {
    const good = mkdtempSync(join(tmpdir(), "lanes-"));
    const blocker = join(good, "blocker");
    writeFileSync(blocker, "x");
    const cfg = config({ ledgerDir: join(blocker, "ledger") }); // a file where the directory should be
    const ledger = new Ledger(cfg);
    const now = Date.UTC(2026, 10, 28, 12);
    expect(() => ledger.record({ ts: now, session: "agent:x-reply:x:1", total: 350 })).toThrow();
    (cfg as { ledgerDir: string }).ledgerDir = good;
    const row = ledger.record({ ts: now + 1, session: "agent:x-reply:x:1", total: 10 });
    expect(row).toMatchObject({ threadDay: 10, laneDay: 10 });
    const over = ledger.record({ ts: now + 2, session: "agent:x-reply:x:1", total: 350 });
    expect(over.over).toEqual(["thread"]);
  });

  it("retries the day's file when reading it failed", () => {
    const cfg = config();
    const now = Date.UTC(2026, 10, 28, 12);
    mkdirSync(join(cfg.ledgerDir, "usage-2026-11-28.jsonl")); // a directory: readFileSync throws EISDIR
    const ledger = new Ledger(cfg);
    expect(() => ledger.record({ ts: now, session: "agent:x-reply:x:1", total: 1 })).toThrow();
    rmSync(join(cfg.ledgerDir, "usage-2026-11-28.jsonl"), { recursive: true });
    writeFileSync(join(cfg.ledgerDir, "usage-2026-11-28.jsonl"), '{"lane":"locked","session":"agent:x-reply:x:1","total":5}\n');
    expect(ledger.record({ ts: now, session: "agent:x-reply:x:1", total: 1 }).threadDay).toBe(6);
  });
});

describe("parsing", () => {
  it("refuses what it cannot trust in the plugin's config", () => {
    const ok = config();
    expect(() => parseLanes({ ...ok, lanes: [{ id: "a", match: [] }] })).toThrow(/match/);
    expect(() => parseLanes({ ...ok, lanes: [{ id: "other", match: ["x"] }] })).toThrow(/other/);
    expect(() => parseLanes({ ...ok, lanes: [{ id: "a", match: ["x"] }, { id: "a", match: ["y"] }] })).toThrow(/twice/);
    expect(() => parseLanes({ ...ok, lanes: [{ id: "a", fallbacks: [], match: ["x"] }] })).toThrow(/needs a model/);
    expect(() => parseLanes({ ...ok, lanes: [{ id: "a", model: "bad model", match: ["x"] }] })).toThrow(/model/);
    expect(() => parseLanes({ ...ok, lanes: [{ id: "a", match: ["x"], dailyTokens: -1 }] })).toThrow(/positive/);
  });

  it("reads lanes.toml into the plugin's shape, in order", () => {
    const lanes = parseLanesToml('[[lane]]\nid = "free"\nmodel = "kimi-k3"\nfallbacks = ["glm-5.3-flash"]\nmatch = ["agent:mind:*"]\ndaily_tokens = 1_000\nthread_daily_tokens = 10\n[[lane]]\nid = "wake"\nmatch = ["x"]\n');
    expect(lanes).toEqual([
      { id: "free", model: "kimi-k3", fallbacks: ["glm-5.3-flash"], match: ["agent:mind:*"], dailyTokens: 1000, threadDailyTokens: 10 },
      { id: "wake", match: ["x"] },
    ]);
    expect(laneModels(lanes)).toEqual(["kimi-k3", "glm-5.3-flash"]);
    expect(parseLanes(lanesPluginConfig(lanes, "local-openai", "/srv/state")).ledgerDir).toBe("/srv/state/lanes");
    expect(() => parseLanesToml('lane = "x"')).toThrow(/array of tables/);
    expect(parseLanesToml("")).toEqual([]);
  });

  it("brain/lanes.toml parses, the plugin accepts it, and it routes the sessions it is meant to", () => {
    const lanes = loadLanes(join(REPO, "brain"));
    const cfg = parseLanes(lanesPluginConfig(lanes, "local-openai", "/tmp/x"));
    expect(resolveModel(cfg, "agent:x-reply:x:1")).toMatchObject({ modelOverride: "glm-5.3-flash", fallbacksOverride: [] });
    expect(resolveModel(cfg, "agent:teeny:teeny:dream")?.modelOverride).toBe("kimi-k3");
    expect(resolveModel(cfg, "agent:teeny:teeny:reflect")?.modelOverride).toBe("kimi-k3");
    expect(resolveModel(cfg, "agent:evolver:evolve:3:1:abcd1234")?.modelOverride).toBe("kimi-k3");
    expect(resolveModel(cfg, "agent:evolver:evolve-review:probe:abcd")).toBeUndefined();
    // The mind's model is its pin in brain/agents/mind.toml, not the lane's.
    expect(laneOf(cfg, "agent:mind:cron:teeny.mind")?.id).toBe("wake");
    expect(resolveModel(cfg, "agent:mind:cron:teeny.mind")).toBeUndefined();
    expect(laneOf(cfg, "agent:teeny:teeny:mind")?.id).toBe("home");
  });
});
