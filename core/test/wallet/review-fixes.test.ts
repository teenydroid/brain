// Regression tests for the CodeRabbit review of #623: one test per fix that is not already pinned beside its module.
import { existsSync, readFileSync, writeFileSync, rmSync, mkdirSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";
import { jsonRpc, transportAllowed } from "../../src/wallet/rpc.js";
import { parsePolicy } from "../../src/wallet/policy.js";
import { USDC_DEVNET, json, makeHarness, mockEvm, mockSolana, policyConfig, setupWallet } from "./helpers.js";
import { generatePhrase } from "../../src/wallet/derive.js";

const RECIPIENT = "4Nd1mBQtrMJVYVfKf2PJy9NZUZdTAsp7D4xWLs4gDB4T";
const stateFile = (h: { stateDir: string }): string => join(h.stateDir, "wallet", "wallet-state.json");

describe("rpc transport and errors", () => {
  it("allows https, and http only for a plain loopback node", () => {
    expect(transportAllowed("https://node.example/abc-key")).toBe(true);
    expect(transportAllowed("http://127.0.0.1:8899/")).toBe(true);
    expect(transportAllowed("http://localhost:8899")).toBe(true);
    expect(transportAllowed("http://node.example/key")).toBe(false);
    expect(transportAllowed("http://127.0.0.1:8899/?api-key=k")).toBe(false);
    expect(transportAllowed("http://127.0.0.1:8899/secret-path")).toBe(false);
    expect(transportAllowed("http://user:pw@127.0.0.1:8899/")).toBe(false);
    expect(transportAllowed("ftp://x")).toBe(false);
  });

  it("never sends a request to a remote http endpoint", async () => {
    let called = false;
    const deps = { fetch: async () => { called = true; return json({}); } } as never;
    await expect(jsonRpc(deps, "http://node.example/key", "getBalance", [])).rejects.toThrow(/must be https/);
    expect(called).toBe(false);
  });

  it("reports the numeric code of a node error, never its text", async () => {
    const deps = { fetch: async () => json({ jsonrpc: "2.0", id: 1, error: { code: -32005, message: "denied for https://node.example/SECRETKEY" } }) } as never;
    const error = await jsonRpc(deps, "https://node.example/x", "getBalance", []).catch((e: Error) => e);
    expect((error as Error).message).toBe("rpc getBalance: the node reported an error (code -32005)");
    const odd = { fetch: async () => json({ jsonrpc: "2.0", id: 1, error: { code: "x", message: "SECRETKEY" } }) } as never;
    expect(((await jsonRpc(odd, "https://node.example/x", "m", []).catch((e: Error) => e)) as Error).message).toBe("rpc m: the node reported an error");
  });

  it("keeps a Solana balance above 2^53 exact", async () => {
    const h = makeHarness();
    await setupWallet(h);
    h.net.onHttp(
      (u) => u.includes("solana.test.invalid"),
      () => new Response('{"jsonrpc":"2.0","id":1,"result":{"context":{"slot":1},"value":9007199254740993}}', { headers: { "content-type": "application/json" } }),
    );
    const rows = (await h.call("wallet_balances")) as Array<Record<string, unknown>>;
    expect(rows.find((r) => r.chain === "solana")).toMatchObject({ raw: "9007199254740993", providerStatus: "ready" });
  });
});

describe("provider keys are never sent in clear", () => {
  it("withholds LIFI_API_KEY and DEBRIDGE_API_KEY from a non-https base URL, and sends nothing", async () => {
    const h = makeHarness({ env: { LIFI_API_KEY: "lifi-key-1234", TEENY_LIFI_API: "http://lifi.test.invalid", DEBRIDGE_API_KEY: "dbg-key-1234", TEENY_DEBRIDGE_API: "http://dbg.test.invalid" } });
    await setupWallet(h);
    mockEvm(h.net);
    await expect(
      h.call("web3_swap_quote", { chainId: "8453", tokenIn: "0x0000000000000000000000000000000000000000", tokenInAmount: "1", tokenOut: "b", slippage: "lifi:1" }),
    ).rejects.toThrow(/LIFI_API_KEY is only sent over https/);
    await expect(
      h.call("web3_swap_quote", { chainId: "8453", tokenIn: "a", tokenInAmount: "1", tokenOut: "b", slippage: "debridge:1" }),
    ).rejects.toThrow(/DEBRIDGE_API_KEY is only sent over https/);
    expect(h.net.calls.filter((c) => c.url.includes("lifi.test.invalid") || c.url.includes("dbg.test.invalid"))).toHaveLength(0);
  });

  it("redacts the deBridge key from an error that echoes it", async () => {
    const h = makeHarness({ env: { DEBRIDGE_API_KEY: "dbg-key-1234", TEENY_DEBRIDGE_API: "https://dbg.test.invalid" } });
    await setupWallet(h);
    h.net.onHttp((u) => u.includes("dbg.test.invalid"), () => json({ errorMessage: "bad accesstoken=dbg-key-1234" }, 400));
    const error = await h.call("web3_swap_quote", { chainId: "8453", tokenIn: "a", tokenInAmount: "1", tokenOut: "b", slippage: "debridge:1" }).catch((e: Error) => e);
    expect((error as Error).message).not.toContain("dbg-key-1234");
  });
});

describe("policy parsing", () => {
  it("refuses a single-bracket [wallet_policy.assets] and still allows no assets at all", () => {
    expect(() => parsePolicy('[wallet_policy]\nautomation_enabled = true\n[wallet_policy.assets]\nchain = "solana"\nbogus = 1\n')).toThrow(/array of tables/);
    expect(() => parsePolicy("[wallet_policy]\nautomation_enabled = true\n")).not.toThrow();
  });
});

describe("the stored wallet", () => {
  it("a forced setup replaces state that no longer loads; an unforced one does not", async () => {
    const h = makeHarness();
    await setupWallet(h);
    writeFileSync(stateFile(h), "{}");
    await expect(h.call("wallet_status")).rejects.toThrow(/failed validation/);
    const phrase = generatePhrase(12);
    const derived = (await h.call("wallet_derive_recovery_material", { phrase })) as { accounts: Array<Record<string, unknown>> };
    const params = {
      consentGranted: true,
      source: "generated",
      mnemonicWordCount: 12,
      encryptedMnemonic: (await h.call("encrypt_secret", { plaintext: phrase })) as string,
      accounts: derived.accounts.map((a) => ({ chain: a.chain, address: a.address, derivationPath: a.derivationPath })),
    };
    await expect(h.call("wallet_setup", params)).rejects.toThrow(/failed validation/);
    await h.call("wallet_setup", { ...params, force: true });
    expect(((await h.call("wallet_status")) as { configured: boolean }).configured).toBe(true);
  });

  it("reads a giveaway row that came first with its canonical index, so the primary account is the primary one", async () => {
    const h = makeHarness();
    await setupWallet(h);
    const file = stateFile(h);
    const state = JSON.parse(readFileSync(file, "utf8"));
    await h.call("wallet_account", { chain: "solana", accountIndex: 1 });
    const withGiveaway = JSON.parse(readFileSync(file, "utf8"));
    const giveaway = withGiveaway.accounts.find((a: { index?: number }) => a.index === 1);
    const { index: _drop, ...bare } = giveaway; // as an older file stored it: no index
    void _drop;
    state.accounts = [bare, ...withGiveaway.accounts.filter((a: { index?: number }) => a.index !== 1)];
    writeFileSync(file, JSON.stringify(state));
    const status = (await h.call("wallet_status")) as { accounts: Array<{ chain: string; address: string; index?: number }> };
    const solanaPrimary = withGiveaway.accounts.find((a: { chain: string; index?: number }) => a.chain === "solana" && !a.index);
    expect(JSON.stringify(status)).toContain(solanaPrimary.address);
    const again = (await h.call("wallet_account", { chain: "solana", accountIndex: 1 })) as { address: string };
    expect(again.address).toBe(giveaway.address);
    expect(JSON.parse(readFileSync(file, "utf8")).accounts).toHaveLength(withGiveaway.accounts.length); // no second giveaway row
  });
});

describe("transfers", () => {
  const caps = { chain: "solana", symbol: "SOL", perTx: 10n ** 30n, daily: 10n ** 30n };

  it("refuses a Solana amount above u64 at prepare, before any quote exists", async () => {
    const h = makeHarness({ config: policyConfig([caps]) });
    await setupWallet(h);
    await expect(h.call("wallet_prepare_transfer", { chain: "solana", toAddress: RECIPIENT, amountRaw: "18446744073709551616" })).rejects.toThrow(/u64/);
    await h.call("wallet_prepare_transfer", { chain: "solana", toAddress: RECIPIENT, amountRaw: "18446744073709551615" });
  });

  it("refuses a quote that expired while it waited for the ledger lock, and signs nothing", async () => {
    const h = makeHarness({ config: policyConfig([{ chain: "solana", symbol: "SOL", perTx: 1_000_000_000, daily: 2_000_000_000 }]) });
    await setupWallet(h);
    const { sent } = mockSolana(h.net);
    const q = (await h.call("wallet_prepare_transfer", { chain: "solana", toAddress: RECIPIENT, amountRaw: "1000" })) as { quoteId: string };
    const dir = join(h.stateDir, "wallet");
    mkdirSync(dir, { recursive: true });
    const lock = join(dir, "spend-ledger.json.lock");
    writeFileSync(lock, "held-by-test");
    const pending = h.call("wallet_execute_prepared", { quoteId: q.quoteId, confirmed: true });
    const settled = pending.then(() => "ok", (e: Error) => e.message);
    await new Promise((r) => setTimeout(r, 80));
    h.clock.now += 10 * 60_000; // past the 5 minute quote while it waits
    rmSync(lock);
    expect(await settled).toMatch(/^WALLET_POLICY_REFUSED: the quote expired/);
    expect(sent).toHaveLength(0);
  });

  it("hands back the transaction hash, flagged, when the final ledger write fails after the broadcast", async () => {
    const h = makeHarness({ config: policyConfig([{ chain: "solana", symbol: "SOL", perTx: 1_000_000_000, daily: 2_000_000_000 }]) });
    await setupWallet(h);
    const { sent } = mockSolana(h.net);
    const ledgerFile = join(h.stateDir, "wallet", "spend-ledger.json");
    const q = (await h.call("wallet_prepare_transfer", { chain: "solana", toAddress: RECIPIENT, amountRaw: "1000" })) as { quoteId: string };
    const original = h.net.fetch;
    h.net.onRpc("sendTransaction", (params) => {
      const wire = Buffer.from(params[0] as string, "base64");
      sent.push(new Uint8Array(wire));
      // make every later ledger write fail: a directory now sits where the ledger file was
      rmSync(ledgerFile, { force: true });
      mkdirSync(ledgerFile);
      return "5".repeat(88);
    });
    void original;
    const out = (await h.call("wallet_execute_prepared", { quoteId: q.quoteId, confirmed: true })) as Record<string, unknown>;
    expect(out).toMatchObject({ transactionHash: "5".repeat(88), ledgerUpdateFailed: true });
    expect(sent).toHaveLength(1); // never sent again
    expect(existsSync(ledgerFile)).toBe(true);
  });
});

describe("program quotes", () => {
  it("refuses an expectedDebits amount given as an unsafe JSON number", async () => {
    const h = makeHarness({ config: policyConfig([], '\n[wallet_policy.accounts.solana_1]\nallowed_programs = []\n') });
    await setupWallet(h);
    await expect(
      h.call("wallet_prepare_program", { accountIndex: 1, transaction: "AA==", expectedDebits: [{ mint: USDC_DEVNET, amountRaw: Number("9007199254740993") }] }),
    ).rejects.toThrow(/decimal string/);
  });
});

