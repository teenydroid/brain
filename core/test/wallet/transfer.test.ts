import { SystemInstruction, Transaction } from "@solana/web3.js";
import { ASSOCIATED_TOKEN_PROGRAM_ID, TOKEN_PROGRAM_ID, getAssociatedTokenAddressSync } from "@solana/spl-token";
import { PublicKey } from "@solana/web3.js";
import { base58 } from "@scure/base";
import { describe, expect, it } from "vitest";
import { decodeTransaction } from "../../src/wallet/solana-program.js";
import { createMethods } from "../../src/wallet/index.js";
import { USDC_DEVNET, makeHarness, mockEvm, mockSolana, parseEvm, policyConfig, setupWallet } from "./helpers.js";

const RECIPIENT = "4Nd1mBQtrMJVYVfKf2PJy9NZUZdTAsp7D4xWLs4gDB4T";
const OTHER = "9xQeWvG816bUx9EPjHmaT23yvVM2ZWbrrpZb9PusVFin";
const EVM_TO = "0x1111111111111111111111111111111111111111";
const BASE_USDC = "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913";

const SOL_CAPS = { chain: "solana", symbol: "SOL", perTx: 1_000_000_000, daily: 2_000_000_000, weekly: 1_500_000_000 };
const USDC_CAPS = { chain: "solana", symbol: "USDC", tokenAddress: USDC_DEVNET, perTx: 5_000_000, daily: 8_000_000, weekly: 6_000_000 };

async function prepare(h: ReturnType<typeof makeHarness>, extra: Record<string, unknown> = {}): Promise<{ quoteId: string } & Record<string, unknown>> {
  return (await h.call("wallet_prepare_transfer", { chain: "solana", toAddress: RECIPIENT, amountRaw: "1000000", ...extra })) as { quoteId: string };
}

const execute = (h: ReturnType<typeof makeHarness>, quoteId: string, extra: Record<string, unknown> = {}) =>
  h.call("wallet_execute_prepared", { quoteId, confirmed: true, ...extra });

describe("a Solana transfer", () => {
  it("prepares a quote the hand's checks accept, then signs, sends and records exactly that SOL transfer", async () => {
    const h = makeHarness({ config: policyConfig([SOL_CAPS]) });
    const { status } = await setupWallet(h);
    const { sent } = mockSolana(h.net);
    const own = (status.accounts as Array<{ chain: string; address: string }>).find((a) => a.chain === "solana")!.address;
    const quote = (await prepare(h, { amountRaw: "250000000" })) as Record<string, unknown>;
    expect(quote).toMatchObject({ chain: "solana", toAddress: RECIPIENT, amountRaw: "250000000", assetSymbol: "SOL", fromAddress: own, kind: "transfer" });
    expect(sent).toHaveLength(0); // nothing is signed or sent by a prepare
    const result = (await execute(h, quote.quoteId as string)) as { transactionHash: string };
    expect(sent).toHaveLength(1);
    const wire = decodeTransaction(sent[0]!);
    expect(base58.encode(wire.signatures[0]!)).toBe(result.transactionHash);
    expect(base58.encode(wire.accountKeys[0]!)).toBe(own);
    const tx = Transaction.from(sent[0]!);
    expect(tx.instructions).toHaveLength(1);
    const decoded = SystemInstruction.decodeTransfer(tx.instructions[0]!);
    expect(decoded.toPubkey.toBase58()).toBe(RECIPIENT);
    expect(decoded.lamports).toBe(250_000_000n);
    const ledger = (await h.call("wallet_ledger_list", { limit: 10 })) as Array<Record<string, unknown>>;
    expect(ledger).toHaveLength(1);
    expect(ledger[0]).toMatchObject({ status: "broadcast", transactionHash: result.transactionHash, amountRaw: "250000000", assetSymbol: "SOL", policyApplied: true, origin: "interactive", originClass: "rpc" });
  });

  it("moves USDC with an idempotent ATA create and a TransferChecked of the devnet mint", async () => {
    const h = makeHarness({ config: policyConfig([USDC_CAPS]) });
    const { status } = await setupWallet(h);
    const { sent } = mockSolana(h.net);
    const own = new PublicKey((status.accounts as Array<{ chain: string; address: string }>).find((a) => a.chain === "solana")!.address);
    const quote = await prepare(h, { assetSymbol: "USDC", amountRaw: "1500000" });
    expect(quote).toMatchObject({ assetSymbol: "USDC", tokenAddress: USDC_DEVNET });
    await execute(h, quote.quoteId);
    const tx = Transaction.from(sent[0]!);
    expect(tx.instructions).toHaveLength(2);
    expect(tx.instructions[0]!.programId.equals(ASSOCIATED_TOKEN_PROGRAM_ID)).toBe(true);
    expect(Array.from(tx.instructions[0]!.data)).toEqual([1]);
    const transfer = tx.instructions[1]!;
    expect(transfer.programId.equals(TOKEN_PROGRAM_ID)).toBe(true);
    expect(transfer.data[0]).toBe(12); // TransferChecked
    expect(Buffer.from(transfer.data).readBigUInt64LE(1)).toBe(1_500_000n);
    expect(transfer.data[9]).toBe(6);
    const mint = new PublicKey(USDC_DEVNET);
    expect(transfer.keys[0]!.pubkey.equals(getAssociatedTokenAddressSync(mint, own))).toBe(true);
    expect(transfer.keys[2]!.pubkey.equals(getAssociatedTokenAddressSync(mint, new PublicKey(RECIPIENT), true))).toBe(true);
  });

  it("refuses a bad request before it is quoted", async () => {
    const h = makeHarness({ config: policyConfig([SOL_CAPS]) });
    await setupWallet(h);
    await expect(prepare(h, { toAddress: "not-an-address" })).rejects.toThrow(/not a Solana address/);
    await expect(prepare(h, { amountRaw: "0" })).rejects.toThrow(/above zero/);
    await expect(prepare(h, { amountRaw: "-5" })).rejects.toThrow(/above zero/);
    await expect(prepare(h, { amountRaw: "1.5" })).rejects.toThrow(/above zero/);
    await expect(prepare(h, { assetSymbol: "DOGE" })).rejects.toThrow(/not an asset/);
    await expect(prepare(h, { chain: "bitcoin" })).rejects.toThrow(/chain is evm or solana/);
    await expect(prepare(h, { surprise: 1 })).rejects.toThrow(/does not take surprise/);
  });
});

describe("an EVM transfer", () => {
  const caps = [
    { chain: "evm", symbol: "ETH", evmNetwork: "base_mainnet", perTx: 10n ** 17n, daily: 2n * 10n ** 17n },
    { chain: "evm", symbol: "USDC", evmNetwork: "base_mainnet", tokenAddress: BASE_USDC, perTx: 5_000_000, daily: 8_000_000 },
  ];

  it("signs a native transfer and an ERC-20 transfer for the right chain, to the right place", async () => {
    const h = makeHarness({ config: policyConfig(caps), env: { OPENHUMAN_SOLANA_CLUSTER: "mainnet" } });
    const { status } = await setupWallet(h);
    const { sent } = mockEvm(h.net);
    const own = (status.accounts as Array<{ chain: string; address: string }>).find((a) => a.chain === "evm")!.address;
    const native = (await h.call("wallet_prepare_transfer", { chain: "evm", evmNetwork: "base_mainnet", toAddress: EVM_TO, amountRaw: "1000" })) as { quoteId: string; evmNetwork: string };
    expect(native.evmNetwork).toBe("base_mainnet");
    await execute(h, native.quoteId);
    const tx1 = parseEvm(sent[0]!);
    expect(tx1.chainId).toBe(8453);
    expect(tx1.to?.toLowerCase()).toBe(EVM_TO);
    expect(tx1.value).toBe(1000n);
    const token = (await h.call("wallet_prepare_transfer", { chain: "evm", evmNetwork: "base_mainnet", toAddress: EVM_TO, amountRaw: "2500000", assetSymbol: "USDC" })) as { quoteId: string };
    await execute(h, token.quoteId);
    const tx2 = parseEvm(sent[1]!);
    expect(tx2.to?.toLowerCase()).toBe(BASE_USDC.toLowerCase());
    expect(tx2.value ?? 0n).toBe(0n);
    // transfer(address,uint256): a9059cbb | to | amount
    expect(tx2.data).toBe(`0xa9059cbb${"0".repeat(24)}${EVM_TO.slice(2)}${(2_500_000).toString(16).padStart(64, "0")}`);
    expect(own).toMatch(/^0x[0-9a-fA-F]{40}$/);
  });

  it("refuses an address with a wrong EIP-55 checksum and an unknown network", async () => {
    const h = makeHarness({ config: policyConfig(caps), env: { OPENHUMAN_SOLANA_CLUSTER: "mainnet" } });
    await setupWallet(h);
    const bad = "0x5aAeb6053F3E94C9b9A09f33669435E7Ef1BeAed".replace("5aAeb", "5AaEb");
    await expect(h.call("wallet_prepare_transfer", { chain: "evm", evmNetwork: "base_mainnet", toAddress: bad, amountRaw: "1" })).rejects.toThrow(/EIP-55/);
    await expect(h.call("wallet_prepare_transfer", { chain: "evm", evmNetwork: "polygon", toAddress: EVM_TO, amountRaw: "1" })).rejects.toThrow(/evmNetwork is required/);
  });
});

describe("the spend caps, enforced before anything is signed", () => {
  async function refused(h: ReturnType<typeof makeHarness>, quoteId: string): Promise<string> {
    try {
      await execute(h, quoteId);
    } catch (error) {
      return (error as Error).message;
    }
    throw new Error("expected a refusal");
  }

  it("refuses an asset the policy does not list", async () => {
    const h = makeHarness({ config: policyConfig([USDC_CAPS]) });
    await setupWallet(h);
    const { sent } = mockSolana(h.net);
    const quote = await prepare(h);
    expect(await refused(h, quote.quoteId)).toBe("WALLET_POLICY_REFUSED: SOL on solana is not in the wallet policy's asset allowlist");
    expect(sent).toHaveLength(0);
    expect(h.net.rpcCalls("getLatestBlockhash")).toHaveLength(0); // not even a blockhash was fetched
  });

  it("refuses a USDC transfer of another mint's symbol twin (token_address pinned)", async () => {
    const h = makeHarness({ config: policyConfig([{ ...USDC_CAPS, tokenAddress: "So11111111111111111111111111111111111111112" }]) });
    await setupWallet(h);
    mockSolana(h.net);
    const quote = await prepare(h, { assetSymbol: "USDC" });
    expect(await refused(h, quote.quoteId)).toMatch(/^WALLET_POLICY_REFUSED: USDC on solana is not in the wallet policy's asset allowlist$/);
  });

  it("refuses an empty policy and a missing config the same way: nothing moves", async () => {
    for (const config of ["", "[wallet_policy]\nassets = []\nautomation_enabled = false\napply_to_interactive = true\n"]) {
      const h = makeHarness({ config });
      await setupWallet(h);
      const { sent } = mockSolana(h.net);
      const quote = await prepare(h);
      expect(await refused(h, quote.quoteId)).toMatch(/is not in the wallet policy's asset allowlist/);
      expect(sent).toHaveLength(0);
    }
  });

  it("refuses over the per-transaction cap, with the exact message", async () => {
    const h = makeHarness({ config: policyConfig([USDC_CAPS]) });
    await setupWallet(h);
    const { sent } = mockSolana(h.net);
    const quote = await prepare(h, { assetSymbol: "USDC", amountRaw: "5000001" });
    expect(await refused(h, quote.quoteId)).toBe("WALLET_POLICY_REFUSED: 5000001 USDC (atomic) exceeds the per-transaction cap of 5000000");
    expect(sent).toHaveLength(0);
    const ledger = (await h.call("wallet_ledger_list", { limit: 5 })) as Array<Record<string, unknown>>;
    expect(ledger[0]).toMatchObject({ status: "refused", error: "5000001 USDC (atomic) exceeds the per-transaction cap of 5000000" });
  });

  it("refuses over the rolling 24h cap, counting what was spent and not what was refused, and lets it roll off", async () => {
    const h = makeHarness({ config: policyConfig([USDC_CAPS]) });
    await setupWallet(h);
    mockSolana(h.net);
    const first = await prepare(h, { assetSymbol: "USDC", amountRaw: "5000000" });
    await execute(h, first.quoteId);
    // a refused over-cap attempt must not count against the day
    const over = await prepare(h, { assetSymbol: "USDC", amountRaw: "6000000" });
    await refused(h, over.quoteId);
    const second = await prepare(h, { assetSymbol: "USDC", amountRaw: "4000000" });
    expect(await refused(h, second.quoteId)).toBe(
      "WALLET_POLICY_REFUSED: 4000000 USDC (atomic) would exceed the rolling 24h cap of 8000000 (5000000 already spent)",
    );
    const fits = await prepare(h, { assetSymbol: "USDC", amountRaw: "3000000", toAddress: OTHER });
    await execute(h, fits.quoteId);
    h.clock.now += 25 * 3600_000;
    const later = await prepare(h, { assetSymbol: "USDC", amountRaw: "3000000", toAddress: OTHER });
    await execute(h, later.quoteId);
  });

  it("refuses over the rolling 7-day per-recipient cap, and another recipient is unaffected", async () => {
    const h = makeHarness({ config: policyConfig([{ ...USDC_CAPS, daily: 100_000_000 }]) });
    await setupWallet(h);
    mockSolana(h.net);
    const a = await prepare(h, { assetSymbol: "USDC", amountRaw: "4000000" });
    await execute(h, a.quoteId);
    const b = await prepare(h, { assetSymbol: "USDC", amountRaw: "3000000" });
    expect(await refused(h, b.quoteId)).toBe(
      "WALLET_POLICY_REFUSED: 3000000 USDC (atomic) would exceed the rolling 7-day cap of 6000000 for this recipient (4000000 already sent)",
    );
    const other = await prepare(h, { assetSymbol: "USDC", amountRaw: "3000000", toAddress: OTHER });
    await execute(h, other.quoteId);
    h.clock.now += 2 * 24 * 3600_000; // still inside the week, outside the day
    const again = await prepare(h, { assetSymbol: "USDC", amountRaw: "3000000" });
    expect(await refused(h, again.quoteId)).toMatch(/rolling 7-day cap of 6000000 for this recipient \(4000000 already sent\)/);
    h.clock.now += 6 * 24 * 3600_000;
    const week = await prepare(h, { assetSymbol: "USDC", amountRaw: "3000000" });
    await execute(h, week.quoteId);
  });

  it("holds each EVM network's asset to its own caps", async () => {
    const h = makeHarness({
      config: policyConfig([{ chain: "evm", symbol: "USDC", evmNetwork: "base_mainnet", tokenAddress: BASE_USDC, perTx: 1_000_000, daily: 2_000_000 }]),
      env: { OPENHUMAN_SOLANA_CLUSTER: "mainnet" },
    });
    await setupWallet(h);
    const { sent } = mockEvm(h.net);
    const arb = await h.call("wallet_prepare_transfer", { chain: "evm", evmNetwork: "arbitrum_one", toAddress: EVM_TO, amountRaw: "1", assetSymbol: "USDC" });
    expect(await refused(h, (arb as { quoteId: string }).quoteId)).toBe("WALLET_POLICY_REFUSED: USDC on evm is not in the wallet policy's asset allowlist");
    const big = await h.call("wallet_prepare_transfer", { chain: "evm", evmNetwork: "base_mainnet", toAddress: EVM_TO, amountRaw: "1000001", assetSymbol: "USDC" });
    expect(await refused(h, (big as { quoteId: string }).quoteId)).toBe("WALLET_POLICY_REFUSED: 1000001 USDC (atomic) exceeds the per-transaction cap of 1000000");
    expect(sent).toHaveLength(0);
  });

  it("counts an in-doubt send against the caps and never retries it", async () => {
    const h = makeHarness({ config: policyConfig([USDC_CAPS]) });
    await setupWallet(h);
    mockSolana(h.net);
    h.net.onRpc("sendTransaction", () => {
      throw new Error("node fell over");
    });
    const q = await prepare(h, { assetSymbol: "USDC", amountRaw: "5000000" });
    await expect(execute(h, q.quoteId)).rejects.toThrow(/node fell over/);
    const ledger = (await h.call("wallet_ledger_list", { limit: 5 })) as Array<Record<string, unknown>>;
    expect(ledger[0]).toMatchObject({ status: "in_doubt" });
    const next = await prepare(h, { assetSymbol: "USDC", amountRaw: "4000000", toAddress: OTHER });
    expect(await refused(h, next.quoteId)).toMatch(/would exceed the rolling 24h cap of 8000000 \(5000000 already spent\)/);
    await expect(execute(h, q.quoteId)).rejects.toThrow(/unavailable or expired/);
  });

  it("refuses everything when the policy cannot be read, rather than falling back to no caps", async () => {
    const h = makeHarness({ config: `${policyConfig([SOL_CAPS])}\n[wallet_policy.typo]\nx = 1\n` });
    await setupWallet(h);
    const { sent } = mockSolana(h.net);
    const q = await prepare(h);
    expect(await refused(h, q.quoteId)).toMatch(/^WALLET_POLICY_REFUSED: the wallet policy cannot be read/);
    const h2 = makeHarness({ config: "[wallet_policy\nbroken" });
    await setupWallet(h2);
    mockSolana(h2.net);
    const q2 = await prepare(h2);
    expect(await refused(h2, q2.quoteId)).toMatch(/^WALLET_POLICY_REFUSED: the wallet policy cannot be read/);
    expect(sent).toHaveLength(0);
  });

  it("decides before it opens the key: a refusal needs no master key, so none was read", async () => {
    const h = makeHarness({ config: policyConfig([USDC_CAPS]) });
    await setupWallet(h);
    mockSolana(h.net);
    const q = await prepare(h, { assetSymbol: "USDC", amountRaw: "5000001" });
    h.env({ OPENHUMAN_KEYRING_MASTER_KEY_FILE: "" }); // the key is gone; a cap refusal must still be the answer
    expect(await refused(h, q.quoteId)).toBe("WALLET_POLICY_REFUSED: 5000001 USDC (atomic) exceeds the per-transaction cap of 5000000");
    const ok = await prepare(h, { assetSymbol: "USDC", amountRaw: "1" });
    await expect(execute(h, ok.quoteId)).rejects.toThrow(/the wallet cannot sign: the wallet's master key is not set/);
  });

  it("has no DRY_RUN of its own: hands/wallet decides that before it ever asks for an execute", async () => {
    const h = makeHarness({ config: policyConfig([SOL_CAPS]), env: { DRY_RUN: "1" } });
    await setupWallet(h);
    const { sent } = mockSolana(h.net);
    const q = await prepare(h);
    await execute(h, q.quoteId);
    expect(sent).toHaveLength(1);
  });

  it("applies the caps whatever apply_to_interactive says: there is no uncapped RPC execute", async () => {
    const h = makeHarness({ config: policyConfig([USDC_CAPS]).replace("apply_to_interactive = true", "apply_to_interactive = false") });
    await setupWallet(h);
    mockSolana(h.net);
    const q = await prepare(h, { assetSymbol: "USDC", amountRaw: "5000001" });
    expect(await refused(h, q.quoteId)).toMatch(/exceeds the per-transaction cap of 5000000/);
  });

  it("ignores policy overrides in the process environment", async () => {
    const h = makeHarness({
      config: policyConfig([USDC_CAPS]),
      env: { OPENHUMAN_WALLET_POLICY_ASSETS: '[{"chain":"solana","symbol":"SOL"}]', OPENHUMAN_WALLET_POLICY_AUTOMATION_ENABLED: "true" },
    });
    await setupWallet(h);
    mockSolana(h.net);
    const q = await prepare(h);
    expect(await refused(h, q.quoteId)).toMatch(/SOL on solana is not in the wallet policy's asset allowlist/);
  });
});

describe("prepared ids", () => {
  it("are single use: a replay is refused, and so is one the core never prepared", async () => {
    const h = makeHarness({ config: policyConfig([SOL_CAPS]) });
    await setupWallet(h);
    const { sent } = mockSolana(h.net);
    const q = await prepare(h);
    await execute(h, q.quoteId);
    await expect(execute(h, q.quoteId)).rejects.toThrow(/prepared quote is unavailable or expired/);
    await expect(execute(h, "q-never-prepared")).rejects.toThrow(/prepared quote is unavailable or expired/);
    expect(sent).toHaveLength(1);
  });

  it("do not survive a restart", async () => {
    const h = makeHarness({ config: policyConfig([SOL_CAPS]) });
    await setupWallet(h);
    mockSolana(h.net);
    const q = await prepare(h);
    const restarted = createMethods({ fetch: h.net.fetch, now: () => h.clock.now });
    await expect(restarted.wallet_execute_prepared!({ quoteId: q.quoteId, confirmed: true }, h.ctx)).rejects.toThrow(/unavailable or expired/);
  });

  it("expire", async () => {
    const h = makeHarness({ config: policyConfig([SOL_CAPS]) });
    await setupWallet(h);
    const { sent } = mockSolana(h.net);
    const q = await prepare(h);
    h.clock.now += 6 * 60_000;
    await expect(execute(h, q.quoteId)).rejects.toThrow(/^WALLET_POLICY_REFUSED: the quote expired at \d+ \(now \d+\); prepare it again$/);
    expect(sent).toHaveLength(0);
  });

  it("need confirmed: true", async () => {
    const h = makeHarness({ config: policyConfig([SOL_CAPS]) });
    await setupWallet(h);
    mockSolana(h.net);
    const q = await prepare(h);
    await expect(h.call("wallet_execute_prepared", { quoteId: q.quoteId, confirmed: false })).rejects.toThrow(/confirmed must be true/);
    await expect(h.call("wallet_execute_prepared", { quoteId: q.quoteId })).rejects.toThrow(/confirmed must be true/);
    await execute(h, q.quoteId); // the quote was not spent by the refusals
  });

  it("cannot outlive the wallet they were prepared for", async () => {
    const h = makeHarness({ config: policyConfig([SOL_CAPS]) });
    await setupWallet(h);
    mockSolana(h.net);
    const q = await prepare(h);
    const { phrase } = await setupWalletForce(h);
    expect(phrase).toBeTruthy();
    await expect(execute(h, q.quoteId)).rejects.toThrow(/unavailable or expired/);
  });
});

async function setupWalletForce(h: ReturnType<typeof makeHarness>): Promise<{ phrase: string }> {
  const { generatePhrase } = await import("../../src/wallet/derive.js");
  const phrase = generatePhrase(12);
  const derived = (await h.call("wallet_derive_recovery_material", { phrase })) as { accounts: unknown[] };
  const encrypted = (await h.call("encrypt_secret", { plaintext: phrase })) as string;
  await h.call("wallet_setup", { consentGranted: true, source: "imported", mnemonicWordCount: 12, encryptedMnemonic: encrypted, accounts: derived.accounts, force: true });
  return { phrase };
}

describe("balances and status", () => {
  it("reads balances as rows the hand accepts, and a failing endpoint reads as not ready", async () => {
    const h = makeHarness();
    await setupWallet(h);
    mockSolana(h.net);
    h.net.onHttp((u) => u.includes("blockstream"), () => new Response("down", { status: 503 }));
    const rows = (await h.call("wallet_balances")) as Array<Record<string, unknown>>;
    expect(rows.find((r) => r.chain === "solana")).toMatchObject({ providerStatus: "ready", raw: "2500000000", symbol: "SOL" });
    expect(rows.find((r) => r.chain === "btc")).toMatchObject({ providerStatus: "unavailable" });
    expect(rows.some((r) => r.chain === "evm")).toBe(false); // devnet: no EVM networks
    const h2 = makeHarness({ env: { OPENHUMAN_SOLANA_CLUSTER: "mainnet" } });
    await setupWallet(h2);
    mockSolana(h2.net);
    mockEvm(h2.net);
    h2.net.onHttp((u) => u.includes("blockstream"), () => new Response(JSON.stringify({ chain_stats: { funded_txo_sum: 700, spent_txo_sum: 200 }, mempool_stats: { funded_txo_sum: 0, spent_txo_sum: 0 } })));
    const rows2 = (await h2.call("wallet_balances")) as Array<Record<string, unknown>>;
    expect(rows2.filter((r) => r.chain === "evm").map((r) => r.evmNetwork).sort()).toEqual(["arbitrum_one", "base_mainnet", "ethereum_mainnet", "optimism_mainnet"]);
    expect(rows2.find((r) => r.chain === "btc")).toMatchObject({ providerStatus: "ready", raw: "500" });
  });

  it("reports a transaction's state", async () => {
    const h = makeHarness();
    await setupWallet(h);
    h.net.onRpc("eth_getTransactionReceipt", (p) => (p[0] === "0xdone" ? { status: "0x1", blockNumber: "0x10" } : p[0] === "0xbad" ? { status: "0x0" } : null));
    for (const [hash, state] of [["0xdone", "confirmed"], ["0xbad", "failed"], ["0xwait", "pending"]]) {
      expect(await h.call("wallet_tx_status", { chain: "evm", hash, evmNetwork: "base_mainnet" })).toMatchObject({ state });
    }
    h.net.onRpc("getSignatureStatuses", () => ({ value: [{ slot: 5, err: null, confirmationStatus: "finalized" }] }));
    expect(await h.call("wallet_tx_status", { chain: "solana", hash: "sig" })).toMatchObject({ state: "confirmed" });
  });
});
