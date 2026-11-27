// x402_fetch and x402_list_payments: one HTTP request that may pay an HTTP 402
// challenge within a cap the caller states, from the wallet's own USDC.
//
// What is implemented: the x402 v2 `exact` scheme on Base-style EVM networks
// (an EIP-3009 transferWithAuthorization signed as EIP-712 typed data) and on
// Solana (a partially signed SPL TransferChecked the facilitator completes and
// pays the fee for), USDC only.
//
// What is NOT implemented, and refused rather than guessed at: the `upto`
// scheme (twitr.sh's search reads use it), and `signIn` (Sign-In-With-X).
//
// The request goes through a URL guard (https only, no local or private
// destination, no redirects), the challenge must name the origin that was
// requested, the amount must fit `maxAmountAtomic` and the per-request,
// daily and monthly budgets (OPENHUMAN_X402_*_MAX, written by
// bin/lib/wallet_config.py), and every payment is written to the ledger
// BEFORE it is signed. Logging names the host, status and amount only.
import { createHash, randomBytes } from "node:crypto";
import { isIP } from "node:net";
import { ComputeBudgetProgram, PublicKey, Transaction } from "@solana/web3.js";
import { ASSOCIATED_TOKEN_PROGRAM_ID, TOKEN_PROGRAM_ID, createTransferCheckedInstruction, getAssociatedTokenAddressSync } from "@solana/spl-token";
import { getAddress } from "viem";
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
export async function guardDestination(rt: Runtime, url: string): Promise<URL> {
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
  return target;
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
}

function decodeChallenge(header: string): Challenge {
  let parsed: unknown;
  try {
    parsed = JSON.parse(Buffer.from(header.trim(), "base64").toString("utf8"));
  } catch {
    throw fail("x402_fetch: payment failed: the challenge is not base64 JSON");
  }
  const c = parsed as Challenge;
  if (typeof c !== "object" || c === null || !Array.isArray(c.accepts)) throw fail("x402_fetch: payment failed: the challenge lists no payment options");
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
  evm?: EvmNetworkInfo;
  amount: bigint;
}

function chooseRail(challenge: Challenge, pin: string | undefined, cluster: ReturnType<typeof solanaCluster>): Rail {
  let usdcMismatch: string | undefined;
  for (const accept of challenge.accepts) {
    if (!matchesPin(accept.network, pin)) continue;
    if (accept.scheme !== "exact") continue;
    const amountText = accept.amount ?? accept.maxAmountRequired;
    if (typeof amountText !== "string" || !/^\d+$/.test(amountText)) continue;
    const amount = BigInt(amountText);
    if (accept.network.startsWith("eip155:")) {
      const network = EVM_NETWORKS.find((n) => `eip155:${n.chainId}` === accept.network);
      if (!network || cluster !== "mainnet") continue;
      if (accept.asset.toLowerCase() !== network.usdc.toLowerCase()) {
        usdcMismatch = `${accept.asset} is not the USDC accepted on ${accept.network}`;
        continue;
      }
      return { accept, chain: "evm", evm: network, amount };
    }
    const wanted = cluster === "mainnet" ? SOLANA_MAINNET_CAIP : cluster === "devnet" ? SOLANA_DEVNET_CAIP : undefined;
    if (wanted && accept.network === wanted) {
      const mint = solanaUsdcMint(cluster);
      if (accept.asset !== mint) {
        usdcMismatch = `${accept.asset} is not the USDC accepted on ${accept.network}`;
        continue;
      }
      return { accept, chain: "solana", amount };
    }
  }
  if (usdcMismatch) throw fail(`${X402_REFUSED_PREFIX} x402 asset ${usdcMismatch}`);
  throw fail(`${X402_REFUSED_PREFIX} no supported payment option in the challenge (this wallet pays the x402 exact scheme in USDC on Base-style EVM networks and Solana)`);
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

function spentSince(records: PaymentRecord[], sinceMs: number): bigint {
  return records
    .filter((r) => r.status !== "failed" && Date.parse(r.timestamp) >= sinceMs)
    .reduce((sum, r) => sum + BigInt(r.amountAtomic), 0n);
}

// --- signing -------------------------------------------------------------------------

async function paymentHeader(rt: Runtime, ctx: CoreContext, challenge: Challenge, rail: Rail, requested: string, now: number): Promise<string> {
  const accepted = rail.accept;
  const resource = typeof challenge.resource === "string" ? { url: challenge.resource } : (challenge.resource ?? { url: requested });
  const payload = await withSeed(ctx, async (seed, state) => {
    if (rail.chain === "evm") {
      const network = rail.evm!;
      const account = state.accounts.find((a) => a.chain === "evm" && isPrimary(a));
      if (!account) throw fail("no wallet account derived for chain 'evm'");
      const signer = privateKeyToAccount(evmPrivateKey(seed, account.derivationPath));
      const timeout = accepted.maxTimeoutSeconds ?? 60;
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
  const body = { x402Version: challenge.x402Version ?? 2, resource, accepted, payload };
  return Buffer.from(JSON.stringify(body)).toString("base64");
}

// --- the fetch -----------------------------------------------------------------------

async function send(rt: Runtime, target: URL, p: FetchParams, credential?: [string, string]): Promise<Response> {
  const headers = new Headers();
  for (const [k, v] of p.headers) headers.set(k, v);
  if (credential) headers.set(credential[0], credential[1]);
  headers.set("user-agent", "teeny-core");
  try {
    return await rt.deps.fetch(target.toString(), {
      method: p.method,
      headers,
      ...(p.body !== undefined ? { body: p.body } : {}),
      redirect: "manual",
      signal: AbortSignal.timeout(REQUEST_TIMEOUT_MS),
    });
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

async function reply(response: Response, payment: Record<string, unknown> | null): Promise<Record<string, unknown>> {
  const { body, truncated } = await bodyOf(response);
  const contentType = response.headers.get("content-type");
  return { status: response.status, ...(contentType ? { contentType } : {}), body, truncated, payment };
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
    const target = await guardDestination(rt, p.url);
    ctx.log("debug", "x402_fetch", { method: p.method, host: target.hostname, cap: p.maxAmountAtomic });
    const first = await send(rt, target, p);
    const challengeHeader = first.headers.get("payment-required") ?? first.headers.get("x-payment-required");
    if (first.status !== 402 || challengeHeader === null) return reply(first, null);
    if (p.signIn) throw fail("x402_fetch: sign-in failed: Sign-In-With-X is not implemented by this core");
    if (!loadState(ctx)) throw fail("wallet is not configured; run wallet setup first");

    const challenge = decodeChallenge(challengeHeader);
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
    // The reservation is written before anything is signed.
    const pending = await log.reserve((records) => {
      if (spentSince(records, now - DAY_MS) + amount > daily) throw fail(`${X402_REFUSED_PREFIX} x402 daily budget exceeded`);
      if (spentSince(records, now - MONTH_MS) + amount > monthly) throw fail(`${X402_REFUSED_PREFIX} x402 monthly budget exceeded`);
      return {
        id: paymentId,
        status: "pending",
        asset: rail.accept.asset,
        amountAtomic: amount.toString(),
        timestamp: new Date(now).toISOString(),
        network: rail.accept.network,
        recipient: rail.accept.payTo,
        host: target.hostname,
      };
    });
    let header: string;
    try {
      header = await paymentHeader(rt, ctx, challenge, rail, target.toString(), now);
    } catch (error) {
      await log.update({ ...pending, status: "failed", timestamp: new Date(rt.now()).toISOString() });
      throw error;
    }
    // No response (send throws): the proof may or may not have settled, and the pending reservation stays counted.
    const retry = await send(rt, target, p, ["PAYMENT-SIGNATURE", header]);
    const settlement = retry.headers.get("payment-response");
    const settled = retry.status >= 200 && retry.status < 300 && settlement !== null;
    await log.update({ ...pending, status: settled ? "settled" : retry.status >= 400 && retry.status < 500 ? "failed" : "pending", timestamp: new Date(rt.now()).toISOString() });
    ctx.log("debug", "x402 paid retry", { host: target.hostname, status: retry.status, settled });
    return reply(retry, {
      paymentId,
      amountAtomic: amount.toString(),
      asset: rail.accept.asset,
      recipient: rail.accept.payTo,
      network: rail.accept.network,
      settled,
      ...(settlement ? { settlementResponse: settlement } : {}),
    });
  };

  return methods;
}

