import { base58 } from "@scure/base";
import { Transaction } from "@solana/web3.js";
import { verifyTypedData } from "viem";
import { describe, expect, it } from "vitest";
import { decodeTransaction, signatureIsValid } from "../../src/wallet/solana-program.js";
import { type Harness, json, makeHarness, mockSolana, setupWallet } from "./helpers.js";
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
function server(h: Harness, accepts: Accept[], opts: { resource?: string; status?: number; big?: number } = {}): { requests: Array<{ headers: Record<string, string> }> } {
  const requests: Array<{ headers: Record<string, string> }> = [];
  const challenge = Buffer.from(JSON.stringify({ x402Version: 2, resource: { url: opts.resource ?? URL_ }, accepts })).toString("base64");
  h.net.onHttp(
    (u) => u.startsWith("https://api.example.test/"),
    (call) => {
      requests.push({ headers: call.headers });
      if (call.headers["payment-signature"]) {
        return new Response(opts.big ? "x".repeat(opts.big) : '{"ok":true}', { status: opts.status ?? 200, headers: { "content-type": "application/json", ...(opts.status && opts.status >= 400 ? {} : { "payment-response": "c2V0dGxlZA==" }) } });
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
    await expect(fetch402(h, { signIn: true, maxAmountAtomic: 1 })).rejects.toThrow(/sign-in failed: Sign-In-With-X is not implemented/);
  });

  it("records a payment the server refused to settle as failed, and one it never answered as still pending", async () => {
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
    expect(after.map((r) => r.status)).toEqual(["pending", "failed"]);
  });
});
