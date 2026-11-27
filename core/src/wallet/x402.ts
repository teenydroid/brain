// x402_fetch and x402_list_payments: one HTTP request that may pay an HTTP 402
// challenge within a cap the caller states, from the wallet's own USDC.
//
// What is implemented (a port of tinywallet-x402): the x402 v2 `exact` scheme
// on Base-style EVM networks (an EIP-3009 transferWithAuthorization signed as
// EIP-712 typed data) and on Solana (a partially signed SPL TransferChecked the
// facilitator completes and pays the fee for); the EVM `upto` scheme (a Permit2
// PermitWitnessTransferFrom for the x402UptoPermit2Proxy, with the optional
// EIP-2612 gas-sponsoring permit), where the signed amount is a ceiling and the
// receipt says what was charged; and `signIn` (Sign-In-With-X, EIP-191), which
// pays nothing. USDC only. Preference: Solana exact, EVM exact, EVM upto.
//
// The request goes through a URL guard (https only, no local or private
// destination, no redirects) and then connects only to the addresses the guard
// vetted (pinned-fetch.ts), so DNS cannot change its mind in between, the challenge must name the origin that was
// requested, the amount must fit `maxAmountAtomic` and the per-request,
// daily and monthly budgets (OPENHUMAN_X402_*_MAX, written by
// bin/lib/wallet_config.py), and every payment is written to the ledger
// BEFORE it is signed. Logging names the host, status and amount only.
import { createHash, randomBytes } from "node:crypto";
import { isIP } from "node:net";
import { ComputeBudgetProgram, PublicKey, Transaction } from "@solana/web3.js";
import { ASSOCIATED_TOKEN_PROGRAM_ID, TOKEN_PROGRAM_ID, createTransferCheckedInstruction, getAssociatedTokenAddressSync } from "@solana/spl-token";
import { encodeFunctionData, getAddress, isAddress, parseAbi } from "viem";
import { privateKeyToAccount } from "viem/accounts";
import type { CoreContext, RpcMethods } from "../server/types.js";
import { evmPrivateKey, solanaKeypair } from "./derive.js";
import { EVM_NETWORKS, type EvmNetworkInfo, solanaCluster, solanaUsdcMint } from "./endpoints.js";
import { X402_REFUSED_PREFIX, fail } from "./errors.js";
import type { Runtime } from "./runtime.js";
import { isPrimary, loadState, withSeed } from "./store.js";
import { DAY_MS, readJsonFile, withFileLock, withMutex, writeFileAtomic } from "./util.js";

const MAX_BODY_BYTES = 256 * 1024;
const REQUEST_TIMEOUT_MS = 30_000;
const MONTH_MS = 30 * DAY_MS;
const PAYMENT_HEADERS = ["payment-signature", "x-payment", "sign-in-with-x"];
const SOLANA_MAINNET_CAIP = "solana:5eykt4UsFv8P8NJdTREpY1vzqKqZKvdp";
const SOLANA_DEVNET_CAIP = "solana:EtWTRABZaYq6iMfeYKouRu166VU2xqa1";

type Params = Record<string, unknown>;

interface PaymentRecord {
  id: string;
  status: "pending" | "settled" | "failed";
  asset: string;
  amountAtomic: string;
  timestamp: string;
  network: string;
  recipient: string;
  host: string;
  /** When the budget was first charged; the windows count from here, however often the row is updated. */
  reservedAtMs?: number;
  /** When the signed authorization stops being usable. Until then it may be settled whatever the server says. */
  expiresAtMs?: number;
  /** True once a signature exists. A payment that failed before one was made owes the budget nothing. */
  signed?: boolean;
  /** What the server's receipt claimed was charged (upto). Unverified, so it never lowers what is counted. */
  reportedChargedAtomic?: string;
}

// --- request validation (port of parse_params) -------------------------------------

interface FetchParams {
  url: string;
  method: "GET" | "POST";
  headers: Array<[string, string]>;
  body?: string;
  maxAmountAtomic: number;
  network?: string;
  signIn: boolean;
}

function validNetwork(network: string): boolean {
  return (
    network.length > 0 &&
    network.length <= 64 &&
    network.split(":").length - 1 <= 1 &&
    !network.startsWith(":") &&
    !network.endsWith(":") &&
    /^[A-Za-z0-9:_-]+$/.test(network)
  );
}

export function parseParams(params: Params): FetchParams {
  const known = ["url", "method", "headers", "body", "maxAmountAtomic", "network", "signIn"];
  for (const k of Object.keys(params)) if (!known.includes(k)) throw fail(`x402_fetch params: unknown field \`${k}\``);
  if (typeof params.url !== "string") throw fail("x402_fetch params: missing field `url`");
  let parsed: URL | undefined;
  try {
    parsed = new URL(params.url.trim());
  } catch {
    parsed = undefined;
  }
  if (!parsed || parsed.protocol !== "https:" || !parsed.hostname) throw fail("x402_fetch params: url must be an absolute https:// URL");
  const rawMethod = params.method === undefined || params.method === null ? undefined : String(params.method).toUpperCase();
  if (rawMethod !== undefined && rawMethod !== "GET" && rawMethod !== "POST") throw fail("x402_fetch params: method must be GET or POST");
  const method = rawMethod ?? "GET";
  if (params.body !== undefined && params.body !== null && typeof params.body !== "string") throw fail("x402_fetch params: body must be a string");
  const body = typeof params.body === "string" ? params.body : undefined;
  if (body !== undefined && method !== "POST") throw fail("x402_fetch params: a body is only allowed with POST");
  const headers: Array<[string, string]> = [];
  if (params.headers !== undefined && params.headers !== null) {
    if (typeof params.headers !== "object" || Array.isArray(params.headers)) throw fail("x402_fetch params: headers must be an object");
    for (const [name, value] of Object.entries(params.headers as Params)) {
      if (PAYMENT_HEADERS.includes(name.toLowerCase())) throw fail(`x402_fetch params: the ${name} header is set by the wallet, not the caller`);
      if (typeof value !== "string") throw fail(`x402_fetch params: header ${name} must have a string value`);
      if (!/^[!#$%&'*+.^_`|~0-9A-Za-z-]+$/.test(name) || /[\r\n\0]/.test(value)) throw fail(`x402_fetch params: header ${name} is not valid`);
      headers.push([name, value]);
    }
  }
  const max = params.maxAmountAtomic;
  if (typeof max !== "number" || !Number.isSafeInteger(max) || max < 0) throw fail("x402_fetch params: maxAmountAtomic must be a whole number");
  if (max === 0) throw fail("x402_fetch params: maxAmountAtomic must be greater than zero");
  let network: string | undefined;
  if (params.network !== undefined && params.network !== null) {
    const trimmed = String(params.network).trim();
    if (trimmed !== "") {
      if (!validNetwork(trimmed)) {
        throw fail("x402_fetch params: network must be a CAIP-2 id (eip155:8453) or a namespace (eip155, solana)");
      }
      network = trimmed;
    }
  }
  return {
    url: params.url.trim(),
    method,
    headers,
    ...(body !== undefined ? { body } : {}),
    maxAmountAtomic: max,
    ...(network !== undefined ? { network } : {}),
    signIn: params.signIn === true,
  };
}

// --- destination guard --------------------------------------------------------------

function privateAddress(address: string): boolean {
  const v = isIP(address);
  if (v === 4) {
    const [a, b] = address.split(".").map(Number) as [number, number];
    return (
      a === 0 || a === 10 || a === 127 || (a === 169 && b === 254) || (a === 172 && b >= 16 && b <= 31) || (a === 192 && b === 168) ||
      (a === 100 && b >= 64 && b <= 127) || a >= 224 || (a === 198 && (b === 18 || b === 19)) || (a === 192 && b === 0)
    );
  }
  if (v === 6) {
    const lower = address.toLowerCase();
    if (lower === "::" || lower === "::1") return true;
    if (lower.startsWith("::ffff:")) return privateAddress(lower.slice(7));
    return /^f[cdef]/.test(lower) || lower.startsWith("fe8") || lower.startsWith("fe9") || lower.startsWith("fea") || lower.startsWith("feb");
  }
  return true;
}

/** https only; local and private destinations are refused, including names that resolve to them. */
export async function guardDestination(rt: Runtime, url: string): Promise<{ target: URL; addresses: string[] }> {
  const target = new URL(url);
  const host = target.hostname.replace(/^\[|\]$/g, "");
  const lower = host.toLowerCase();
  if (lower === "localhost" || lower.endsWith(".localhost") || lower.endsWith(".local") || lower.endsWith(".internal")) {
    throw fail("x402_fetch: destination refused: local or private hosts are not allowed");
  }
  const addresses = isIP(host) ? [host] : await rt.deps.resolve(host).catch(() => []);
  if (addresses.length === 0 || addresses.some(privateAddress)) {
    throw fail("x402_fetch: destination refused: local or private hosts are not allowed");
  }
  return { target, addresses };
}

// --- the challenge ------------------------------------------------------------------

interface Accept {
  scheme: string;
  network: string;
  amount?: string;
  maxAmountRequired?: string;
  asset: string;
  payTo: string;
  maxTimeoutSeconds?: number;
  extra?: Record<string, unknown>;
}

interface Challenge {
  x402Version: number;
  resource?: { url?: string } | string;
  accepts: Accept[];
  extensions?: Record<string, unknown>;
}

function decodeChallenge(header: string, needAccepts: boolean): Challenge {
  let parsed: unknown;
  try {
    parsed = JSON.parse(Buffer.from(header.trim(), "base64").toString("utf8"));
  } catch {
    throw fail("x402_fetch: payment failed: the challenge is not base64 JSON");
  }
  const c = parsed as Challenge;
  if (typeof c !== "object" || c === null) throw fail("x402_fetch: payment failed: the challenge is not an object");
  if (!Array.isArray(c.accepts)) {
    if (needAccepts) throw fail("x402_fetch: payment failed: the challenge lists no payment options");
    c.accepts = [];
  }
  return c;
}

const CROSS_ORIGIN_HEAD = "openhuman.x402_fetch: x402_fetch: payment failed: ai.tinyhumans.tinywallet.Error.X402OperationFailed: x402 protocol: challenge resource ";
const CROSS_ORIGIN_MID = " is not on the origin of the requested URL ";

function ensureSameOrigin(challenge: Challenge, requested: string): void {
  const resource = typeof challenge.resource === "string" ? challenge.resource : challenge.resource?.url;
  if (resource === undefined) return;
  let same = false;
  try {
    same = new URL(resource).origin === new URL(requested).origin;
  } catch {
    same = false;
  }
  if (!same) throw fail(`${CROSS_ORIGIN_HEAD}${resource}${CROSS_ORIGIN_MID}${requested}`);
}

function matchesPin(network: string, pin: string | undefined): boolean {
  if (pin === undefined) return true;
  return pin.includes(":") ? network === pin : network.split(":")[0] === pin;
}

interface Rail {
  accept: Accept;
  chain: "evm" | "solana";
  scheme: "exact" | "upto";
  evm?: EvmNetworkInfo;
  /** The exact price, or the ceiling of an `upto` payment. */
  amount: bigint;
  rank: number;
}

const isAddr = (a: unknown): a is string => typeof a === "string" && isAddress(a);

/** An `upto` requirement carries what a Permit2 authorization needs: a facilitator to bind, and no other transfer method. */
function payableUpto(accept: Accept): boolean {
  const extra = accept.extra ?? {};
  const method = extra.assetTransferMethod;
  return isAddr(extra.facilitatorAddress) && (method === undefined || method === "permit2");
}

/** The best-ranked payable option: Solana exact, then EVM exact, then EVM upto; the first on a tie. */
function chooseRail(challenge: Challenge, pin: string | undefined, cluster: ReturnType<typeof solanaCluster>): Rail {
  let usdcMismatch: string | undefined;
  let best: Rail | undefined;
  for (const accept of challenge.accepts) {
    if (!matchesPin(accept.network, pin)) continue;
    if (accept.scheme !== "exact" && accept.scheme !== "upto") continue;
    const amountText = accept.amount ?? accept.maxAmountRequired;
    if (typeof amountText !== "string" || !/^\d+$/.test(amountText)) continue;
    const amount = BigInt(amountText);
    let candidate: Rail | undefined;
    if (accept.network.startsWith("eip155:")) {
      const network = EVM_NETWORKS.find((n) => `eip155:${n.chainId}` === accept.network);
      if (!network || cluster !== "mainnet") continue;
      if (accept.scheme === "upto" && !payableUpto(accept)) continue;
      if (accept.asset.toLowerCase() !== network.usdc.toLowerCase()) {
        usdcMismatch ??= `${accept.asset} is not the USDC accepted on ${accept.network}`;
        continue;
      }
      candidate = { accept, chain: "evm", scheme: accept.scheme, evm: network, amount, rank: accept.scheme === "exact" ? 1 : 2 };
    } else {
      const wanted = cluster === "mainnet" ? SOLANA_MAINNET_CAIP : cluster === "devnet" ? SOLANA_DEVNET_CAIP : undefined;
      if (accept.scheme !== "exact" || !wanted || accept.network !== wanted) continue;
      if (accept.asset !== solanaUsdcMint(cluster)) {
        usdcMismatch ??= `${accept.asset} is not the USDC accepted on ${accept.network}`;
        continue;
      }
      candidate = { accept, chain: "solana", scheme: "exact", amount, rank: 0 };
    }
    if (!best || candidate.rank < best.rank) best = candidate;
  }
  if (best) return best;
  if (usdcMismatch) throw fail(`${X402_REFUSED_PREFIX} x402 asset ${usdcMismatch}`);
  throw fail(
    `${X402_REFUSED_PREFIX} no supported payment option in the challenge (this wallet pays the x402 exact scheme in USDC on Base-style EVM networks and Solana, and the upto scheme on those EVM networks)`,
  );
}

/** The amount a receipt says was charged for a payment signed for SIGNED, or undefined when it does not settle it (tinywallet's `settled_amount`). */
export function settledAmount(receipt: unknown, network: string, scheme: string, signed: bigint): bigint | undefined {
  if (typeof receipt !== "object" || receipt === null) return undefined;
  const r = receipt as { success?: unknown; network?: unknown; amount?: unknown };
  if (r.success !== true || r.network !== network) return undefined;
  if (r.amount === undefined || r.amount === null) return signed;
  const reported = typeof r.amount === "string" && /^\d+$/.test(r.amount) ? BigInt(r.amount) : undefined;
  if (reported === undefined) return undefined;
  if (scheme === "upto") return reported <= signed ? reported : undefined;
  return reported === signed ? signed : undefined;
}

function decodeReceipt(header: string | null): unknown {
  if (header === null) return undefined;
  try {
    return JSON.parse(Buffer.from(header.trim(), "base64").toString("utf8"));
  } catch {
    return undefined;
  }
}

// --- budgets and the payment ledger --------------------------------------------------

function budget(env: CoreContext["env"], name: string): bigint | undefined {
  const raw = env[name]?.trim();
  return raw && /^\d+$/.test(raw) && BigInt(raw) > 0n ? BigInt(raw) : undefined;
}

class PaymentLog {
  private readonly file: string;
  constructor(file: string) {
    this.file = file;
  }
  all(): PaymentRecord[] {
    return readJsonFile<{ records: PaymentRecord[] }>(this.file)?.records ?? [];
  }
  /** The newest record per id, newest first. */
  latest(): PaymentRecord[] {
    const seen = new Set<string>();
    const out: PaymentRecord[] = [];
    for (const r of this.all().slice().reverse()) {
      if (seen.has(r.id)) continue;
      seen.add(r.id);
      out.push(r);
    }
    return out;
  }
  append(record: PaymentRecord): void {
    writeFileAtomic(this.file, JSON.stringify({ records: [...this.all(), record] }, null, 1));
  }
  reserve(decide: (current: PaymentRecord[]) => PaymentRecord): Promise<PaymentRecord> {
    return withMutex(this.file, () => withFileLock(this.file, () => {
      const record = decide(this.latest());
      this.append(record);
      return record;
    }));
  }
  update(record: PaymentRecord): Promise<void> {
    return withMutex(this.file, () => withFileLock(this.file, () => this.append(record)));
  }
}

/**
 * What the budget counts as spent: every payment at the full value it was signed for, until it provably cannot
 * settle. A signed authorization stays valid until it expires whatever the server answered, so a refusal from the
 * server (a 4xx), or a receipt that reports less than the ceiling, frees nothing: the server can still settle the
 * authorization itself. Only a payment that was never signed, or one that failed and whose authorization has
 * expired, stops counting.
 */
export function spentSince(records: PaymentRecord[], sinceMs: number, nowMs: number): bigint {
  return records
    .filter((r) => {
      if ((r.reservedAtMs ?? Date.parse(r.timestamp)) < sinceMs) return false;
      if (r.status !== "failed") return true;
      return r.signed === true && (r.expiresAtMs ?? Number.POSITIVE_INFINITY) > nowMs;
    })
    .reduce((sum, r) => sum + BigInt(r.amountAtomic), 0n);
}

// --- signing -------------------------------------------------------------------------

const PERMIT2 = "0x000000000022D473030F116dDEE9F6B43aC78BA3";
const UPTO_PERMIT2_PROXY = "0x4020A4f3b7b90ccA423B9fabCc0CE57C6C240002";
const EIP2612_GAS_SPONSORING = "eip2612GasSponsoring";
const MAX_AUTHORIZATION_SECS = 300;
const ERC20_READS = parseAbi(["function allowance(address owner, address spender) view returns (uint256)", "function nonces(address owner) view returns (uint256)"]);

const freshNonce = (): bigint => BigInt(`0x${randomBytes(32).toString("hex")}`);

function mergeInto(target: Record<string, unknown>, source: Record<string, unknown>): Record<string, unknown> {
  const out: Record<string, unknown> = { ...target };
  for (const [key, value] of Object.entries(source)) {
    const existing = out[key];
    out[key] =
      typeof existing === "object" && existing !== null && !Array.isArray(existing) && typeof value === "object" && value !== null && !Array.isArray(value)
        ? mergeInto(existing as Record<string, unknown>, value as Record<string, unknown>)
        : value;
  }
  return out;
}

async function readUint(rt: Runtime, ctx: CoreContext, network: EvmNetworkInfo, token: string, data: `0x${string}`): Promise<bigint> {
  const reply = await rt.evm(ctx, network).call<string>("eth_call", [{ to: token, data }, "latest"]);
  if (typeof reply !== "string" || !/^0x[0-9a-fA-F]*$/.test(reply)) throw new Error("eth_call returned a non-hex result");
  return BigInt(reply === "0x" ? "0x0" : reply);
}

type Signer = ReturnType<typeof privateKeyToAccount>;

/** The `upto` proof: a Permit2 PermitWitnessTransferFrom for the x402UptoPermit2Proxy, and the EIP-2612 permit when the server sponsors it. */
async function uptoProof(
  rt: Runtime,
  ctx: CoreContext,
  signer: Signer,
  challenge: Challenge,
  rail: Rail,
  nowSecs: number,
): Promise<{ payload: Record<string, unknown>; extensions: Record<string, unknown>; deadlineSecs: number }> {
  const accepted = rail.accept;
  const network = rail.evm!;
  const extra = accepted.extra ?? {};
  const token = getAddress(accepted.asset);
  const to = getAddress(accepted.payTo);
  const facilitator = getAddress(extra.facilitatorAddress as string);
  const deadline = BigInt(nowSecs + Math.min(Math.max(accepted.maxTimeoutSeconds ?? 60, 1), MAX_AUTHORIZATION_SECS));
  const nonce = freshNonce();
  const amountText = (accepted.amount ?? accepted.maxAmountRequired ?? "").trim();
  const signature = await signer.signTypedData({
    domain: { name: "Permit2", chainId: network.chainId, verifyingContract: PERMIT2 },
    types: {
      PermitWitnessTransferFrom: [
        { name: "permitted", type: "TokenPermissions" },
        { name: "spender", type: "address" },
        { name: "nonce", type: "uint256" },
        { name: "deadline", type: "uint256" },
        { name: "witness", type: "Witness" },
      ],
      TokenPermissions: [
        { name: "token", type: "address" },
        { name: "amount", type: "uint256" },
      ],
      Witness: [
        { name: "to", type: "address" },
        { name: "facilitator", type: "address" },
        { name: "validAfter", type: "uint256" },
      ],
    },
    primaryType: "PermitWitnessTransferFrom",
    message: {
      permitted: { token, amount: rail.amount },
      spender: UPTO_PERMIT2_PROXY,
      nonce,
      deadline,
      witness: { to, facilitator, validAfter: 0n },
    },
  });
  const payload = {
    signature,
    permit2Authorization: {
      from: signer.address,
      permitted: { token, amount: amountText },
      spender: UPTO_PERMIT2_PROXY,
      nonce: nonce.toString(),
      deadline: deadline.toString(),
      witness: { to, facilitator, validAfter: "0" },
    },
  };
  let extensions: Record<string, unknown> = { ...(challenge.extensions ?? {}) };
  if (challenge.extensions && EIP2612_GAS_SPONSORING in challenge.extensions && typeof extra.name === "string" && typeof extra.version === "string") {
    let covered = false;
    try {
      const allowance = await readUint(rt, ctx, network, token, encodeFunctionData({ abi: ERC20_READS, functionName: "allowance", args: [signer.address, PERMIT2] }));
      covered = allowance >= rail.amount;
    } catch {
      covered = false; // unreadable: treated as insufficient, as the reference client does
    }
    if (!covered) {
      let tokenNonce: bigint;
      try {
        tokenNonce = await readUint(rt, ctx, network, token, encodeFunctionData({ abi: ERC20_READS, functionName: "nonces", args: [signer.address] }));
      } catch {
        throw fail("x402_fetch: payment failed: could not read the EIP-2612 nonce");
      }
      const permitSignature = await signer.signTypedData({
        domain: { name: extra.name, version: extra.version, chainId: network.chainId, verifyingContract: token },
        types: {
          Permit: [
            { name: "owner", type: "address" },
            { name: "spender", type: "address" },
            { name: "value", type: "uint256" },
            { name: "nonce", type: "uint256" },
            { name: "deadline", type: "uint256" },
          ],
        },
        primaryType: "Permit",
        message: { owner: signer.address, spender: PERMIT2, value: rail.amount, nonce: tokenNonce, deadline },
      });
      extensions = mergeInto(extensions, {
        [EIP2612_GAS_SPONSORING]: {
          info: {
            from: signer.address,
            asset: token,
            spender: PERMIT2,
            amount: amountText,
            nonce: tokenNonce.toString(),
            deadline: deadline.toString(),
            signature: permitSignature,
            version: "1",
          },
        },
      });
    }
  }
  return { payload, extensions, deadlineSecs: Number(deadline) };
}

async function paymentHeader(rt: Runtime, ctx: CoreContext, challenge: Challenge, rail: Rail, requested: string, now: number): Promise<{ header: string; expiresAtMs: number }> {
  const accepted = rail.accept;
  const resource = typeof challenge.resource === "string" ? { url: challenge.resource } : (challenge.resource ?? { url: requested });
  let extensions: Record<string, unknown> = challenge.extensions && Object.keys(challenge.extensions).length > 0 ? challenge.extensions : {};
  let expiresAtMs = now + 120_000; // a Solana blockhash lives about 60-90s
  const payload = await withSeed(ctx, async (seed, state) => {
    if (rail.chain === "evm") {
      const network = rail.evm!;
      const account = state.accounts.find((a) => a.chain === "evm" && isPrimary(a));
      if (!account) throw fail("no wallet account derived for chain 'evm'");
      const signer = privateKeyToAccount(evmPrivateKey(seed, account.derivationPath));
      if (rail.scheme === "upto") {
        const built = await uptoProof(rt, ctx, signer, challenge, rail, Math.floor(now / 1000));
        extensions = built.extensions;
        expiresAtMs = built.deadlineSecs * 1000;
        return built.payload;
      }
      const timeout = Math.min(accepted.maxTimeoutSeconds ?? 60, MAX_AUTHORIZATION_SECS);
      const authorization = {
        from: signer.address,
        to: getAddress(accepted.payTo),
        value: rail.amount.toString(),
        validAfter: String(Math.floor(now / 1000) - 600),
        validBefore: String(Math.floor(now / 1000) + timeout),
        nonce: `0x${randomBytes(32).toString("hex")}` as `0x${string}`,
      };
      const extra = accepted.extra ?? {};
      const signature = await signer.signTypedData({
        domain: {
          name: typeof extra.name === "string" ? extra.name : "USD Coin",
          version: typeof extra.version === "string" ? extra.version : "2",
          chainId: network.chainId,
          verifyingContract: getAddress(accepted.asset),
        },
        types: {
          TransferWithAuthorization: [
            { name: "from", type: "address" },
            { name: "to", type: "address" },
            { name: "value", type: "uint256" },
            { name: "validAfter", type: "uint256" },
            { name: "validBefore", type: "uint256" },
            { name: "nonce", type: "bytes32" },
          ],
        },
        primaryType: "TransferWithAuthorization",
        message: {
          from: authorization.from,
          to: authorization.to,
          value: BigInt(authorization.value),
          validAfter: BigInt(authorization.validAfter),
          validBefore: BigInt(authorization.validBefore),
          nonce: authorization.nonce,
        },
      });
      expiresAtMs = Number(authorization.validBefore) * 1000;
      return { signature, authorization };
    }
    const account = state.accounts.find((a) => a.chain === "solana" && isPrimary(a));
    if (!account) throw fail("no wallet account derived for chain 'solana'");
    const keypair = solanaKeypair(seed, account.derivationPath);
    const feePayerText = accepted.extra?.feePayer;
    if (typeof feePayerText !== "string") throw fail("x402_fetch: payment failed: the challenge names no fee payer");
    const feePayer = new PublicKey(feePayerText);
    const mint = new PublicKey(accepted.asset);
    const recipient = new PublicKey(accepted.payTo);
    const source = getAssociatedTokenAddressSync(mint, keypair.publicKey, false, TOKEN_PROGRAM_ID, ASSOCIATED_TOKEN_PROGRAM_ID);
    const destination = getAssociatedTokenAddressSync(mint, recipient, true, TOKEN_PROGRAM_ID, ASSOCIATED_TOKEN_PROGRAM_ID);
    const blockhash = await rt
      .solana(ctx)
      .call<{ value: { blockhash: string } }>("getLatestBlockhash", [{ commitment: "confirmed" }]);
    const tx = new Transaction({ feePayer, recentBlockhash: blockhash.value.blockhash });
    tx.add(
      ComputeBudgetProgram.setComputeUnitLimit({ units: 20_000 }),
      ComputeBudgetProgram.setComputeUnitPrice({ microLamports: 1 }),
      createTransferCheckedInstruction(source, mint, destination, keypair.publicKey, rail.amount, 6, [], TOKEN_PROGRAM_ID),
    );
    tx.partialSign(keypair);
    return { transaction: tx.serialize({ requireAllSignatures: false }).toString("base64") };
  });
  const body = { x402Version: challenge.x402Version ?? 2, resource, accepted, payload, ...(Object.keys(extensions).length > 0 ? { extensions } : {}) };
  return { header: Buffer.from(JSON.stringify(body)).toString("base64"), expiresAtMs };
}

// --- Sign-In-With-X (port of tinywallet-x402 crypto/siwx.rs) -------------------------------

interface SiwxInfo {
  domain: string;
  uri: string;
  statement?: string;
  version: string;
  nonce: string;
  issuedAt: string;
  expirationTime?: string;
  notBefore?: string;
  requestId?: string;
  resources?: string[];
}

const SIWX_REFUSED = (reason: string): Error => fail(`x402_fetch: sign-in failed: sign-in refused: ${reason}`);
const hasControl = (s: string | undefined): boolean => s !== undefined && Array.from(s).some((ch) => ch.charCodeAt(0) < 0x20 || (ch.charCodeAt(0) >= 0x7f && ch.charCodeAt(0) <= 0x9f));

function siweMessage(info: SiwxInfo, chainId: number, address: string): string {
  const lines = [`URI: ${info.uri}`, `Version: ${info.version}`, `Chain ID: ${chainId}`, `Nonce: ${info.nonce}`, `Issued At: ${info.issuedAt}`];
  if (info.expirationTime !== undefined) lines.push(`Expiration Time: ${info.expirationTime}`);
  if (info.notBefore !== undefined) lines.push(`Not Before: ${info.notBefore}`);
  if (info.requestId !== undefined) lines.push(`Request ID: ${info.requestId}`);
  if (info.resources !== undefined) lines.push("Resources:", ...info.resources.map((r) => `- ${r}`));
  const header = `${info.domain} wants you to sign in with your Ethereum account:\n${address}`;
  const prefix = info.statement === undefined ? `${header}\n\n` : `${header}\n\n${info.statement}\n`;
  return `${prefix}\n${lines.join("\n")}`;
}

async function signIn(ctx: CoreContext, challenge: Challenge, requestUrl: string, nowMs: number): Promise<{ header: string; address: string }> {
  const extension = challenge.extensions?.["sign-in-with-x"] as { info?: SiwxInfo; supportedChains?: Array<{ chainId?: string; type?: string }> } | undefined;
  if (!extension) throw SIWX_REFUSED("the challenge does not ask for sign-in-with-x");
  const info = extension.info;
  if (!info || typeof info.domain !== "string" || typeof info.uri !== "string" || typeof info.version !== "string" || typeof info.nonce !== "string" || typeof info.issuedAt !== "string" || !Array.isArray(extension.supportedChains)) {
    throw SIWX_REFUSED("unreadable sign-in-with-x extension");
  }
  // Before anything is parsed: URL parsing drops CR, LF and tab, so a value could pass the origin check yet forge a line of the message.
  for (const [field, value] of [["domain", info.domain], ["uri", info.uri], ["statement", info.statement], ["version", info.version], ["nonce", info.nonce], ["issuedAt", info.issuedAt], ["expirationTime", info.expirationTime], ["notBefore", info.notBefore], ["requestId", info.requestId], ...(info.resources ?? []).map((r) => ["resources", r] as const), ...extension.supportedChains.map((c) => ["chainId", c.chainId] as const)] as Array<[string, string | undefined]>) {
    if (hasControl(value)) throw SIWX_REFUSED(`${field} contains a control character`);
  }
  const request = new URL(requestUrl);
  if (request.protocol !== "https:") throw SIWX_REFUSED("sign-in is only answered for an https request");
  if (info.domain !== request.host) throw SIWX_REFUSED(`domain ${info.domain} is not the requested host ${request.host}`);
  const sameOrigin = (uri: string): boolean => {
    try {
      const u = new URL(uri);
      return u.protocol === "https:" && u.origin === request.origin;
    } catch {
      return false;
    }
  };
  if (!sameOrigin(info.uri)) throw SIWX_REFUSED(`uri ${info.uri} is not https on the requested origin`);
  for (const resource of info.resources ?? []) if (!sameOrigin(resource)) throw SIWX_REFUSED(`resource ${resource} is not https on the requested origin`);
  if (info.version !== "1") throw SIWX_REFUSED(`unsupported version ${info.version}`);
  if (info.nonce.length < 8 || !/^[A-Za-z0-9]+$/.test(info.nonce)) throw SIWX_REFUSED("the nonce is not at least 8 alphanumeric characters");
  const instant = (field: string, value: string): number => {
    const t = Date.parse(value);
    if (Number.isNaN(t) || !/^\d{4}-\d{2}-\d{2}T/.test(value)) throw SIWX_REFUSED(`${field} is not an RFC 3339 time`);
    return t;
  };
  if (instant("issuedAt", info.issuedAt) > nowMs + 300_000) throw SIWX_REFUSED("issuedAt is in the future");
  if (info.expirationTime === undefined) throw SIWX_REFUSED("the challenge has no expirationTime");
  const expiresAt = instant("expirationTime", info.expirationTime);
  if (expiresAt <= nowMs) throw SIWX_REFUSED("the challenge has expired");
  if (info.notBefore !== undefined) instant("notBefore", info.notBefore);
  const ids = extension.supportedChains
    .filter((c) => c.type === "eip191" && typeof c.chainId === "string" && /^eip155:\d+$/.test(c.chainId))
    .map((c) => Number((c.chainId as string).slice(7)));
  const chainId = ids.find((id) => id === 8453) ?? ids[0];
  if (chainId === undefined) throw SIWX_REFUSED("no eip155 chain with eip191 signatures is offered");
  return withSeed(ctx, async (seed, state) => {
    const account = state.accounts.find((a) => a.chain === "evm" && isPrimary(a));
    if (!account) throw fail("no wallet account derived for chain 'evm'");
    const signer = privateKeyToAccount(evmPrivateKey(seed, account.derivationPath));
    const signature = await signer.signMessage({ message: siweMessage(info, chainId, signer.address) });
    const proof = {
      domain: info.domain,
      address: signer.address,
      ...(info.statement !== undefined ? { statement: info.statement } : {}),
      uri: info.uri,
      version: info.version,
      chainId: `eip155:${chainId}`,
      type: "eip191",
      nonce: info.nonce,
      issuedAt: info.issuedAt,
      expirationTime: info.expirationTime,
      ...(info.notBefore !== undefined ? { notBefore: info.notBefore } : {}),
      ...(info.requestId !== undefined ? { requestId: info.requestId } : {}),
      ...(info.resources !== undefined ? { resources: info.resources } : {}),
      signature,
    };
    return { header: Buffer.from(JSON.stringify(proof)).toString("base64"), address: signer.address };
  });
}

// --- the fetch -----------------------------------------------------------------------

async function send(rt: Runtime, target: URL, addresses: string[], p: FetchParams, credential?: [string, string]): Promise<Response> {
  const headers = new Headers();
  for (const [k, v] of p.headers) headers.set(k, v);
  if (credential) headers.set(credential[0], credential[1]);
  headers.set("user-agent", "teeny-core");
  try {
    return await rt.deps.pinnedFetch(
      target.toString(),
      { method: p.method, headers, ...(p.body !== undefined ? { body: p.body } : {}), redirect: "manual", signal: AbortSignal.timeout(REQUEST_TIMEOUT_MS) },
      addresses,
    );
  } catch {
    throw fail("x402_fetch: request failed");
  }
}

async function bodyOf(response: Response): Promise<{ body: string; truncated: boolean }> {
  const bytes = new Uint8Array(await response.arrayBuffer());
  const truncated = bytes.length > MAX_BODY_BYTES;
  const cut = truncated ? bytes.subarray(0, MAX_BODY_BYTES) : bytes;
  return { body: new TextDecoder("utf-8").decode(cut), truncated };
}

async function reply(response: Response, payment: Record<string, unknown> | null, extra: Record<string, unknown> = {}): Promise<Record<string, unknown>> {
  const { body, truncated } = await bodyOf(response);
  const contentType = response.headers.get("content-type");
  return { status: response.status, ...(contentType ? { contentType } : {}), body, truncated, payment, ...extra };
}

export function x402Methods(rt: Runtime): RpcMethods {
  const methods: RpcMethods = {};

  methods.x402_list_payments = async (params, ctx) => {
    const raw = params.limit;
    const limit = typeof raw === "number" && Number.isFinite(raw) ? Math.min(500, Math.max(1, Math.trunc(raw))) : 50;
    const log = new PaymentLog(rt.x402File(ctx));
    return {
      payments: log.latest().slice(0, limit).map((r) => ({
        id: r.id,
        status: r.status,
        asset: r.asset,
        amountAtomic: r.amountAtomic,
        timestamp: r.timestamp,
        network: r.network,
        recipient: r.recipient,
      })),
    };
  };

  methods.x402_fetch = async (params, ctx) => {
    const p = parseParams(params);
    const { target, addresses } = await guardDestination(rt, p.url);
    ctx.log("debug", "x402_fetch", { method: p.method, host: target.hostname, cap: p.maxAmountAtomic });
    const first = await send(rt, target, addresses, p);
    const challengeHeader = first.headers.get("payment-required") ?? first.headers.get("x-payment-required");
    if (first.status !== 402 || challengeHeader === null) return reply(first, null);
    if (!loadState(ctx)) throw fail("wallet is not configured; run wallet setup first");

    const challenge = decodeChallenge(challengeHeader, !p.signIn);
    if (p.signIn) {
      // Nothing is paid: a challenge that asks for payment rather than sign-in is refused, and the response comes back with payment: null.
      const proof = await signIn(ctx, challenge, target.toString(), rt.now());
      const retry = await send(rt, target, addresses, p, ["SIGN-IN-WITH-X", proof.header]);
      ctx.log("debug", "x402 sign-in retry", { host: target.hostname, status: retry.status });
      return reply(retry, null, { signedInAs: proof.address });
    }
    ensureSameOrigin(challenge, target.toString());
    const cluster = solanaCluster(ctx.env);
    const rail = chooseRail(challenge, p.network, cluster);
    const amount = rail.amount;
    if (amount > BigInt(p.maxAmountAtomic)) {
      throw fail(`${X402_REFUSED_PREFIX} x402 payment of ${amount} exceeds maxAmountAtomic ${p.maxAmountAtomic}`);
    }
    const perRequest = budget(ctx.env, "OPENHUMAN_X402_PER_REQUEST_MAX");
    const daily = budget(ctx.env, "OPENHUMAN_X402_DAILY_MAX");
    const monthly = budget(ctx.env, "OPENHUMAN_X402_MONTHLY_MAX");
    if (perRequest === undefined || daily === undefined || monthly === undefined) {
      throw fail(`${X402_REFUSED_PREFIX} the x402 budget is not set (OPENHUMAN_X402_PER_REQUEST_MAX, _DAILY_MAX, _MONTHLY_MAX)`);
    }
    if (amount > perRequest) throw fail(`${X402_REFUSED_PREFIX} x402 payment of ${amount} exceeds per-request cap of ${perRequest}`);
    const now = rt.now();
    const log = new PaymentLog(rt.x402File(ctx));
    const paymentId = `x402-${createHash("sha256").update(randomBytes(16)).digest("hex").slice(0, 24)}`;
    // The reservation, at the full ceiling for an upto payment, is written before anything is signed.
    const pending = await log.reserve((records) => {
      if (spentSince(records, now - DAY_MS, now) + amount > daily) throw fail(`${X402_REFUSED_PREFIX} x402 daily budget exceeded`);
      if (spentSince(records, now - MONTH_MS, now) + amount > monthly) throw fail(`${X402_REFUSED_PREFIX} x402 monthly budget exceeded`);
      return {
        id: paymentId,
        status: "pending",
        asset: rail.accept.asset,
        amountAtomic: amount.toString(),
        timestamp: new Date(now).toISOString(),
        reservedAtMs: now,
        network: rail.accept.network,
        recipient: rail.accept.payTo,
        host: target.hostname,
      };
    });
    let signed: { header: string; expiresAtMs: number };
    try {
      signed = await paymentHeader(rt, ctx, challenge, rail, target.toString(), now);
    } catch (error) {
      // Nothing was signed that could be used: the reservation is released.
      await log.update({ ...pending, status: "failed", signed: false, timestamp: new Date(rt.now()).toISOString() });
      throw error;
    }
    // From here a signature exists. Until it expires it can be settled by whoever holds it, so the row keeps counting
    // at the full signed value, whatever the server answers. No response (send throws): same, and still pending.
    const held = { ...pending, signed: true, expiresAtMs: signed.expiresAtMs };
    await log.update({ ...held, timestamp: new Date(rt.now()).toISOString() });
    const retry = await send(rt, target, addresses, p, ["PAYMENT-SIGNATURE", signed.header]);
    const settlement = retry.headers.get("payment-response");
    const charged = retry.status >= 200 && retry.status < 300 ? settledAmount(decodeReceipt(settlement), rail.accept.network, rail.scheme, amount) : undefined;
    const settled = charged !== undefined;
    // `failed` here only says the server refused; the budget still counts it until the authorization expires (spentSince).
    // An upto receipt's amount is the server's word, so it is kept beside the row and never replaces the ceiling.
    await log.update({
      ...held,
      status: settled ? "settled" : retry.status >= 400 && retry.status < 500 ? "failed" : "pending",
      ...(settled && rail.scheme === "upto" ? { reportedChargedAtomic: charged.toString() } : {}),
      timestamp: new Date(rt.now()).toISOString(),
    });
    ctx.log("debug", "x402 paid retry", { host: target.hostname, status: retry.status, settled });
    return reply(retry, {
      paymentId,
      amountAtomic: amount.toString(),
      scheme: rail.scheme,
      ...(settled ? { chargedAtomic: charged.toString() } : {}),
      asset: rail.accept.asset,
      recipient: rail.accept.payTo,
      network: rail.accept.network,
      settled,
      ...(settlement ? { settlementResponse: settlement } : {}),
    });
  };

  return methods;
}
