// Host-built Solana program transactions: decode, summarise, simulate. A port
// of OpenHuman's web3::wallet::program (mod.rs and simulate.rs). The pure parts
// (decoding, summarising) are separate functions so they test without a chain.
import { createPublicKey, verify } from "node:crypto";
import { PublicKey } from "@solana/web3.js";
import { PolicyRefusal, refusal } from "./errors.js";
import type { MintTotal } from "./policy.js";
import type { SolanaRpc } from "./rpc.js";
import { bytesToHex } from "./util.js";

export const SYSTEM_PROGRAM = "11111111111111111111111111111111";
export const TOKEN_PROGRAM = "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA";
export const TOKEN_2022_PROGRAM = "TokenzQdBNbLqP5VEhdkAS6EPFLC1PHnBqCXEpPxuEb";
export const ASSOCIATED_TOKEN_PROGRAM = "ATokenGPvbdGVxr1b2hvZbsiqW5xWH25efTNsLJA8knL";

/** A Solana blockhash lives about 60-90s, so a longer quote would only fail at the RPC. */
export const QUOTE_TTL_MS = 60_000;
export const MAX_TRANSACTION_BYTES = 1232;
export const QUOTE_PREFIX = "prog-";
const TOKEN_TRANSFER_CHECKED = 12;
const TOKEN_ACCOUNT_LEN = 165;
const SNAPSHOT_ATTEMPTS = 3;

export function base58(bytes: Uint8Array): string {
  return new PublicKey(bytes).toBase58();
}

export interface CompiledInstruction {
  programIndex: number;
  accounts: number[];
  data: Uint8Array;
}

export interface DecodedTransaction {
  signatureCount: number;
  signaturesOffset: number;
  signatures: Uint8Array[];
  message: Uint8Array;
  versioned: boolean;
  numRequiredSignatures: number;
  accountKeys: Uint8Array[];
  recentBlockhash: Uint8Array;
  instructions: CompiledInstruction[];
  lookupTableCount: number;
}

class Reader {
  pos = 0;
  constructor(readonly bytes: Uint8Array) {}

  take(len: number): Uint8Array {
    const end = this.pos + len;
    if (len < 0 || end > this.bytes.length) throw new Error("transaction is truncated");
    const slice = this.bytes.subarray(this.pos, end);
    this.pos = end;
    return slice;
  }

  u8(): number {
    return this.take(1)[0]!;
  }

  key(): Uint8Array {
    return this.take(32).slice();
  }

  /** Solana's compact-u16 ("shortvec") length. */
  compactU16(): number {
    let value = 0;
    for (const shift of [0, 7, 14]) {
      const byte = this.u8();
      value |= (byte & 0x7f) << shift;
      if ((byte & 0x80) === 0) {
        if (value > 0xffff) break;
        return value;
      }
    }
    throw new Error("transaction has an invalid compact-u16 length");
  }
}

/** Decode a transaction's wire bytes (signature slots followed by a legacy or v0 message). */
export function decodeTransaction(bytes: Uint8Array): DecodedTransaction {
  if (bytes.length > MAX_TRANSACTION_BYTES) {
    throw new Error(`transaction is ${bytes.length} bytes; the Solana limit is ${MAX_TRANSACTION_BYTES}`);
  }
  const r = new Reader(bytes);
  const signatureCount = r.compactU16();
  const signaturesOffset = r.pos;
  const sigBytes = r.take(signatureCount * 64);
  const signatures: Uint8Array[] = [];
  for (let i = 0; i < signatureCount; i++) signatures.push(sigBytes.slice(i * 64, i * 64 + 64));
  const messageStart = r.pos;
  const first = bytes[messageStart];
  if (first === undefined) throw new Error("transaction has no message");
  const versioned = (first & 0x80) !== 0;
  if (versioned) {
    const version = r.u8() & 0x7f;
    if (version !== 0) throw new Error(`unsupported transaction message version ${version}`);
  }
  const numRequiredSignatures = r.u8();
  r.u8();
  r.u8();
  const keyCount = r.compactU16();
  const accountKeys: Uint8Array[] = [];
  for (let i = 0; i < keyCount; i++) accountKeys.push(r.key());
  const recentBlockhash = r.key();
  const instructionCount = r.compactU16();
  const instructions: CompiledInstruction[] = [];
  for (let i = 0; i < instructionCount; i++) {
    const programIndex = r.u8();
    const accountCount = r.compactU16();
    const accounts = Array.from(r.take(accountCount));
    const dataLen = r.compactU16();
    const data = r.take(dataLen).slice();
    instructions.push({ programIndex, accounts, data });
  }
  let lookupTableCount = 0;
  if (versioned) {
    lookupTableCount = r.compactU16();
    for (let i = 0; i < lookupTableCount; i++) {
      r.key();
      r.take(r.compactU16());
      r.take(r.compactU16());
    }
  }
  if (r.pos !== bytes.length) throw new Error("transaction has trailing bytes after the message");
  return {
    signatureCount,
    signaturesOffset,
    signatures,
    message: bytes.slice(messageStart),
    versioned,
    numRequiredSignatures,
    accountKeys,
    recentBlockhash,
    instructions,
    lookupTableCount,
  };
}

/** BYTES with SIGNATURE in every signer slot whose key is SIGNER. */
export function attachSignature(bytes: Uint8Array, decoded: DecodedTransaction, signer: Uint8Array, signature: Uint8Array): Uint8Array {
  const signed = bytes.slice();
  let placed = false;
  for (let slot = 0; slot < decoded.signatureCount; slot++) {
    const key = decoded.accountKeys[slot];
    if (key && Buffer.from(key).equals(Buffer.from(signer))) {
      signed.set(signature, decoded.signaturesOffset + slot * 64);
      placed = true;
    }
  }
  if (!placed) throw new Error("the signing account holds no signer slot in the transaction");
  return signed;
}

const ED25519_SPKI_PREFIX = Buffer.from("302a300506032b6570032100", "hex");

/** Whether SIGNATURE is a valid ed25519 signature by KEY over MESSAGE. */
export function signatureIsValid(key: Uint8Array, message: Uint8Array, signature: Uint8Array): boolean {
  try {
    const publicKey = createPublicKey({ key: Buffer.concat([ED25519_SPKI_PREFIX, Buffer.from(key)]), format: "der", type: "spki" });
    return verify(null, message, publicKey, signature);
  } catch {
    return false;
  }
}

const isZero = (bytes: Uint8Array): boolean => bytes.every((b) => b === 0);

/** Refuse unless every required signer other than SIGNER has already signed the message validly. */
export function checkCosigners(tx: DecodedTransaction, signer: Uint8Array): void {
  for (let slot = 0; slot < tx.signatureCount; slot++) {
    const key = tx.accountKeys[slot];
    if (!key) throw refusal("transaction_not_allowed", "a signer slot has no account key");
    if (Buffer.from(key).equals(Buffer.from(signer))) continue;
    const signature = tx.signatures[slot]!;
    if (isZero(signature)) {
      throw refusal("transaction_not_allowed", `required signer ${base58(key)} has not signed; co-sign before preparing`);
    }
    if (!signatureIsValid(key, tx.message, signature)) {
      throw refusal("transaction_not_allowed", `the signature of required signer ${base58(key)} does not verify over the message`);
    }
  }
}

export interface InstructionSummary {
  programId: string;
  accounts: string[];
  dataHex: string;
}

export interface TokenMovement {
  programId: string;
  mint: string;
  source: string;
  destination: string;
  authority: string;
  amountRaw: string;
  decimals: number;
}

export interface ProgramTransactionSummary {
  feePayer: string;
  signers: string[];
  recentBlockhash: string;
  programs: string[];
  instructions: InstructionSummary[];
  movements: TokenMovement[];
  totals: MintTotal[];
}

const structural = (message: string): PolicyRefusal => refusal("transaction_not_allowed", message);
const forbidden = (message: string): PolicyRefusal => refusal("instruction_not_allowed", message);

/**
 * Decode TX into its summary and check what the host refuses whatever the
 * policy says: SIGNER must be the fee payer, every other required signer must
 * already have signed validly, no lookup tables, and only the token, ATA and
 * System instructions this path can account for.
 */
export function summarize(tx: DecodedTransaction, signer: string): ProgramTransactionSummary {
  if (tx.lookupTableCount > 0) {
    throw structural("transactions that load address lookup tables are not supported; list every account in the message");
  }
  if (tx.numRequiredSignatures === 0 || tx.numRequiredSignatures !== tx.signatureCount) {
    throw structural(`the transaction needs ${tx.numRequiredSignatures} signatures but carries ${tx.signatureCount} slots`);
  }
  const first = tx.accountKeys[0];
  if (!first) throw structural("the transaction lists no accounts");
  const feePayer = base58(first);
  if (feePayer !== signer) throw structural(`the fee payer ${feePayer} is not the signing account ${signer}`);
  checkCosigners(tx, first);
  const signers = tx.accountKeys.slice(0, tx.signatureCount).map(base58);
  const key = (index: number): string => {
    const k = tx.accountKeys[index];
    if (!k) throw structural(`account index ${index} is out of range`);
    return base58(k);
  };
  const programs: string[] = [];
  const instructions: InstructionSummary[] = [];
  const movements: TokenMovement[] = [];
  const totals = new Map<string, bigint>();
  const mintOrder: string[] = [];
  tx.instructions.forEach((instruction, position) => {
    const programId = key(instruction.programIndex);
    const accounts = instruction.accounts.map(key);
    if (programId === SYSTEM_PROGRAM) {
      throw forbidden(`instruction ${position} calls the System program; native SOL movements are not signed through program executes`);
    } else if (programId === TOKEN_PROGRAM || programId === TOKEN_2022_PROGRAM) {
      const movement = tokenTransferChecked(position, programId, instruction, accounts);
      if (!totals.has(movement.mint)) {
        mintOrder.push(movement.mint);
        totals.set(movement.mint, 0n);
      }
      totals.set(movement.mint, totals.get(movement.mint)! + BigInt(movement.amountRaw));
      movements.push(movement);
    } else if (programId === ASSOCIATED_TOKEN_PROGRAM) {
      // Create (empty data or 0) and CreateIdempotent (1) only; RecoverNested moves tokens.
      const d = instruction.data;
      const ok = d.length === 0 || (d.length === 1 && (d[0] === 0 || d[0] === 1));
      if (!ok) throw forbidden(`instruction ${position} is not an associated token account create`);
    }
    if (!programs.includes(programId)) programs.push(programId);
    instructions.push({ programId, accounts, dataHex: bytesToHex(instruction.data) });
  });
  return {
    feePayer,
    signers,
    recentBlockhash: base58(tx.recentBlockhash),
    programs,
    instructions,
    movements,
    totals: mintOrder.map((mint) => ({ mint, amountRaw: (totals.get(mint) ?? 0n).toString() })),
  };
}

function tokenTransferChecked(position: number, programId: string, instruction: CompiledInstruction, accounts: string[]): TokenMovement {
  const tag = instruction.data[0];
  if (tag === 3) {
    throw forbidden(`instruction ${position} is a token Transfer, which does not name its mint; use TransferChecked`);
  }
  if (tag !== TOKEN_TRANSFER_CHECKED) {
    // Rust prints Option<u8> Debug here.
    throw forbidden(
      `instruction ${position} is token instruction ${tag === undefined ? "None" : `Some(${tag})`}; only TransferChecked is signed through program executes`,
    );
  }
  if (instruction.data.length !== 10 || accounts.length < 4) throw structural(`instruction ${position} is a malformed TransferChecked`);
  const amount = Buffer.from(instruction.data.subarray(1, 9)).readBigUInt64LE(0);
  return {
    programId,
    source: accounts[0]!,
    mint: accounts[1]!,
    destination: accounts[2]!,
    authority: accounts[3]!,
    amountRaw: amount.toString(),
    decimals: instruction.data[9]!,
  };
}

// ---------------------------------------------------------------------------
// Simulation
// ---------------------------------------------------------------------------

export interface SimulationReport {
  err?: unknown;
  debits: MintTotal[];
  tokenAccounts: string[];
  unitsConsumed?: number;
}

export interface TokenAccountState {
  mint: Uint8Array;
  owner: Uint8Array;
  amount: bigint;
}

export function parseTokenAccount(data: Uint8Array): TokenAccountState | undefined {
  if (data.length < TOKEN_ACCOUNT_LEN) return undefined;
  return {
    mint: data.slice(0, 32),
    owner: data.slice(32, 64),
    amount: Buffer.from(data.subarray(64, 72)).readBigUInt64LE(0),
  };
}

const sameKey = (a: Uint8Array, b: Uint8Array): boolean => Buffer.from(a).equals(Buffer.from(b));

/** Per-mint debits: each pre-state account the signer owns, its balance minus its post-state balance. */
export function debits(owner: Uint8Array, pre: TokenAccountState[], post: Array<TokenAccountState | undefined>): MintTotal[] {
  // Keyed by the mint's bytes (hex), so the order is Rust's BTreeMap<[u8; 32]> order.
  const byMint = new Map<string, [bigint, bigint]>();
  pre.forEach((before, index) => {
    if (!sameKey(before.owner, owner)) return;
    const afterState = post[index];
    const after = afterState && sameKey(afterState.owner, owner) && sameKey(afterState.mint, before.mint) ? afterState.amount : 0n;
    const mint = bytesToHex(before.mint);
    const entry = byMint.get(mint) ?? [0n, 0n];
    byMint.set(mint, [entry[0] + before.amount, entry[1] + after]);
  });
  return [...byMint.entries()]
    .filter(([, [before, after]]) => before > after)
    .sort(([a], [b]) => (a < b ? -1 : a > b ? 1 : 0))
    .map(([mint, [before, after]]) => ({ mint: base58(Buffer.from(mint, "hex")), amountRaw: (before - after).toString() }));
}

/** Refuse unless the simulation succeeded and every debit equals DECLARED. */
export function checkSimulation(declared: MintTotal[], report: SimulationReport): void {
  if (report.err !== undefined && report.err !== null) {
    throw refusal("simulation_mismatch", `the transaction fails in simulation: ${JSON.stringify(report.err)}`);
  }
  const mints = declared.map((d) => d.mint);
  for (const d of report.debits) if (!mints.includes(d.mint)) mints.push(d.mint);
  const amount = (list: MintTotal[], mint: string): bigint => {
    const found = list.find((d) => d.mint === mint);
    return found && /^\s*\d+\s*$/.test(found.amountRaw) ? BigInt(found.amountRaw.trim()) : found ? (1n << 128n) - 1n : 0n;
  };
  for (const mint of mints) {
    const stated = amount(declared, mint);
    const simulated = amount(report.debits, mint);
    if (stated !== simulated) {
      throw refusal("simulation_mismatch", `the simulation debits ${simulated} of mint ${mint} from the account, but ${stated} was declared`);
    }
  }
}

interface RpcAccount {
  data?: [string, string];
}

function accountData(account: RpcAccount | null | undefined): Uint8Array | undefined {
  const encoded = account?.data?.[0];
  return typeof encoded === "string" ? new Uint8Array(Buffer.from(encoded, "base64")) : undefined;
}

function contextSlot(answer: { context?: { slot?: number } }, method: string): number {
  const slot = answer.context?.slot;
  if (typeof slot !== "number") throw new Error(`${method} returned no context slot`);
  return slot;
}

/** Read the signer's token accounts the transaction names, simulate it and measure the debits. */
export async function simulate(
  rpc: SolanaRpc,
  transaction: Uint8Array,
  owner: Uint8Array,
  accountKeys: Uint8Array[],
): Promise<SimulationReport> {
  let seen: number[] = [];
  for (let attempt = 0; attempt < SNAPSHOT_ATTEMPTS; attempt++) {
    const measured = await measure(rpc, transaction, owner, accountKeys);
    if ("report" in measured) return measured.report;
    seen = measured.slots;
  }
  throw new Error(
    `the token balances and the simulation were read at different slots (${JSON.stringify(seen)}) on every attempt; the transaction was not measured, retry`,
  );
}

async function measure(
  rpc: SolanaRpc,
  transaction: Uint8Array,
  owner: Uint8Array,
  accountKeys: Uint8Array[],
): Promise<{ report: SimulationReport } | { slots: number[] }> {
  const ownerText = base58(owner);
  const named = new Set(accountKeys.map(base58));
  const slots: number[] = [];
  const addresses: string[] = [];
  const pre: TokenAccountState[] = [];
  for (const program of [TOKEN_PROGRAM, TOKEN_2022_PROGRAM]) {
    const answer = await rpc.call<{ context?: { slot?: number }; value?: Array<{ pubkey?: string; account?: RpcAccount }> }>(
      "getTokenAccountsByOwner",
      [ownerText, { programId: program }, { encoding: "base64", commitment: "confirmed" }],
    );
    slots.push(contextSlot(answer, "getTokenAccountsByOwner"));
    if (!Array.isArray(answer.value)) throw new Error("getTokenAccountsByOwner returned no value list");
    for (const row of answer.value) {
      if (typeof row.pubkey !== "string") throw new Error("getTokenAccountsByOwner row has no pubkey");
      if (!named.has(row.pubkey)) continue;
      const data = accountData(row.account);
      const state = data ? parseTokenAccount(data) : undefined;
      if (!state) throw new Error("getTokenAccountsByOwner row is not a token account");
      addresses.push(row.pubkey);
      pre.push(state);
    }
  }
  const answer = await rpc.call<{
    context?: { slot?: number };
    value?: { err?: unknown; accounts?: Array<RpcAccount | null>; unitsConsumed?: number };
  }>("simulateTransaction", [
    Buffer.from(transaction).toString("base64"),
    {
      encoding: "base64",
      sigVerify: false,
      replaceRecentBlockhash: false,
      commitment: "confirmed",
      accounts: { encoding: "base64", addresses },
    },
  ]);
  slots.push(contextSlot(answer, "simulateTransaction"));
  if (slots.some((s) => s !== slots[0])) return { slots };
  const value = answer.value;
  if (!value) throw new Error("simulateTransaction returned no value");
  const err = value.err !== null && value.err !== undefined ? value.err : undefined;
  let post: Array<TokenAccountState | undefined> = [];
  if (Array.isArray(value.accounts)) {
    post = value.accounts.map((a) => {
      const data = accountData(a);
      return data ? parseTokenAccount(data) : undefined;
    });
  } else if (err === undefined) {
    throw new Error("simulateTransaction returned no accounts");
  }
  if (err === undefined && post.length !== pre.length) throw new Error("simulateTransaction returned the wrong number of accounts");
  return {
    report: {
      ...(err !== undefined ? { err } : {}),
      debits: err !== undefined ? [] : debits(owner, pre, post),
      tokenAccounts: addresses,
      ...(typeof value.unitsConsumed === "number" ? { unitsConsumed: value.unitsConsumed } : {}),
    },
  };
}
