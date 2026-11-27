// The spend policy: what a prepared execute may move, and the caps every
// execute is held to BEFORE anything is signed. A port of OpenHuman's
// web3::wallet::policy and program::evaluate_program, with their messages.
//
// Differences from OpenHuman, all of them stricter:
//   - every RPC execute is policy-applied (OpenHuman skipped the caps for an
//     "interactive" execute unless `apply_to_interactive` was set; this core
//     has no interactive user, and Teeny always wrote it true);
//   - a config that cannot be read, or has an unknown field, refuses every
//     execute instead of falling back to defaults.
import { existsSync, readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { PublicKey } from "@solana/web3.js";
import { parse as parseToml } from "smol-toml";
import type { CoreContext } from "../server/types.js";
import { refusal } from "./errors.js";
import { type LedgerRecord, countsAgainstCaps, recordAmount } from "./ledger.js";
import { DAY_MS, WEEK_MS, newId } from "./util.js";

export interface AssetCap {
  chain: string;
  symbol: string;
  evmNetwork?: string;
  tokenAddress?: string;
  perTxMaxRaw?: bigint;
  dailyMaxRaw?: bigint;
  recipientWeeklyMaxRaw?: bigint;
}

export interface MintCap {
  mint: string;
  perTxMaxRaw: bigint;
  dailyMaxRaw: bigint;
}

export interface AccountPolicy {
  allowedPrograms: string[];
  mints: MintCap[];
}

export interface WalletPolicy {
  automationEnabled: boolean;
  applyToInteractive: boolean;
  assets: AssetCap[];
  accounts: Record<string, AccountPolicy>;
}

export const EMPTY_POLICY: WalletPolicy = { automationEnabled: false, applyToInteractive: true, assets: [], accounts: {} };

/** Where the core's config.toml is: TEENY_CORE_CONFIG, else the .openhuman/ sibling of the brain. */
export function configPath(ctx: CoreContext): string {
  const explicit = ctx.env.TEENY_CORE_CONFIG?.trim();
  if (explicit) return explicit;
  const workspace = ctx.env.OPENHUMAN_WORKSPACE?.trim();
  if (workspace) return join(dirname(workspace), ".openhuman", "config.toml");
  return join(ctx.root, ".openhuman", "config.toml");
}

type Table = Record<string, unknown>;

function isTable(value: unknown): value is Table {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function checkFields(table: Table, allowed: readonly string[], where: string): void {
  for (const key of Object.keys(table)) {
    if (!allowed.includes(key)) throw new Error(`${where} has an unknown field ${key}`);
  }
}

function cap(value: unknown, name: string): bigint | undefined {
  if (value === undefined) return undefined;
  if (typeof value === "bigint" && value >= 0n) return value;
  if (typeof value === "number" && Number.isSafeInteger(value) && value >= 0) return BigInt(value);
  throw new Error(`${name} is not a whole number`);
}

function text(value: unknown, name: string): string {
  if (typeof value !== "string") throw new Error(`${name} is not a string`);
  return value;
}

/** Parses the `[wallet_policy]` section of TEXT (a config.toml), strictly. */
export function parsePolicy(textOfConfig: string): WalletPolicy {
  // u64 caps (wei) exceed 2^53: integers stay exact as bigint.
  const doc = parseToml(textOfConfig, { integersAsBigInt: true }) as Table;
  const section = doc.wallet_policy;
  if (section === undefined) return { ...EMPTY_POLICY };
  if (!isTable(section)) throw new Error("[wallet_policy] is not a table");
  checkFields(section, ["automation_enabled", "apply_to_interactive", "assets", "accounts"], "[wallet_policy]");
  const assets: AssetCap[] = [];
  for (const raw of Array.isArray(section.assets) ? section.assets : []) {
    if (!isTable(raw)) throw new Error("a [[wallet_policy.assets]] entry is not a table");
    checkFields(
      raw,
      ["chain", "symbol", "evm_network", "token_address", "per_tx_max_raw", "daily_max_raw", "recipient_weekly_max_raw"],
      "[[wallet_policy.assets]]",
    );
    assets.push({
      chain: text(raw.chain, "assets.chain"),
      symbol: text(raw.symbol, "assets.symbol"),
      ...(raw.evm_network !== undefined ? { evmNetwork: text(raw.evm_network, "assets.evm_network") } : {}),
      ...(raw.token_address !== undefined ? { tokenAddress: text(raw.token_address, "assets.token_address") } : {}),
      ...optionalCap("perTxMaxRaw", raw.per_tx_max_raw),
      ...optionalCap("dailyMaxRaw", raw.daily_max_raw),
      ...optionalCap("recipientWeeklyMaxRaw", raw.recipient_weekly_max_raw),
    });
  }
  const accounts: Record<string, AccountPolicy> = {};
  if (section.accounts !== undefined) {
    if (!isTable(section.accounts)) throw new Error("[wallet_policy.accounts] is not a table");
    for (const [key, raw] of Object.entries(section.accounts)) {
      if (!isTable(raw)) throw new Error(`[wallet_policy.accounts.${key}] is not a table`);
      checkFields(raw, ["allowed_programs", "mints"], `[wallet_policy.accounts.${key}]`);
      const programs = Array.isArray(raw.allowed_programs) ? raw.allowed_programs.map((p) => text(p, "allowed_programs")) : [];
      const mints: MintCap[] = [];
      for (const m of Array.isArray(raw.mints) ? raw.mints : []) {
        if (!isTable(m)) throw new Error("a mints entry is not a table");
        checkFields(m, ["mint", "per_tx_max_raw", "daily_max_raw"], "a mints entry");
        const perTx = cap(m.per_tx_max_raw, "mints.per_tx_max_raw");
        const daily = cap(m.daily_max_raw, "mints.daily_max_raw");
        if (perTx === undefined || daily === undefined) throw new Error("a mint needs both per_tx_max_raw and daily_max_raw");
        mints.push({ mint: text(m.mint, "mints.mint"), perTxMaxRaw: perTx, dailyMaxRaw: daily });
      }
      accounts[key] = { allowedPrograms: programs, mints };
    }
  }
  return {
    automationEnabled: section.automation_enabled === true,
    applyToInteractive: true,
    assets,
    accounts,
  };
}

function optionalCap<K extends string>(key: K, value: unknown): { [P in K]?: bigint } {
  const parsed = cap(value, key);
  return (parsed === undefined ? {} : { [key]: parsed }) as { [P in K]?: bigint };
}

/** The policy in force, read fresh from config.toml. An unreadable config refuses everything. */
export function loadPolicy(ctx: CoreContext): WalletPolicy {
  const path = configPath(ctx);
  if (!existsSync(path)) return { ...EMPTY_POLICY };
  try {
    return parsePolicy(readFileSync(path, "utf8"));
  } catch (error) {
    throw refusal("policy_unreadable", `the wallet policy cannot be read (${(error as Error).message}); nothing is executed`);
  }
}

export function accountKey(chain: string, index: number): string {
  return `${chain.toLowerCase()}_${index}`;
}

// ---------------------------------------------------------------------------
// Transfers
// ---------------------------------------------------------------------------

/** What a prepared transfer says, as far as the caps are concerned. */
export interface TransferFacts {
  quoteId: string;
  chain: string;
  evmNetwork?: string;
  assetSymbol: string;
  tokenAddress?: string;
  toAddress: string;
  amountRaw: string;
}

/**
 * One spelling per recipient or token, so a cap cannot be split across spellings of the same address:
 * EVM addresses compare lowercase (a checksum is a spelling, not an identity); Solana addresses go through
 * a base58 decode and encode. Anything that does not parse is compared as written.
 */
export function canonicalAddress(chain: string, address: string): string {
  const text = address.trim();
  if (chain.toLowerCase() === "evm") return text.toLowerCase();
  if (chain.toLowerCase() === "solana") {
    try {
      return new PublicKey(text).toBase58();
    } catch {
      return text;
    }
  }
  return text;
}

function capMatches(c: AssetCap, a: { chain: string; evmNetwork?: string | undefined; symbol: string; tokenAddress?: string | undefined }): boolean {
  return (
    c.chain.toLowerCase() === a.chain.toLowerCase() &&
    c.symbol.toLowerCase() === a.symbol.toLowerCase() &&
    (c.evmNetwork === undefined || c.evmNetwork === a.evmNetwork) &&
    (c.tokenAddress === undefined ||
      (a.tokenAddress !== undefined && canonicalAddress(c.chain, c.tokenAddress) === canonicalAddress(a.chain, a.tokenAddress)))
  );
}

export const RPC_ORIGIN = { label: "interactive", class: "rpc" } as const;

/**
 * Decide whether QUOTE may execute. Throws the refusal (message starts with
 * WALLET_POLICY_REFUSED:) naming the cap and the amounts.
 */
export function evaluateTransfer(policy: WalletPolicy, quote: TransferFacts, records: LedgerRecord[], nowMs: number): void {
  const asset = { chain: quote.chain, evmNetwork: quote.evmNetwork, symbol: quote.assetSymbol, tokenAddress: quote.tokenAddress };
  const matching = policy.assets.find((c) => capMatches(c, asset));
  if (!matching) {
    throw refusal("asset_not_allowed", `${quote.assetSymbol} on ${quote.chain} is not in the wallet policy's asset allowlist`);
  }
  if (!/^\s*\d+\s*$/.test(quote.amountRaw)) throw refusal("per_transaction", "the quote amount is not an integer");
  const amount = BigInt(quote.amountRaw.trim());
  const symbol = quote.assetSymbol;
  if (matching.perTxMaxRaw !== undefined && amount > matching.perTxMaxRaw) {
    throw refusal("per_transaction", `${amount} ${symbol} (atomic) exceeds the per-transaction cap of ${matching.perTxMaxRaw}`);
  }
  const counted = records.filter(
    (r) =>
      r.policyApplied &&
      (r.kind ?? "transfer") === "transfer" &&
      (r.accountIndex ?? 0) === 0 &&
      countsAgainstCaps(r.status) &&
      capMatches(matching, { chain: r.chain, evmNetwork: r.evmNetwork, symbol: r.assetSymbol, tokenAddress: r.tokenAddress }),
  );
  if (matching.dailyMaxRaw !== undefined) {
    const since = nowMs - DAY_MS;
    const spent = counted.filter((r) => r.createdAtMs >= since).reduce((sum, r) => sum + recordAmount(r), 0n);
    if (spent + amount > matching.dailyMaxRaw) {
      throw refusal(
        "daily",
        `${amount} ${symbol} (atomic) would exceed the rolling 24h cap of ${matching.dailyMaxRaw} (${spent} already spent)`,
      );
    }
  }
  if (matching.recipientWeeklyMaxRaw !== undefined) {
    const since = nowMs - WEEK_MS;
    const spent = counted
      .filter((r) => r.createdAtMs >= since && canonicalAddress(r.chain, r.toAddress) === canonicalAddress(quote.chain, quote.toAddress))
      .reduce((sum, r) => sum + recordAmount(r), 0n);
    if (spent + amount > matching.recipientWeeklyMaxRaw) {
      throw refusal(
        "recipient_weekly",
        `${amount} ${symbol} (atomic) would exceed the rolling 7-day cap of ${matching.recipientWeeklyMaxRaw} for this recipient (${spent} already sent)`,
      );
    }
  }
}

/** The earlier attempt on QUOTEID that may have moved funds, as OpenHuman's guard refuses a retry. */
export function alreadyAttempted(records: LedgerRecord[], quoteId: string): LedgerRecord | undefined {
  return [...records].reverse().find((r) => r.quoteId === quoteId && countsAgainstCaps(r.status));
}

export function alreadyAttemptedMessage(quoteId: string, status: string, program: boolean): string {
  return program
    ? `quote ${quoteId} was already executed (status ${statusName(status)}); it is never retried`
    : `quote ${quoteId} was already executed (status ${statusName(status)}); it is never retried. Check wallet_tx_status before preparing a new transfer`;
}

/** Rust's Debug names of the ledger statuses, as the messages print them. */
function statusName(status: string): string {
  return { pending: "Pending", broadcast: "Broadcast", in_doubt: "InDoubt", refused: "Refused" }[status] ?? status;
}

export function newRecord(
  facts: TransferFacts,
  status: LedgerRecord["status"],
  nowMs: number,
  extra: Partial<LedgerRecord> = {},
): LedgerRecord {
  return {
    id: newId(),
    quoteId: facts.quoteId,
    createdAtMs: nowMs,
    updatedAtMs: nowMs,
    origin: RPC_ORIGIN.label,
    originClass: RPC_ORIGIN.class,
    policyApplied: true,
    chain: facts.chain,
    ...(facts.evmNetwork !== undefined ? { evmNetwork: facts.evmNetwork } : {}),
    assetSymbol: facts.assetSymbol,
    ...(facts.tokenAddress !== undefined ? { tokenAddress: facts.tokenAddress } : {}),
    toAddress: facts.toAddress,
    amountRaw: facts.amountRaw,
    status,
    ...extra,
  };
}

// ---------------------------------------------------------------------------
// Program transactions (per-account policy)
// ---------------------------------------------------------------------------

export interface MintTotal {
  mint: string;
  amountRaw: string;
}

export function mintAmount(total: MintTotal): bigint {
  return /^\s*\d+\s*$/.test(total.amountRaw) ? BigInt(total.amountRaw.trim()) : (1n << 128n) - 1n;
}

export interface ProgramSummaryFacts {
  programs: string[];
  totals: MintTotal[];
  movements: { mint: string }[];
}

/** Port of `check_declared`: the declared debits must cover every visible transfer. */
export function checkDeclared(summary: { totals: MintTotal[] }, declared: MintTotal[]): void {
  const seen: string[] = [];
  for (const debit of declared) {
    if (seen.includes(debit.mint)) throw refusal("transaction_not_allowed", `mint ${debit.mint} is declared twice`);
    seen.push(debit.mint);
    if (!/^\d+$/.test(debit.amountRaw.trim()) || BigInt(debit.amountRaw.trim()) > 0xffffffffffffffffn) {
      throw refusal("transaction_not_allowed", `the declared amount of mint ${debit.mint} is not a whole number of atomic units`);
    }
  }
  for (const total of summary.totals) {
    const visible = mintAmount(total);
    const stated = declared.find((d) => d.mint === total.mint);
    const statedAmount = stated ? mintAmount(stated) : 0n;
    if (statedAmount < visible) {
      throw refusal("simulation_mismatch", `the transaction transfers ${visible} of mint ${total.mint} but declares ${statedAmount}`);
    }
  }
}

/** Port of `evaluate_program`. */
export function evaluateProgram(
  policy: WalletPolicy,
  chain: string,
  accountIndex: number,
  summary: ProgramSummaryFacts,
  declared: MintTotal[],
  records: LedgerRecord[],
  nowMs: number,
): void {
  const key = accountKey(chain, accountIndex);
  const section = policy.accounts[key];
  if (!section) throw refusal("account_not_allowed", `account ${key} has no [wallet_policy.accounts.${key}] section`);
  const program = summary.programs.find((p) => !section.allowedPrograms.includes(p));
  if (program !== undefined) throw refusal("program_not_allowed", `program ${program} is not in ${key}'s allowed_programs`);
  checkDeclared(summary, declared);
  const debits = declared.filter((d) => mintAmount(d) > 0n);
  if (debits.length > 1) {
    throw refusal("transaction_not_allowed", `the transaction debits ${debits.length} mints; a program execute may move at most one`);
  }
  const total = debits[0];
  if (!total) return;
  const mintCap = section.mints.find((m) => m.mint === total.mint);
  if (!mintCap) throw refusal("mint_not_allowed", `mint ${total.mint} is not in ${key}'s mints`);
  const amount = mintAmount(total);
  if (amount > mintCap.perTxMaxRaw) {
    throw refusal(
      "per_transaction",
      `${amount} of mint ${total.mint} (atomic) exceeds ${key}'s per-transaction cap of ${mintCap.perTxMaxRaw}`,
    );
  }
  const since = nowMs - DAY_MS;
  const spent = records
    .filter(
      (r) =>
        r.kind === "program" &&
        (r.accountIndex ?? 0) === accountIndex &&
        r.chain.toLowerCase() === chain.toLowerCase() &&
        countsAgainstCaps(r.status) &&
        r.tokenAddress !== undefined &&
        canonicalAddress("solana", r.tokenAddress) === canonicalAddress("solana", total.mint) &&
        r.createdAtMs >= since,
    )
    .reduce((sum, r) => sum + recordAmount(r), 0n);
  if (spent + amount > mintCap.dailyMaxRaw) {
    throw refusal(
      "daily",
      `${amount} of mint ${total.mint} (atomic) would exceed ${key}'s rolling 24h cap of ${mintCap.dailyMaxRaw} (${spent} already spent)`,
    );
  }
}
