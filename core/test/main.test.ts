import { EventEmitter } from "node:events";
import { mkdtempSync, mkdirSync, readFileSync, statSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { describe, expect, it, vi } from "vitest";
import { cronShellTimeoutSecs, run, startCore } from "../src/main.js";
import { resolvePaths, serveSettings } from "../src/config/paths.js";
import { resolveToken } from "../src/config/token.js";
import { startTestCore } from "./helpers.js";

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
      expect(env.OPENCLAW_GATEWAY_TOKEN).toMatch(/^[0-9a-f]{64}$/);
      expect(Object.values(env)).not.toContain("core-token-xyz");
      expect(Object.keys(env).some((k) => k.startsWith("OPENHUMAN_"))).toBe(false);
    } finally {
      await core.stop();
    }
  });
});

describe("cron wiring", () => {
  it("serves cron_list and reads [cron] shell_timeout_secs from the core's config.toml", async () => {
    const dir = mkdtempSync(join(tmpdir(), "cfg-"));
    expect(cronShellTimeoutSecs({ TEENY_CORE_CONFIG: join(dir, "none.toml") }, dir)).toBe(1800);
    writeFileSync(join(dir, "config.toml"), "[cron]\nshell_timeout_secs = 900\n");
    expect(cronShellTimeoutSecs({ TEENY_CORE_CONFIG: join(dir, "config.toml") }, dir)).toBe(900);
    expect(cronShellTimeoutSecs({ OPENHUMAN_WORKSPACE: join(dir, "workspace") }, dir)).toBe(1800);
    mkdirSync(join(dir, ".openhuman"));
    writeFileSync(join(dir, ".openhuman", "config.toml"), "[cron]\nshell_timeout_secs = 60\n");
    expect(cronShellTimeoutSecs({ OPENHUMAN_WORKSPACE: join(dir, "workspace") }, dir)).toBe(60);

    const t = await startTestCore({ OPENHUMAN_STORAGE_URL: `sqlite:${join(dir, "storage.db")}` });
    try {
      const res = await t.rpc("cron_list", {});
      expect(res.error).toBeUndefined();
    } finally {
      await t.core.stop();
    }
  });
});
