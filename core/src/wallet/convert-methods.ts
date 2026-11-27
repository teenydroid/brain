// web3_swap_*, web3_bridge_*, web3_dapp_*: quote a swap or bridge from LI.FI
// or deBridge (routes/), or a generic EVM contract call; then, on
// `confirmed: true`, sign the unsigned transaction in the core and send it.
//
// As in OpenHuman, the core applies no cap to these: hands/wallet (convert.py,
// buy.py) checks every quote and holds its own caps before it ever asks for
// an execute. What the core adds is plumbing it can verify itself: a quote is
// single-use and expires, the transaction is signed only by the wallet's own
// primary account for a chain the wallet supports, and every attempt is
// written to the ledger (kinds swap/bridge/dapp, never counted against the
// transfer caps) before anything is signed.
import { createHash } from "node:crypto";
import type { CoreContext, RpcMethods } from "../server/types.js";
import { evmPrivateKey, solanaKeypair } from "./derive.js";
import { evmNetwork, evmNetworkByChainId } from "./endpoints.js";
import { PolicyRefusal, fail, refusal } from "./errors.js";
import * as evm from "./evm-chain.js";
import type { LedgerRecord } from "./ledger.js";
import { alreadyAttempted, alreadyAttemptedMessage, RPC_ORIGIN } from "./policy.js";
import { backendAnswer } from "./routes/index.js";
import { isRecord } from "./routes/types.js";
import type { ConvertQuote, Runtime } from "./runtime.js";
import * as sol from "./solana-chain.js";
import { isPrimary, loadState, withSeed } from "./store.js";
import { WALLET_NOT_CONFIGURED_MESSAGE } from "./store.js";
import { bigintOf, hexToBytes, newId, requireConfirmed, requireStr } from "./util.js";

/** The id the wallet module (after deBridge) gives Solana. */
const SOLANA_CHAIN_ID = "7565164";
const QUOTE_TTL_MS = 90_000;
const DAPP_TTL_MS = 120_000;

type Params = Record<string, unknown>;

const text = (v: unknown): string | undefined => (typeof v === "string" ? v : typeof v === "number" && Number.isSafeInteger(v) ? String(v) : undefined);
const fingerprint = (encrypted: string): string => createHash("sha256").update(encrypted).digest("hex");

function chainOf(id: string | undefined): { signingChain: "solana" | "evm"; evmChainId?: number } {
  if (id === undefined || !/^\d+$/.test(id)) throw fail("invalid params: the chain id is not a number");
  if (id === SOLANA_CHAIN_ID) return { signingChain: "solana" };
  const network = evmNetworkByChainId(id);
  if (!network) throw fail(`the wallet has no account on chain ${id}`);
  return { signingChain: "evm", evmChainId: network.chainId };
}

function txOf(answer: unknown): ConvertQuote["tx"] {
  const quote = isRecord(answer) ? answer : {};
  const tx = quote.tx;
  if (!isRecord(tx)) throw fail("the provider's quote carries no transaction");
  return {
    ...(typeof tx.to === "string" ? { to: tx.to } : {}),
    ...(typeof tx.data === "string" ? { data: tx.data } : {}),
    ...(tx.value !== undefined ? { value: String(tx.value) } : {}),
  };
}

export function convertMethods(rt: Runtime): RpcMethods {
  const methods: RpcMethods = {};

  const own = (ctx: CoreContext, chain: "solana" | "evm"): string => {
    const state = loadState(ctx);
    if (!state) throw fail(WALLET_NOT_CONFIGURED_MESSAGE);
    const found = state.accounts.find((a) => a.chain === chain && isPrimary(a))?.address;
    if (!found) throw fail(`no wallet account derived for chain '${chain}'`);
    return found;
  };

  const store = (ctx: CoreContext, quote: Omit<ConvertQuote, "quoteId" | "stateDir" | "wallet" | "createdAtMs" | "expiresAtMs">, ttl: number): ConvertQuote => {
    const state = loadState(ctx);
    if (!state) throw fail(WALLET_NOT_CONFIGURED_MESSAGE);
    const now = rt.now();
    const full: ConvertQuote = {
      ...quote,
      quoteId: newId("q-"),
      stateDir: ctx.stateDir,
      wallet: fingerprint(state.encryptedMnemonic),
      createdAtMs: now,
      expiresAtMs: now + ttl,
    };
    rt.converts.put(full, now);
    return full;
  };

  methods.web3_swap_quote = async (params, ctx) => {
    const body: Params = { ...params };
    const chain = chainOf(text(body.chainId));
    // Defaults the module filled: the wallet's own address on this chain.
    for (const field of ["senderAddress", "tokenOutRecipient"]) {
      if (body[field] === undefined || body[field] === null || body[field] === "") body[field] = own(ctx, chain.signingChain);
    }
    if (body.slippage === undefined || body.slippage === null || body.slippage === "") body.slippage = "auto";
    const answer = await backendAnswer(ctx.env, rt.deps, { kind: "swap", body });
    const quote = store(
      ctx,
      {
        kind: "swap",
        signingChain: chain.signingChain,
        ...(chain.evmChainId !== undefined ? { evmChainId: chain.evmChainId } : {}),
        tx: txOf(answer),
        summary: {
          fromToken: String(body.tokenIn ?? ""),
          toToken: String(body.tokenOut ?? ""),
          amountRaw: String(body.tokenInAmount ?? "0"),
          toAddress: String(body.tokenOutRecipient ?? ""),
        },
      },
      QUOTE_TTL_MS,
    );
    return envelope(quote, answer);
  };

  methods.web3_bridge_quote = async (params, ctx) => {
    const body: Params = { ...params };
    const chain = chainOf(text(body.srcChainId));
    const answer = await backendAnswer(ctx.env, rt.deps, { kind: "bridge", body });
    const quote = store(
      ctx,
      {
        kind: "bridge",
        signingChain: chain.signingChain,
        ...(chain.evmChainId !== undefined ? { evmChainId: chain.evmChainId } : {}),
        tx: txOf(answer),
        summary: {
          fromToken: String(body.srcChainTokenIn ?? ""),
          toToken: String(body.dstChainTokenOut ?? ""),
          amountRaw: String(body.srcChainTokenInAmount ?? "0"),
          toAddress: String(body.dstChainTokenOutRecipient ?? ""),
        },
      },
      QUOTE_TTL_MS,
    );
    return envelope(quote, answer);
  };

  methods.web3_swap_routes = async (_params, ctx) => backendAnswer(ctx.env, rt.deps, { kind: "routes" });
  methods.web3_bridge_routes = methods.web3_swap_routes;

  methods.web3_dapp_call = async (params, ctx) => {
    for (const k of Object.keys(params)) {
      if (!["contractAddress", "calldata", "valueRaw", "evmNetwork"].includes(k)) throw fail(`invalid params: web3_dapp_call does not take ${k}`);
    }
    const network = evmNetwork(typeof params.evmNetwork === "string" ? params.evmNetwork : undefined);
    if (!network) throw fail("invalid params: evmNetwork is not a known network");
    const contract = requireStr(params, "contractAddress").trim();
    if (!evm.isEvmAddress(contract)) throw fail("invalid params: contractAddress is not a valid EVM address");
    const calldata = requireStr(params, "calldata").trim();
    try {
      hexToBytes(calldata);
    } catch {
      throw fail("invalid params: calldata is not hex");
    }
    const valueRaw = params.valueRaw === undefined ? "0" : String(params.valueRaw);
    if (bigintOf(valueRaw) === undefined) throw fail("invalid params: valueRaw is a whole number of wei");
    own(ctx, "evm");
    const quote = store(
      ctx,
      {
        kind: "dapp",
        signingChain: "evm",
        evmChainId: network.chainId,
        tx: { to: contract, data: calldata.startsWith("0x") ? calldata : `0x${calldata}`, value: valueRaw },
        summary: { fromToken: "", toToken: "", amountRaw: valueRaw, toAddress: contract },
      },
      DAPP_TTL_MS,
    );
    return {
      quoteId: quote.quoteId,
      kind: "dapp",
      signingChain: "evm",
      evmNetwork: network.id,
      expiresAtMs: quote.expiresAtMs,
      quote: { contractAddress: contract, calldata, valueRaw },
    };
  };

  const execute = (kind: ConvertQuote["kind"]) => async (params: Params, ctx: CoreContext): Promise<unknown> => {
    for (const k of Object.keys(params)) {
      if (k !== "quoteId" && k !== "confirmed") throw fail(`invalid params: web3_${kind}_execute does not take ${k}`);
    }
    requireConfirmed(params);
    const quoteId = requireStr(params, "quoteId");
    const state = loadState(ctx);
    if (!state) throw fail(WALLET_NOT_CONFIGURED_MESSAGE);
    const held = rt.converts.peek(quoteId);
    if (!held || held.kind !== kind || held.stateDir !== ctx.stateDir || held.wallet !== fingerprint(state.encryptedMnemonic)) {
      throw fail("prepared quote is unavailable or expired");
    }
    const now = rt.now();
    rt.converts.take(quoteId);
    if (held.expiresAtMs <= now) throw refusal("quote_expired", `the quote expired at ${held.expiresAtMs} (now ${now}); prepare it again`);
    const ledger = rt.ledger(ctx);
    const base = (status: LedgerRecord["status"], extra: Partial<LedgerRecord> = {}): LedgerRecord => ({
      id: newId(),
      quoteId: held.quoteId,
      createdAtMs: now,
      updatedAtMs: now,
      origin: RPC_ORIGIN.label,
      originClass: RPC_ORIGIN.class,
      // The core's transfer caps do not govern this path; the record says so.
      policyApplied: false,
      chain: held.signingChain,
      assetSymbol: held.kind,
      ...(held.summary.fromToken ? { tokenAddress: held.summary.fromToken } : {}),
      toAddress: held.summary.toAddress,
      amountRaw: held.summary.amountRaw,
      status,
      kind: held.kind,
      ...extra,
    });
    let record: LedgerRecord;
    try {
      record = await ledger.reserve((records) => {
        const earlier = alreadyAttempted(records, held.quoteId);
        if (earlier) throw refusal("already_attempted", alreadyAttemptedMessage(held.quoteId, earlier.status, false));
        return base("pending");
      });
    } catch (error) {
      if (error instanceof PolicyRefusal) await ledger.put(base("refused", { error: error.detail })).catch(() => undefined);
      throw error;
    }
    let hash: string;
    try {
      hash = await signAndSend(rt, ctx, held);
    } catch (error) {
      const message = error instanceof Error ? error.message : String(error);
      await ledger.put({ ...record, status: "in_doubt", error: message.slice(0, 300), updatedAtMs: Math.max(now, rt.now()) }).catch(() => undefined);
      throw error;
    }
    await ledger.put({ ...record, status: "broadcast", transactionHash: hash, updatedAtMs: Math.max(now, rt.now()) });
    return { quoteId: held.quoteId, kind: held.kind, transactionHash: hash };
  };

  methods.web3_swap_execute = execute("swap");
  methods.web3_bridge_execute = execute("bridge");
  methods.web3_dapp_execute = execute("dapp");

  return methods;
}

function envelope(quote: ConvertQuote, answer: unknown): Record<string, unknown> {
  return {
    quoteId: quote.quoteId,
    kind: quote.kind,
    signingChain: quote.signingChain,
    expiresAtMs: quote.expiresAtMs,
    quote: answer,
  };
}

async function signAndSend(rt: Runtime, ctx: CoreContext, quote: ConvertQuote): Promise<string> {
  return withSeed(ctx, async (seed, state) => {
    if (quote.signingChain === "solana") {
      const account = state.accounts.find((a) => a.chain === "solana" && isPrimary(a));
      if (!account) throw fail("no wallet account derived for chain 'solana'");
      const data = quote.tx.data;
      if (!data) throw fail("the quote's transaction carries no data");
      const keypair = solanaKeypair(seed, account.derivationPath);
      const rpc = rt.solana(ctx);
      const wire = await sol.signProviderTransaction(rpc, keypair, hexToBytes(data));
      return sol.sendTransaction(rpc, wire);
    }
    const network = evmNetworkByChainId(quote.evmChainId ?? -1);
    if (!network) throw fail("the quote names an EVM chain this wallet does not use");
    const account = state.accounts.find((a) => a.chain === "evm" && isPrimary(a));
    if (!account) throw fail("no wallet account derived for chain 'evm'");
    const { to, data, value } = quote.tx;
    if (!to || !evm.isEvmAddress(to)) throw fail("the quote's transaction has no valid `to`");
    const wei = bigintOf(value ?? "0");
    if (wei === undefined) throw fail("the quote's transaction value is not a number");
    const key = evmPrivateKey(seed, account.derivationPath);
    const rpc = rt.evm(ctx, network);
    const raw = await evm.signEvmTransaction(rpc, key, network.chainId, { to, value: wei, data: data ?? "0x" });
    return evm.sendRaw(rpc, raw);
  });
}
