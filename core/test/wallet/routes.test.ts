// Ports of every unit test in spine/src/{lifi,debridge,wallet_backend}.rs, plus
// the HTTP edges (base URL override, API key headers, error mapping).
import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";
import * as debridge from "../../src/wallet/routes/debridge.js";
import * as lifi from "../../src/wallet/routes/lifi.js";
import { route } from "../../src/wallet/routes/index.js";
import { backendAnswer } from "../../src/wallet/routes/index.js";
import type { BackendCall } from "../../src/wallet/routes/types.js";
import { MockNet, json } from "./helpers.js";

const here = dirname(fileURLToPath(import.meta.url));
const fixture = (name: string): Record<string, unknown> =>
  JSON.parse(readFileSync(join(here, "..", "..", "..", "hands", "tests", "wallet", "fixtures", "lifi", name), "utf8")) as Record<string, unknown>;

const OWN_SOL = "9xQeWvG816bUx9EPjHmaT23yvVM2ZWbrrpZb9PusVFin";
const OWN_EVM = "0x1111111111111111111111111111111111111111";
const BASE_USDC = "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913";
const SOL_USDC = "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v";

const pairs = (r: { query: Array<[string, string]> }): Array<[string, string]> => r.query;
const swapBody = (): Record<string, unknown> => ({
  chainId: 7565164,
  tokenIn: "11111111111111111111111111111111",
  tokenInAmount: "100000000",
  tokenOut: SOL_USDC,
  tokenOutRecipient: OWN_SOL,
  senderAddress: OWN_SOL,
  slippage: "1",
});
const bridgeBody = (): Record<string, unknown> => ({
  srcChainId: 7565164,
  srcChainTokenIn: "11111111111111111111111111111111",
  srcChainTokenInAmount: "100000000",
  dstChainId: 8453,
  dstChainTokenOut: BASE_USDC,
  dstChainTokenOutAmount: "auto",
  dstChainTokenOutRecipient: OWN_EVM,
  srcChainOrderAuthorityAddress: OWN_SOL,
  dstChainOrderAuthorityAddress: OWN_EVM,
});
const swap = (body: Record<string, unknown>): BackendCall => ({ kind: "swap", body });
const bridge = (body: Record<string, unknown>): BackendCall => ({ kind: "bridge", body });

describe("LI.FI (spine/src/lifi.rs)", () => {
  it("a bridge asks for a quote from and to Teeny's own addresses", () => {
    const request = lifi.requestFor(bridge(bridgeBody()));
    expect(request.path).toBe("/v1/quote");
    expect(pairs(request)).toEqual([
      ["fromChain", "1151111081099710"],
      ["toChain", "8453"],
      ["fromToken", "11111111111111111111111111111111"],
      ["toToken", "0x833589fcd6edb6e08f4c7c32d4f71b54bda02913"],
      ["fromAmount", "100000000"],
      ["fromAddress", OWN_SOL],
      ["toAddress", OWN_EVM],
      ["order", "CHEAPEST"],
      ["denyBridges", "near"],
    ]);
  });

  it("a swap stays on one chain with its slippage as a fraction", () => {
    const got = pairs(lifi.requestFor(swap(swapBody())));
    expect(got.slice(0, 2)).toEqual([
      ["fromChain", "1151111081099710"],
      ["toChain", "1151111081099710"],
    ]);
    expect(got).toContainEqual(["toAddress", OWN_SOL]);
    expect(got).toContainEqual(["slippage", "0.01"]);
  });

  it("auto slippage leaves it to LI.FI and a fraction is exact", () => {
    const body = swapBody();
    body.slippage = "auto";
    expect(pairs(lifi.requestFor(swap(body))).every(([k]) => k !== "slippage")).toBe(true);
    body.slippage = "0.5";
    expect(pairs(lifi.requestFor(swap(body)))).toContainEqual(["slippage", "0.005"]);
    for (const bad of ["-1", "abc", "101", "1e3"]) {
      body.slippage = bad;
      expect(() => lifi.requestFor(swap(body)), bad).toThrow();
    }
    for (const [percent, fraction] of [["0", "0"], ["100", "1"], ["100.0", "1"], ["0.01", "0.0001"], ["12.34", "0.1234"], ["5", "0.05"], ["10", "0.1"], ["99.99", "0.9999"]]) {
      expect(lifi.fraction(percent!), percent).toBe(fraction);
    }
  });

  it("an unknown field is refused, not dropped", () => {
    const body = swapBody();
    body.affiliateFeeRecipient = "0xevil";
    expect(() => lifi.requestFor(swap(body))).toThrow(/affiliateFeeRecipient/);
    const b = bridgeBody();
    b.toAddress = "0xevil";
    expect(() => lifi.requestFor(bridge(b))).toThrow();
  });

  it("a missing recipient is refused, not defaulted", () => {
    const body = bridgeBody();
    delete body.dstChainTokenOutRecipient;
    expect(() => lifi.requestFor(bridge(body))).toThrow(/dstChainTokenOutRecipient/);
  });

  it("a bridge for a fixed output is refused", () => {
    const body = bridgeBody();
    body.dstChainTokenOutAmount = "5000000";
    expect(() => lifi.requestFor(bridge(body))).toThrow(/dstChainTokenOutAmount is auto/);
  });

  it("a non-scalar field is refused, and routes are not LI.FI's to answer", () => {
    const body = swapBody();
    body.tokenIn = { x: 1 };
    expect(() => lifi.requestFor(swap(body))).toThrow(/not a string or a whole number/);
    expect(() => lifi.requestFor({ kind: "routes" })).toThrow(/swap and bridge quotes only/);
  });

  it("a Solana quote becomes the hex blob the module signs", () => {
    const recorded = fixture("sol_to_base_usdc.json");
    const blob = (recorded.transactionRequest as { data: string }).data;
    const envelope = lifi.translate(recorded);
    expect((envelope.tx as { data: string }).data).toBe(`0x${Buffer.from(blob, "base64").toString("hex")}`);
    expect((envelope.tx as Record<string, unknown>).to).toBeUndefined();
    expect(envelope.provider).toBe("lifi");
    expect(envelope.tool).toEqual(recorded.tool);
    expect(envelope.estimate).toEqual(recorded.estimate);
    expect(envelope.transactionRequest).toBeUndefined();
  });

  it("an EVM quote keeps to and data and gives the value in decimal wei", () => {
    const recorded = fixture("base_eth_to_usdc_swap.json");
    const request = recorded.transactionRequest as { to: string; data: string };
    const tx = lifi.translate(recorded).tx as Record<string, unknown>;
    expect(tx.to).toBe(request.to);
    expect(tx.data).toBe(request.data);
    expect(tx.value).toBe("5000000000000000");
    expect(Object.keys(tx)).toHaveLength(3);
  });

  it("a quote without a usable transaction is refused", () => {
    expect(() => lifi.translate({ action: { fromChainId: 1151111081099710 }, transactionRequest: { data: "not base64!" } })).toThrow(/not base64/);
    expect(() => lifi.translate({ action: { fromChainId: 8453 }, transactionRequest: { to: OWN_EVM, data: "0x", value: "0xzz" } })).toThrow(/value is not a number/);
    expect(() => lifi.translate({ action: { fromChainId: 8453 } })).toThrow(/carries no transaction/);
    expect(() => lifi.translate({ transactionRequest: { to: OWN_EVM, data: "0x" } })).toThrow(/does not say which chain/);
  });

  it("errors carry LI.FI's message, not the request", () => {
    expect(lifi.errorMessage(404, '{"message":"No available quotes for the requested transfer","code":1002}')).toBe(
      "LI.FI answered HTTP 404: No available quotes for the requested transfer",
    );
    expect(lifi.errorMessage(502, "<html>")).toBe("LI.FI answered HTTP 502");
    expect(lifi.errorMessage(400, JSON.stringify({ message: "x".repeat(400) })).length).toBe("LI.FI answered HTTP 400: ".length + 300);
  });
});

describe("deBridge (spine/src/debridge.rs)", () => {
  it("routes reads the supported chains", () => {
    const request = debridge.requestFor({ kind: "routes" });
    expect(request.path).toBe("/v1.0/supported-chains-info");
    expect(request.query).toEqual([]);
  });

  it("swap forwards only the known fields", () => {
    const request = debridge.requestFor(
      swap({
        chainId: 8453,
        tokenIn: "0x0000000000000000000000000000000000000000",
        tokenInAmount: "1000",
        tokenOut: BASE_USDC,
        tokenOutRecipient: "0xabc",
        senderAddress: "0xabc",
        slippage: "auto",
        affiliateFeeRecipient: "0xevil",
      }),
    );
    expect(request.path).toBe("/v1.0/chain/transaction");
    expect(request.query[0]).toEqual(["chainId", "8453"]);
    expect(request.query.every(([k]) => k !== "affiliateFeeRecipient")).toBe(true);
    expect(request.query).toHaveLength(7);
  });

  it("bridge goes to dln create-tx", () => {
    const request = debridge.requestFor(
      bridge({
        srcChainId: 8453,
        srcChainTokenIn: BASE_USDC,
        srcChainTokenInAmount: "5000000",
        dstChainId: 7565164,
        dstChainTokenOut: SOL_USDC,
        dstChainTokenOutAmount: "auto",
        dstChainTokenOutRecipient: "So1ana",
        srcChainOrderAuthorityAddress: "0xabc",
        dstChainOrderAuthorityAddress: "So1ana",
      }),
    );
    expect(request.path).toBe("/v1.0/dln/order/create-tx");
    expect(request.query[3]).toEqual(["dstChainId", "7565164"]);
  });

  it("a missing recipient is refused, a non-scalar field is refused", () => {
    expect(() => debridge.requestFor(swap({ chainId: 1, tokenIn: "a", tokenInAmount: "1", tokenOut: "b" }))).toThrow(/tokenOutRecipient/);
    expect(() => debridge.requestFor(swap({ chainId: { x: 1 }, tokenIn: "a", tokenInAmount: "1", tokenOut: "b", tokenOutRecipient: "c" }))).toThrow();
  });

  it("errors carry deBridge's message, not the request", () => {
    expect(debridge.errorMessage(400, '{"errorCode":8,"errorMessage":"insufficient liquidity"}')).toBe("deBridge answered HTTP 400: insufficient liquidity");
    expect(debridge.errorMessage(502, "<html>")).toBe("deBridge answered HTTP 502");
  });
});

describe("the router (spine/src/wallet_backend.rs)", () => {
  const swapWith = (slippage: unknown): BackendCall => swap({ chainId: 8453, tokenIn: "a", slippage });
  const bridgeWith = (amount: unknown): BackendCall => bridge({ srcChainId: 8453, dstChainTokenOutAmount: amount });
  const body = (call: BackendCall): Record<string, unknown> => (call as { body: Record<string, unknown> }).body;

  it("a tagged swap goes to its provider with the tag taken off", () => {
    let routed = route(swapWith("lifi:1"));
    expect(routed.provider).toBe("lifi");
    expect(body(routed.call).slippage).toBe("1");
    expect(body(routed.call).tokenIn).toBe("a");
    routed = route(swapWith("debridge:0.5"));
    expect(routed.provider).toBe("debridge");
    expect(body(routed.call).slippage).toBe("0.5");
  });

  it("a tagged bridge goes to its provider with the tag taken off", () => {
    let routed = route(bridgeWith("lifi:auto"));
    expect(routed.provider).toBe("lifi");
    expect(body(routed.call).dstChainTokenOutAmount).toBe("auto");
    routed = route(bridgeWith("debridge:auto"));
    expect(routed.provider).toBe("debridge");
    expect(body(routed.call).dstChainTokenOutAmount).toBe("auto");
  });

  it("an untagged call stays with deBridge unchanged", () => {
    for (const call of [swapWith("0.5"), swapWith(null), bridgeWith("auto")]) {
      const routed = route(call);
      expect(routed.provider).toBe("debridge");
      expect(routed.call).toEqual(call);
    }
    expect(route({ kind: "routes" }).provider).toBe("debridge");
  });

  it("an unknown provider is refused, and tags are exact", () => {
    expect(() => route(swapWith("jupiter:1"))).toThrow(/jupiter/);
    expect(() => route(bridgeWith("LIFI:auto"))).toThrow();
  });
});

describe("the HTTP edges", () => {
  it("asks LI.FI at TEENY_LIFI_API with the key as a header, and gives back the translated envelope", async () => {
    const net = new MockNet();
    net.onHttp(
      (u) => u.startsWith("https://lifi.test.invalid/v1/quote"),
      () => json(fixture("base_eth_to_usdc_swap.json")),
    );
    const env = { TEENY_LIFI_API: "https://lifi.test.invalid/", LIFI_API_KEY: "  secret-lifi-key  " };
    const answer = (await backendAnswer(env, { fetch: net.fetch, now: Date.now, resolve: async () => [], pinnedFetch: net.fetch }, swap({ ...swapBody(), chainId: 8453, slippage: "lifi:1", tokenOutRecipient: OWN_EVM, senderAddress: OWN_EVM, tokenIn: "0x0000000000000000000000000000000000000000", tokenOut: BASE_USDC }))) as Record<string, unknown>;
    expect(answer.provider).toBe("lifi");
    const call = net.calls[0]!;
    expect(call.headers["x-lifi-api-key"]).toBe("secret-lifi-key");
    expect(new URL(call.url).searchParams.get("slippage")).toBe("0.01");
    expect(new URL(call.url).searchParams.get("fromChain")).toBe("8453");
  });

  it("asks deBridge at TEENY_DEBRIDGE_API with the key as accesstoken, and says what it said when it fails, not what was asked", async () => {
    const net = new MockNet();
    net.onHttp(
      (u) => u.includes("dln.test.invalid"),
      () => json({ errorMessage: "insufficient liquidity" }, 400),
    );
    const env = { TEENY_DEBRIDGE_API: "https://dln.test.invalid", DEBRIDGE_API_KEY: "tok" };
    const deps = { fetch: net.fetch, now: Date.now, resolve: async () => [], pinnedFetch: net.fetch };
    await expect(backendAnswer(env, deps, swap({ chainId: 8453, tokenIn: "a", tokenInAmount: "1", tokenOut: "b", tokenOutRecipient: OWN_EVM, slippage: "debridge:auto" }))).rejects.toThrow(
      /^deBridge answered HTTP 400: insufficient liquidity$/,
    );
    expect(new URL(net.calls[0]!.url).searchParams.get("accesstoken")).toBe("tok");
    const failing = new MockNet();
    failing.onHttp(() => true, () => {
      throw new TypeError("connect ECONNREFUSED 10.0.0.1 with a secret URL");
    });
    await expect(backendAnswer(env, { ...deps, fetch: failing.fetch }, { kind: "routes" })).rejects.toThrow(/^could not reach deBridge \(connect\)$/);
  });
});
