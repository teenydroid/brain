// wallet_status, wallet_setup, mnemonic and recovery material, encrypt_secret,
// balances, accounts, ledger, tx status, and the prepare/execute transfer flow.
import { createHash } from "node:crypto";
import type { CoreContext, RpcMethods } from "../server/types.js";
import { encryptEnc2, loadMasterKey } from "./crypto.js";
import {
  type WalletAccount,
  type WalletChain,
  WALLET_CHAINS,
  addressOf,
  deriveAllAccounts,
  evmPrivateKey,
  generatePhrase,
  seedOf,
  solanaKeypair,
} from "./derive.js";
import { EVM_NETWORKS, btcApiUrl, evmNetwork, solanaCluster, solanaExplorerTx, solanaUsdcMint } from "./endpoints.js";
import { PolicyRefusal, fail, refusal, trackSecret } from "./errors.js";
import * as evm from "./evm-chain.js";
import { type LedgerRecord, recordBroadcast } from "./ledger.js";
import { alreadyAttempted, alreadyAttemptedMessage, evaluateTransfer, loadPolicy, newRecord } from "./policy.js";
import type { Runtime, TransferQuote } from "./runtime.js";
import * as sol from "./solana-chain.js";
import {
  StoredWalletState,
  WALLET_NOT_CONFIGURED_MESSAGE,
  checkWordCount,
  ensureAccount,
  isPrimary,
  loadState,
  saveState,
  validateAccounts,
  withSeed,
  withStateLock,
} from "./store.js";
import { bigintOf, newId, requireConfirmed, requireStr, str } from "./util.js";

const TRANSFER_TTL_MS = 5 * 60_000;
const U64_MAX = (1n << 64n) - 1n;

type Params = Record<string, unknown>;

/** Fields the Rust controllers reject when they are not part of the call (deny_unknown_fields). */
function onlyFields(params: Params, allowed: readonly string[], method: string): void {
  for (const key of Object.keys(params)) {
    if (!allowed.includes(key)) throw fail(`invalid params: ${method} does not take ${key}`);
  }
}

function publicStatus(state: StoredWalletState | undefined): Record<string, unknown> {
  if (!state) {
    return { configured: false, onboardingCompleted: false, consentGranted: false, secretStored: false, accounts: [] };
  }
  const primary = state.accounts.filter(isPrimary);
  const additional = state.accounts.filter((a) => !isPrimary(a));
  return {
    configured: true,
    onboardingCompleted: state.consentGranted && primary.length > 0,
    consentGranted: state.consentGranted,
    secretStored: state.encryptedMnemonic.trim() !== "",
    source: state.source,
    mnemonicWordCount: state.mnemonicWordCount,
    accounts: primary,
    ...(additional.length > 0 ? { additionalAccounts: additional } : {}),
    updatedAtMs: state.updatedAtMs,
  };
}

const walletFingerprint = (state: StoredWalletState): string => createHash("sha256").update(state.encryptedMnemonic).digest("hex");

export function walletMethods(rt: Runtime): RpcMethods {
  const methods: RpcMethods = {};

  methods.wallet_status = async (_params, ctx) => publicStatus(loadState(ctx));

  methods.wallet_generate_mnemonic = async (params) => {
    const wordCount = typeof params.wordCount === "number" ? params.wordCount : Number(params.wordCount);
    const problem = checkWordCount(wordCount);
    if (problem) throw fail(problem);
    const phrase = generatePhrase(wordCount);
    trackSecret(phrase);
    return { phrase, wordCount };
  };

  methods.wallet_derive_recovery_material = async (params) => {
    const phrase = requireStr(params, "phrase");
    trackSecret(phrase);
    const words = phrase.trim().split(/\s+/).length;
    const problem = checkWordCount(words);
    if (problem) throw fail(`recovery phrase must contain one of 12, 15, 18, 21, 24 words`);
    let seed: Uint8Array;
    try {
      seed = seedOf(phrase);
    } catch (error) {
      throw fail((error as Error).message);
    }
    try {
      // Public addresses only. OpenHuman also returned the BIP-39 seed for its storage-key KDF; nothing here needs it, so it never leaves.
      return { accounts: deriveAllAccounts(seed) };
    } finally {
      seed.fill(0);
    }
  };

  methods.encrypt_secret = async (params, ctx) => {
    const plaintext = typeof params.plaintext === "string" ? params.plaintext : undefined;
    if (plaintext === undefined) throw fail("invalid params: plaintext is required");
    trackSecret(plaintext);
    if (plaintext === "") return "";
    let key: Buffer;
    try {
      key = loadMasterKey(ctx.env);
    } catch (error) {
      throw fail(`cannot encrypt: ${(error as Error).message}`);
    }
    try {
      return encryptEnc2(key, plaintext);
    } finally {
      key.fill(0);
    }
  };

  methods.wallet_setup = async (params, ctx) => {
    onlyFields(params, ["consentGranted", "source", "mnemonicWordCount", "encryptedMnemonic", "accounts", "force"], "wallet_setup");
    if (params.consentGranted !== true) throw fail("wallet setup requires explicit consent");
    const source = params.source;
    if (source !== "generated" && source !== "imported") throw fail("invalid params: source is generated or imported");
    const count = params.mnemonicWordCount;
    const countProblem = typeof count === "number" ? checkWordCount(count) : "invalid params: mnemonicWordCount is a number";
    if (countProblem) throw fail(countProblem);
    const encrypted = typeof params.encryptedMnemonic === "string" ? params.encryptedMnemonic.trim() : "";
    if (!encrypted) throw fail("wallet setup requires encrypted mnemonic material for signing-enabled local wallets");
    if (!Array.isArray(params.accounts)) throw fail("invalid params: accounts is a list");
    const checked = validateAccounts(params.accounts as WalletAccount[], false);
    if (typeof checked === "string") throw fail(checked);
    const force = params.force === true;
    // Not in OpenHuman: the ciphertext must open under this core's master key
    // and derive exactly the accounts named, so a setup can never store a
    // phrase this core cannot sign with, or addresses that are not its own.
    let key: Buffer;
    try {
      key = loadMasterKey(ctx.env);
    } catch (error) {
      throw fail(`wallet setup cannot proceed: ${(error as Error).message}`);
    }
    try {
      const { decryptEnc2 } = await import("./crypto.js");
      let phrase: string;
      try {
        const plain = decryptEnc2(key, encrypted);
        phrase = plain.toString("utf8");
        plain.fill(0);
      } catch {
        throw fail("wallet setup: the encrypted mnemonic does not open under this core's keyring master key");
      }
      trackSecret(phrase);
      let seed: Uint8Array;
      try {
        seed = seedOf(phrase);
      } catch {
        throw fail("wallet setup: the encrypted mnemonic is not a valid recovery phrase");
      }
      try {
        if (phrase.trim().split(/\s+/).length !== count) throw fail("wallet setup: mnemonicWordCount does not match the phrase");
        for (const account of checked) {
          if (addressOf(seed, account.chain, account.derivationPath) !== account.address) {
            throw fail(`wallet setup: the ${account.chain} address does not derive from the stored phrase`);
          }
        }
      } finally {
        seed.fill(0);
      }
    } finally {
      key.fill(0);
    }
    return withStateLock(ctx, async () => {
      // A forced setup must be able to replace state that no longer loads, so it never reads the old state.
      if (!force && loadState(ctx)) throw fail("wallet is already configured; pass force=true to overwrite");
      const state: StoredWalletState = {
        consentGranted: true,
        source,
        mnemonicWordCount: count as number,
        encryptedMnemonic: encrypted,
        accounts: checked,
        updatedAtMs: rt.now(),
      };
      saveState(ctx, state);
      ctx.log("info", "wallet setup saved", { accounts: state.accounts.length, source });
      return publicStatus(state);
    });
  };

  methods.wallet_account = async (params, ctx) => {
    onlyFields(params, ["chain", "accountIndex", "account_index"], "wallet_account");
    const chain = str(params, "chain");
    if (!chain || !(WALLET_CHAINS as readonly string[]).includes(chain)) throw fail("invalid params: chain is evm, btc, solana or tron");
    const rawIndex = params.accountIndex ?? params.account_index ?? 0;
    if (typeof rawIndex !== "number" || !Number.isInteger(rawIndex) || rawIndex < 0) throw fail("invalid params: accountIndex is a whole number");
    const account = await ensureAccount(ctx, chain as WalletChain, rawIndex);
    return { address: account.address, derivationPath: account.derivationPath, chain: account.chain, accountIndex: rawIndex };
  };

  methods.wallet_ledger_list = async (params, ctx) => {
    const raw = params.limit;
    const limit = typeof raw === "number" && Number.isFinite(raw) ? Math.min(500, Math.max(1, Math.trunc(raw))) : 50;
    return rt.ledger(ctx).recent(limit);
  };

  methods.wallet_balances = async (_params, ctx) => {
    const state = loadState(ctx);
    if (!state) throw fail(WALLET_NOT_CONFIGURED_MESSAGE);
    const primary = (chain: WalletChain): string | undefined => state.accounts.find((a) => a.chain === chain && isPrimary(a))?.address;
    const row = async (
      base: Record<string, unknown>,
      read: () => Promise<bigint>,
    ): Promise<Record<string, unknown>> => {
      try {
        return { ...base, raw: (await read()).toString(), providerStatus: "ready" };
      } catch {
        return { ...base, raw: "0", providerStatus: "unavailable" };
      }
    };
    const jobs: Array<Promise<Record<string, unknown>>> = [];
    const solAddress = primary("solana");
    if (solAddress) {
      jobs.push(row({ chain: "solana", symbol: "SOL", decimals: 9, address: solAddress }, () => sol.solBalance(rt.solana(ctx), solAddress)));
    }
    const evmAddress = primary("evm");
    if (evmAddress && solanaCluster(ctx.env) === "mainnet") {
      for (const network of EVM_NETWORKS) {
        jobs.push(
          row({ chain: "evm", evmNetwork: network.id, symbol: network.native, decimals: 18, address: evmAddress }, () =>
            evm.ethBalance(rt.evm(ctx, network), evmAddress),
          ),
        );
      }
    }
    const btcAddress = primary("btc");
    if (btcAddress) {
      jobs.push(row({ chain: "btc", symbol: "BTC", decimals: 8, address: btcAddress }, () => btcBalance(rt, ctx, btcAddress)));
    }
    return Promise.all(jobs);
  };

  methods.wallet_tx_status = async (params, ctx) => {
    const chain = str(params, "chain");
    const hash = requireStr(params, "hash").trim();
    if (chain === "evm") {
      const network = evmNetwork(str(params, "evmNetwork") ?? "ethereum_mainnet");
      if (!network) throw fail("invalid params: evmNetwork is not a known network");
      const result = await evm.receiptStatus(rt.evm(ctx, network), hash);
      return { chain: "evm", evmNetwork: network.id, hash, ...result };
    }
    if (chain === "solana") {
      const result = await sol.signatureStatus(rt.solana(ctx), hash);
      return { chain: "solana", hash, ...result };
    }
    throw fail("invalid params: chain is evm or solana");
  };

  methods.wallet_prepare_transfer = async (params, ctx) => {
    onlyFields(params, ["chain", "toAddress", "amountRaw", "evmNetwork", "assetSymbol"], "wallet_prepare_transfer");
    const chain = str(params, "chain");
    if (chain !== "solana" && chain !== "evm") throw fail("invalid params: chain is evm or solana");
    const to = requireStr(params, "toAddress").trim();
    const amount = bigintOf(params.amountRaw);
    if (amount === undefined || amount === 0n) throw fail("invalid params: amountRaw is a whole number of atomic units above zero");
    if (chain === "solana" && amount > U64_MAX) throw fail("invalid params: amountRaw is more than a Solana amount can hold (u64)");
    const state = loadState(ctx);
    if (!state) throw fail(WALLET_NOT_CONFIGURED_MESSAGE);
    const asset = (str(params, "assetSymbol") ?? "").trim().toUpperCase();
    const now = rt.now();
    const base = { quoteId: newId("q-"), stateDir: ctx.stateDir, wallet: walletFingerprint(state), createdAtMs: now, expiresAtMs: now + TRANSFER_TTL_MS };
    let quote: TransferQuote;
    if (chain === "solana") {
      const from = state.accounts.find((a) => a.chain === "solana" && isPrimary(a))?.address;
      if (!from) throw fail("no wallet account derived for chain 'solana'");
      if (!sol.isSolanaAddress(to)) throw fail("invalid params: toAddress is not a Solana address");
      if (str(params, "evmNetwork")) throw fail("invalid params: evmNetwork is for evm transfers");
      if (asset === "" || asset === "SOL") {
        quote = { ...base, chain, fromAddress: from, toAddress: to, amountRaw: amount.toString(), assetSymbol: "SOL" };
      } else if (asset === "USDC") {
        const mint = solanaUsdcMint(solanaCluster(ctx.env));
        if (!mint) throw fail("USDC is not catalogued on this Solana cluster");
        quote = { ...base, chain, fromAddress: from, toAddress: to, amountRaw: amount.toString(), assetSymbol: "USDC", tokenAddress: mint };
      } else {
        throw fail(`invalid params: ${asset} is not an asset this wallet transfers on solana`);
      }
    } else {
      const network = evmNetwork(str(params, "evmNetwork"));
      if (!network) throw fail("invalid params: evmNetwork is required for evm transfers (base_mainnet, arbitrum_one, ethereum_mainnet, optimism_mainnet)");
      const from = state.accounts.find((a) => a.chain === "evm" && isPrimary(a))?.address;
      if (!from) throw fail("no wallet account derived for chain 'evm'");
      if (!evm.isEvmAddress(to)) throw fail("invalid params: toAddress is not a valid EVM address (or its EIP-55 checksum is wrong)");
      if (asset === "" || asset === network.native) {
        quote = { ...base, chain, evmNetwork: network.id, fromAddress: from, toAddress: to, amountRaw: amount.toString(), assetSymbol: network.native };
      } else if (asset === "USDC") {
        quote = {
          ...base,
          chain,
          evmNetwork: network.id,
          fromAddress: from,
          toAddress: to,
          amountRaw: amount.toString(),
          assetSymbol: "USDC",
          tokenAddress: network.usdc,
        };
      } else {
        throw fail(`invalid params: ${asset} is not an asset this wallet transfers on ${network.id}`);
      }
    }
    rt.transfers.put(quote, now);
    return {
      quoteId: quote.quoteId,
      kind: "transfer",
      chain: quote.chain,
      ...(quote.evmNetwork ? { evmNetwork: quote.evmNetwork } : {}),
      fromAddress: quote.fromAddress,
      toAddress: quote.toAddress,
      amountRaw: quote.amountRaw,
      assetSymbol: quote.assetSymbol,
      ...(quote.tokenAddress ? { tokenAddress: quote.tokenAddress } : {}),
      createdAtMs: quote.createdAtMs,
      expiresAtMs: quote.expiresAtMs,
    };
  };

  methods.wallet_execute_prepared = async (params, ctx) => {
    onlyFields(params, ["quoteId", "confirmed", "quote_id"], "wallet_execute_prepared");
    requireConfirmed(params);
    const quoteId = requireStr(params, params.quoteId !== undefined ? "quoteId" : "quote_id");
    const state = loadState(ctx);
    if (!state) throw fail(WALLET_NOT_CONFIGURED_MESSAGE);
    const held = rt.transfers.peek(quoteId);
    const unavailable = (): Error => fail("prepared quote is unavailable or expired");
    if (!held || held.stateDir !== ctx.stateDir || held.wallet !== walletFingerprint(state)) throw unavailable();
    const ledger = rt.ledger(ctx);
    const now = rt.now();
    const facts = {
      quoteId: held.quoteId,
      chain: held.chain,
      ...(held.evmNetwork ? { evmNetwork: held.evmNetwork } : {}),
      assetSymbol: held.assetSymbol,
      ...(held.tokenAddress ? { tokenAddress: held.tokenAddress } : {}),
      toAddress: held.toAddress,
      amountRaw: held.amountRaw,
    };
    if (held.expiresAtMs <= now) {
      rt.transfers.take(quoteId);
      throw refusal("quote_expired", `the quote expired at ${held.expiresAtMs} (now ${now}); prepare it again`);
    }
    // Caps are evaluated, and the spend reserved, BEFORE anything is signed.
    let record: LedgerRecord;
    try {
      record = await ledger.reserve((records) => {
        // The clock and the policy at the point of no return: a quote that expired, or a cap lowered, while this
        // waited for the ledger is judged as it stands now.
        const reservedAt = rt.now();
        if (held.expiresAtMs <= reservedAt) {
          throw refusal("quote_expired", `the quote expired at ${held.expiresAtMs} (now ${reservedAt}); prepare it again`);
        }
        const policy = loadPolicy(ctx);
        const earlier = alreadyAttempted(records, held.quoteId);
        if (earlier) throw refusal("already_attempted", alreadyAttemptedMessage(held.quoteId, earlier.status, false));
        evaluateTransfer(policy, facts, records, reservedAt);
        return newRecord(facts, "pending", reservedAt);
      });
    } catch (error) {
      if (error instanceof PolicyRefusal) {
        await ledger.put(newRecord(facts, "refused", now, { error: error.detail })).catch(() => undefined);
        if (error.kind === "already_attempted" || error.kind === "quote_expired") rt.transfers.take(quoteId);
      }
      throw error;
    }
    rt.transfers.take(quoteId);
    ctx.log("info", "wallet execute recorded as pending", { quoteId: held.quoteId, recordId: record.id });
    let hash: string;
    try {
      hash = await signAndSendTransfer(rt, ctx, held);
    } catch (error) {
      // Anything after the reservation may or may not be on chain: in doubt, never retried.
      const message = error instanceof Error ? error.message : String(error);
      await ledger
        .put({ ...record, status: "in_doubt", error: message.slice(0, 300), updatedAtMs: Math.max(now, rt.now()) })
        .catch(() => undefined);
      throw error;
    }
    const recorded = await recordBroadcast(ledger, record, hash, Math.max(now, rt.now()));
    if (!recorded) {
      ctx.log("error", "wallet transfer was sent but the ledger update failed; the record stays pending", { quoteId: held.quoteId, recordId: record.id, transactionHash: hash });
    }
    return {
      quoteId: held.quoteId,
      kind: "transfer",
      transactionHash: hash,
      ...(recorded ? {} : { ledgerUpdateFailed: true }),
      ...(held.chain === "solana" ? { explorerUrl: solanaExplorerTx(hash, solanaCluster(ctx.env)) } : {}),
    };
  };

  return methods;
}

async function signAndSendTransfer(rt: Runtime, ctx: CoreContext, quote: TransferQuote): Promise<string> {
  const amount = BigInt(quote.amountRaw);
  return withSeed(ctx, async (seed, state) => {
    // The wallet may have been replaced while this waited; the quote is good only for the wallet it was prepared for.
    if (walletFingerprint(state) !== quote.wallet) throw fail("the wallet changed after the quote was prepared; prepare it again");
    if (quote.chain === "solana") {
      const account = state.accounts.find((a) => a.chain === "solana" && isPrimary(a));
      if (!account) throw fail("no wallet account derived for chain 'solana'");
      const keypair = solanaKeypair(seed, account.derivationPath);
      if (keypair.publicKey.toBase58() !== quote.fromAddress) throw fail("the stored wallet no longer matches the quote");
      const rpc = rt.solana(ctx);
      const wire = await sol.buildSignedTransfer(rpc, keypair, quote.toAddress, amount, quote.tokenAddress);
      return sol.sendTransaction(rpc, wire);
    }
    const network = evmNetwork(quote.evmNetwork);
    if (!network) throw fail("the quote names an unknown EVM network");
    const account = state.accounts.find((a) => a.chain === "evm" && isPrimary(a));
    if (!account) throw fail("no wallet account derived for chain 'evm'");
    const key = evmPrivateKey(seed, account.derivationPath);
    const rpc = rt.evm(ctx, network);
    const tx = quote.tokenAddress
      ? { to: quote.tokenAddress, value: 0n, data: evm.erc20TransferData(quote.toAddress, amount) }
      : { to: quote.toAddress, value: amount, data: "0x" };
    return evm.signAndBroadcast(rpc, key, network.chainId, tx);
  });
}

async function btcBalance(rt: Runtime, ctx: CoreContext, address: string): Promise<bigint> {
  const response = await rt.deps.fetch(`${btcApiUrl(ctx.env)}/address/${encodeURIComponent(address)}`, {
    signal: AbortSignal.timeout(20_000),
    redirect: "error",
  });
  if (!response.ok) throw new Error("btc api error");
  const body = (await response.json()) as { chain_stats?: Record<string, number>; mempool_stats?: Record<string, number> };
  const n = (v: number | undefined): bigint => BigInt(Math.trunc(v ?? 0));
  return n(body.chain_stats?.funded_txo_sum) - n(body.chain_stats?.spent_txo_sum) + n(body.mempool_stats?.funded_txo_sum) - n(body.mempool_stats?.spent_txo_sum);
}
