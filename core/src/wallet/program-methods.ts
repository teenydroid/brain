// wallet_prepare_program / wallet_execute_prepared_program: host-built Solana
// program transactions signed by one wallet account under that account's own
// `[wallet_policy.accounts.solana_<n>]` section. A port of OpenHuman's
// web3::wallet::program::ops.
//
// 1. Prepare. The caller hands over an unsigned transaction (base64), the
//    account index and `expectedDebits`. The host decodes it and refuses it
//    unless the policy section exists, the account is the fee payer, every
//    other signer has signed validly, every instruction invokes an allowed
//    program, token instructions are TransferChecked only, the declared debits
//    are within the mint's caps, and a simulation debits exactly what was
//    declared. It stores the exact bytes under a quote id for 60 seconds.
// 2. Execute. Simulates again, re-evaluates the policy against the ledger and
//    reserves the spend atomically, signs the message with the account's key,
//    verifies every signature, and sends. A quote is never executed twice.
import { createHash } from "node:crypto";
import type { CoreContext, RpcMethods } from "../server/types.js";
import { signEd25519, solanaKeypair } from "./derive.js";
import { PolicyRefusal, fail, refusal } from "./errors.js";
import type { LedgerRecord } from "./ledger.js";
import { type MintTotal, accountKey, alreadyAttempted, alreadyAttemptedMessage, evaluateProgram, loadPolicy, mintAmount, RPC_ORIGIN } from "./policy.js";
import type { ProgramQuote, Runtime } from "./runtime.js";
import { sendTransaction } from "./solana-chain.js";
import {
  MAX_NATIVE_DEBIT_LAMPORTS,
  QUOTE_PREFIX,
  QUOTE_TTL_MS,
  type SimulationReport,
  attachSignature,
  base58,
  checkSimulation,
  decodeTransaction,
  signatureIsValid,
  simulate,
  summarize,
} from "./solana-program.js";
import { ensureAccount, loadState, withSeed } from "./store.js";
import { newId, requireStr } from "./util.js";

type Params = Record<string, unknown>;

function accountIndexOf(params: Params): number {
  const raw = params.accountIndex ?? params.account_index;
  if (typeof raw !== "number" || !Number.isInteger(raw) || raw < 0) throw fail("invalid params: accountIndex is a whole number");
  return raw;
}

/** The native SOL bound: the fixed one, or lower when OPENHUMAN_WALLET_PROGRAM_MAX_NATIVE_LAMPORTS says so (the env can only lower it). */
function nativeCap(ctx: CoreContext): bigint {
  const raw = ctx.env.OPENHUMAN_WALLET_PROGRAM_MAX_NATIVE_LAMPORTS?.trim();
  const lower = raw && /^\d+$/.test(raw) ? BigInt(raw) : undefined;
  return lower !== undefined && lower < MAX_NATIVE_DEBIT_LAMPORTS ? lower : MAX_NATIVE_DEBIT_LAMPORTS;
}

function onlyFields(params: Params, allowed: readonly string[], method: string): void {
  for (const key of Object.keys(params)) {
    if (!allowed.includes(key)) throw fail(`invalid params: ${method} does not take ${key}`);
  }
}

function declaredOf(params: Params): MintTotal[] {
  const raw = params.expectedDebits ?? params.expected_debits;
  if (!Array.isArray(raw)) throw fail("invalid params: expectedDebits is a list of {mint, amountRaw}");
  return raw.map((entry) => {
    if (typeof entry !== "object" || entry === null || Array.isArray(entry)) throw fail("invalid params: an expectedDebits entry is not an object");
    const e = entry as Params;
    onlyFields(e, ["mint", "amountRaw", "amount_raw"], "expectedDebits");
    const amount = e.amountRaw ?? e.amount_raw;
    if (typeof e.mint !== "string" || (typeof amount !== "string" && typeof amount !== "number")) {
      throw fail("invalid params: an expectedDebits entry needs a mint and an amountRaw");
    }
    return { mint: e.mint, amountRaw: String(amount) };
  });
}

function programRecord(quote: ProgramQuote, status: LedgerRecord["status"], nowMs: number): LedgerRecord {
  const total = quote.declaredDebits.find((d) => mintAmount(d) > 0n);
  const dests = quote.summary.movements.map((m) => m.destination);
  const destination = dests.length === 0 ? "" : dests.every((d) => d === dests[0]) ? dests[0]! : "multiple";
  return {
    id: newId(),
    quoteId: quote.quoteId,
    createdAtMs: nowMs,
    updatedAtMs: nowMs,
    origin: RPC_ORIGIN.label,
    originClass: RPC_ORIGIN.class,
    policyApplied: true,
    chain: "solana",
    assetSymbol: total ? "SPL" : "",
    ...(total ? { tokenAddress: total.mint } : {}),
    toAddress: destination,
    amountRaw: total ? total.amountRaw : "0",
    status,
    kind: "program",
    ...(quote.accountIndex !== 0 ? { accountIndex: quote.accountIndex } : {}),
  };
}

const fingerprint = (encrypted: string): string => createHash("sha256").update(encrypted).digest("hex");

function publicQuote(q: ProgramQuote): Record<string, unknown> {
  return {
    quoteId: q.quoteId,
    chain: "solana",
    accountIndex: q.accountIndex,
    address: q.address,
    summary: q.summary,
    declaredDebits: q.declaredDebits,
    simulation: { err: q.simulation.err ?? null, debits: q.simulation.debits, nativeDebitLamports: q.simulation.nativeDebitLamports, tokenAccounts: q.simulation.tokenAccounts, ...(q.simulation.unitsConsumed !== undefined ? { unitsConsumed: q.simulation.unitsConsumed } : {}) },
    createdAtMs: q.createdAtMs,
    expiresAtMs: q.expiresAtMs,
  };
}

export function programMethods(rt: Runtime): RpcMethods {
  const methods: RpcMethods = {};

  methods.wallet_prepare_program = async (params, ctx) => {
    onlyFields(params, ["accountIndex", "account_index", "transaction", "expectedDebits", "expected_debits"], "wallet_prepare_program");
    const accountIndex = accountIndexOf(params);
    const key = accountKey("solana", accountIndex);
    const policy = loadPolicy(ctx);
    // The policy section is checked before any key derivation.
    if (!policy.accounts[key]) throw refusal("account_not_allowed", `account ${key} has no [wallet_policy.accounts.${key}] section`);
    const transactionText = requireStr(params, "transaction").trim();
    if (!/^[A-Za-z0-9+/]*={0,2}$/.test(transactionText) || transactionText.length % 4 !== 0) throw fail("transaction is not valid base64");
    const transaction = new Uint8Array(Buffer.from(transactionText, "base64"));
    const declared = declaredOf(params);
    const decoded = decodeTransaction(transaction);
    const account = await ensureAccount(ctx, "solana", accountIndex);
    const state = loadState(ctx)!;
    const summary = summarize(decoded, account.address);
    const now = rt.now();
    const ledger = rt.ledger(ctx);
    evaluateProgram(policy, "solana", accountIndex, summary, declared, ledger.records(), now);
    const rpc = rt.solana(ctx);
    const simulation = await simulateQuote(rpc, transaction, decoded, ctx);
    checkSimulation(declared, simulation, nativeCap(ctx));
    const quote: ProgramQuote = {
      quoteId: `${QUOTE_PREFIX}${newId()}`,
      stateDir: ctx.stateDir,
      wallet: fingerprint(state.encryptedMnemonic),
      endpoint: rt.solanaEndpoint(ctx),
      accountIndex,
      address: account.address,
      summary,
      declaredDebits: declared,
      simulation,
      transaction,
      decoded,
      createdAtMs: now,
      expiresAtMs: now + QUOTE_TTL_MS,
    };
    rt.programs.put(quote, now);
    ctx.log("info", "wallet program prepared", { quoteId: quote.quoteId, account: key, programs: summary.programs.length });
    return publicQuote(quote);
  };

  methods.wallet_execute_prepared_program = async (params, ctx) => {
    onlyFields(params, ["accountIndex", "account_index", "quoteId", "quote_id"], "wallet_execute_prepared_program");
    const accountIndex = accountIndexOf(params);
    const quoteId = requireStr(params, params.quoteId !== undefined ? "quoteId" : "quote_id");
    const unavailable = (): Error => fail("prepared program quote is unavailable or expired");
    const held = rt.programs.peek(quoteId);
    const state = loadState(ctx);
    if (!held || !state) throw unavailable();
    // A quote from another workspace, wallet, account or endpoint is reported as unavailable and left for its own context.
    if (
      held.stateDir !== ctx.stateDir ||
      held.wallet !== fingerprint(state.encryptedMnemonic) ||
      held.accountIndex !== accountIndex ||
      held.endpoint !== rt.solanaEndpoint(ctx)
    ) {
      throw unavailable();
    }
    const now = rt.now();
    if (held.expiresAtMs <= now) {
      rt.programs.take(quoteId);
      throw unavailable();
    }
    rt.programs.take(quoteId);
    const rpc = rt.solana(ctx);
    const policy = loadPolicy(ctx);
    const ledger = rt.ledger(ctx);
    // Simulate against the chain as it is now. The caps and the declaration are checked against this snapshot only: an
    // opaque program can debit a different amount when the transaction is included, so the caps are not a bound on the
    // chain's final state.
    let simulated: PolicyRefusal | undefined;
    try {
      const report = await simulateQuote(rpc, held.transaction, held.decoded, ctx);
      try {
        checkSimulation(held.declaredDebits, report, nativeCap(ctx));
      } catch (error) {
        if (error instanceof PolicyRefusal) simulated = error;
        else throw error;
      }
    } catch (error) {
      rt.programs.put(held, now);
      throw error;
    }
    let record: LedgerRecord;
    try {
      record = await ledger.reserve((records) => {
        // The clock at the point of no return: a quote that expired while the simulation ran is refused before anything is signed.
        const reservedAt = rt.now();
        if (held.expiresAtMs <= reservedAt) {
          throw refusal("quote_expired", `the quote expired at ${held.expiresAtMs} (now ${reservedAt}); prepare it again`);
        }
        if (simulated) throw simulated;
        const earlier = alreadyAttempted(records, held.quoteId);
        if (earlier) throw refusal("already_attempted", alreadyAttemptedMessage(held.quoteId, earlier.status, true));
        evaluateProgram(policy, "solana", held.accountIndex, held.summary, held.declaredDebits, records, reservedAt);
        return programRecord(held, "pending", reservedAt);
      });
    } catch (error) {
      if (error instanceof PolicyRefusal) {
        await ledger.put({ ...programRecord(held, "refused", now), error: error.detail }).catch(() => undefined);
        if (error.kind !== "already_attempted") rt.programs.put(held, now);
      }
      throw error;
    }
    ctx.log("info", "wallet program execute recorded as pending", { quoteId: held.quoteId, recordId: record.id });
    let hash: string;
    try {
      hash = await signAndSend(rt, ctx, held);
    } catch (error) {
      const message = error instanceof Error ? error.message : String(error);
      await ledger.put({ ...record, status: "in_doubt", error: message.slice(0, 300), updatedAtMs: Math.max(now, rt.now()) }).catch(() => undefined);
      throw error;
    }
    await ledger.put({ ...record, status: "broadcast", transactionHash: hash, updatedAtMs: Math.max(now, rt.now()) });
    return { quoteId: held.quoteId, chain: "solana", accountIndex: held.accountIndex, address: held.address, transactionHash: hash };
  };

  return methods;
}

async function simulateQuote(
  rpc: import("./rpc.js").SolanaRpc,
  transaction: Uint8Array,
  decoded: ReturnType<typeof decodeTransaction>,
  ctx: CoreContext,
): Promise<SimulationReport> {
  const owner = decoded.accountKeys[0];
  if (!owner) throw fail("the transaction lists no accounts");
  // Native SOL is watched on the fee payer and on any other Solana account of this wallet the message names.
  const mine = new Set((loadState(ctx)?.accounts ?? []).filter((a) => a.chain === "solana").map((a) => a.address));
  const watch = decoded.accountKeys.filter((k) => mine.has(base58(k)));
  try {
    return await simulate(rpc, transaction, owner, decoded.accountKeys, watch);
  } catch (error) {
    throw fail(`could not simulate the transaction: ${error instanceof Error ? error.message : "error"}`);
  }
}

async function signAndSend(rt: Runtime, ctx: CoreContext, quote: ProgramQuote): Promise<string> {
  return withSeed(ctx, async (seed) => {
    const account = await ensureAccount(ctx, "solana", quote.accountIndex);
    const keypair = solanaKeypair(seed, account.derivationPath);
    const signer = quote.decoded.accountKeys[0];
    if (!signer || keypair.publicKey.toBase58() !== base58(signer)) throw fail("the stored wallet no longer matches the quote");
    const signature = signEd25519(keypair, quote.decoded.message);
    const signed = attachSignature(quote.transaction, quote.decoded, signer, signature);
    // Every slot must now hold a valid signature: the co-signers' were checked at prepare, and this proves the fee
    // payer's key signed.
    const check = decodeTransaction(signed);
    for (let slot = 0; slot < check.signatures.length; slot++) {
      if (!signatureIsValid(check.accountKeys[slot]!, check.message, check.signatures[slot]!)) {
        throw fail(`signature ${slot} does not verify; the transaction was not sent`);
      }
    }
    return sendTransaction(rt.solana(ctx), signed);
  });
}
