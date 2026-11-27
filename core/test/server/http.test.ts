import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { buildRegistry } from "../../src/server/registry.js";
import { startTestCore, type TestCore } from "../helpers.js";

let t: TestCore;
beforeEach(async () => {
  t = await startTestCore();
});
afterEach(async () => {
  await t.core.stop();
});

const post = (body: string, headers: Record<string, string> = {}) =>
  fetch(`${t.url}/rpc`, { method: "POST", headers: { authorization: `Bearer ${t.token}`, "content-type": "application/json", ...headers }, body });

describe("transport (core/test/contract/_rpc_envelope.json)", () => {
  it("GET /health answers 200 with no token", async () => {
    const res = await fetch(`${t.url}/health`);
    expect(res.status).toBe(200);
  });

  it("refuses /rpc without the bearer, and with a wrong or near-miss one", async () => {
    const body = JSON.stringify({ jsonrpc: "2.0", id: 1, method: "openhuman.get_config", params: {} });
    for (const authorization of [undefined, "Bearer wrong", `Bearer ${t.token}x`, t.token]) {
      const res = await fetch(`${t.url}/rpc`, { method: "POST", body, headers: authorization ? { authorization } : {} });
      expect(res.status).toBe(401);
    }
  });

  it("answers {jsonrpc, id, result} for openhuman.<method> and the bare method name", async () => {
    for (const method of ["openhuman.config_get_agent_settings", "config_get_agent_settings"]) {
      const res = await post(JSON.stringify({ jsonrpc: "2.0", id: 7, method, params: {} }));
      expect(res.status).toBe(200);
      expect(await res.json()).toEqual({ jsonrpc: "2.0", id: 7, result: { agent_timeout_secs: 120 } });
    }
  });

  it("answers params omitted as {}", async () => {
    const res = await post(JSON.stringify({ jsonrpc: "2.0", id: 1, method: "openhuman.config_get_agent_settings" }));
    expect(((await res.json()) as { result: unknown }).result).toEqual({ agent_timeout_secs: 120 });
  });

  it("answers errors in JSON-RPC with a message callers can quote", async () => {
    const unknown = await (await post(JSON.stringify({ jsonrpc: "2.0", id: 1, method: "openhuman.nope", params: {} }))).json();
    expect(unknown).toMatchObject({ id: 1, error: { code: -32601 } });
    expect((unknown as { error: { message: string } }).error.message).toContain("nope");
    const parse = await (await post("{not json")).json();
    expect(parse).toMatchObject({ id: null, error: { code: -32700 } });
    const arr = await (await post(JSON.stringify({ jsonrpc: "2.0", id: 1, method: "openhuman.get_config", params: [] }))).json();
    expect(arr).toMatchObject({ error: { code: -32602 } });
    const bad = await (await post(JSON.stringify([1]))).json();
    expect(bad).toMatchObject({ error: { code: -32600 } });
  });

  it("answers a handler failure as an error, not an HTTP failure", async () => {
    const res = await post(JSON.stringify({ jsonrpc: "2.0", id: 1, method: "openhuman.cost_report", params: {} }));
    expect(res.status).toBe(200);
    expect(((await res.json()) as { error: { message: string } }).error.message).toMatch(/days/);
  });

  it("404s other paths and 405s GET /rpc", async () => {
    expect((await fetch(`${t.url}/nope`)).status).toBe(404);
    expect((await fetch(`${t.url}/rpc`)).status).toBe(405);
  });
});

describe("registry", () => {
  it("serves every method this phase owns", () => {
    const names = [...buildRegistry().keys()].sort();
    // The wallet, cron and inbound modules join these (their own tests, and the next test).
    expect(names.filter((n) => !/^(wallet_|web3_|x402_|inbound_|cron_)/.test(n))).toEqual(
      [
        "auth_store_provider_credentials", "config_get_agent_settings", "config_update_agent_settings", "config_update_local_ai_settings",
        "config_update_model_settings", "cost_report", "encrypt_secret", "get_config", "inference_agent_chat", "inference_agent_chat_simple",
        "inference_update_model_settings", "modules_browser_check_readiness",
        "memory_engine_set", "memory_forget", "memory_items_list", "memory_learn",
      ].sort(),
    );
  });

  it("serves the cron, wallet, memory and inbound methods too (a module left out of MODULES is a -32601 for every caller)", () => {
    const names = new Set(buildRegistry().keys());
    for (const n of ["cron_list", "cron_add", "cron_update", "cron_remove", "cron_run", "wallet_status", "wallet_prepare_transfer", "web3_swap_quote", "x402_fetch", "encrypt_secret", "memory_learn", "memory_engine_set", "inbound_offer", "inbound_results", "inbound_result_ack"]) {
      expect(names.has(n), n).toBe(true);
    }
  });

  it("refuses two modules that serve one method", () => {
    const h = async () => null;
    expect(() => buildRegistry([{ a: h }, { a: h }])).toThrow(/two modules/);
  });
});
