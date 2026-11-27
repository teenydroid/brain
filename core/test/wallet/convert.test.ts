import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { base58 } from "@scure/base";
import { Keypair, MessageV0, PublicKey, SystemProgram, VersionedTransaction } from "@solana/web3.js";
import { describe, expect, it } from "vitest";
import { signatureIsValid } from "../../src/wallet/solana-program.js";
import { BLOCKHASH, type Harness, json, makeHarness, mockEvm, mockSolana, parseEvm, policyConfig, setupWallet } from "./helpers.js";

const here = dirname(fileURLToPath(import.meta.url));
const lifiFixture = (name: string): Record<string, any> => // eslint-disable-line @typescript-eslint/no-explicit-any
  JSON.parse(readFileSync(join(here, "..", "..", "..", "hands", "tests", "wallet", "fixtures", "lifi", name), "utf8"));

const SOL_USDC = "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v";
const BASE_USDC = "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913";
const ZERO = "0x0000000000000000000000000000000000000000";

function solanaTx(payer: string, blockhash = "11111111111111111111111111111111"): string {
  const key = new PublicKey(payer);
  const message = MessageV0.compile({
    payerKey: key,
    instructions: [SystemProgram.transfer({ fromPubkey: key, toPubkey: Keypair.generate().publicKey, lamports: 5 })],
    recentBlockhash: blockhash,
  });
  return `0x${Buffer.from(new VersionedTransaction(message).serialize()).toString("hex")}`;
}

async function own(h: Harness, chain: string): Promise<string> {
  const status = (await h.call("wallet_status")) as { accounts: Array<{ chain: string; address: string }> };
  return status.accounts.find((a) => a.chain === chain)!.address;
}

describe("a Solana swap through deBridge", () => {
  it("quotes with the wallet's own addresses filled in, and signs the provider's transaction with a fresh blockhash", async () => {
    const h = makeHarness();
    await setupWallet(h);
    const { sent } = mockSolana(h.net);
    const me = await own(h, "solana");
    const hex = solanaTx(me);
    h.net.onHttp(
      (u) => u.startsWith("https://dln.debridge.finance/v1.0/chain/transaction"),
      () => json({ tokenIn: { address: "11111111111111111111111111111111", amount: "100000000" }, tokenOut: { address: SOL_USDC, amount: "9000000" }, tx: { data: hex } }),
    );
    const quote = (await h.call("web3_swap_quote", { chainId: "7565164", tokenIn: "11111111111111111111111111111111", tokenInAmount: "100000000", tokenOut: SOL_USDC, tokenOutRecipient: "OWN", senderAddress: "OWN", slippage: "1.0" })) as Record<string, any>; // eslint-disable-line @typescript-eslint/no-explicit-any
    // "OWN" is a placeholder the hand's test fixture uses; real callers send the address. Absent means own.
    expect(quote).toMatchObject({ kind: "swap", signingChain: "solana" });
    expect(quote.expiresAtMs).toBeGreaterThan(h.clock.now);
    const quote2 = (await h.call("web3_swap_quote", { chainId: 7565164, tokenIn: "11111111111111111111111111111111", tokenInAmount: "100000000", tokenOut: SOL_USDC })) as Record<string, any>; // eslint-disable-line @typescript-eslint/no-explicit-any
    const url = new URL(h.net.calls.filter((c) => c.url.includes("dln.debridge"))[1]!.url);
    expect(url.searchParams.get("senderAddress")).toBe(me);
    expect(url.searchParams.get("tokenOutRecipient")).toBe(me);
    expect(url.searchParams.get("slippage")).toBe("auto");
    expect(quote2.quote.tokenOut.amount).toBe("9000000");
    expect(sent).toHaveLength(0);
    const result = (await h.call("web3_swap_execute", { quoteId: quote2.quoteId, confirmed: true })) as { transactionHash: string };
    const tx = VersionedTransaction.deserialize(sent[0]!);
    expect(tx.message.recentBlockhash).toBe(BLOCKHASH);
    expect(signatureIsValid(tx.message.staticAccountKeys[0]!.toBytes(), tx.message.serialize(), tx.signatures[0]!)).toBe(true);
    expect(base58.encode(tx.signatures[0]!)).toBe(result.transactionHash);
    const ledger = (await h.call("wallet_ledger_list", { limit: 3 })) as Array<Record<string, unknown>>;
    expect(ledger[0]).toMatchObject({ kind: "swap", status: "broadcast", policyApplied: false, chain: "solana" });
  });

  it("refuses to sign a transaction whose fee payer is not the wallet", async () => {
    const h = makeHarness();
    await setupWallet(h);
    const { sent } = mockSolana(h.net);
    h.net.onHttp(
      (u) => u.includes("dln.debridge.finance"),
      () => json({ tx: { data: solanaTx(Keypair.generate().publicKey.toBase58()) } }),
    );
    const quote = (await h.call("web3_swap_quote", { chainId: "7565164", tokenIn: "a", tokenInAmount: "1", tokenOut: "b" })) as { quoteId: string };
    await expect(h.call("web3_swap_execute", { quoteId: quote.quoteId, confirmed: true })).rejects.toThrow(/does not name the signing account as its fee payer/);
    expect(sent).toHaveLength(0);
    const ledger = (await h.call("wallet_ledger_list", { limit: 3 })) as Array<Record<string, unknown>>;
    expect(ledger[0]).toMatchObject({ status: "in_doubt" });
  });
});

describe("an EVM swap through LI.FI, and a Solana bridge", () => {
  it("signs LI.FI's EVM transaction for its own chain: the same to, data and value", async () => {
    const h = makeHarness();
    await setupWallet(h);
    const { sent } = mockEvm(h.net);
    const me = await own(h, "evm");
    const recorded = lifiFixture("base_eth_to_usdc_swap.json");
    h.net.onHttp((u) => u.startsWith("https://li.quest/v1/quote"), () => json(recorded));
    const quote = (await h.call("web3_swap_quote", { chainId: "8453", tokenIn: ZERO, tokenInAmount: "5000000000000000", tokenOut: BASE_USDC, tokenOutRecipient: me, senderAddress: me, slippage: "lifi:1" })) as Record<string, any>; // eslint-disable-line @typescript-eslint/no-explicit-any
    expect(quote).toMatchObject({ kind: "swap", signingChain: "evm" });
    expect(quote.quote.provider).toBe("lifi");
    const url = new URL(h.net.calls.find((c) => c.url.includes("li.quest"))!.url);
    expect(url.searchParams.get("fromAddress")).toBe(me.toLowerCase());
    await h.call("web3_swap_execute", { quoteId: quote.quoteId, confirmed: true });
    const tx = parseEvm(sent[0]!);
    expect(tx.chainId).toBe(8453);
    expect(tx.to?.toLowerCase()).toBe(recorded.transactionRequest.to.toLowerCase());
    expect(tx.data).toBe(recorded.transactionRequest.data);
    expect(tx.value).toBe(5_000_000_000_000_000n);
  });

  it("bridges from Solana with LI.FI: the Solana tx is signed, and the bridge kind is recorded", async () => {
    const h = makeHarness();
    await setupWallet(h);
    const { sent } = mockSolana(h.net);
    const me = await own(h, "solana");
    const evmMe = await own(h, "evm");
    const recorded = lifiFixture("sol_to_base_usdc.json");
    recorded.transactionRequest.data = Buffer.from(solanaTx(me).slice(2), "hex").toString("base64");
    h.net.onHttp((u) => u.includes("li.quest"), () => json(recorded));
    const quote = (await h.call("web3_bridge_quote", {
      srcChainId: "7565164",
      srcChainTokenIn: "11111111111111111111111111111111",
      srcChainTokenInAmount: "100000000",
      dstChainId: "8453",
      dstChainTokenOut: BASE_USDC,
      dstChainTokenOutAmount: "lifi:auto",
      dstChainTokenOutRecipient: evmMe,
      srcChainOrderAuthorityAddress: me,
      dstChainOrderAuthorityAddress: evmMe,
    })) as Record<string, any>; // eslint-disable-line @typescript-eslint/no-explicit-any
    expect(quote).toMatchObject({ kind: "bridge", signingChain: "solana" });
    await h.call("web3_bridge_execute", { quoteId: quote.quoteId, confirmed: true });
    expect(sent).toHaveLength(1);
    const ledger = (await h.call("wallet_ledger_list", { limit: 3 })) as Array<Record<string, unknown>>;
    expect(ledger[0]).toMatchObject({ kind: "bridge", toAddress: evmMe });
  });

  it("refuses a chain the wallet has no account on, and an unknown provider tag, before asking anyone", async () => {
    const h = makeHarness();
    await setupWallet(h);
    await expect(h.call("web3_swap_quote", { chainId: "56", tokenIn: "a", tokenInAmount: "1", tokenOut: "b" })).rejects.toThrow(/no account on chain 56/);
    await expect(h.call("web3_swap_quote", { chainId: "8453", tokenIn: "a", tokenInAmount: "1", tokenOut: "b", slippage: "jupiter:1" })).rejects.toThrow(/no swap or bridge provider is called "jupiter"/);
    expect(h.net.calls).toHaveLength(0);
  });
});

describe("a quote's life", () => {
  async function quoted(h: Harness): Promise<string> {
    const me = await own(h, "evm");
    h.net.onHttp((u) => u.includes("li.quest"), () => json(lifiFixture("base_eth_to_usdc_swap.json")));
    const q = (await h.call("web3_swap_quote", { chainId: "8453", tokenIn: ZERO, tokenInAmount: "1", tokenOut: BASE_USDC, tokenOutRecipient: me, senderAddress: me, slippage: "lifi:1" })) as { quoteId: string };
    return q.quoteId;
  }

  it("is single-use, needs confirmed, expires, and is for its own kind only", async () => {
    const h = makeHarness();
    await setupWallet(h);
    const { sent } = mockEvm(h.net);
    const id = await quoted(h);
    await expect(h.call("web3_swap_execute", { quoteId: id })).rejects.toThrow(/confirmed must be true/);
    await expect(h.call("web3_bridge_execute", { quoteId: id, confirmed: true })).rejects.toThrow(/unavailable or expired/);
    await h.call("web3_swap_execute", { quoteId: id, confirmed: true });
    await expect(h.call("web3_swap_execute", { quoteId: id, confirmed: true })).rejects.toThrow(/unavailable or expired/);
    expect(sent).toHaveLength(1);
    const late = await quoted(h);
    h.clock.now += 91_000;
    await expect(h.call("web3_swap_execute", { quoteId: late, confirmed: true })).rejects.toThrow(/^WALLET_POLICY_REFUSED: the quote expired at/);
    expect(sent).toHaveLength(1);
  });

  it("does not touch the transfer caps, and is not counted by them", async () => {
    const h = makeHarness({ config: policyConfig([{ chain: "solana", symbol: "SOL", perTx: 1000, daily: 1000 }]) });
    await setupWallet(h);
    mockSolana(h.net);
    mockEvm(h.net);
    await h.call("web3_swap_execute", { quoteId: await quoted(h), confirmed: true });
    const t = (await h.call("wallet_prepare_transfer", { chain: "solana", toAddress: Keypair.generate().publicKey.toBase58(), amountRaw: "1000" })) as { quoteId: string };
    await h.call("wallet_execute_prepared", { quoteId: t.quoteId, confirmed: true }); // the swap row is not in the day's total
  });
});

/** A live bridge quote on Base whose input is `amount` USDC, from deBridge, whose contract is the pinned DlnSource. */
async function liveQuote(h: Harness, amount: string): Promise<{ quoteId: string }> {
  const me = await own(h, "evm");
  h.net.onHttp((u) => u.includes("dln.debridge.finance"), () => json({ tx: { to: "0xef4fb24ad0916217251f553c0596f8edc630eb66", data: "0x12", value: "0" } }));
  return (await h.call("web3_bridge_quote", {
    srcChainId: "8453",
    srcChainTokenIn: BASE_USDC,
    srcChainTokenInAmount: amount,
    dstChainId: "42161",
    dstChainTokenOut: "0xaf88d065e77c8cC2239327C5EDb3A432268e5831",
    dstChainTokenOutAmount: "debridge:auto",
    dstChainTokenOutRecipient: me,
    srcChainOrderAuthorityAddress: me,
    dstChainOrderAuthorityAddress: me,
  })) as { quoteId: string };
}

const approve = (spender: string, amount: bigint): string => `0x095ea7b3${spender.toLowerCase().replace(/^0x/, "").padStart(64, "0")}${amount.toString(16).padStart(64, "0")}`;

describe("a dapp call", () => {
  it("quotes an approval, echoes what it will sign, and signs exactly that to the contract with no value", async () => {
    const h = makeHarness();
    await setupWallet(h);
    const { sent } = mockEvm(h.net);
    await liveQuote(h, "1000000");
    const calldata = approve("ef4fb24ad0916217251f553c0596f8edc630eb66", 1_000_000n);
    const quote = (await h.call("web3_dapp_call", { contractAddress: BASE_USDC, calldata, valueRaw: "0", evmNetwork: "base_mainnet" })) as Record<string, any>; // eslint-disable-line @typescript-eslint/no-explicit-any
    expect(quote.quote).toEqual({ contractAddress: BASE_USDC, calldata, valueRaw: "0" });
    await h.call("web3_dapp_execute", { quoteId: quote.quoteId, confirmed: true });
    const tx = parseEvm(sent[0]!);
    expect(tx.to?.toLowerCase()).toBe(BASE_USDC.toLowerCase());
    expect(tx.data).toBe(calldata);
    expect(tx.chainId).toBe(8453);
    expect(tx.value ?? 0n).toBe(0n);
  });

  it("refuses a malformed call", async () => {
    const h = makeHarness();
    await setupWallet(h);
    await liveQuote(h, "1");
    const base = { contractAddress: BASE_USDC, calldata: approve("ef4fb24ad0916217251f553c0596f8edc630eb66", 1n), valueRaw: "0", evmNetwork: "base_mainnet" };
    await expect(h.call("web3_dapp_call", { ...base, contractAddress: "0xabc" })).rejects.toThrow(/not a valid EVM address/);
    await expect(h.call("web3_dapp_call", { ...base, calldata: "0xzz" })).rejects.toThrow(/calldata is not hex/);
    await expect(h.call("web3_dapp_call", { ...base, valueRaw: "-1" })).rejects.toThrow(/valueRaw/);
    await expect(h.call("web3_dapp_call", { ...base, evmNetwork: "polygon" })).rejects.toThrow(/evmNetwork/);
    await expect(h.call("web3_dapp_call", { ...base, extra: 1 })).rejects.toThrow(/does not take extra/);
  });
});

describe("routes", () => {
  it("lists deBridge's supported chains", async () => {
    const h = makeHarness();
    await setupWallet(h);
    h.net.onHttp((u) => u.includes("supported-chains-info"), () => json({ chains: [{ chainId: 1 }] }));
    expect(await h.call("web3_swap_routes")).toEqual({ chains: [{ chainId: 1 }] });
  });
});

describe("a dapp call is only ever an exact USDC approval for a contract we can account for", () => {
  const PINNED = "ef4fb24ad0916217251f553c0596f8edc630eb66";
  const STRANGER = "1234567890123456789012345678901234567890";
  const call = (h: Harness, over: Record<string, unknown>) =>
    h.call("web3_dapp_call", { contractAddress: BASE_USDC, calldata: approve(PINNED, 1000n), valueRaw: "0", evmNetwork: "base_mainnet", ...over });
  const refused = async (p: Promise<unknown>): Promise<string> => {
    try {
      await p;
    } catch (e) {
      return (e as Error).message;
    }
    throw new Error("expected a refusal");
  };

  it("refuses everything else, with a policy refusal, and stores nothing to execute", async () => {
    const h = makeHarness();
    await setupWallet(h);
    const { sent } = mockEvm(h.net);
    const prefix = "WALLET_POLICY_REFUSED: a dapp call may only approve USDC for a swap or bridge contract: ";
    // another contract
    expect(await refused(call(h, { contractAddress: "0x1111111111111111111111111111111111111111" }))).toBe(`${prefix}0x1111111111111111111111111111111111111111 is not the USDC on base_mainnet`);
    // the USDC of another network
    expect(await refused(call(h, { contractAddress: "0xaf88d065e77c8cC2239327C5EDb3A432268e5831" }))).toMatch(/is not the USDC on base_mainnet/);
    // a transfer, not an approval
    expect(await refused(call(h, { calldata: `0xa9059cbb${"0".repeat(24)}${PINNED}${"0".repeat(63)}1` }))).toBe(`${prefix}it is not approve(spender, amount)`);
    // value
    expect(await refused(call(h, { valueRaw: "1" }))).toBe(`${prefix}it sends value`);
    // an unlimited allowance
    expect(await refused(call(h, { calldata: approve(PINNED, 2n ** 256n - 1n) }))).toBe(`${prefix}an unlimited allowance is never signed`);
    // a spender nobody quoted
    expect(await refused(call(h, { calldata: approve(STRANGER, 1000n) }))).toBe(`${prefix}0x${STRANGER} is neither the contract of a live quote nor a pinned deBridge contract`);
    // extra bytes, a dirty address word, selector only
    expect(await refused(call(h, { calldata: `${approve(PINNED, 1n)}00` }))).toMatch(/exactly two 32-byte arguments/);
    expect(await refused(call(h, { calldata: `0x095ea7b3${"f".repeat(24)}${PINNED}${"0".repeat(64)}` }))).toMatch(/the spender is not an address/);
    expect(await refused(call(h, { calldata: "0x095ea7b3" }))).toMatch(/exactly two 32-byte arguments/);
    // a pinned spender needs a live quote too: with none, nothing is approved, however small
    expect(await refused(call(h, { calldata: approve(PINNED, 1n) }))).toBe(`${prefix}1 of USDC for 0x${PINNED} is not covered by a live swap or bridge quote (there is none, or its input is smaller)`);
    // and a quote id nobody gave us executes nothing
    await expect(h.call("web3_dapp_execute", { quoteId: "q-made-up", confirmed: true })).rejects.toThrow(/unavailable or expired/);
    expect(sent).toHaveLength(0);
  });

  it("bounds a pinned deBridge spender by the quote about to be executed, not by a very large number", async () => {
    const h = makeHarness();
    await setupWallet(h);
    mockEvm(h.net);
    await liveQuote(h, "5000000");
    expect(await refused(call(h, { calldata: approve(PINNED, 2n ** 254n) }))).toMatch(/is not covered by a live swap or bridge quote/);
    expect(await refused(call(h, { calldata: approve(PINNED, 5_000_001n) }))).toMatch(/is not covered/);
    await call(h, { calldata: approve(PINNED, 5_000_000n) });
    // a quote for a different input token covers nothing
    const other = makeHarness();
    await setupWallet(other);
    other.net.onHttp((u) => u.includes("dln.debridge.finance"), () => json({ tx: { to: `0x${PINNED}`, data: "0x12", value: "0" } }));
    const me = await own(other, "evm");
    await other.call("web3_bridge_quote", { srcChainId: "8453", srcChainTokenIn: ZERO, srcChainTokenInAmount: "9999999999", dstChainId: "42161", dstChainTokenOut: "b", dstChainTokenOutAmount: "debridge:auto", dstChainTokenOutRecipient: me, srcChainOrderAuthorityAddress: me, dstChainOrderAuthorityAddress: me });
    expect(await refused(call(other, { calldata: approve(PINNED, 1n) }))).toMatch(/is not covered/);
  });

  it("allows an approval for the contract a live quote of ours names, up to that quote's input, on that chain only", async () => {
    const h = makeHarness();
    await setupWallet(h);
    const { sent } = mockEvm(h.net);
    const me = await own(h, "evm");
    const recorded = lifiFixture("base_usdc_to_arbitrum.json");
    const spender = (recorded.transactionRequest as { to: string }).to;
    h.net.onHttp((u) => u.includes("li.quest"), () => json(recorded));
    expect(await refused(call(h, { calldata: approve(spender, 5_000_000n) }))).toMatch(/neither the contract of a live quote/); // before there is a quote
    const q = (await h.call("web3_bridge_quote", {
      srcChainId: "8453",
      srcChainTokenIn: BASE_USDC,
      srcChainTokenInAmount: "5000000",
      dstChainId: "42161",
      dstChainTokenOut: "0xaf88d065e77c8cC2239327C5EDb3A432268e5831",
      dstChainTokenOutAmount: "lifi:auto",
      dstChainTokenOutRecipient: me,
      srcChainOrderAuthorityAddress: me,
      dstChainOrderAuthorityAddress: me,
    })) as { quoteId: string };
    expect(await refused(call(h, { calldata: approve(spender, 5_000_001n) }))).toMatch(/is not covered by a live swap or bridge quote/); // more than the quote's input
    const approval = (await call(h, { calldata: approve(spender, 5_000_000n) })) as { quoteId: string };
    await h.call("web3_dapp_execute", { quoteId: approval.quoteId, confirmed: true });
    expect(parseEvm(sent[0]!).to?.toLowerCase()).toBe(BASE_USDC.toLowerCase());
    // another chain's USDC is not covered by a Base quote
    expect(await refused(call(h, { contractAddress: "0xaf88d065e77c8cC2239327C5EDb3A432268e5831", evmNetwork: "arbitrum_one", calldata: approve(spender, 1n) }))).toMatch(/neither the contract of a live quote|is not covered/);
    // once the quote has expired it vouches for nothing
    h.clock.now += 91_000;
    expect(await refused(call(h, { calldata: approve(spender, 1n) }))).toMatch(/neither the contract of a live quote/);
    expect(q.quoteId).toBeTruthy();
  });
});
