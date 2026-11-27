import { existsSync, readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";
import {
  CONTRACT_DIR,
  assertMatchesContract,
  assertParamsMatchContract,
  contractErrors,
  loadContract,
  methodNames,
} from "./contract.js";

export { assertMatchesContract } from "./contract.js";

const names = methodNames();

describe("rpc contract fixtures", () => {
  it("has a fixture for every method the callers use", () => {
    // The exhaustive list from grepping bin/, bin/lib/ and hands/lib/ (not tests).
    const expected = [
      "auth_store_provider_credentials", "config_get_agent_settings", "config_update_agent_settings",
      "config_update_local_ai_settings", "config_update_model_settings", "cost_report", "cron_add",
      "cron_list", "cron_remove", "cron_run", "cron_update", "encrypt_secret", "get_config",
      "inbound_offer", "inbound_result_ack", "inbound_results", "inference_agent_chat",
      "inference_agent_chat_simple", "inference_update_model_settings", "memory_engine_set",
      "memory_forget", "memory_items_list", "memory_learn", "modules_browser_check_readiness",
      "wallet_account", "wallet_balances", "wallet_derive_recovery_material", "wallet_execute_prepared",
      "wallet_execute_prepared_program", "wallet_generate_mnemonic", "wallet_ledger_list",
      "wallet_prepare_program", "wallet_prepare_transfer", "wallet_setup", "wallet_status",
      "wallet_tx_status", "web3_bridge_execute", "web3_bridge_quote", "web3_dapp_call",
      "web3_dapp_execute", "web3_swap_execute", "web3_swap_quote", "x402_fetch", "x402_list_payments",
    ];
    expect(names).toEqual([...expected].sort());
  });

  it.each(names)("%s is well formed", (name) => {
    const c = loadContract(name);
    expect(c.method).toBe(`openhuman.${name}`);
    expect(c.call_sites.length).toBeGreaterThan(0);
    for (const site of c.call_sites) expect(site).toMatch(/^(bin|hands)\/.+:\d+$/);
    expect(c.params).toBeTypeOf("object");
    expect(c.params.fields).toBeTypeOf("object");
    expect(c.params.example).toBeTypeOf("object");
    expect(c.response).toBeTypeOf("object");
    expect(c.response.type).toBeTypeOf("string");
  });

  it.each(names)("%s: its call sites point at real lines that name it", (name) => {
    const repo = join(CONTRACT_DIR, "..", "..", "..");
    for (const site of loadContract(name).call_sites) {
      const [file, line] = site.split(/:(?=\d+$)/) as [string, string];
      const path = join(repo, file);
      expect(existsSync(path), site).toBe(true);
      const text = readFileSync(path, "utf8").split("\n")[Number(line) - 1] ?? "";
      expect(text, site).toContain(name);
    }
  });

  it.each(names)("%s: its params example satisfies its own params spec", (name) => {
    assertParamsMatchContract(name, loadContract(name).params.example);
  });

  it.each(names)("%s: its response example satisfies its own response spec", (name) => {
    const c = loadContract(name);
    if (c.response.example === undefined) return;
    assertMatchesContract(name, c.response.example);
  });

  it("records the transport and storage.db reads", () => {
    for (const file of ["_rpc_envelope.json", "_storage_db.json"]) {
      expect(() => JSON.parse(readFileSync(join(CONTRACT_DIR, file), "utf8"))).not.toThrow();
    }
    const db = JSON.parse(readFileSync(join(CONTRACT_DIR, "_storage_db.json"), "utf8"));
    expect(Object.keys(db.collections).sort()).toEqual(["cron.jobs", "cron.runs", "tsd.leases"]);
    expect(db.table.name).toBe("docs");
  });
});

describe("assertMatchesContract", () => {
  it("accepts a good answer and unwraps a {logs, result} envelope", () => {
    assertMatchesContract("cost_report", { totals: { cost_usd: 1 } });
    assertMatchesContract("openhuman.cost_report", { logs: [], result: { totals: {} } });
    assertMatchesContract("inference_agent_chat_simple", "just words");
  });

  it("refuses a missing required field and a wrong type", () => {
    expect(() => assertMatchesContract("wallet_execute_prepared", {})).toThrow(/transactionHash/);
    expect(() => assertMatchesContract("wallet_execute_prepared", { transactionHash: 5 })).toThrow(/expected string/);
    expect(contractErrors("cron_list", { not: "a list" })[0]).toMatch(/expected array/);
  });

  it("checks each item of a list", () => {
    expect(contractErrors("cron_list", [{ id: "a" }, { name: "x" }])).toEqual(["response[1].id: required, missing"]);
  });
});
