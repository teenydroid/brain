import { decryptEnc2 } from "../../src/wallet/crypto.js";
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { assertMatchesContract, assertParamsMatchContract, loadContract } from "../contract.js";
import { FAKE_KEY, startTestCore, type TestCore } from "../helpers.js";

let t: TestCore;
beforeEach(async () => {
  t = await startTestCore();
});
afterEach(async () => {
  await t.core.stop();
});

/** Calls METHOD with its contract's own example params and checks both sides of the contract. */
async function viaContract(method: string): Promise<unknown> {
  const params = loadContract(method).params.example;
  assertParamsMatchContract(method, params);
  const answer = await t.rpc(method, params);
  expect(answer.error, JSON.stringify(answer.error)).toBeUndefined();
  assertMatchesContract(method, answer.result);
  return answer.result;
}

describe("contract methods answer in the recorded shape", () => {
  it.each([
    "inference_agent_chat",
    "inference_agent_chat_simple",
    "config_update_local_ai_settings",
    "config_update_model_settings",
    "config_update_agent_settings",
    "config_get_agent_settings",
    "get_config",
    "inference_update_model_settings",
    "auth_store_provider_credentials",
    "encrypt_secret",
    "cost_report",
    "modules_browser_check_readiness",
  ])("%s", async (method) => {
    await viaContract(method);
  });
});

describe("inbound_* (the core's queue, TEENY_INBOX=core)", () => {
  it.each(["inbound_offer", "inbound_results", "inbound_result_ack"])("%s answers that this core does not support it", async (method) => {
    const answer = await t.rpc(method, {});
    expect(answer.error?.message).toMatch(/inbound queue is not supported by this core.*TEENY_INBOX/);
  });
});

describe("config_update_agent_settings / config_get_agent_settings", () => {
  it("round-trips the turn budget and survives a restart of the store", async () => {
    await t.rpc("config_update_agent_settings", { agent_timeout_secs: 1800 });
    expect((await t.rpc("config_get_agent_settings")).result).toEqual({ agent_timeout_secs: 1800 });
    const saved = JSON.parse(readFileSync(join(t.stateDir, "core-settings.json"), "utf8"));
    expect(saved.agentTimeoutSecs).toBe(1800);
  });

  it("starts at OpenHuman's own 120 s default, which bin/doctor flags until bin/spine raises it", async () => {
    expect((await t.rpc("config_get_agent_settings")).result).toEqual({ agent_timeout_secs: 120 });
  });

  it("refuses a bad budget", async () => {
    expect((await t.rpc("config_update_agent_settings", { agent_timeout_secs: "soon" })).error?.message).toMatch(/positive number/);
    expect((await t.rpc("config_update_agent_settings", { agent_timeout_secs: 0 })).error).toBeDefined();
    // a fraction that floors to zero is not a positive timeout; 1.5 stores and returns 1
    expect((await t.rpc("config_update_agent_settings", { agent_timeout_secs: 0.5 })).error?.message).toMatch(/positive number/);
    expect((await t.rpc("config_update_agent_settings", { agent_timeout_secs: 1.5 })).result).toEqual({ agent_timeout_secs: 1 });
  });
});

describe("secrets", () => {
  it("encrypt_secret seals under the master key, differently each time, and never echoes the plaintext", async () => {
    const a = (await t.rpc("encrypt_secret", { plaintext: "word word word" })).result as string;
    const b = (await t.rpc("encrypt_secret", { plaintext: "word word word" })).result as string;
    expect(a).not.toContain("word");
    expect(a).not.toBe(b);
    // The wallet module serves it: the enc2 envelope under the master key file.
    expect(a.startsWith("enc2:")).toBe(true);
    expect(decryptEnc2(Buffer.from(FAKE_KEY, "hex"), a).toString("utf8")).toBe("word word word");
    expect(() => decryptEnc2(Buffer.alloc(32, 1), a)).toThrow();
  });

  it("encrypt_secret refuses a missing plaintext", async () => {
    expect((await t.rpc("encrypt_secret", {})).error).toBeDefined();
  });

  it("stores the endpoint key and provider tokens sealed, and answers none of them", async () => {
    const secretKey = "sk-FAKE-endpoint-key";
    const token = "FAKE-captcha-token";
    const local = await t.rpc("config_update_local_ai_settings", { runtime_enabled: true, opt_in_confirmed: true, native_tool_calling: true, api_key: secretKey });
    const cred = await t.rpc("auth_store_provider_credentials", { provider: "provider:typesafe", token, setActive: true });
    const config = await t.rpc("get_config");
    for (const body of [local, cred, config]) {
      expect(JSON.stringify(body)).not.toContain(secretKey);
      expect(JSON.stringify(body)).not.toContain(token);
    }
    expect((config.result as { config: { local_ai: { has_api_key: boolean } } }).config.local_ai.has_api_key).toBe(true);
    const file = readFileSync(join(t.stateDir, "core-settings.json"), "utf8");
    expect(file).not.toContain(secretKey);
    expect(file).not.toContain(token);
    expect(file).toContain("enc2:");
  });

  it("keeps the stored key when a later boot sends none", async () => {
    await t.rpc("config_update_local_ai_settings", { runtime_enabled: true, opt_in_confirmed: true, native_tool_calling: true, api_key: "sk-FAKE" });
    await t.rpc("config_update_local_ai_settings", { runtime_enabled: true, opt_in_confirmed: true, native_tool_calling: false });
    const file = JSON.parse(readFileSync(join(t.stateDir, "core-settings.json"), "utf8"));
    expect(file.localAi.apiKeySealed).toMatch(/^enc2:/);
    expect(file.localAi.nativeToolCalling).toBe(false);
  });
});

describe("models", () => {
  it("get_config reads back the registry that inference_update_model_settings replaced", async () => {
    const one = [{ id: "m1", provider: "local-openai", context_window: 1000 }];
    const two = [{ id: "m2", provider: "local-openai", context_window: 2000 }];
    await t.rpc("inference_update_model_settings", { model_registry: one });
    await t.rpc("inference_update_model_settings", { model_registry: two });
    const config = (await t.rpc("get_config")).result as { config: { model_registry: unknown[] } };
    expect(config.config.model_registry).toEqual(two);
  });

  it("refuses a registry entry without a window, and a non-array", async () => {
    expect((await t.rpc("inference_update_model_settings", { model_registry: [{ id: "x", provider: "p" }] })).error?.message).toMatch(/context_window/);
    expect((await t.rpc("inference_update_model_settings", { model_registry: "no" })).error).toBeDefined();
  });

  it("config_update_model_settings keeps the default model and needs all seven keys", async () => {
    const params = loadContract("config_update_model_settings").params.example;
    await t.rpc("config_update_model_settings", params);
    const config = (await t.rpc("get_config")).result as { config: { default_model: string } };
    expect(config.config.default_model).toBe("glm-5.3-flash");
    const { memory_provider: _dropped, ...partial } = params as Record<string, unknown>;
    void _dropped;
    expect((await t.rpc("config_update_model_settings", partial)).error?.message).toMatch(/memory_provider/);
  });
});

describe("cost_report", () => {
  it("adds up the runs the core saw, counts the unpriced ones, and ignores older days", async () => {
    t.runner.reply = { text: "x", costUsd: 0.25, usage: { total: 10 }, model: "m" };
    await t.rpc("inference_agent_chat_simple", { message: "a" });
    await t.rpc("inference_agent_chat_simple", { message: "b" });
    t.runner.reply = { text: "x" };
    await t.rpc("inference_agent_chat_simple", { message: "c" });
    const report = (await t.rpc("cost_report", { days: 1 })).result as { totals: Record<string, number> };
    expect(report.totals).toEqual({ cost_usd: 0.5, calls: 3, unpriced_calls: 1, charged_usd: 0.5, estimated_usd: 0 });
  });

  it("is zero on a fresh core and refuses a missing days", async () => {
    const report = (await t.rpc("cost_report", { days: 7 })).result as { totals: Record<string, number> };
    expect(report.totals.calls).toBe(0);
    expect((await t.rpc("cost_report", {})).error).toBeDefined();
  });
});

describe("modules_browser_check_readiness", () => {
  it("is not ready without the gateway (the test core runs none) and says why", async () => {
    const r = (await t.rpc("modules_browser_check_readiness")).result as Record<string, unknown>;
    expect(r.module_ready).toBe(false);
    expect(r.error).toMatch(/gateway/);
  });
});
