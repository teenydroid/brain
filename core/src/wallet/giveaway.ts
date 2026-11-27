// `giveaway-tx create|close`: the unsigned transactions of a Tiny Drops round,
// a port of spine/src/giveaway_tx.rs. It holds no key of Teeny's, reads
// nothing from the network and signs nothing that can move a token: the
// giveaway wallet (Solana index 1) signs inside the core, after
// wallet_prepare_program has checked the transaction against
// [wallet_policy.accounts.solana_1].
//
// The program is the Solana Foundation Rewards program; every fact below is
// the one web/src/lib/distributor.ts encodes (docs/drops-schema.md), and
// hands/tests/giveaway/fixtures/rewards_tx.json (made by @solana/web3.js) is
// the vector both this and the Rust original are held to.
//
//   distribution  PDA ["merkle_distribution", mint, authority, seed]
//   event auth.   PDA ["event_authority"]
//   vault         ATA(distribution, mint, token program)
//
// `create` (CreateMerkleDistribution, discriminator 5): data
// `5 | bump | revocable 0 | u64 amount | root[32] | u64 total_amount | i64 clawback_ts`
// with amount = total_amount. `seed` is a fresh throwaway keypair made here,
// which signs the message at once and is then forgotten. The fee payer and
// authority are the giveaway wallet; its signature slot is left empty.
//
// `close` (CloseMerkleDistribution, discriminator 8, data `[8]`) is refused
// unless `distributor` is the address `distribution_seed` derives.
//
// Both messages are legacy, compiled the way @solana/web3.js
// TransactionMessage.compileToLegacyMessage does: the fee payer first, then
// every key in first-use order (each instruction's program before its
// accounts), grouped writable signers, read-only signers, writable
// non-signers, read-only non-signers.
import { createPrivateKey, createPublicKey, randomBytes, sign as edSign } from "node:crypto";
import { PublicKey } from "@solana/web3.js";
import { bytesToHex } from "./util.js";

export const COMMAND = "giveaway-tx";
/** The binary's name in the usage line (the Rust original printed `teeny-spine`). */
export const PROGRAM_NAME = "teeny-spine";

type Key = Uint8Array;

const SYSTEM_PROGRAM: Key = new Uint8Array(32);
const ATA_PROGRAM = "ATokenGPvbdGVxr1b2hvZbsiqW5xWH25efTNsLJA8knL";
const IX_CREATE_MERKLE_DISTRIBUTION = 5;
const IX_CLOSE_MERKLE_DISTRIBUTION = 8;

/** A failure that the CLI reports as `{"error": ...}` on stderr with exit 2. */
export class GiveawayError extends Error {}

function key(what: string, text: string): Key {
  let bytes: Uint8Array;
  try {
    bytes = decodeBase58(text);
  } catch {
    throw new GiveawayError(`${what} is not base58`);
  }
  if (bytes.length !== 32) throw new GiveawayError(`${what} is not a 32-byte address`);
  return bytes;
}

const B58 = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz";

function decodeBase58(text: string): Uint8Array {
  let value = 0n;
  for (const ch of text) {
    const digit = B58.indexOf(ch);
    if (digit < 0) throw new Error("bad base58");
    value = value * 58n + BigInt(digit);
  }
  const zeros = text.length - text.replace(/^1+/, "").length;
  const hex = value === 0n ? "" : value.toString(16);
  const body = Buffer.from(hex.length % 2 ? `0${hex}` : hex, "hex");
  return new Uint8Array(Buffer.concat([Buffer.alloc(zeros), body]));
}

function b58(bytes: Key): string {
  return new PublicKey(bytes).toBase58();
}

const eq = (a: Key, b: Key): boolean => Buffer.from(a).equals(Buffer.from(b));

/** PublicKey.findProgramAddressSync: the first bump from 255 down whose hash is not a point on ed25519. */
function findProgramAddress(seeds: Uint8Array[], program: Key): [Key, number] {
  const [address, bump] = PublicKey.findProgramAddressSync(seeds, new PublicKey(program));
  return [address.toBytes(), bump];
}

function associatedTokenAddress(owner: Key, mint: Key, tokenProgram: Key): Key {
  return findProgramAddress([owner, tokenProgram, mint], key("the ATA program", ATA_PROGRAM))[0];
}

function distributionAddress(program: Key, mint: Key, authority: Key, seed: Key): [Key, number] {
  return findProgramAddress([Buffer.from("merkle_distribution"), mint, authority, seed], program);
}

function eventAuthority(program: Key): Key {
  return findProgramAddress([Buffer.from("event_authority")], program)[0];
}

interface Meta {
  key: Key;
  signer: boolean;
  writable: boolean;
}

const meta = (k: Key, signer: boolean, writable: boolean): Meta => ({ key: k, signer, writable });

interface Instruction {
  program: Key;
  accounts: Meta[];
  data: Uint8Array;
}

function shortvec(out: number[], value: number): void {
  let v = value;
  for (;;) {
    const byte = v & 0x7f;
    v >>>= 7;
    if (v === 0) {
      out.push(byte);
      return;
    }
    out.push(byte | 0x80);
  }
}

interface Message {
  bytes: Uint8Array;
  signers: Key[];
}

function compile(payer: Key, instructions: Instruction[], blockhash: Key): Message {
  const keys: Meta[] = [meta(payer, true, true)];
  const upsert = (m: Meta): void => {
    const existing = keys.find((k) => eq(k.key, m.key));
    if (existing) {
      existing.signer ||= m.signer;
      existing.writable ||= m.writable;
    } else keys.push({ ...m });
  };
  for (const ix of instructions) {
    upsert(meta(ix.program, false, false));
    for (const account of ix.accounts) upsert(account);
  }
  const group = (signer: boolean, writable: boolean): Meta[] => keys.filter((k) => k.signer === signer && k.writable === writable);
  const ordered = [...group(true, true), ...group(true, false), ...group(false, true), ...group(false, false)];
  if (ordered.length > 256) throw new GiveawayError("too many accounts for one transaction");
  const index = (k: Key): number => {
    const at = ordered.findIndex((m) => eq(m.key, k));
    if (at < 0) throw new GiveawayError("program missing from the keys");
    return at;
  };
  const signers = ordered.filter((m) => m.signer).map((m) => m.key);
  const readonlySigned = ordered.filter((m) => m.signer && !m.writable).length;
  const readonlyUnsigned = ordered.filter((m) => !m.signer && !m.writable).length;
  const bytes: number[] = [signers.length, readonlySigned, readonlyUnsigned];
  shortvec(bytes, ordered.length);
  for (const m of ordered) bytes.push(...m.key);
  bytes.push(...blockhash);
  shortvec(bytes, instructions.length);
  for (const ix of instructions) {
    bytes.push(index(ix.program));
    shortvec(bytes, ix.accounts.length);
    for (const account of ix.accounts) bytes.push(index(account.key));
    shortvec(bytes, ix.data.length);
    bytes.push(...ix.data);
  }
  return { bytes: Uint8Array.from(bytes), signers };
}

const PKCS8_ED25519 = Buffer.from("302e020100300506032b657004220420", "hex");

interface Ed25519Key {
  secret: Key;
  publicKey: Key;
  sign(message: Uint8Array): Uint8Array;
}

function ed25519(secret: Key): Ed25519Key {
  const priv = createPrivateKey({ key: Buffer.concat([PKCS8_ED25519, Buffer.from(secret)]), format: "der", type: "pkcs8" });
  const spki = createPublicKey(priv).export({ format: "der", type: "spki" });
  return {
    secret,
    publicKey: new Uint8Array(spki.subarray(spki.length - 32)),
    sign: (message) => new Uint8Array(edSign(null, message, priv)),
  };
}

/** The wire bytes: every signer's slot, filled for the keys given, zeros for the rest. */
function wire(message: Message, signing: Ed25519Key[]): Uint8Array {
  const out: number[] = [];
  shortvec(out, message.signers.length);
  for (const signer of message.signers) {
    const k = signing.find((s) => eq(s.publicKey, signer));
    out.push(...(k ? k.sign(message.bytes) : new Uint8Array(64)));
  }
  out.push(...message.bytes);
  return Uint8Array.from(out);
}

function u64le(value: bigint): Buffer {
  const b = Buffer.alloc(8);
  b.writeBigUInt64LE(value);
  return b;
}

/** CreateMerkleDistribution's data, byte for byte as distributor.ts's createMerkleDistributionData. */
export function createData(bump: number, root: Key, totalAmount: bigint, clawbackTs: bigint): Uint8Array {
  const ts = Buffer.alloc(8);
  ts.writeBigInt64LE(clawbackTs);
  return Uint8Array.from([IX_CREATE_MERKLE_DISTRIBUTION, bump, 0, ...u64le(totalAmount), ...root, ...u64le(totalAmount), ...ts]);
}

export interface CreateRequest {
  program_id: string;
  mint: string;
  token_program: string;
  authority: string;
  merkle_root: string;
  total_amount: bigint;
  clawback_ts: bigint;
  recent_blockhash: string;
  /** Tests only: the seed keypair's secret, for a reproducible vector. */
  seed_secret_hex?: string;
}

export interface CloseRequest {
  program_id: string;
  mint: string;
  token_program: string;
  authority: string;
  distribution_seed: string;
  distributor: string;
  recent_blockhash: string;
}

export interface Options {
  /** The source of the seed keypair's secret; the default is the OS generator. */
  randomBytes?: (n: number) => Uint8Array;
}

function hex32(text: string, error: string): Key {
  if (text.length !== 64 || !/^[0-9a-fA-F]+$/.test(text)) throw new GiveawayError(error);
  return new Uint8Array(Buffer.from(text, "hex"));
}

const b64 = (bytes: Uint8Array): string => Buffer.from(bytes).toString("base64");

export function create(request: CreateRequest, options: Options = {}): Record<string, unknown> {
  const program = key("program_id", request.program_id);
  const mint = key("mint", request.mint);
  const tokenProgram = key("token_program", request.token_program);
  const authority = key("authority", request.authority);
  const blockhash = key("recent_blockhash", request.recent_blockhash);
  const root = hex32(request.merkle_root, "merkle_root is not 32 bytes of hex");
  if (request.total_amount === 0n) throw new GiveawayError("total_amount is above zero");
  if (request.clawback_ts <= 0n) throw new GiveawayError("clawback_ts is a unix time");
  const seedKey =
    request.seed_secret_hex !== undefined
      ? ed25519(hex32(request.seed_secret_hex, "seed_secret_hex is not 32 bytes of hex"))
      : ed25519((options.randomBytes ?? randomBytes)(32));
  const seed = seedKey.publicKey;
  const [distribution, bump] = distributionAddress(program, mint, authority, seed);
  const vault = associatedTokenAddress(distribution, mint, tokenProgram);
  const authorityTokens = associatedTokenAddress(authority, mint, tokenProgram);
  const events = eventAuthority(program);
  const data = createData(bump, root, request.total_amount, request.clawback_ts);
  const ix: Instruction = {
    program,
    accounts: [
      meta(authority, true, true), // payer
      meta(authority, true, false), // authority
      meta(seed, true, false),
      meta(distribution, false, true),
      meta(mint, false, false),
      meta(vault, false, true),
      meta(authorityTokens, false, true),
      meta(SYSTEM_PROGRAM, false, false),
      meta(tokenProgram, false, false),
      meta(key("the ATA program", ATA_PROGRAM), false, false),
      meta(events, false, false),
      meta(program, false, false),
    ],
    data,
  };
  const message = compile(authority, [ix], blockhash);
  return sorted({
    transaction_b64: b64(wire(message, [seedKey])),
    distribution: b58(distribution),
    distribution_bump: bump,
    vault: b58(vault),
    seed_pubkey: b58(seed),
    authority_token_account: b58(authorityTokens),
    event_authority: b58(events),
    data_hex: bytesToHex(data),
  });
}

export function close(request: CloseRequest): Record<string, unknown> {
  const program = key("program_id", request.program_id);
  const mint = key("mint", request.mint);
  const tokenProgram = key("token_program", request.token_program);
  const authority = key("authority", request.authority);
  const seed = key("distribution_seed", request.distribution_seed);
  const blockhash = key("recent_blockhash", request.recent_blockhash);
  const [distribution] = distributionAddress(program, mint, authority, seed);
  if (b58(distribution) !== request.distributor) {
    throw new GiveawayError("distributor is not the address distribution_seed derives for this mint and authority");
  }
  const vault = associatedTokenAddress(distribution, mint, tokenProgram);
  const authorityTokens = associatedTokenAddress(authority, mint, tokenProgram);
  const events = eventAuthority(program);
  const data = Uint8Array.from([IX_CLOSE_MERKLE_DISTRIBUTION]);
  const ix: Instruction = {
    program,
    accounts: [
      meta(authority, true, true),
      meta(distribution, false, true),
      meta(mint, false, false),
      meta(vault, false, true),
      meta(authorityTokens, false, true),
      meta(tokenProgram, false, false),
      meta(events, false, false),
      meta(program, false, false),
    ],
    data,
  };
  const message = compile(authority, [ix], blockhash);
  return sorted({
    transaction_b64: b64(wire(message, [])),
    distribution: b58(distribution),
    vault: b58(vault),
    authority_token_account: b58(authorityTokens),
    event_authority: b58(events),
    data_hex: bytesToHex(data),
  });
}

/** Keys in byte order, as serde_json prints a map. */
function sorted(value: Record<string, unknown>): Record<string, unknown> {
  return Object.fromEntries(Object.entries(value).sort(([a], [b]) => (a < b ? -1 : a > b ? 1 : 0)));
}

// --- the request, as serde would read it --------------------------------------

const CREATE_FIELDS = ["program_id", "mint", "token_program", "authority", "merkle_root", "total_amount", "clawback_ts", "recent_blockhash", "seed_secret_hex"];
const CLOSE_FIELDS = ["program_id", "mint", "token_program", "authority", "distribution_seed", "distributor", "recent_blockhash"];
const U64_MAX = 0xffffffffffffffffn;
const I64_MAX = 0x7fffffffffffffffn;

type JsonReviver = (this: unknown, key: string, value: unknown, context: { source?: string }) => unknown;

/** JSON.parse that keeps every whole number exact, as a bigint (a u64 does not fit a double). */
function parseExact(text: string): unknown {
  const reviver: JsonReviver = (_key, value, context) => {
    if (typeof value === "number" && context.source !== undefined && /^-?\d+$/.test(context.source)) return BigInt(context.source);
    return value;
  };
  return (JSON.parse as (text: string, reviver: JsonReviver) => unknown)(text, reviver);
}

function readObject(op: string, input: string, fields: string[], required: string[]): Record<string, unknown> {
  let parsed: unknown;
  try {
    parsed = parseExact(input);
  } catch (error) {
    throw new GiveawayError(`bad ${op} request: ${(error as Error).message}`);
  }
  if (typeof parsed !== "object" || parsed === null || Array.isArray(parsed)) {
    throw new GiveawayError(`bad ${op} request: invalid type, expected struct`);
  }
  const object = parsed as Record<string, unknown>;
  for (const k of Object.keys(object)) {
    if (!fields.includes(k)) {
      throw new GiveawayError(`bad ${op} request: unknown field \`${k}\`, expected one of ${fields.map((f) => `\`${f}\``).join(", ")}`);
    }
  }
  for (const k of required) {
    if (!(k in object)) throw new GiveawayError(`bad ${op} request: missing field \`${k}\``);
  }
  return object;
}

function stringField(op: string, object: Record<string, unknown>, k: string): string {
  const value = object[k];
  if (typeof value !== "string") throw new GiveawayError(`bad ${op} request: invalid type for \`${k}\`, expected a string`);
  return value;
}

function intField(op: string, object: Record<string, unknown>, k: string, min: bigint, max: bigint, type: string): bigint {
  const value = object[k];
  if (typeof value !== "bigint") throw new GiveawayError(`bad ${op} request: invalid type for \`${k}\`, expected ${type}`);
  if (value < min || value > max) throw new GiveawayError(`bad ${op} request: invalid value for \`${k}\`, expected ${type}`);
  return value;
}

function parseCreate(input: string): CreateRequest {
  const o = readObject("create", input, CREATE_FIELDS, CREATE_FIELDS.slice(0, 8));
  const seed = o.seed_secret_hex;
  if (seed !== undefined && seed !== null && typeof seed !== "string") {
    throw new GiveawayError("bad create request: invalid type for `seed_secret_hex`, expected a string");
  }
  return {
    program_id: stringField("create", o, "program_id"),
    mint: stringField("create", o, "mint"),
    token_program: stringField("create", o, "token_program"),
    authority: stringField("create", o, "authority"),
    merkle_root: stringField("create", o, "merkle_root"),
    total_amount: intField("create", o, "total_amount", 0n, U64_MAX, "u64"),
    clawback_ts: intField("create", o, "clawback_ts", -I64_MAX - 1n, I64_MAX, "i64"),
    recent_blockhash: stringField("create", o, "recent_blockhash"),
    ...(typeof seed === "string" ? { seed_secret_hex: seed } : {}),
  };
}

function parseClose(input: string): CloseRequest {
  const o = readObject("close", input, CLOSE_FIELDS, CLOSE_FIELDS);
  return {
    program_id: stringField("close", o, "program_id"),
    mint: stringField("close", o, "mint"),
    token_program: stringField("close", o, "token_program"),
    authority: stringField("close", o, "authority"),
    distribution_seed: stringField("close", o, "distribution_seed"),
    distributor: stringField("close", o, "distributor"),
    recent_blockhash: stringField("close", o, "recent_blockhash"),
  };
}

export function build(op: string, input: string, options: Options = {}): Record<string, unknown> {
  if (op === "create") return create(parseCreate(input), options);
  if (op === "close") return close(parseClose(input));
  throw new GiveawayError(`usage: ${PROGRAM_NAME} ${COMMAND} create|close < request.json`);
}

export interface GiveawayResult {
  /** 0, or 2 on any failure. */
  code: number;
  stdout: string;
  stderr: string;
}

/**
 * The subcommand: ARGV is what follows `giveaway-tx` (`create` or `close`),
 * STDIN the request. The answer is one JSON line on stdout with exit 0, or
 * `{"error": ...}` on stderr with exit 2.
 */
export function giveawayTxMain(argv: readonly string[], stdin: string, options: Options = {}): GiveawayResult {
  const op = argv[0] ?? "";
  try {
    return { code: 0, stdout: `${JSON.stringify(build(op, stdin, options))}\n`, stderr: "" };
  } catch (error) {
    const message = error instanceof GiveawayError ? error.message : "internal error";
    return { code: 2, stdout: "", stderr: `${JSON.stringify({ error: message })}\n` };
  }
}
