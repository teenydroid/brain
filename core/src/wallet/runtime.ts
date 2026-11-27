// What every wallet operation shares: the injected deps, the in-memory quote
// stores, and small constructors for RPC clients. Quotes live in memory only,
// as OpenHuman's did: a restart forgets them, which is the safe direction.
import { join } from "node:path";
import type { CoreContext } from "../server/types.js";
import { type Deps, type EvmNetworkInfo, defaultDeps, evmRpcUrl, solanaRpcUrl } from "./endpoints.js";
import { SpendLedger } from "./ledger.js";
import { EvmRpc, SolanaRpc } from "./rpc.js";
import { walletDir } from "./store.js";

export class QuoteStore<T extends { quoteId: string; expiresAtMs: number }> {
  private readonly quotes = new Map<string, T>();

  put(quote: T, nowMs: number): void {
    for (const [id, q] of this.quotes) if (q.expiresAtMs <= nowMs) this.quotes.delete(id);
    this.quotes.set(quote.quoteId, quote);
  }

  has(quoteId: string): boolean {
    return this.quotes.has(quoteId);
  }

  peek(quoteId: string): T | undefined {
    return this.quotes.get(quoteId);
  }

  take(quoteId: string): T | undefined {
    const quote = this.quotes.get(quoteId);
    this.quotes.delete(quoteId);
    return quote;
  }

  /** Every quote still executable. */
  live(nowMs: number): T[] {
    return [...this.quotes.values()].filter((q) => q.expiresAtMs > nowMs);
  }
}

export interface TransferQuote {
  quoteId: string;
  stateDir: string;
  /** Hash of the stored encrypted phrase: a quote cannot outlive a wallet replacement. */
  wallet: string;
  chain: "solana" | "evm";
  evmNetwork?: string;
  fromAddress: string;
  toAddress: string;
  amountRaw: string;
  assetSymbol: string;
  tokenAddress?: string;
  createdAtMs: number;
  expiresAtMs: number;
}

export interface ProgramQuote {
  quoteId: string;
  stateDir: string;
  wallet: string;
  endpoint: string;
  accountIndex: number;
  address: string;
  summary: import("./solana-program.js").ProgramTransactionSummary;
  declaredDebits: import("./policy.js").MintTotal[];
  simulation: import("./solana-program.js").SimulationReport;
  transaction: Uint8Array;
  decoded: import("./solana-program.js").DecodedTransaction;
  createdAtMs: number;
  expiresAtMs: number;
}

export interface ConvertQuote {
  quoteId: string;
  stateDir: string;
  wallet: string;
  kind: "swap" | "bridge" | "dapp";
  signingChain: "solana" | "evm";
  /** The EVM chain id the transaction is signed for. */
  evmChainId?: number;
  /** The unsigned transaction, as the provider gave it. */
  tx: { to?: string; data?: string; value?: string };
  /** What a ledger row says about it. */
  summary: { fromToken: string; toToken: string; amountRaw: string; toAddress: string };
  createdAtMs: number;
  expiresAtMs: number;
}

export class Runtime {
  readonly transfers = new QuoteStore<TransferQuote>();
  readonly programs = new QuoteStore<ProgramQuote>();
  readonly converts = new QuoteStore<ConvertQuote>();
  readonly deps: Deps;

  constructor(deps: Partial<Deps> = {}) {
    this.deps = { ...defaultDeps, ...deps };
  }

  now(): number {
    return this.deps.now();
  }

  ledger(ctx: CoreContext): SpendLedger {
    return new SpendLedger(walletDir(ctx));
  }

  x402File(ctx: CoreContext): string {
    return join(walletDir(ctx), "x402-payments.json");
  }

  solana(ctx: CoreContext): SolanaRpc {
    return new SolanaRpc(this.deps, solanaRpcUrl(ctx.env));
  }

  solanaEndpoint(ctx: CoreContext): string {
    return solanaRpcUrl(ctx.env);
  }

  evm(ctx: CoreContext, network: EvmNetworkInfo): EvmRpc {
    return new EvmRpc(this.deps, evmRpcUrl(ctx.env, network));
  }
}
