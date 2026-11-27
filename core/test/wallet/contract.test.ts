// Every wallet_*, web3_*, x402_* and encrypt_secret method of the RPC contract
// answers in the recorded shape, and takes the params the callers send.
import { Keypair } from "@solana/web3.js";
import { describe, expect, it } from "vitest";
import { assertMatchesContract, assertParamsMatchContract, methodNames } from "../contract.js";
import { type Harness, json, makeHarness, mockEvm, mockSolana, policyConfig, setupWallet } from "./helpers.js";
import { build } from "../../src/wallet/giveaway.js";
import { TOKEN_2022_PROGRAM } from "../../src/wallet/solana-program.js";
import { base58 } from "@scure/base";
import { methods } from "../../src/wallet/index.js";

const OURS = (name: string): boolean => /^(wallet_|web3_|x402_)/.test(name) || name === "encrypt_secret";

describe("the contract", () => {
  it("is served in full: every fixture method is a handler, and every handler has a fixture (or is a documented extra)", () => {
    const fixtures = methodNames().filter(OURS);
    expect(fixtures.length).toBeGreaterThanOrEqual(20);
    for (const name of fixtures) expect(methods[name], `${name} has no handler`).toBeTypeOf("function");
    const extras = Object.keys(methods).filter((m) => !fixtures.includes(m));
    expect(extras.sort()).toEqual(["web3_bridge_routes", "web3_swap_routes"]);
  });

  it("answers each method in the recorded shape, to the params the callers send", async () => {
    const REWARDS = "REWArDioXgQJ2fZKkfu9LCLjQfRwYWVVfsvcsR5hoXi";
    const MINT = "2AF7CqwieUjUPALL7icuZtL3X7wENdjUjGBMmfV2pump";
    const BASE_USDC = "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913";
    const h: Harness = makeHarness({
      env: { OPENHUMAN_SOLANA_CLUSTER: "mainnet" },
      config: policyConfig(
        [{ chain: "solana", symbol: "SOL", perTx: 10_000_000, daily: 20_000_000 }],
        `\n[wallet_policy.accounts.solana_1]\nallowed_programs = ["${REWARDS}"]\n\n[[wallet_policy.accounts.solana_1.mints]]\nmint = "${MINT}"\nper_tx_max_raw = 5000000\ndaily_max_raw = 8000000\n`,
      ),
    });
    mockSolana(h.net);
    mockEvm(h.net);
    const covered = new Set<string>();
    const check = async (method: string, params: Record<string, unknown>): Promise<any> => { // eslint-disable-line @typescript-eslint/no-explicit-any
      assertParamsMatchContract(method, params);
      const out = await h.call(method, params);
      assertMatchesContract(method, out);
      covered.add(method);
      return out;
    };
    await check("wallet_status", {});
    await check("wallet_generate_mnemonic", { wordCount: 12 });
    const phrase = (await h.call("wallet_generate_mnemonic", { wordCount: 24 })) as { phrase: string };
    const derived = await check("wallet_derive_recovery_material", { phrase: phrase.phrase });
    const encrypted = await check("encrypt_secret", { plaintext: phrase.phrase });
    await check("wallet_setup", { consentGranted: true, source: "generated", mnemonicWordCount: 24, encryptedMnemonic: encrypted, accounts: derived.accounts.map((a: Record<string, unknown>) => ({ chain: a.chain, address: a.address, derivationPath: a.derivationPath })) });
    const status = await check("wallet_status", {});
    expect(status.configured).toBe(true);
    const giveaway = await check("wallet_account", { chain: "solana", accountIndex: 1 });
    await check("wallet_balances", {});
    const quote = await check("wallet_prepare_transfer", { chain: "solana", toAddress: Keypair.generate().publicKey.toBase58(), amountRaw: "1000" });
    const executed = await check("wallet_execute_prepared", { quoteId: quote.quoteId, confirmed: true });
    expect(executed.transactionHash).toBeTruthy();
    await check("wallet_ledger_list", { limit: 50 });
    h.net.onRpc("eth_getTransactionReceipt", () => ({ status: "0x1" }));
    await check("wallet_tx_status", { chain: "evm", hash: "0xabc", evmNetwork: "base_mainnet" });

    const built = build("create", JSON.stringify({ program_id: REWARDS, mint: MINT, token_program: TOKEN_2022_PROGRAM, authority: giveaway.address, merkle_root: "ab".repeat(32), total_amount: 3_500_042, clawback_ts: 1_798_000_000, recent_blockhash: "LbUiWL3xVV8hTFYBVdbTNrpDo41NKS6o3LHHuDzjfcY" })) as Record<string, string>;
    const tokenAccount = (amount: bigint): string => {
      const d = Buffer.alloc(165);
      Buffer.from(base58.decode(MINT)).copy(d, 0);
      Buffer.from(base58.decode(giveaway.address)).copy(d, 32);
      d.writeBigUInt64LE(amount, 64);
      return d.toString("base64");
    };
    h.net.onRpc("getTokenAccountsByOwner", (p) => ({ context: { slot: 9 }, value: (p[1] as { programId: string }).programId === TOKEN_2022_PROGRAM ? [{ pubkey: built.authority_token_account, account: { data: [tokenAccount(9_000_000n), "base64"] } }] : [] }));
    h.net.onRpc("simulateTransaction", () => ({ context: { slot: 9 }, value: { err: null, accounts: [{ data: [tokenAccount(9_000_000n - 3_500_042n), "base64"] }] } }));
    const program = await check("wallet_prepare_program", { accountIndex: 1, transaction: built.transaction_b64, expectedDebits: [{ mint: MINT, amountRaw: "3500042" }] });
    expect(program.simulation.err).toBeNull();
    await check("wallet_execute_prepared_program", { accountIndex: 1, quoteId: program.quoteId });

    h.net.onHttp((u) => u.includes("dln.debridge"), () => json({ tx: { to: "0xef4fb24ad0916217251f553c0596f8edc630eb66", data: "0x1234", value: "0" } }));
    const swap = await check("web3_swap_quote", { chainId: "8453", tokenIn: "0x0000000000000000000000000000000000000000", tokenInAmount: "1000", tokenOut: BASE_USDC, tokenOutRecipient: "0x1111111111111111111111111111111111111111", senderAddress: "0x1111111111111111111111111111111111111111", slippage: "1.0" });
    expect(swap.expiresAtMs).toBeGreaterThan(h.clock.now);
    await check("web3_swap_execute", { quoteId: swap.quoteId, confirmed: true });
    const bridge = await check("web3_bridge_quote", { srcChainId: "8453", srcChainTokenIn: BASE_USDC, srcChainTokenInAmount: "1000", dstChainId: "42161", dstChainTokenOut: "0xaf88d065e77c8cC2239327C5EDb3A432268e5831", dstChainTokenOutAmount: "debridge:auto", dstChainTokenOutRecipient: "0x1111111111111111111111111111111111111111", srcChainOrderAuthorityAddress: "0x1111111111111111111111111111111111111111", dstChainOrderAuthorityAddress: "0x1111111111111111111111111111111111111111" });
    await check("web3_bridge_execute", { quoteId: bridge.quoteId, confirmed: true });
    const dapp = await check("web3_dapp_call", { contractAddress: BASE_USDC, calldata: "0x095ea7b3", valueRaw: "0", evmNetwork: "base_mainnet" });
    expect(dapp.quote).toMatchObject({ contractAddress: BASE_USDC, calldata: "0x095ea7b3", valueRaw: "0" });
    await check("web3_dapp_execute", { quoteId: dapp.quoteId, confirmed: true });

    const challenge = Buffer.from(JSON.stringify({ x402Version: 2, resource: { url: "https://api.example.test/x" }, accepts: [{ scheme: "exact", network: "eip155:8453", amount: "10", asset: BASE_USDC, payTo: "0x2222222222222222222222222222222222222222", maxTimeoutSeconds: 60, extra: { name: "USD Coin", version: "2" } }] })).toString("base64");
    h.net.onHttp((u) => u.startsWith("https://api.example.test/"), (call) => (call.headers["payment-signature"] ? new Response("{}", { headers: { "content-type": "application/json", "payment-response": "x" } }) : new Response("", { status: 402, headers: { "payment-required": challenge } })));
    const paid = await check("x402_fetch", { url: "https://api.example.test/x", method: "GET", maxAmountAtomic: 100 });
    expect(paid.payment).toMatchObject({ amountAtomic: "10", asset: BASE_USDC, settled: true });
    await check("x402_list_payments", { limit: 500 });
    await setupWallet(makeHarness()); // (a second wallet, elsewhere, is independent)

    const wanted = methodNames().filter(OURS);
    expect([...covered].sort()).toEqual(wanted.sort());
  });
});
