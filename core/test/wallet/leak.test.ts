// The leak test: run every wallet method with a known throwaway phrase and grep
// everything that leaves the module (responses, errors, log lines, outbound
// requests) and everything it writes to disk for the phrase, the seed, the
// private keys and the master key, in every encoding they could be printed in.
import { readFileSync, readdirSync, statSync } from "node:fs";
import { join } from "node:path";
import { entropyToMnemonic } from "@scure/bip39";
import { wordlist } from "@scure/bip39/wordlists/english";
import { base58 } from "@scure/base";
import { Keypair } from "@solana/web3.js";
import { describe, expect, it } from "vitest";
import { evmPrivateKey, seedOf, solanaKeypair } from "../../src/wallet/derive.js";
import { safeError, trackSecret } from "../../src/wallet/errors.js";
import { build } from "../../src/wallet/giveaway.js";
import { TOKEN_2022_PROGRAM } from "../../src/wallet/solana-program.js";
import { type Harness, json, makeHarness, mockEvm, mockSolana, policyConfig, setupWallet } from "./helpers.js";

// A fixed, valid, throwaway phrase that has never held anything.
const PHRASE = entropyToMnemonic(new Uint8Array(32).fill(0x42), wordlist);

interface Needle {
  what: string;
  value: string;
}

function needles(masterKey: string): Needle[] {
  const seed = seedOf(PHRASE);
  const sol0 = solanaKeypair(seed, "m/44'/501'/0'/0'");
  const sol1 = solanaKeypair(seed, "m/44'/501'/1'/0'");
  const evm = evmPrivateKey(seed, "m/44'/60'/0'/0/0");
  const all: Needle[] = [{ what: "the phrase", value: PHRASE }, { what: "the master key", value: masterKey }];
  const words = PHRASE.split(" ");
  for (let i = 0; i + 3 <= words.length; i++) all.push({ what: `three consecutive words from ${i}`, value: words.slice(i, i + 3).join(" ") });
  const bytes = (what: string, b: Uint8Array): void => {
    const buf = Buffer.from(b);
    all.push({ what: `${what} hex`, value: buf.toString("hex") }, { what: `${what} base64`, value: buf.toString("base64") }, { what: `${what} base58`, value: base58.encode(buf) });
    all.push({ what: `${what} json array`, value: `[${Array.from(buf.subarray(0, 8)).join(",")}` });
  };
  bytes("the BIP-39 seed", seed);
  bytes("the first half of the seed", seed.subarray(0, 32));
  for (const [name, kp] of [["solana 0", sol0], ["solana 1", sol1]] as const) {
    bytes(`${name} secret key`, kp.secretKey);
    bytes(`${name} private seed`, kp.secretKey.subarray(0, 32));
  }
  bytes("the evm private key", Buffer.from(evm.slice(2), "hex"));
  all.push({ what: "the evm private key (0x)", value: evm });
  all.push({ what: "the master key base64", value: Buffer.from(masterKey, "hex").toString("base64") });
  return all;
}

function scan(outputs: Array<{ where: string; text: string }>, all: Needle[]): string[] {
  const leaks: string[] = [];
  for (const out of outputs) for (const n of all) if (out.text.includes(n.value)) leaks.push(`${n.what} in ${out.where}`);
  return leaks;
}

function filesUnder(dir: string): string[] {
  return readdirSync(dir).flatMap((name) => {
    const path = join(dir, name);
    return statSync(path).isDirectory() ? filesUnder(path) : [path];
  });
}

describe("no method, error, log or file ever carries key material", () => {
  it("runs every method and finds nothing", async () => {
    const REWARDS = "REWArDioXgQJ2fZKkfu9LCLjQfRwYWVVfsvcsR5hoXi";
    const MINT = "2AF7CqwieUjUPALL7icuZtL3X7wENdjUjGBMmfV2pump";
    const accountsToml = `\n[wallet_policy.accounts.solana_1]\nallowed_programs = ["${REWARDS}"]\n\n[[wallet_policy.accounts.solana_1.mints]]\nmint = "${MINT}"\nper_tx_max_raw = 5000000\ndaily_max_raw = 8000000\n`;
    const h = makeHarness({
      env: { OPENHUMAN_SOLANA_CLUSTER: "mainnet" },
      config: policyConfig(
        [
          { chain: "solana", symbol: "SOL", perTx: 1_000_000_000, daily: 2_000_000_000 },
          { chain: "evm", symbol: "ETH", evmNetwork: "base_mainnet", perTx: 10n ** 17n },
        ],
        accountsToml,
      ),
    });
    const masterKey = readFileSync(h.keyFile, "utf8").trim();
    const seen: Array<{ where: string; text: string }> = [];
    const record = (where: string, value: unknown): void => {
      seen.push({ where, text: typeof value === "string" ? value : JSON.stringify(value) });
    };
    const run = async (method: string, params: Record<string, unknown> = {}): Promise<unknown> => {
      record(`${method} params`, params); // (params may hold the phrase on purpose; it is only grepped for echoes below)
      try {
        const out = await h.call(method, params);
        record(`${method} response`, out);
        return out;
      } catch (error) {
        record(`${method} error`, (error as Error).message);
        record(`${method} error stack`, (error as Error).stack ?? "");
        return undefined;
      }
    };
    const sendsPhrase = new Set<string>();

    mockSolana(h.net);
    mockEvm(h.net);
    // setup, as bin/wallet-setup does it
    await run("wallet_status");
    sendsPhrase.add("wallet_derive_recovery_material params");
    await run("wallet_derive_recovery_material", { phrase: PHRASE });
    sendsPhrase.add("encrypt_secret params");
    const encrypted = (await run("encrypt_secret", { plaintext: PHRASE })) as string;
    const derived = (await h.call("wallet_derive_recovery_material", { phrase: PHRASE })) as { accounts: Array<Record<string, unknown>> };
    await run("wallet_setup", { consentGranted: true, source: "imported", mnemonicWordCount: 24, encryptedMnemonic: encrypted, accounts: derived.accounts });
    // misuse that puts the phrase in the wrong place: the error must not echo it
    sendsPhrase.add("wallet_setup params");
    await run("wallet_setup", { consentGranted: true, source: "imported", mnemonicWordCount: 24, encryptedMnemonic: PHRASE, accounts: derived.accounts, force: true });
    sendsPhrase.add("wallet_derive_recovery_material params");
    await run("wallet_derive_recovery_material", { phrase: `${PHRASE} extra` });
    await run("wallet_derive_recovery_material", { phrase: PHRASE.replace(/\w+$/, "zoo") });
    await run("wallet_generate_mnemonic", { wordCount: 12 });
    await run("wallet_status");
    await run("wallet_account", { chain: "solana", accountIndex: 0 });
    await run("wallet_account", { chain: "solana", accountIndex: 1 });
    await run("wallet_account", { chain: "evm", accountIndex: 0 });
    await run("wallet_account", { chain: "solana", accountIndex: 7 });
    await run("wallet_balances");
    // transfers
    const t1 = (await run("wallet_prepare_transfer", { chain: "solana", toAddress: Keypair.generate().publicKey.toBase58(), amountRaw: "1000" })) as { quoteId: string };
    await run("wallet_execute_prepared", { quoteId: t1.quoteId, confirmed: true });
    await run("wallet_execute_prepared", { quoteId: t1.quoteId, confirmed: true });
    const t2 = (await run("wallet_prepare_transfer", { chain: "evm", evmNetwork: "base_mainnet", toAddress: "0x1111111111111111111111111111111111111111", amountRaw: "5" })) as { quoteId: string };
    await run("wallet_execute_prepared", { quoteId: t2.quoteId, confirmed: true });
    const t3 = (await run("wallet_prepare_transfer", { chain: "solana", toAddress: Keypair.generate().publicKey.toBase58(), amountRaw: "9999999999999" })) as { quoteId: string };
    await run("wallet_execute_prepared", { quoteId: t3.quoteId, confirmed: true }); // refused by a cap
    await run("wallet_tx_status", { chain: "evm", hash: "0xabc", evmNetwork: "base_mainnet" });
    h.net.onRpc("eth_getTransactionReceipt", () => null);
    await run("wallet_tx_status", { chain: "evm", hash: "0xabc", evmNetwork: "base_mainnet" });
    await run("wallet_ledger_list", { limit: 50 });
    // program
    const giveaway = (await h.call("wallet_account", { chain: "solana", accountIndex: 1 })) as { address: string };
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
    const p1 = (await run("wallet_prepare_program", { accountIndex: 1, transaction: built.transaction_b64, expectedDebits: [{ mint: MINT, amountRaw: "3500042" }] })) as { quoteId: string };
    await run("wallet_execute_prepared_program", { accountIndex: 1, quoteId: p1.quoteId });
    await run("wallet_prepare_program", { accountIndex: 1, transaction: built.transaction_b64, expectedDebits: [] });
    // swaps, bridges, dapp
    h.net.onHttp((u) => u.includes("dln.debridge"), () => json({ tx: { to: "0xef4fb24ad0916217251f553c0596f8edc630eb66", data: "0x1234", value: "0" }, estimation: {} }));
    const s1 = (await run("web3_swap_quote", { chainId: "8453", tokenIn: "0x0000000000000000000000000000000000000000", tokenInAmount: "1", tokenOut: "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913", slippage: "0.5" })) as { quoteId: string };
    await run("web3_swap_execute", { quoteId: s1.quoteId, confirmed: true });
    const b1 = (await run("web3_bridge_quote", { srcChainId: "8453", srcChainTokenIn: "a", srcChainTokenInAmount: "1", dstChainId: "42161", dstChainTokenOut: "b", dstChainTokenOutAmount: "debridge:auto", dstChainTokenOutRecipient: "0x1111111111111111111111111111111111111111", srcChainOrderAuthorityAddress: "x", dstChainOrderAuthorityAddress: "y" })) as { quoteId: string };
    await run("web3_bridge_execute", { quoteId: b1.quoteId, confirmed: true });
    const d1 = (await run("web3_dapp_call", { contractAddress: "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913", calldata: `0x095ea7b3${"0".repeat(24)}ef4fb24ad0916217251f553c0596f8edc630eb66${"0".repeat(63)}1`, valueRaw: "0", evmNetwork: "base_mainnet" })) as { quoteId: string };
    await run("web3_dapp_execute", { quoteId: d1.quoteId, confirmed: true });
    await run("web3_dapp_execute", { quoteId: d1.quoteId, confirmed: true });
    // x402
    const challenge = Buffer.from(JSON.stringify({ x402Version: 2, resource: { url: "https://api.example.test/x" }, accepts: [{ scheme: "exact", network: "eip155:8453", amount: "10", asset: "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913", payTo: "0x2222222222222222222222222222222222222222", maxTimeoutSeconds: 60, extra: { name: "USD Coin", version: "2" } }] })).toString("base64");
    h.net.onHttp((u) => u.startsWith("https://api.example.test/"), (call) => (call.headers["payment-signature"] ? new Response("ok", { headers: { "payment-response": Buffer.from(JSON.stringify({ success: true, network: "eip155:8453" })).toString("base64") } }) : new Response("", { status: 402, headers: { "payment-required": challenge } })));
    await run("x402_fetch", { url: "https://api.example.test/x", method: "GET", maxAmountAtomic: 100 });
    await run("x402_fetch", { url: "https://api.example.test/x", method: "GET", maxAmountAtomic: 1 });
    await run("x402_list_payments", { limit: 500 });
    // a wrong master key, and a core with no key at all
    const wrong = makeHarness({ config: "" });
    await setupWallet(wrong);
    for (const method of ["wallet_balances"]) await wrong.call(method);
    h.env({ OPENHUMAN_KEYRING_MASTER_KEY_FILE: wrong.keyFile });
    await run("wallet_prepare_transfer", { chain: "solana", toAddress: Keypair.generate().publicKey.toBase58(), amountRaw: "1" });
    await run("wallet_execute_prepared", { quoteId: "q-x", confirmed: true });
    await run("wallet_account", { chain: "solana", accountIndex: 1 });
    h.env({ OPENHUMAN_KEYRING_MASTER_KEY_FILE: "" });
    await run("encrypt_secret", { plaintext: PHRASE });
    await run("wallet_account", { chain: "solana", accountIndex: 1 });

    expect(seen.filter((s) => s.where.endsWith("response")).length).toBeGreaterThan(20);
    const all = needles(masterKey);
    const outputs = [
      ...seen.filter((s) => !s.where.endsWith("params")),
      ...h.logs.map((l, i) => ({ where: `log ${i}`, text: JSON.stringify(l) })),
      ...h.net.calls.map((c, i) => ({ where: `outbound request ${i} (${c.url.split("?")[0]})`, text: JSON.stringify({ url: c.url, headers: Object.entries(c.headers).filter(([k]) => k !== "payment-signature"), body: c.body }) })),
      ...filesUnder(h.stateDir).map((f) => ({ where: `file ${f.slice(h.stateDir.length)}`, text: readFileSync(f, "latin1") + readFileSync(f, "utf8") })),
    ];
    // the encrypted-mnemonic response is ciphertext; the phrase itself may only appear where it was sent in
    expect(scan(outputs, all)).toEqual([]);
    expect(sendsPhrase.size).toBeGreaterThan(0);
    // the scan is only worth anything if it can fail: planted leaks, in every encoding, are found
    const seed = seedOf(PHRASE);
    const planted = [
      { where: "planted phrase", text: `x ${PHRASE} y` },
      { where: "planted seed", text: JSON.stringify({ seed: Buffer.from(seed).toString("hex") }) },
      { where: "planted solana key", text: base58.encode(solanaKeypair(seed, "m/44'/501'/0'/0'").secretKey) },
      { where: "planted evm key", text: evmPrivateKey(seed, "m/44'/60'/0'/0/0") },
      { where: "planted master key", text: Buffer.from(masterKey, "hex").toString("base64") },
    ];
    const found = scan(planted, all).join("\n");
    for (const where of ["planted phrase", "planted seed", "planted solana key", "planted evm key", "planted master key"]) expect(found).toContain(`in ${where}`);
  });

  it("neither logs nor stores a phrase it generated", async () => {
    const h = makeHarness();
    const { phrase } = (await h.call("wallet_generate_mnemonic", { wordCount: 24 })) as { phrase: string };
    await setupWallet(h, phrase);
    await h.call("wallet_account", { chain: "solana", accountIndex: 1 });
    const text = [JSON.stringify(h.logs), JSON.stringify(h.net.calls), ...filesUnder(h.stateDir).map((f) => readFileSync(f, "utf8"))].join("\n");
    for (const w of [0, 7, 12, 20]) expect(text).not.toContain(phrase.split(" ").slice(w, w + 3).join(" "));
  });

  it("scrubs a tracked secret out of any error that would carry it", () => {
    trackSecret(PHRASE);
    const err = safeError(new Error(`rpc exploded while handling ${PHRASE} and more`));
    expect(err.message).not.toContain(PHRASE);
    expect(err.message).toContain("[redacted]");
  });

  it("never returns the seed from wallet_derive_recovery_material, only public accounts", async () => {
    const h = makeHarness();
    const out = (await h.call("wallet_derive_recovery_material", { phrase: PHRASE })) as Record<string, unknown>;
    expect(Object.keys(out)).toEqual(["accounts"]);
    for (const account of out.accounts as Array<Record<string, unknown>>) {
      expect(Object.keys(account).sort().every((k) => ["address", "chain", "derivationPath", "index", "label"].includes(k))).toBe(true);
    }
  });

  it("holds the phrase on disk only as an enc2 envelope under the master key", async () => {
    const h: Harness = makeHarness();
    await setupWallet(h, PHRASE);
    for (const file of filesUnder(h.stateDir)) {
      const text = readFileSync(file, "utf8");
      expect(text).not.toContain(PHRASE.split(" ").slice(0, 2).join(" "));
      expect(statSync(file).mode & 0o077).toBe(0);
    }
    expect(statSync(join(h.stateDir, "wallet")).mode & 0o077).toBe(0);
  });
});
