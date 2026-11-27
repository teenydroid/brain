import { execFileSync } from "node:child_process";
import { mkdtempSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { describe, expect, it } from "vitest";
import { AgentCatalog } from "../../src/agents/provision.js";
import { buildGatewayConfig, guardedEntries, type GatewayConfigInput } from "../../src/gateway/config.js";
import { openclawCommand } from "../../src/gateway/cli.js";
import { REPO } from "../helpers.js";

function input(over: Partial<GatewayConfigInput> = {}): GatewayConfigInput {
  const dir = mkdtempSync(join(tmpdir(), "oc-cfg-"));
  return {
    port: 18789,
    modelUrl: "http://127.0.0.1:18080/v1",
    model: "glm-5.3-flash",
    models: [{ id: "glm-5.3-flash", contextWindow: 128000 }, { id: "studio-writer", contextWindow: 32000 }],
    compactionTriggerTokens: 100_000,
    keepRecentTokens: 40_000,
    agentTimeoutSecs: 1800,
    defaultWorkspace: join(dir, "workspaces"),
    agents: new AgentCatalog(join(REPO, "brain"), dir).entries(),
    ...over,
  };
}

type Cfg = Record<string, any>; // eslint-disable-line @typescript-eslint/no-explicit-any

describe("generated OpenClaw config: the lockdown", () => {
  const cfg = buildGatewayConfig(input()) as Cfg;

  it("binds loopback behind a token, in local mode", () => {
    expect(cfg.gateway).toMatchObject({ mode: "local", bind: "loopback", port: 18789, auth: { mode: "token" } });
  });

  it("turns off hooks, both OpenAI-style HTTP endpoints, the control UI, update checks and mDNS", () => {
    expect(cfg.hooks).toEqual({ enabled: false });
    expect(cfg.gateway.http.endpoints.chatCompletions.enabled).toBe(false);
    expect(cfg.gateway.http.endpoints.responses.enabled).toBe(false);
    expect(cfg.gateway.controlUi.enabled).toBe(false);
    expect(cfg.update.checkOnStart).toBe(false);
    expect(cfg.discovery.mdns.mode).toBe("off");
    expect(cfg.telemetry.enabled).toBe(false);
  });

  it("configures no channel and no binding, and skips missed cron slots", () => {
    expect(cfg).not.toHaveProperty("channels");
    expect(cfg).not.toHaveProperty("bindings");
    expect(cfg.cron).toEqual({ skipMissedJobs: true });
  });

  it("allows only the browser plugin without the guardrails, and denies the channel plugins", () => {
    expect(cfg.plugins.allow).toEqual(["browser"]);
    expect(cfg.plugins.deny).toEqual(expect.arrayContaining(["telegram", "x"]));
    expect(cfg.plugins).not.toHaveProperty("load");
  });

  it("adds teeny-guardrails as a local path when the plugin exists, and nothing else", () => {
    const withPlugin = buildGatewayConfig(input({ guardrailsPath: "/srv/core/plugins/teeny-guardrails" })) as Cfg;
    expect(withPlugin.plugins.allow).toEqual(["browser", "teeny-guardrails"]);
    expect(withPlugin.plugins.load.paths).toEqual(["/srv/core/plugins/teeny-guardrails"]);
    expect(withPlugin.plugins.entries["teeny-guardrails"].enabled).toBe(true);
  });

  it("names the model key's environment variable and never carries a secret", () => {
    const text = JSON.stringify(cfg);
    expect(cfg.models.providers["local-openai"].apiKey).toEqual({ source: "env", provider: "default", id: "LOCAL_OPENAI_API_KEY" });
    expect(text).not.toMatch(/OPENCLAW_GATEWAY_TOKEN\s*"\s*:\s*"/);
    expect(cfg.gateway.auth).not.toHaveProperty("token");
  });
});

describe("generated OpenClaw config: model, compaction, agents", () => {
  const cfg = buildGatewayConfig(input()) as Cfg;

  it("makes local-openai an OpenAI-compatible provider at $LOCAL_OPENAI_URL and the default model", () => {
    const p = cfg.models.providers["local-openai"];
    expect(p).toMatchObject({ baseUrl: "http://127.0.0.1:18080/v1", api: "openai-completions" });
    expect(p.models.map((m: { id: string }) => m.id)).toEqual(["glm-5.3-flash", "studio-writer"]);
    expect(cfg.agents.defaults.model.primary).toBe("local-openai/glm-5.3-flash");
  });

  it("carries the 100k fold and the 40k kept tail, capped by a small window", () => {
    expect(cfg.agents.defaults.compaction).toMatchObject({ enabled: true, keepRecentTokens: 40_000 });
    const [big, small] = cfg.models.providers["local-openai"].models;
    expect(big.contextTokens).toBe(100_000);
    expect(small.contextTokens).toBe(32_000);
  });

  it("gives every agent an entry and the turn budget", () => {
    expect(cfg.agents.ownership).toBe("explicit");
    expect(cfg.agents.defaults.timeoutSeconds).toBe(1800);
    expect(Object.keys(cfg.agents.entries)).toEqual(expect.arrayContaining(["teeny", "act", "judge", "mind", "simple"]));
    expect(cfg.agents.entries.judge.tools).toEqual({ allow: [], deny: ["*"] });
  });

  it("omits the provider when no endpoint or model is known yet", () => {
    const bare = buildGatewayConfig(input({ modelUrl: undefined, model: undefined, models: [] })) as Cfg;
    expect(bare).not.toHaveProperty("models");
    expect(bare.agents.defaults).not.toHaveProperty("model");
  });
});

// The strongest check there is: the installed openclaw's own validator, on the config we generate.
describe("generated OpenClaw config is valid for the pinned openclaw", () => {
  it("passes `openclaw config validate`", { timeout: 90_000 }, () => {
    const dir = mkdtempSync(join(tmpdir(), "oc-validate-"));
    const file = join(dir, "openclaw.json");
    writeFileSync(file, JSON.stringify(buildGatewayConfig(input({ guardrailsPath: undefined }))));
    const { cmd, args } = openclawCommand({});
    const out = execFileSync(cmd, [...args, "config", "validate"], {
      env: { PATH: process.env.PATH ?? "", HOME: dir, OPENCLAW_STATE_DIR: dir, OPENCLAW_CONFIG_PATH: file },
      encoding: "utf8",
      stdio: ["ignore", "pipe", "pipe"],
    });
    expect(out).toMatch(/Config valid/);
  });

  it("names the system agent that owns shell cron jobs, only when asked", () => {
    const withOwner = buildGatewayConfig(input({ systemAgentId: "simple" })) as { agents: { defaults: { systemAgent?: { agentId: string } } } };
    expect(withOwner.agents.defaults.systemAgent).toEqual({ agentId: "simple" });
    const without = buildGatewayConfig(input()) as { agents: { defaults: { systemAgent?: unknown } } };
    expect(without.agents.defaults.systemAgent).toBeUndefined();
  });

  it("names the token and the model key as file SecretRefs, never as values or environment variables", () => {
    const cfg = buildGatewayConfig(input({ secretFiles: { gatewayToken: "/s/gateway.token", modelKey: "/s/model.key" } })) as {
      gateway: { auth: { token: unknown } };
      secrets: { providers: Record<string, unknown> };
      models: { providers: { "local-openai": { apiKey: unknown } } };
    };
    expect(cfg.gateway.auth.token).toEqual({ source: "file", provider: "teeny_gateway_token", id: "value" });
    expect(cfg.models.providers["local-openai"].apiKey).toEqual({ source: "file", provider: "teeny_model_key", id: "value" });
    expect(cfg.secrets.providers.teeny_gateway_token).toEqual({ source: "file", path: "/s/gateway.token", mode: "singleValue" });
    expect(JSON.stringify(cfg)).not.toMatch(/OPENCLAW_GATEWAY_TOKEN|LOCAL_OPENAI_API_KEY/);
  });
});

describe("generated OpenClaw config: agent models and hand skills", () => {
  const cfg = buildGatewayConfig(input({ skillDirs: ["/srv/brain/skills/hands"] })) as Cfg;

  it("pins the mind to its own model and lets its helpers inherit it, while the scripts keep the default", () => {
    expect(cfg.agents.entries.mind.model).toEqual({ primary: "local-openai/claude-opus-4.6" });
    expect(cfg.agents.entries.worker.model).toEqual({ primary: "local-openai/claude-opus-4.6" });
    expect(cfg.agents.entries.babysitter.model).toEqual({ primary: "local-openai/glm-5.3-flash" });
    expect(cfg.agents.entries.simple).not.toHaveProperty("model");
    expect(cfg.agents.defaults.model.primary).toBe("local-openai/glm-5.3-flash");
  });

  it("loads the generated hand skills from the directory it is given", () => {
    expect(cfg.skills).toEqual({ load: { extraDirs: ["/srv/brain/skills/hands"] } });
    expect(buildGatewayConfig(input()) as Cfg).not.toHaveProperty("skills");
  });
});

describe("generated OpenClaw config: exec and the guardrails plugin", () => {
  const execSecurity = (cfg: Cfg) =>
    Object.fromEntries(Object.entries(cfg.agents.entries as Cfg).filter(([, e]) => e.tools.exec).map(([id, e]) => [id, e.tools.exec.security]));

  it("runs a shell agent's exec with OpenClaw's own check off only when the plugin is in the config", () => {
    const withPlugin = buildGatewayConfig(input({ guardrailsPath: "/srv/teeny/core/plugins/teeny-guardrails", guardrailsPolicy: {}, guardrailsProven: true })) as Cfg;
    const security = execSecurity(withPlugin);
    expect(Object.keys(security)).toContain("worker");
    expect(Object.values(security).every((v) => v === "full")).toBe(true);
    expect(withPlugin.agents.entries.worker.tools.exec).toMatchObject({ host: "gateway", ask: "off" });
  });

  it("denies every shell agent's exec while the plugin is in the config but not yet proven loaded (fail closed)", () => {
    const cfg = buildGatewayConfig(input({ guardrailsPath: "/srv/teeny/core/plugins/teeny-guardrails", guardrailsPolicy: {} })) as Cfg;
    expect(Object.values(execSecurity(cfg)).every((v) => v === "deny")).toBe(true);
  });

  it("denies every shell agent's exec when there is no plugin to gate it (fail closed)", () => {
    const security = execSecurity(buildGatewayConfig(input()) as Cfg);
    expect(Object.keys(security)).toContain("worker");
    expect(Object.values(security).every((v) => v === "deny")).toBe(true);
  });

  it("leaves tool-less agents and the input untouched", () => {
    const agents = input().agents;
    const before = JSON.stringify(agents);
    guardedEntries(agents, false);
    expect(JSON.stringify(agents)).toBe(before);
  });
});

describe("generated OpenClaw config: no memory plugin, so no dreaming job", () => {
  const cfg = buildGatewayConfig(input({ guardrailsPath: "/srv/teeny/core/plugins/teeny-guardrails", guardrailsPolicy: {} })) as Cfg;

  it("turns the memory slot off and memory-core's dreaming with it", () => {
    expect(cfg.plugins.slots).toEqual({ memory: "none" });
    expect(cfg.plugins.entries["memory-core"]).toEqual({ enabled: false, config: { dreaming: { enabled: false } } });
    expect(cfg.plugins.entries["teeny-guardrails"].enabled).toBe(true);
  });

  it("does so with no guardrails plugin too", () => {
    const bare = buildGatewayConfig(input()) as Cfg;
    expect(bare.plugins.slots).toEqual({ memory: "none" });
    expect(bare.plugins.entries["memory-core"].enabled).toBe(false);
  });

  it("names no memory_search or memory_get in any agent's tool allowlist", () => {
    for (const [id, entry] of Object.entries(cfg.agents.entries as Cfg)) {
      expect(entry.tools.allow, id).not.toContain("memory_search");
      expect(entry.tools.allow, id).not.toContain("memory_get");
    }
  });
});
