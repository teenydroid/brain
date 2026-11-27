import { base58 } from "@scure/base";
import { Transaction } from "@solana/web3.js";
import { verifyTypedData } from "viem";
import { describe, expect, it } from "vitest";
import { decodeTransaction, signatureIsValid } from "../../src/wallet/solana-program.js";
import { type Harness, json, makeHarness, mockSolana, setupWallet } from "./helpers.js";
import { pinnedFetch, pinnedLookup } from "../../src/wallet/pinned-fetch.js";
import { createServer } from "node:http";
import { verifyMessage } from "viem";
import { Keypair } from "@solana/web3.js";

const MAINNET = { OPENHUMAN_SOLANA_CLUSTER: "mainnet" };
const URL_ = "https://api.example.test/thing";
const BASE_USDC = "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913";
const PAY_TO = "0x2222222222222222222222222222222222222222";
const DEVNET_USDC = "4zMMC9srt5Ri5X14GAgXhaHii3GnPAEERYPJgZJDncDU";
const DEVNET_CAIP = "solana:EtWTRABZaYq6iMfeYKouRu166VU2xqa1";

interface Accept {
  scheme: string;
  network: string;
  amount: string;
  asset: string;
  payTo: string;
  maxTimeoutSeconds: number;
  extra?: Record<string, unknown>;
}

const evmAccept = (over: Partial<Accept> = {}): Accept => ({
  scheme: "exact",
  network: "eip155:8453",
  amount: "1000",
  asset: BASE_USDC,
  payTo: PAY_TO,
  maxTimeoutSeconds: 60,
  extra: { name: "USD Coin", version: "2" },
  ...over,
});

/** A server that wants payment once: 402 with a challenge, then 200 with a receipt when it is paid. */
const receipt = (network: string, amount?: string): string => Buffer.from(JSON.stringify({ success: true, network, ...(amount ? { amount } : {}) })).toString("base64");

function server(
  h: Harness,
  accepts: Accept[],
  opts: { resource?: string; status?: number; big?: number; receipt?: string; extensions?: Record<string, unknown> } = {},
): { requests: Array<{ headers: Record<string, string> }> } {
  const requests: Array<{ headers: Record<string, string> }> = [];
  const challenge = Buffer.from(JSON.stringify({ x402Version: 2, resource: { url: opts.resource ?? URL_ }, accepts, ...(opts.extensions ? { extensions: opts.extensions } : {}) })).toString("base64");
  h.net.onHttp(
    (u) => u.startsWith("https://api.example.test/"),
    (call) => {
      requests.push({ headers: call.headers });
      if (call.headers["payment-signature"]) {
        return new Response(opts.big ? "x".repeat(opts.big) : '{"ok":true}', { status: opts.status ?? 200, headers: { "content-type": "application/json", ...(opts.status && opts.status >= 400 ? {} : { "payment-response": opts.receipt ?? receipt(accepts[0]!.network) }) } });
      }
      return new Response("payment required", { status: 402, headers: { "payment-required": challenge } });
    },
  );
  return { requests };
}

const fetch402 = (h: Harness, extra: Record<string, unknown> = {}) =>
  h.call("x402_fetch", { url: URL_, method: "GET", maxAmountAtomic: 5000, ...extra }) as Promise<Record<string, any>>; // eslint-disable-line @typescript-eslint/no-explicit-any

async function message(promise: Promise<unknown>): Promise<string> {
  try {
    await promise;
  } catch (error) {
    return (error as Error).message;
  }
  throw new Error("expected a refusal");
}

describe("x402_fetch", () => {
  it("passes a free response through with no payment", async () => {
    const h = makeHarness({ env: MAINNET });
    h.net.onHttp((u) => u.startsWith("https://api.example.test/"), () => json({ free: true }));
    const out = await fetch402(h);
    expect(out).toMatchObject({ status: 200, contentType: "application/json", body: '{"free":true}', truncated: false, payment: null });
    // a 402 with no x402 challenge also passes through unpaid
    h.net.onHttp((u) => u.startsWith("https://api.example.test/"), () => new Response("pay me", { status: 402 }));
    expect(await fetch402(h)).toMatchObject({ status: 402, body: "pay me", payment: null });
  });

  it("pays USDC on an EVM network with a signed EIP-3009 authorization that verifies, and reports the settlement", async () => {
    const h = makeHarness({ env: MAINNET });
    const { status } = await setupWallet(h);
    const me = (status.accounts as Array<{ chain: string; address: string }>).find((a) => a.chain === "evm")!.address;
    const { requests } = server(h, [evmAccept()]);
    const out = await fetch402(h);
    expect(out).toMatchObject({ status: 200, body: '{"ok":true}', payment: { amountAtomic: "1000", asset: BASE_USDC, recipient: PAY_TO, network: "eip155:8453", settled: true } });
    expect(out.payment.paymentId).toMatch(/^x402-/);
    expect(requests).toHaveLength(2);
    const header = JSON.parse(Buffer.from(requests[1]!.headers["payment-signature"]!, "base64").toString("utf8"));
    expect(header.accepted.payTo).toBe(PAY_TO);
    const auth = header.payload.authorization;
    expect(auth).toMatchObject({ from: me, to: PAY_TO, value: "1000" });
    expect(
      await verifyTypedData({
        address: me as `0x${string}`,
        domain: { name: "USD Coin", version: "2", chainId: 8453, verifyingContract: BASE_USDC },
        types: { TransferWithAuthorization: [{ name: "from", type: "address" }, { name: "to", type: "address" }, { name: "value", type: "uint256" }, { name: "validAfter", type: "uint256" }, { name: "validBefore", type: "uint256" }, { name: "nonce", type: "bytes32" }] },
        primaryType: "TransferWithAuthorization",
        message: { from: auth.from, to: auth.to, value: BigInt(auth.value), validAfter: BigInt(auth.validAfter), validBefore: BigInt(auth.validBefore), nonce: auth.nonce },
        signature: header.payload.signature,
      }),
    ).toBe(true);
    const listed = (await h.call("x402_list_payments", { limit: 500 })) as { payments: Array<Record<string, any>> }; // eslint-disable-line @typescript-eslint/no-explicit-any
    expect(listed.payments).toHaveLength(1);
    expect(listed.payments[0]).toMatchObject({ id: out.payment.paymentId, status: "settled", asset: BASE_USDC, amountAtomic: "1000" });
    expect(Date.parse(listed.payments[0]!.timestamp as string)).toBeGreaterThan(0);
  });

  it("pays USDC on Solana with a transaction the facilitator completes: our signature is in, the fee payer's slot is empty", async () => {
    const h = makeHarness();
    const { status } = await setupWallet(h);
    mockSolana(h.net);
    const me = (status.accounts as Array<{ chain: string; address: string }>).find((a) => a.chain === "solana")!.address;
    const facilitator = Keypair.generate().publicKey.toBase58();
    const recipient = Keypair.generate().publicKey.toBase58();
    const { requests } = server(h, [{ scheme: "exact", network: DEVNET_CAIP, amount: "2500", asset: DEVNET_USDC, payTo: recipient, maxTimeoutSeconds: 60, extra: { feePayer: facilitator } }]);
    const out = await fetch402(h, { network: "solana" });
    expect(out.payment).toMatchObject({ amountAtomic: "2500", asset: DEVNET_USDC, network: DEVNET_CAIP, settled: true });
    const header = JSON.parse(Buffer.from(requests[1]!.headers["payment-signature"]!, "base64").toString("utf8"));
    const wire = Buffer.from(header.payload.transaction, "base64");
    const tx = decodeTransaction(new Uint8Array(wire));
    expect(base58.encode(tx.accountKeys[0]!)).toBe(facilitator);
    expect(tx.signatures[0]!.every((b) => b === 0)).toBe(true);
    const mine = tx.accountKeys.findIndex((k) => base58.encode(k) === me);
    expect(signatureIsValid(tx.accountKeys[mine]!, tx.message, tx.signatures[mine]!)).toBe(true);
    const parsed = Transaction.from(wire);
    const transfer = parsed.instructions.find((i) => i.data[0] === 12 && i.data.length === 10)!;
    expect(Buffer.from(transfer.data).readBigUInt64LE(1)).toBe(2500n);
  });

  it("refuses over maxAmountAtomic, over the per-request, daily and monthly budgets, and signs nothing", async () => {
    const h = makeHarness({ env: { ...MAINNET, OPENHUMAN_X402_PER_REQUEST_MAX: "1200", OPENHUMAN_X402_DAILY_MAX: "2000", OPENHUMAN_X402_MONTHLY_MAX: "3500" } });
    await setupWallet(h);
    const { requests } = server(h, [evmAccept()]);
    expect(await message(fetch402(h, { maxAmountAtomic: 999 }))).toBe("X402_REFUSED: x402 payment of 1000 exceeds maxAmountAtomic 999");
    expect(requests).toHaveLength(1); // challenge fetched, no payment sent
    server(h, [evmAccept({ amount: "1201" })]);
    expect(await message(fetch402(h))).toBe("X402_REFUSED: x402 payment of 1201 exceeds per-request cap of 1200");
    server(h, [evmAccept()]);
    await fetch402(h);
    await fetch402(h);
    expect(await message(fetch402(h))).toBe("X402_REFUSED: x402 daily budget exceeded");
    h.clock.now += 25 * 3600_000;
    await fetch402(h); // a day on, the day's total has rolled off, the month's has not
    expect(await message(fetch402(h))).toBe("X402_REFUSED: x402 monthly budget exceeded");
    h.clock.now += 31 * 24 * 3600_000;
    await fetch402(h); // the month rolled over
  });

  it("refuses when no budget is set, rather than guessing one", async () => {
    const h = makeHarness({ env: { ...MAINNET, OPENHUMAN_X402_PER_REQUEST_MAX: "", OPENHUMAN_X402_DAILY_MAX: "", OPENHUMAN_X402_MONTHLY_MAX: "" } });
    await setupWallet(h);
    const { requests } = server(h, [evmAccept()]);
    expect(await message(fetch402(h))).toMatch(/^X402_REFUSED: the x402 budget is not set/);
    expect(requests).toHaveLength(1);
  });

  it("refuses a challenge with no option this wallet pays: another network, another asset, the upto scheme", async () => {
    const h = makeHarness({ env: MAINNET });
    await setupWallet(h);
    server(h, [evmAccept()]);
    expect(await message(fetch402(h, { network: "eip155:1" }))).toMatch(/^X402_REFUSED: no supported payment option/);
    expect(await message(fetch402(h, { network: "solana" }))).toMatch(/^X402_REFUSED: no supported payment option/);
    server(h, [evmAccept({ asset: "0x00000000000000000000000000000000000000aa" })]);
    expect(await message(fetch402(h))).toBe(`X402_REFUSED: x402 asset 0x00000000000000000000000000000000000000aa is not the USDC accepted on eip155:8453`);
    server(h, [evmAccept({ scheme: "upto" })]);
    expect(await message(fetch402(h))).toMatch(/^X402_REFUSED: no supported payment option/);
    expect(await h.call("x402_list_payments", { limit: 10 })).toEqual({ payments: [] });
  });

  it("refuses a challenge whose resource is on another origin, in the exact words hands/wallet matches", async () => {
    const h = makeHarness({ env: MAINNET });
    await setupWallet(h);
    const { requests } = server(h, [evmAccept()], { resource: "https://evil.example.test/other" });
    const error = await message(fetch402(h));
    expect(error).toBe(
      `openhuman.x402_fetch: x402_fetch: payment failed: ai.tinyhumans.tinywallet.Error.X402OperationFailed: x402 protocol: challenge resource https://evil.example.test/other is not on the origin of the requested URL ${URL_}`,
    );
    expect(error.startsWith("X402_REFUSED:")).toBe(false); // wallet/x402.py reads this one by its own marker
    expect(requests).toHaveLength(1);
  });

  it("guards the destination and the request", async () => {
    const h = makeHarness({ env: MAINNET });
    await setupWallet(h);
    await expect(fetch402(h, { url: "http://api.example.test/x" })).rejects.toThrow("x402_fetch params: url must be an absolute https:// URL");
    await expect(fetch402(h, { url: "https://localhost/x" })).rejects.toThrow(/destination refused/);
    await expect(fetch402(h, { url: "https://10.1.2.3/x" })).rejects.toThrow(/destination refused/);
    await expect(fetch402(h, { url: "https://[::1]/x" })).rejects.toThrow(/destination refused/);
    await expect(fetch402(h, { url: "https://169.254.169.254/latest" })).rejects.toThrow(/destination refused/);
    h.env({});
    const rebound = makeHarness({ env: MAINNET });
    // a name that resolves to a private address is refused too
    const methods = (await import("../../src/wallet/index.js")).createMethods({ fetch: h.net.fetch, resolve: async () => ["10.0.0.7"] });
    await expect(methods.x402_fetch!({ url: URL_, maxAmountAtomic: 5 }, rebound.ctx)).rejects.toThrow(/destination refused/);
    await expect(fetch402(h, { method: "PUT" })).rejects.toThrow("x402_fetch params: method must be GET or POST");
    await expect(fetch402(h, { body: "x" })).rejects.toThrow("x402_fetch params: a body is only allowed with POST");
    await expect(fetch402(h, { headers: { "PAYMENT-SIGNATURE": "x" } })).rejects.toThrow("the PAYMENT-SIGNATURE header is set by the wallet, not the caller");
    await expect(fetch402(h, { headers: { "x-payment": "x" } })).rejects.toThrow(/set by the wallet/);
    await expect(fetch402(h, { headers: { "sign-in-with-x": "x" } })).rejects.toThrow(/set by the wallet/);
    await expect(fetch402(h, { headers: { a: 1 } })).rejects.toThrow("header a must have a string value");
    await expect(fetch402(h, { maxAmountAtomic: 0 })).rejects.toThrow("maxAmountAtomic must be greater than zero");
    await expect(fetch402(h, { network: "bad network" })).rejects.toThrow(/network must be a CAIP-2 id/);
    await expect(fetch402(h, { surprise: 1 })).rejects.toThrow("unknown field `surprise`");
    expect(h.net.calls).toHaveLength(0);
  });

  it("does not follow redirects, cuts a long body, and leaves sign-in unimplemented", async () => {
    const h = makeHarness({ env: MAINNET });
    await setupWallet(h);
    h.net.onHttp((u) => u.startsWith("https://api.example.test/"), () => new Response("", { status: 302, headers: { location: "https://elsewhere.test/" } }));
    expect(await fetch402(h)).toMatchObject({ status: 302 });
    server(h, [evmAccept()], { big: 300 * 1024 });
    const out = await fetch402(h);
    expect(out.truncated).toBe(true);
    expect(out.body.length).toBe(256 * 1024);
    h.net.onHttp((u) => u.startsWith("https://api.example.test/"), () => new Response("", { status: 402, headers: { "payment-required": Buffer.from(JSON.stringify({ x402Version: 2, resource: { url: URL_ }, accepts: [evmAccept()] })).toString("base64") } }));
    await expect(fetch402(h, { signIn: true, maxAmountAtomic: 1 })).rejects.toThrow(/sign-in refused: the challenge does not ask for sign-in-with-x/);
  });

  it("records a payment the server refused to settle as failed, and so one it never answered", async () => {
    const h = makeHarness({ env: MAINNET });
    await setupWallet(h);
    server(h, [evmAccept()], { status: 402 });
    const out = await fetch402(h);
    expect(out.payment.settled).toBe(false);
    const rows = ((await h.call("x402_list_payments", { limit: 10 })) as { payments: Array<{ status: string }> }).payments;
    expect(rows.map((r) => r.status)).toEqual(["failed"]);
    h.net.onHttp((u) => u.startsWith("https://api.example.test/"), (call) => {
      if (call.headers["payment-signature"]) throw new TypeError("socket hang up");
      return new Response("", { status: 402, headers: { "payment-required": Buffer.from(JSON.stringify({ x402Version: 2, resource: { url: URL_ }, accepts: [evmAccept()] })).toString("base64") } });
    });
    await expect(fetch402(h)).rejects.toThrow("x402_fetch: request failed");
    const after = ((await h.call("x402_list_payments", { limit: 10 })) as { payments: Array<{ status: string }> }).payments;
    expect(after.map((r) => r.status)).toEqual(["failed", "failed"]);
  });
});

const PERMIT2 = "0x000000000022D473030F116dDEE9F6B43aC78BA3";
const PROXY = "0x4020A4f3b7b90ccA423B9fabCc0CE57C6C240002";
const FACILITATOR = "0x3333333333333333333333333333333333333333";
const uptoAccept = (over: Partial<Accept> = {}): Accept =>
  evmAccept({ scheme: "upto", extra: { name: "USD Coin", version: "2", assetTransferMethod: "permit2", facilitatorAddress: FACILITATOR }, ...over });

const permit2Types = {
  PermitWitnessTransferFrom: [
    { name: "permitted", type: "TokenPermissions" },
    { name: "spender", type: "address" },
    { name: "nonce", type: "uint256" },
    { name: "deadline", type: "uint256" },
    { name: "witness", type: "Witness" },
  ],
  TokenPermissions: [{ name: "token", type: "address" }, { name: "amount", type: "uint256" }],
  Witness: [{ name: "to", type: "address" }, { name: "facilitator", type: "address" }, { name: "validAfter", type: "uint256" }],
} as const;

describe("x402 upto (a ceiling, settled for what was used)", () => {
  it("signs a Permit2 permit for the upto proxy that verifies, and reports what the receipt says without trusting it", async () => {
    const h = makeHarness({ env: { ...MAINNET, OPENHUMAN_X402_DAILY_MAX: "1500", OPENHUMAN_X402_MONTHLY_MAX: "9999" } });
    const { status } = await setupWallet(h);
    const me = (status.accounts as Array<{ chain: string; address: string }>).find((a) => a.chain === "evm")!.address as `0x${string}`;
    const { requests } = server(h, [uptoAccept()], { receipt: receipt("eip155:8453", "400") });
    const out = await fetch402(h);
    expect(out.payment).toMatchObject({ amountAtomic: "1000", chargedAtomic: "400", scheme: "upto", settled: true, network: "eip155:8453" });
    const header = JSON.parse(Buffer.from(requests[1]!.headers["payment-signature"]!, "base64").toString("utf8"));
    expect(header.accepted.scheme).toBe("upto");
    const auth = header.payload.permit2Authorization;
    expect(auth).toMatchObject({ from: me, spender: PROXY, permitted: { token: BASE_USDC, amount: "1000" }, witness: { to: PAY_TO, facilitator: FACILITATOR, validAfter: "0" } });
    expect(Number(auth.deadline) - Math.floor(h.clock.now / 1000)).toBe(60);
    expect(
      await (await import("viem")).verifyTypedData({
        address: me,
        domain: { name: "Permit2", chainId: 8453, verifyingContract: PERMIT2 },
        types: permit2Types,
        primaryType: "PermitWitnessTransferFrom",
        message: { permitted: { token: BASE_USDC, amount: 1000n }, spender: PROXY, nonce: BigInt(auth.nonce), deadline: BigInt(auth.deadline), witness: { to: PAY_TO, facilitator: FACILITATOR, validAfter: 0n } },
        signature: header.payload.signature,
      }),
    ).toBe(true);
    // the server's word about what it charged never lowers what the budget holds: the row stays at the signed ceiling
    const rows = ((await h.call("x402_list_payments", { limit: 5 })) as { payments: Array<Record<string, string>> }).payments;
    expect(rows[0]).toMatchObject({ status: "settled", amountAtomic: "1000" });
    expect(await message(fetch402(h))).toBe("X402_REFUSED: x402 daily budget exceeded");
  });

  it("holds the whole ceiling against the budget while it is pending, and counts a receipt above the ceiling as unsettled", async () => {
    const h = makeHarness({ env: { ...MAINNET, OPENHUMAN_X402_DAILY_MAX: "1500", OPENHUMAN_X402_MONTHLY_MAX: "9999" } });
    await setupWallet(h);
    server(h, [uptoAccept()], { receipt: receipt("eip155:8453", "1001") });
    const out = await fetch402(h);
    expect(out.payment.settled).toBe(false);
    // unsettled upto stays pending at its ceiling: 1000 held, so another 1000 does not fit under 1500
    expect(await message(fetch402(h))).toBe("X402_REFUSED: x402 daily budget exceeded");
  });

  it("is capped like any payment: maxAmountAtomic, the per-request budget, and the cap passed down", async () => {
    const h = makeHarness({ env: { ...MAINNET, OPENHUMAN_X402_PER_REQUEST_MAX: "900" } });
    await setupWallet(h);
    const { requests } = server(h, [uptoAccept()]);
    expect(await message(fetch402(h))).toBe("X402_REFUSED: x402 payment of 1000 exceeds per-request cap of 900");
    expect(await message(fetch402(h, { maxAmountAtomic: 999 }))).toBe("X402_REFUSED: x402 payment of 1000 exceeds maxAmountAtomic 999");
    expect(requests.every((r) => !r.headers["payment-signature"])).toBe(true);
  });

  it("prefers a fixed price to a ceiling, and refuses an upto it cannot bind to a facilitator or that asks for another transfer method", async () => {
    const h = makeHarness({ env: MAINNET });
    await setupWallet(h);
    const both = server(h, [uptoAccept(), evmAccept()]);
    await fetch402(h);
    expect(JSON.parse(Buffer.from(both.requests[1]!.headers["payment-signature"]!, "base64").toString("utf8")).accepted.scheme).toBe("exact");
    for (const extra of [{ name: "USD Coin", version: "2" }, { facilitatorAddress: "nope" }, { facilitatorAddress: FACILITATOR, assetTransferMethod: "eip3009" }]) {
      server(h, [evmAccept({ scheme: "upto", extra })]);
      expect(await message(fetch402(h))).toMatch(/^X402_REFUSED: no supported payment option/);
    }
  });

  it("adds the EIP-2612 permit only when the server sponsors it and the Permit2 allowance is short", async () => {
    const h = makeHarness({ env: MAINNET });
    const { status } = await setupWallet(h);
    const me = (status.accounts as Array<{ chain: string; address: string }>).find((a) => a.chain === "evm")!.address as `0x${string}`;
    let allowance = 0n;
    h.net.onRpc("eth_call", (p) => {
      const data = (p[0] as { data: string }).data;
      if (data.startsWith("0xdd62ed3e")) return `0x${allowance.toString(16)}`;
      if (data.startsWith("0x7ecebe00")) return "0x5";
      throw new Error("unexpected call");
    });
    const sponsored = { [EIP2612]: { info: { description: "gas", version: "1" } } };
    let seen = server(h, [uptoAccept()], { extensions: sponsored });
    await fetch402(h);
    let header = JSON.parse(Buffer.from(seen.requests[1]!.headers["payment-signature"]!, "base64").toString("utf8"));
    const info = header.extensions[EIP2612].info;
    expect(info).toMatchObject({ from: me, asset: BASE_USDC, spender: PERMIT2, amount: "1000", nonce: "5", version: "1" });
    expect(header.extensions[EIP2612].description).toBeUndefined(); // the server's own info object is replaced, as the reference client merges
    expect(
      await (await import("viem")).verifyTypedData({
        address: me,
        domain: { name: "USD Coin", version: "2", chainId: 8453, verifyingContract: BASE_USDC },
        types: { Permit: [{ name: "owner", type: "address" }, { name: "spender", type: "address" }, { name: "value", type: "uint256" }, { name: "nonce", type: "uint256" }, { name: "deadline", type: "uint256" }] },
        primaryType: "Permit",
        message: { owner: me, spender: PERMIT2, value: 1000n, nonce: 5n, deadline: BigInt(info.deadline) },
        signature: info.signature,
      }),
    ).toBe(true);
    allowance = 5000n; // already approved: no permit
    seen = server(h, [uptoAccept()], { extensions: sponsored });
    await fetch402(h);
    header = JSON.parse(Buffer.from(seen.requests[1]!.headers["payment-signature"]!, "base64").toString("utf8"));
    expect(header.extensions?.[EIP2612]?.info?.signature).toBeUndefined();
    // not sponsored: no permit either
    allowance = 0n;
    seen = server(h, [uptoAccept()]);
    await fetch402(h);
    header = JSON.parse(Buffer.from(seen.requests[1]!.headers["payment-signature"]!, "base64").toString("utf8"));
    expect(header.extensions).toBeUndefined();
  });
});

const EIP2612 = "eip2612GasSponsoring";

describe("x402 sign-in (SIWX)", () => {
  const info = (over: Record<string, unknown> = {}): Record<string, unknown> => ({
    domain: "api.example.test",
    uri: URL_,
    statement: "Sign in to api.example.test",
    version: "1",
    nonce: "abcdef123456",
    issuedAt: new Date(1_800_000_000_000 - 1000).toISOString(),
    expirationTime: new Date(1_800_000_000_000 + 300_000).toISOString(),
    resources: [URL_],
    ...over,
  });
  const challenge = (i: Record<string, unknown>, chains = [{ chainId: "eip155:1", type: "eip191" }, { chainId: "eip155:8453", type: "eip191" }]): Record<string, unknown> => ({
    x402Version: 2,
    resource: { url: URL_ },
    accepts: [],
    extensions: { "sign-in-with-x": { info: i, supportedChains: chains } },
  });
  function signInServer(h: Harness, body: Record<string, unknown>): Array<Record<string, string>> {
    const seen: Array<Record<string, string>> = [];
    h.net.onHttp((u) => u.startsWith("https://api.example.test/"), (call) => {
      seen.push(call.headers);
      return call.headers["sign-in-with-x"] ? json({ ok: true }) : new Response("", { status: 402, headers: { "payment-required": Buffer.from(JSON.stringify(body)).toString("base64") } });
    });
    return seen;
  }

  it("signs the EIP-4361 message with the wallet's EVM key, prefers Base, pays nothing, and says who signed in", async () => {
    const h = makeHarness({ env: MAINNET });
    const { status } = await setupWallet(h);
    const me = (status.accounts as Array<{ chain: string; address: string }>).find((a) => a.chain === "evm")!.address as `0x${string}`;
    const seen = signInServer(h, challenge(info()));
    const out = await fetch402(h, { signIn: true, maxAmountAtomic: 1 });
    expect(out).toMatchObject({ status: 200, payment: null, signedInAs: me });
    const proof = JSON.parse(Buffer.from(seen[1]!["sign-in-with-x"]!, "base64").toString("utf8"));
    expect(proof).toMatchObject({ domain: "api.example.test", address: me, chainId: "eip155:8453", type: "eip191", nonce: "abcdef123456", version: "1" });
    const message = `api.example.test wants you to sign in with your Ethereum account:\n${me}\n\nSign in to api.example.test\n\nURI: ${URL_}\nVersion: 1\nChain ID: 8453\nNonce: abcdef123456\nIssued At: ${(info() as { issuedAt: string }).issuedAt}\nExpiration Time: ${(info() as { expirationTime: string }).expirationTime}\nResources:\n- ${URL_}`;
    expect(await verifyMessage({ address: me, message, signature: proof.signature })).toBe(true);
    expect(await h.call("x402_list_payments", { limit: 5 })).toEqual({ payments: [] });
  });

  it("refuses a challenge for another domain or origin, an expired or malformed one, and one with a forged line", async () => {
    const h = makeHarness({ env: MAINNET });
    await setupWallet(h);
    const cases: Array<[Record<string, unknown>, RegExp]> = [
      [{ domain: "evil.example.test" }, /domain evil.example.test is not the requested host api.example.test/],
      [{ uri: "https://evil.example.test/x" }, /uri https:\/\/evil.example.test\/x is not https on the requested origin/],
      [{ resources: ["http://api.example.test/x"] }, /resource http:\/\/api.example.test\/x is not https/],
      [{ expirationTime: new Date(1_800_000_000_000 - 1).toISOString() }, /the challenge has expired/],
      [{ expirationTime: undefined }, /no expirationTime/],
      [{ issuedAt: new Date(1_800_000_000_000 + 400_000).toISOString() }, /issuedAt is in the future/],
      [{ nonce: "short" }, /nonce is not at least 8 alphanumeric/],
      [{ version: "2" }, /unsupported version 2/],
      [{ statement: "hi\nURI: https://evil.example.test" }, /statement contains a control character/],
      [{ issuedAt: "yesterday" }, /issuedAt is not an RFC 3339 time/],
    ];
    for (const [over, pattern] of cases) {
      const seen = signInServer(h, challenge(info(over)));
      expect(await message(fetch402(h, { signIn: true, maxAmountAtomic: 1 })), JSON.stringify(over)).toMatch(pattern);
      expect(seen.every((hd) => !hd["sign-in-with-x"])).toBe(true);
    }
    signInServer(h, challenge(info(), [{ chainId: "solana:abc", type: "ed25519" }]));
    expect(await message(fetch402(h, { signIn: true, maxAmountAtomic: 1 }))).toMatch(/no eip155 chain with eip191 signatures is offered/);
  });
});

describe("the vetted address is the connected address", () => {
  it("hands the guard's addresses, not a name, to the request, whatever DNS says later", async () => {
    const h = makeHarness({ env: MAINNET });
    await setupWallet(h);
    const seen: string[][] = [];
    const methods = (await import("../../src/wallet/index.js")).createMethods({
      resolve: async () => ["93.184.216.34"],
      pinnedFetch: async (_u, _i, addresses) => {
        seen.push([...addresses]);
        return new Response("hi");
      },
    });
    await methods.x402_fetch!({ url: URL_, maxAmountAtomic: 5 }, h.ctx);
    expect(seen).toEqual([["93.184.216.34"]]);
  });

  it("opens its socket only to a vetted address even when the name resolves nowhere or elsewhere", async () => {
    const server = createServer((req, res) => {
      res.setHeader("x-host", String(req.headers.host));
      res.end("pinned");
    });
    await new Promise<void>((resolve) => server.listen(0, "127.0.0.1", resolve));
    const port = (server.address() as { port: number }).port;
    try {
      const response = await pinnedFetch(`http://rebind.invalid:${port}/x`, {}, ["127.0.0.1"]);
      expect(await response.text()).toBe("pinned");
      expect(response.headers.get("x-host")).toBe(`rebind.invalid:${port}`); // the name still rides in Host (and the TLS server name)
      await expect(pinnedFetch(`http://rebind.invalid:${port}/x`, {}, [])).rejects.toThrow(/no vetted address/);
    } finally {
      server.close();
    }
    const lookup = pinnedLookup(["10.9.9.9"]);
    await new Promise<void>((resolve) => lookup("anything.example", { all: false }, (_e, address) => {
      expect(address).toBe("10.9.9.9");
      resolve();
    }));
  });
});

describe("review fixes: pinnedFetch", () => {
  it("refuses a Host header that names another authority, and accepts the matching one", async () => {
    const server = createServer((req, res) => res.end(String(req.headers.host)));
    await new Promise<void>((resolve) => server.listen(0, "127.0.0.1", resolve));
    const port = (server.address() as { port: number }).port;
    try {
      await expect(pinnedFetch(`http://good.invalid:${port}/x`, { headers: { Host: "other.invalid" } }, ["127.0.0.1"])).rejects.toThrow(/Host header does not match/);
      const ok = await pinnedFetch(`http://good.invalid:${port}/x`, { headers: { Host: `good.invalid:${port}` } }, ["127.0.0.1"]);
      expect(await ok.text()).toBe(`good.invalid:${port}`);
    } finally {
      server.close();
    }
  });

  it("rejects, instead of throwing in an event handler, when the server answers a status Response() refuses", async () => {
    const { createServer: raw } = await import("node:net");
    const server = raw((socket) => {
      socket.once("data", () => socket.end("HTTP/1.1 600 Odd\r\ncontent-length: 0\r\n\r\n"));
    });
    await new Promise<void>((resolve) => server.listen(0, "127.0.0.1", resolve));
    const port = (server.address() as { port: number }).port;
    try {
      await expect(pinnedFetch(`http://odd.invalid:${port}/x`, {}, ["127.0.0.1"])).rejects.toThrow();
    } finally {
      server.close();
    }
  });
});

describe("the x402 budget counts settled and in-flight payments, not failed fetches", () => {
  const tight = { ...MAINNET, OPENHUMAN_X402_DAILY_MAX: "1500", OPENHUMAN_X402_MONTHLY_MAX: "9999" };

  it("does not count a signed payment whose fetch failed: a 4xx, a 5xx or no answer frees the budget at once", async () => {
    const h = makeHarness({ env: { ...tight, OPENHUMAN_X402_DAILY_MAX: "1000" } });
    await setupWallet(h);
    for (const status of [400, 402, 500, 503]) {
      server(h, [evmAccept()], { status });
      expect((await fetch402(h)).payment.settled).toBe(false);
    }
    h.net.onHttp((u) => u.startsWith("https://api.example.test/"), (call) => {
      if (call.headers["payment-signature"]) throw new TypeError("socket hang up");
      return new Response("", { status: 402, headers: { "payment-required": Buffer.from(JSON.stringify({ x402Version: 2, resource: { url: URL_ }, accepts: [evmAccept()] })).toString("base64") } });
    });
    await expect(fetch402(h)).rejects.toThrow("x402_fetch: request failed");
    // the whole day's 1000 is still there, and a good payment still counts at its full value
    server(h, [evmAccept()]);
    expect((await fetch402(h)).payment.settled).toBe(true);
    expect(await message(fetch402(h))).toBe("X402_REFUSED: x402 daily budget exceeded");
  });

  it("keeps an upto ceiling whatever the receipt reports, even a receipt of nothing", async () => {
    const h = makeHarness({ env: tight });
    await setupWallet(h);
    server(h, [uptoAccept()], { receipt: receipt("eip155:8453", "0") });
    const out = await fetch402(h);
    expect(out.payment).toMatchObject({ amountAtomic: "1000", settled: true });
    const rows = ((await h.call("x402_list_payments", { limit: 5 })) as { payments: Array<Record<string, string>> }).payments;
    expect(rows[0]).toMatchObject({ amountAtomic: "1000" });
    expect(await message(fetch402(h))).toBe("X402_REFUSED: x402 daily budget exceeded");
  });

  it("frees only what was never signed: a payment that failed before a signature owes nothing", async () => {
    const h = makeHarness({ env: { ...tight, OPENHUMAN_X402_DAILY_MAX: "1000" } });
    await setupWallet(h);
    server(h, [evmAccept()]);
    const key = h.ctx.env.OPENHUMAN_KEYRING_MASTER_KEY_FILE as string;
    h.env({ OPENHUMAN_KEYRING_MASTER_KEY_FILE: "" });
    await expect(fetch402(h)).rejects.toThrow(/cannot sign/);
    h.env({ OPENHUMAN_KEYRING_MASTER_KEY_FILE: key });
    const out = await fetch402(h); // the whole day's 1000 is still there
    expect(out.payment.settled).toBe(true);
  });

  it("keeps counting a payment that got a 2xx with no settlement receipt", async () => {
    const h = makeHarness({ env: { ...tight, OPENHUMAN_X402_DAILY_MAX: "1000" } });
    await setupWallet(h);
    server(h, [evmAccept()], { receipt: "" });
    expect((await fetch402(h)).payment.settled).toBe(false);
    expect(await message(fetch402(h))).toBe("X402_REFUSED: x402 daily budget exceeded");
  });
});

describe("review fixes: sign-in result and the Solana fee payer", () => {
  it("does not say we signed in when the server rejects the proof", async () => {
    const h = makeHarness({ env: MAINNET });
    await setupWallet(h);
    const body = {
      x402Version: 2,
      resource: { url: URL_ },
      accepts: [],
      extensions: {
        "sign-in-with-x": {
          info: {
            domain: "api.example.test",
            uri: URL_,
            statement: "Sign in",
            version: "1",
            nonce: "abcdef123456",
            issuedAt: new Date(1_800_000_000_000 - 1000).toISOString(),
            expirationTime: new Date(1_800_000_000_000 + 300_000).toISOString(),
            resources: [URL_],
          },
          supportedChains: [{ chainId: "eip155:8453", type: "eip191" }],
        },
      },
    };
    h.net.onHttp((u) => u.startsWith("https://api.example.test/"), (call) =>
      call.headers["sign-in-with-x"]
        ? new Response("no", { status: 401 })
        : new Response("", { status: 402, headers: { "payment-required": Buffer.from(JSON.stringify(body)).toString("base64") } }),
    );
    const out = await fetch402(h, { signIn: true, maxAmountAtomic: 1 });
    expect(out.status).toBe(401);
    expect(out).not.toHaveProperty("signedInAs");
  });

  it("refuses a challenge that names this wallet as the Solana fee payer, and signs nothing", async () => {
    const h = makeHarness();
    const { status } = await setupWallet(h);
    mockSolana(h.net);
    const me = (status.accounts as Array<{ chain: string; address: string }>).find((a) => a.chain === "solana")!.address;
    const recipient = Keypair.generate().publicKey.toBase58();
    const { requests } = server(h, [{ scheme: "exact", network: DEVNET_CAIP, amount: "2500", asset: DEVNET_USDC, payTo: recipient, maxTimeoutSeconds: 60, extra: { feePayer: me } }]);
    expect(await message(fetch402(h, { network: "solana" }))).toMatch(/names this wallet as its own fee payer/);
    expect(requests.every((r) => !r.headers["payment-signature"])).toBe(true);
  });
});
