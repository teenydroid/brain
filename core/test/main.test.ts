import { EventEmitter } from "node:events";
import { chmodSync, existsSync, mkdtempSync, mkdirSync, readFileSync, rmSync, statSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { describe, expect, it, vi } from "vitest";
import { cronShellTimeoutSecs, portOpen, run, startCore, wireCron } from "../src/main.js";
import { methods as cronMethods, setShellTimeoutSecs } from "../src/cron/index.js";
import { resolvePaths, serveSettings } from "../src/config/paths.js";
import { resolveToken } from "../src/config/token.js";
import { createServer } from "node:net";
import { FAKE_KEY, REPO, startTestCore } from "./helpers.js";

class QuietChild extends EventEmitter {
  pid = 1;
  exitCode: number | null = null;
  killed = false;
  stdout = new EventEmitter();
  stderr = new EventEmitter();
  kill(): boolean {
    this.exitCode = 0;
    this.emit("exit", 0, null);
    return true;
  }
}

describe("main", () => {
  it("anything but `serve` prints usage and exits 2", async () => {
    const write = vi.spyOn(process.stderr, "write").mockReturnValue(true);
    expect(await run([])).toBe(2);
    expect(await run(["frobnicate"])).toBe(2);
    expect(String(write.mock.calls[0]?.[0])).toContain("usage: teeny-core serve");
    write.mockRestore();
  });

  it("serve without a token fails before it listens", async () => {
    const root = mkdtempSync(join(tmpdir(), "teeny-core-"));
    const write = vi.spyOn(process.stderr, "write").mockReturnValue(true);
    expect(await run(["serve"], { TEENY_ROOT: root, OPENHUMAN_CORE_PORT: "0" })).toBe(1);
    expect(String(write.mock.calls[0]?.[0])).toMatch(/no core token/);
    write.mockRestore();
  });
});

describe("config", () => {
  it("serves on 127.0.0.1:7788 by default and takes OPENHUMAN_CORE_HOST and _PORT", () => {
    expect(serveSettings({})).toEqual({ host: "127.0.0.1", port: 7788 });
    expect(serveSettings({ OPENHUMAN_CORE_HOST: "0.0.0.0", OPENHUMAN_CORE_PORT: "9000" })).toEqual({ host: "0.0.0.0", port: 9000 });
    expect(() => serveSettings({ OPENHUMAN_CORE_PORT: "nope" })).toThrow(/not a port/);
  });

  it("finds brain, state and secrets where bin/lib/teeny.sh puts them, and honours the overrides", () => {
    expect(resolvePaths({ TEENY_ROOT: "/srv/teeny" })).toMatchObject({ brainDir: "/srv/teeny/brain", stateDir: "/srv/teeny/state", secretsDir: "/srv/teeny/secrets", openclawDir: "/srv/teeny/state/openclaw" });
    expect(resolvePaths({ TEENY_ROOT: "/r", OPENHUMAN_WORKSPACE: "/b", TEENY_STATE: "/s", TEENY_SECRETS: "/k" })).toMatchObject({ brainDir: "/b", stateDir: "/s", secretsDir: "/k", openclawDir: "/s/openclaw" });
  });

  it("takes the token from the environment first, then secrets/core.token, whitespace stripped", () => {
    const dir = mkdtempSync(join(tmpdir(), "sec-"));
    writeFileSync(join(dir, "core.token"), "from-file\n");
    expect(resolveToken({ OPENHUMAN_CORE_TOKEN: " from-env\n" }, dir)).toBe("from-env");
    expect(resolveToken({}, dir)).toBe("from-file");
    expect(() => resolveToken({}, join(dir, "none"))).toThrow(/no core token/);
  });
});

describe("startCore", () => {
  it("writes the Gateway config 0600 and the agents' workspaces under state/openclaw, and never the tokens", async () => {
    const t = await startTestCore({ LOCAL_OPENAI_URL: "http://127.0.0.1:18080/v1", SPINE_MODEL: "glm-5.3-flash" });
    try {
      await t.rpc("config_update_agent_settings", { agent_timeout_secs: 1800 });
      await new Promise((r) => setTimeout(r, 700)); // the debounced config write
      const file = join(t.stateDir, "openclaw", "openclaw.json");
      expect(statSync(file).mode & 0o777).toBe(0o600);
      const text = readFileSync(file, "utf8");
      expect(JSON.parse(text).agents.defaults.timeoutSeconds).toBe(1800);
      expect(text).not.toContain(t.token);
      expect(statSync(join(t.stateDir, "openclaw", "workspaces", "teeny", "AGENTS.md")).isFile()).toBe(true);
    } finally {
      await t.core.stop();
    }
  });

  it("starts the Gateway child with the loopback bind, the state dir, the config path and a random token, and none of the core's own secrets", async () => {
    const root = mkdtempSync(join(tmpdir(), "teeny-core-"));
    mkdirSync(join(root, "secrets"));
    const spawned: { args: string[]; env: Record<string, string> }[] = [];
    const core = await startCore(
      { PATH: "/usr/bin", TEENY_ROOT: root, OPENHUMAN_WORKSPACE: join(root, "brain"), OPENHUMAN_CORE_PORT: "0", OPENHUMAN_CORE_TOKEN: "core-token-xyz", OPENHUMAN_KEYRING_MASTER_KEY_FILE: "/nonexistent" },
      {
        log: () => undefined,
        spawn: (_cmd, args, opts) => {
          spawned.push({ args, env: opts.env as Record<string, string> });
          return new QuietChild() as never;
        },
      },
    );
    try {
      expect(spawned).toHaveLength(1);
      expect(spawned[0]!.args).toEqual(expect.arrayContaining(["gateway", "run", "--bind", "loopback"]));
      const env = spawned[0]!.env;
      expect(env.OPENCLAW_STATE_DIR).toBe(join(root, "state", "openclaw"));
      expect(env.OPENCLAW_CONFIG_PATH).toBe(join(root, "state", "openclaw", "openclaw.json"));
      // The Gateway's exec tool hands its environment to every command an agent runs:
      // neither the token nor the model key may be in it. They are SecretRef files.
      expect(env).not.toHaveProperty("OPENCLAW_GATEWAY_TOKEN");
      expect(env).not.toHaveProperty("LOCAL_OPENAI_API_KEY");
      const config = JSON.parse(readFileSync(join(root, "state", "openclaw", "openclaw.json"), "utf8"));
      expect(config.gateway.auth.token).toEqual({ source: "file", provider: "teeny_gateway_token", id: "value" });
      expect(config.secrets.providers.teeny_gateway_token).toMatchObject({ source: "file", mode: "singleValue" });
      expect(readFileSync(config.secrets.providers.teeny_gateway_token.path, "utf8").trim()).toMatch(/^[0-9a-f]{64}$/);
      expect(statSync(config.secrets.providers.teeny_gateway_token.path).mode & 0o777).toBe(0o600);
      expect(statSync(config.secrets.providers.teeny_model_key.path).mode & 0o777).toBe(0o600);
      expect(Object.values(env)).not.toContain("core-token-xyz");
      expect(Object.keys(env).some((k) => k.startsWith("OPENHUMAN_"))).toBe(false);
    } finally {
      await core.stop();
    }
  });
});

describe("the Gateway's exec stays denied until the plugin is proven loaded", () => {
  it("starts on a config that denies exec, opens it once the ready file carries the Gateway's pid, and ignores a file from another process", async () => {
    const root = mkdtempSync(join(tmpdir(), "teeny-core-"));
    mkdirSync(join(root, "secrets"));
    mkdirSync(join(root, "core", "plugins", "teeny-guardrails"), { recursive: true });
    const ready = join(root, "state", "openclaw", "guardrails.ready");
    const configFile = join(root, "state", "openclaw", "openclaw.json");
    const execModes = (): string[] => {
      const entries = JSON.parse(readFileSync(configFile, "utf8")).agents.entries as Record<string, { tools: { exec?: { security: string } } }>;
      return Object.values(entries).flatMap((e) => (e.tools.exec ? [e.tools.exec.security] : []));
    };
    const core = await startCore(
      { PATH: "/usr/bin", TEENY_ROOT: root, OPENHUMAN_WORKSPACE: join(REPO, "brain"), OPENHUMAN_CORE_PORT: "0", OPENHUMAN_CORE_TOKEN: "t", OPENHUMAN_KEYRING_MASTER_KEY_FILE: "/nonexistent" },
      { log: () => undefined, spawn: () => new QuietChild() as never },
    );
    try {
      expect(execModes().length).toBeGreaterThan(0);
      expect(execModes().every((m) => m === "deny")).toBe(true);
      writeFileSync(ready, JSON.stringify({ pid: 4242 })); // not the Gateway (pid 1 here)
      await new Promise((r) => setTimeout(r, 400));
      expect(execModes().every((m) => m === "deny")).toBe(true);
      writeFileSync(ready, JSON.stringify({ pid: 1 }));
      await vi.waitFor(() => expect(execModes().every((m) => m === "full")).toBe(true), { timeout: 3000 });
    } finally {
      await core.stop();
      rmSync(root, { recursive: true, force: true });
    }
  });

  it("never writes exec open beside a change the Gateway must restart for, even while the core drains turns before that restart", async () => {
    const root = mkdtempSync(join(tmpdir(), "teeny-core-"));
    mkdirSync(join(root, "secrets"));
    mkdirSync(join(root, "core", "plugins", "teeny-guardrails"), { recursive: true });
    const ready = join(root, "state", "openclaw", "guardrails.ready");
    const configFile = join(root, "state", "openclaw", "openclaw.json");
    const execModes = (): string[] => {
      const entries = JSON.parse(readFileSync(configFile, "utf8")).agents.entries as Record<string, { tools: { exec?: { security: string } } }>;
      return Object.values(entries).flatMap((e) => (e.tools.exec ? [e.tools.exec.security] : []));
    };
    let finish!: () => void;
    const slow = { run: () => new Promise<{ text: string }>((r) => (finish = () => r({ text: "done" }))) };
    const children: QuietChild[] = [];
    const core = await startCore(
      { PATH: "/usr/bin", TEENY_ROOT: root, OPENHUMAN_WORKSPACE: join(REPO, "brain"), OPENHUMAN_CORE_PORT: "0", OPENHUMAN_CORE_TOKEN: "tok", OPENHUMAN_KEYRING_MASTER_KEY_FILE: "/nonexistent", LOCAL_OPENAI_URL: "http://127.0.0.1:1/v1", SPINE_MODEL: "m" },
      { log: () => undefined, runner: slow, drainMs: 2000, settleMs: 100, bootSettleMs: 100, spawn: () => { const c = new QuietChild(); children.push(c); return c as never; } },
    );
    const rpc = async (method: string, params: Record<string, unknown>) =>
      (await fetch(`http://127.0.0.1:${core.port}/rpc`, { method: "POST", headers: { "content-type": "application/json", authorization: "Bearer tok" }, body: JSON.stringify({ jsonrpc: "2.0", id: 1, method: `openhuman.${method}`, params }) })).json();
    try {
      writeFileSync(ready, JSON.stringify({ pid: 1 }));
      await vi.waitFor(() => expect(execModes().every((m) => m === "full")).toBe(true), { timeout: 3000 });
      await new Promise((r) => setTimeout(r, 400)); // past the boot settle
      const started = children.length;
      const turn = rpc("inference_agent_chat", { message: "hi", agent_id: "simple" });
      await new Promise((r) => setTimeout(r, 100));
      rmSync(ready, { force: true });
      // A new turn budget is in the Gateway's config. Under reload mode "hybrid" the Gateway may restart itself for
      // it, so the file must say "deny" from the write on, through the drain, until the core's own restart is proven.
      await rpc("config_update_agent_settings", { agent_timeout_secs: 4444 });
      expect(execModes().every((m) => m === "deny")).toBe(true);
      await new Promise((r) => setTimeout(r, 600)); // past the settle: the core is draining the turn now
      expect(children).toHaveLength(started);
      expect(execModes().every((m) => m === "deny")).toBe(true);
      finish();
      await turn;
      await vi.waitFor(() => expect(children.length).toBe(started + 1), { timeout: 3000 });
      expect(execModes().every((m) => m === "deny")).toBe(true);
      writeFileSync(ready, JSON.stringify({ pid: 1 }));
      await vi.waitFor(() => expect(execModes().every((m) => m === "full")).toBe(true), { timeout: 3000 });
    } finally {
      await core.stop();
      rmSync(root, { recursive: true, force: true });
    }
  });

  it("does not leave the model meter listening when the core fails to start", async () => {
    const root = mkdtempSync(join(tmpdir(), "teeny-core-"));
    mkdirSync(join(root, "secrets"));
    mkdirSync(join(root, "core", "plugins", "teeny-guardrails"), { recursive: true });
    const free = async (): Promise<{ port: number; srv: ReturnType<typeof createServer> }> => {
      const srv = createServer().listen(0, "127.0.0.1");
      await new Promise((r) => srv.once("listening", r));
      return { port: (srv.address() as { port: number }).port, srv };
    };
    const taken = await free(); // the RPC port is in use, so listen fails after the meter is up
    const meterSlot = await free();
    const meterPort = meterSlot.port;
    await new Promise((r) => meterSlot.srv.close(r));
    try {
      await expect(
        startCore(
          { PATH: "/usr/bin", TEENY_ROOT: root, OPENHUMAN_WORKSPACE: join(REPO, "brain"), OPENHUMAN_CORE_PORT: String(taken.port), OPENHUMAN_CORE_TOKEN: "tok", OPENHUMAN_KEYRING_MASTER_KEY_FILE: "/nonexistent", LOCAL_OPENAI_URL: "http://127.0.0.1:1/v1", SPINE_MODEL: "m", TEENY_METER_PORT: String(meterPort) },
          { log: () => undefined, drainMs: 100, settleMs: 100, bootSettleMs: 100, spawn: () => new QuietChild() as never },
        ),
      ).rejects.toThrow();
      expect(await portOpen(meterPort)).toBe(false);
    } finally {
      await new Promise((r) => taken.srv.close(r));
      rmSync(root, { recursive: true, force: true });
    }
  });

  it("portOpen says whether something listens", async () => {
    const srv = createServer().listen(0, "127.0.0.1");
    await new Promise((r) => srv.once("listening", r));
    const port = (srv.address() as { port: number }).port;
    expect(await portOpen(port)).toBe(true);
    await new Promise((r) => srv.close(r));
    expect(await portOpen(port)).toBe(false);
  });
});

describe("DRY_RUN and the Gateway's environment", () => {
  async function gatewayEnvFor(dry: string | undefined): Promise<Record<string, string>> {
    const root = mkdtempSync(join(tmpdir(), "teeny-core-"));
    mkdirSync(join(root, "secrets"));
    let seen: Record<string, string> = {};
    const core = await startCore(
      {
        PATH: "/usr/bin", TEENY_ROOT: root, OPENHUMAN_WORKSPACE: join(root, "brain"), OPENHUMAN_CORE_PORT: "0", OPENHUMAN_CORE_TOKEN: "t",
        OPENHUMAN_KEYRING_MASTER_KEY_FILE: "/nonexistent", ...(dry === undefined ? {} : { DRY_RUN: dry }),
      },
      { log: () => undefined, spawn: (_c, _a, opts) => { seen = opts.env as Record<string, string>; return new QuietChild() as never; } },
    );
    await core.stop();
    return seen;
  }

  it("forwards a forced rehearsal to the hands the agents' shells start", async () => {
    expect((await gatewayEnvFor("1")).DRY_RUN).toBe("1");
    expect((await gatewayEnvFor("true")).DRY_RUN).toBe("1");
  });

  it("never forwards DRY_RUN=0 or an unset one: the environment can only turn dry-run on", async () => {
    expect(await gatewayEnvFor("0")).not.toHaveProperty("DRY_RUN");
    expect(await gatewayEnvFor(undefined)).not.toHaveProperty("DRY_RUN");
  });
});

describe("a settings change and the Gateway", () => {
  async function boot(runner: import("../src/gateway/client.js").AgentRunner) {
    const root = mkdtempSync(join(tmpdir(), "teeny-core-"));
    mkdirSync(join(root, "secrets"));
    const children: QuietChild[] = [];
    const core = await startCore(
      { PATH: "/usr/bin", TEENY_ROOT: root, OPENHUMAN_WORKSPACE: join(root, "brain"), OPENHUMAN_CORE_PORT: "0", OPENHUMAN_CORE_TOKEN: "tok", OPENHUMAN_KEYRING_MASTER_KEY_FILE: "/nonexistent", LOCAL_OPENAI_URL: "http://127.0.0.1:1/v1", SPINE_MODEL: "m" },
      { log: () => undefined, runner, drainMs: 300, settleMs: 100, bootSettleMs: 100, spawn: () => { const c = new QuietChild(); children.push(c); return c as never; } },
    );
    const rpc = async (method: string, params: Record<string, unknown>) =>
      (await (await fetch(`http://127.0.0.1:${core.port}/rpc`, { method: "POST", headers: { "content-type": "application/json", authorization: "Bearer tok" }, body: JSON.stringify({ jsonrpc: "2.0", id: 1, method: `openhuman.${method}`, params }) })).json()) as { error?: unknown };
    return { core, children, rpc };
  }
  const idle = { run: async () => ({ text: "x" }) };

  it("does not restart the Gateway when nothing it reads changed", async () => {
    const t = await boot(idle);
    try {
      await t.rpc("config_update_agent_settings", { agent_timeout_secs: 1800 });
      await new Promise((r) => setTimeout(r, 1200));
      expect(t.children).toHaveLength(2); // the turn budget is in the Gateway's config: that one restarts it
      await t.rpc("config_update_agent_settings", { agent_timeout_secs: 1800 }); // the same value again
      await new Promise((r) => setTimeout(r, 1200));
      expect(t.children).toHaveLength(2);
    } finally {
      await t.core.stop();
    }
  });

  it("waits for a turn in flight before it restarts the Gateway, and holds new turns meanwhile", async () => {
    let finish!: () => void;
    const slow = { run: () => new Promise<{ text: string }>((r) => (finish = () => r({ text: "done" }))) };
    const t = await boot(slow);
    try {
      const turn = t.rpc("inference_agent_chat", { message: "hi", agent_id: "simple" });
      await new Promise((r) => setTimeout(r, 100));
      await t.rpc("config_update_agent_settings", { agent_timeout_secs: 2222 });
      await new Promise((r) => setTimeout(r, 1200)); // past the debounce and the 300 ms drain deadline
      // The deadline passed with the turn still running: it restarts anyway, but only after waiting.
      expect(t.children).toHaveLength(2);
      finish();
      await turn;
    } finally {
      await t.core.stop();
    }
  });

  it("restarts at once when no turn is running", async () => {
    const t = await boot(idle);
    try {
      await t.rpc("config_update_agent_settings", { agent_timeout_secs: 3333 });
      await new Promise((r) => setTimeout(r, 1200));
      expect(t.children).toHaveLength(2);
    } finally {
      await t.core.stop();
    }
  });
});

describe("the boot burst of settings calls", () => {
  const idle = { run: async () => ({ text: "x" }) };
  const sleep = (ms: number): Promise<void> => new Promise((r) => setTimeout(r, ms));
  /** What bin/spine sends at boot, in its order, with a gap between the calls longer than the steady-state debounce. */
  const bootCalls: Array<[string, Record<string, unknown>]> = [
    ["config_update_agent_settings", { agent_timeout_secs: 4000 }],
    ["config_update_local_ai_settings", { runtime_enabled: true, opt_in_confirmed: true, native_tool_calling: true, api_key: "fake-key" }],
    ["config_update_model_settings", Object.fromEntries(["default_model", "chat_provider", "reasoning_provider", "agentic_provider", "coding_provider", "vision_provider", "memory_provider"].map((k) => [k, k === "default_model" ? "m" : "local-openai:m"]))],
    ["inference_update_model_settings", { model_registry: [{ id: "m", provider: "local-openai", context_window: 128000 }] }],
    ["auth_store_provider_credentials", { provider: "provider:typesafe", token: "fake", setActive: true }],
    ["memory_engine_set", { engine: "cortexdb", endpoint: "http://127.0.0.1:1" }],
  ];

  async function bootAt(root: string, settings: { gapMs?: number } = {}) {
    mkdirSync(join(root, "secrets"), { recursive: true });
    const keyFile = join(root, "secrets", "openhuman.key");
    if (!existsSync(keyFile)) writeFileSync(keyFile, `${FAKE_KEY}\n`, { mode: 0o600 });
    const children: QuietChild[] = [];
    const umasks: number[] = [];
    const core = await startCore(
      { PATH: "/usr/bin", TEENY_ROOT: root, OPENHUMAN_WORKSPACE: join(root, "brain"), OPENHUMAN_CORE_PORT: "0", OPENHUMAN_CORE_TOKEN: "tok", OPENHUMAN_KEYRING_MASTER_KEY_FILE: keyFile, LOCAL_OPENAI_URL: "http://127.0.0.1:1/v1", SPINE_MODEL: "m" },
      {
        log: () => undefined, runner: idle, drainMs: 100, settleMs: 50, bootSettleMs: 500, bootWindowMs: 60_000,
        spawn: () => { umasks.push(process.umask()); const c = new QuietChild(); children.push(c); return c as never; },
      },
    );
    const rpc = async (method: string, params: Record<string, unknown>) =>
      (await (await fetch(`http://127.0.0.1:${core.port}/rpc`, { method: "POST", headers: { "content-type": "application/json", authorization: "Bearer tok" }, body: JSON.stringify({ jsonrpc: "2.0", id: 1, method: `openhuman.${method}`, params }) })).json()) as { error?: unknown };
    const sendAll = async (): Promise<void> => {
      for (const [m, p] of bootCalls) {
        expect((await rpc(m, p)).error).toBeUndefined();
        await sleep(settings.gapMs ?? 150);
      }
    };
    return { core, children, umasks, sendAll };
  }

  it("restarts the Gateway once for the whole burst, though its calls come further apart than the steady-state debounce", async () => {
    const t = await bootAt(mkdtempSync(join(tmpdir(), "teeny-core-")));
    try {
      await t.sendAll();
      await sleep(1300);
      expect(t.children).toHaveLength(2); // the one start and one restart
    } finally {
      await t.core.stop();
    }
  });

  it("does not restart it at all when the same settings come again on the next boot", async () => {
    const root = mkdtempSync(join(tmpdir(), "teeny-core-"));
    const first = await bootAt(root);
    await first.sendAll();
    await sleep(1300);
    await first.core.stop();
    const second = await bootAt(root);
    try {
      await second.sendAll();
      await sleep(1300);
      expect(second.children).toHaveLength(1);
    } finally {
      await second.core.stop();
    }
  });

  it("compares with the config the Gateway was started on: a change put back before the burst ends is no restart", async () => {
    const t = await bootAt(mkdtempSync(join(tmpdir(), "teeny-core-")));
    try {
      const method = "config_update_agent_settings";
      const call = (secs: number) => fetch(`http://127.0.0.1:${t.core.port}/rpc`, { method: "POST", headers: { "content-type": "application/json", authorization: "Bearer tok" }, body: JSON.stringify({ jsonrpc: "2.0", id: 1, method: `openhuman.${method}`, params: { agent_timeout_secs: secs } }) });
      await call(5000);
      await sleep(1000);
      expect(t.children).toHaveLength(2); // the Gateway now runs on 5000
      await call(7777);
      await sleep(200);
      await call(5000); // put back before the burst ended
      await sleep(1000);
      expect(t.children).toHaveLength(2);
    } finally {
      await t.core.stop();
    }
  });

  it("makes state/openclaw private under a group-writable state/, and sets umask 077 for the Gateway only while it runs", async () => {
    const root = mkdtempSync(join(tmpdir(), "teeny-core-"));
    mkdirSync(join(root, "state", "openclaw", "workspaces", "old"), { recursive: true });
    for (const d of ["state", "state/openclaw", "state/openclaw/workspaces", "state/openclaw/workspaces/old"]) chmodSync(join(root, d), 0o775);
    const before = process.umask(0o002);
    process.umask(before);
    const t = await bootAt(root);
    try {
      expect(t.umasks).toEqual([0o077]);
      const mode = (p: string): number => statSync(join(root, p)).mode & 0o7777;
      expect(mode("state/openclaw")).toBe(0o700);
      expect(mode("state/openclaw/logs")).toBe(0o700);
      expect(mode("state/openclaw/workspaces/old") & 0o022).toBe(0);
      expect(mode("state/openclaw/workspaces") & 0o022).toBe(0);
      const config = JSON.parse(readFileSync(join(root, "state", "openclaw", "openclaw.json"), "utf8")) as { logging?: { file?: string } };
      expect(config.logging?.file).toBe(join(root, "state", "openclaw", "logs", "openclaw-YYYY-MM-DD.log"));
    } finally {
      await t.core.stop();
    }
    expect(process.umask()).toBe(before);
  });
});

describe("cron wiring", () => {
  it("wireCron hands [cron] shell_timeout_secs from config.toml to the cron module", async () => {
    const dir = mkdtempSync(join(tmpdir(), "wire-"));
    mkdirSync(join(dir, "bin"));
    const log = join(dir, "args.log");
    // A stand-in `openclaw`: the skipMissedJobs check passes; every other call is logged, one line each.
    writeFileSync(join(dir, "bin", "openclaw"), `#!/bin/sh\nif [ "$1" = config ]; then echo true; exit 0; fi\necho "$@" >> '${log}'\necho '{}'\n`, { mode: 0o755 });
    writeFileSync(join(dir, "config.toml"), "[cron]\nshell_timeout_secs = 900\n");
    const ctx = { env: { TEENY_CORE_CONFIG: join(dir, "config.toml"), HOME: "/h", PATH: process.env.PATH ?? "/usr/bin:/bin" }, root: dir, stateDir: dir, log: vi.fn() };
    try {
      wireCron(ctx, () => ({}));
      await cronMethods.cron_add!({ name: "n", job_type: "shell", command: "bin/x", schedule: { kind: "cron", expr: "* * * * *" } }, ctx).catch(() => undefined);
      const added = readFileSync(log, "utf8");
      expect(added).toContain('"timeout","-k","10","900"');
      expect(added).toContain("--timeout-seconds 930");
      expect(ctx.log).not.toHaveBeenCalledWith("warn", expect.stringContaining("shell_timeout_secs"), expect.anything());
    } finally {
      setShellTimeoutSecs(1800);
    }
  });

  it("serves cron_list and reads [cron] shell_timeout_secs from the core's config.toml", async () => {
    const dir = mkdtempSync(join(tmpdir(), "cfg-"));
    expect(cronShellTimeoutSecs({ TEENY_CORE_CONFIG: join(dir, "none.toml") }, dir)).toBe(1800);
    writeFileSync(join(dir, "config.toml"), "[cron]\nshell_timeout_secs = 900\n");
    expect(cronShellTimeoutSecs({ TEENY_CORE_CONFIG: join(dir, "config.toml") }, dir)).toBe(900);
    // a boolean is not a number: `= true` is the default, not one second
    writeFileSync(join(dir, "bool.toml"), "[cron]\nshell_timeout_secs = true\n");
    expect(cronShellTimeoutSecs({ TEENY_CORE_CONFIG: join(dir, "bool.toml") }, dir)).toBe(1800);
    expect(cronShellTimeoutSecs({ OPENHUMAN_WORKSPACE: join(dir, "workspace") }, dir)).toBe(1800);
    mkdirSync(join(dir, ".openhuman"));
    writeFileSync(join(dir, ".openhuman", "config.toml"), "[cron]\nshell_timeout_secs = 60\n");
    expect(cronShellTimeoutSecs({ OPENHUMAN_WORKSPACE: join(dir, "workspace") }, dir)).toBe(60);

    // No Gateway here, so the CLI is a stub that answers "unreachable" at once: the real CLI takes
    // seconds to start on a busy runner, and a non-probe call retries an unreachable Gateway for 90 s.
    const stub = join(dir, "openclaw-stub");
    writeFileSync(stub, "#!/bin/sh\necho 'gateway closed' >&2\nexit 1\n", { mode: 0o755 });
    const t = await startTestCore({ OPENHUMAN_STORAGE_URL: `sqlite:${join(dir, "storage.db")}`, OPENCLAW_BIN: stub });
    try {
      // The method is served; the probe asks once and answers an error without waiting.
      const res = await t.rpc("cron_list", { probe: true });
      expect(res.error?.code).not.toBe(-32601);
      expect(res.error?.message).toContain("cron is closed");
    } finally {
      await t.core.stop();
    }
  });
});
