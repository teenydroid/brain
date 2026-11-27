// LI.FI, the default provider for swap and bridge quotes: a port of
// spine/src/lifi.rs. The wallet module asks for one unsigned transaction per
// swap or bridge in deBridge's terms; this turns such a body into one
// `GET /v1/quote` and LI.FI's answer back into the envelope the module signs.
//
//   Module body (after the router)                LI.FI query
//   chainId / srcChainId, dstChainId               fromChain, toChain
//   tokenIn / srcChainTokenIn                      fromToken
//   tokenOut / dstChainTokenOut                    toToken
//   tokenInAmount / srcChainTokenInAmount          fromAmount
//   senderAddress / srcChainOrderAuthorityAddress  fromAddress
//   tokenOutRecipient / dstChainTokenOutRecipient  toAddress
//   slippage (percent, or auto)                    slippage (a fraction)
//
// A field the module does not send today is refused, not dropped. EVM
// addresses go lowercased (LI.FI refuses a wrong EIP-55 checksum; lowercase
// carries none). `TEENY_LIFI_API` overrides the base URL (a test double);
// `LIFI_API_KEY`, when set, goes as `x-lifi-api-key`. No log line carries an
// address or the key.
import type { Deps } from "../endpoints.js";
import { type BackendCall, ERROR_CHARS, type Env, type ProviderRequest, cut, isRecord } from "./types.js";

export const LIFI_SOLANA = 1_151_111_081_099_710;
export const MODULE_SOLANA = 7_565_164;
const DEFAULT_BASE = "https://li.quest";
const TIMEOUT_MS = 30_000;

type Field = readonly [string, string | null];

const SWAP: readonly Field[] = [
  ["chainId", "fromChain"],
  ["tokenIn", "fromToken"],
  ["tokenOut", "toToken"],
  ["tokenInAmount", "fromAmount"],
  ["senderAddress", "fromAddress"],
  ["tokenOutRecipient", "toAddress"],
  ["slippage", null],
];

const BRIDGE: readonly Field[] = [
  ["srcChainId", "fromChain"],
  ["dstChainId", "toChain"],
  ["srcChainTokenIn", "fromToken"],
  ["dstChainTokenOut", "toToken"],
  ["srcChainTokenInAmount", "fromAmount"],
  ["srcChainOrderAuthorityAddress", "fromAddress"],
  ["dstChainTokenOutRecipient", "toAddress"],
  ["dstChainTokenOutAmount", null],
  ["dstChainOrderAuthorityAddress", null],
];

const QUERY_ORDER = ["fromChain", "toChain", "fromToken", "toToken", "fromAmount", "fromAddress", "toAddress"] as const;

/** A string or a whole number, trimmed; undefined when absent, null or empty. */
function scalar(object: Record<string, unknown>, field: string): string | undefined {
  const value = object[field];
  let text: string;
  if (value === undefined || value === null) return undefined;
  if (typeof value === "string") text = value.trim();
  else if (typeof value === "number" && Number.isInteger(value) && value >= 0) text = String(value);
  else throw new Error(`LI.FI field ${field} is not a string or a whole number`);
  return text === "" ? undefined : text;
}

function moduleName(fields: readonly Field[], query: string): string {
  return fields.find(([, q]) => q === query)?.[0] ?? "chainId";
}

const U64_MAX = 0xffffffffffffffffn;

function chainId(text: string): string {
  if (!/^\+?\d+$/.test(text) || BigInt(text) > U64_MAX) throw new Error(`chain id ${text} is not a number`);
  const id = BigInt(text);
  return (id === BigInt(MODULE_SOLANA) ? BigInt(LIFI_SOLANA) : id).toString();
}

function lowerIfEvm(text: string): string {
  return text.length === 42 && text.startsWith("0x") && /^[0-9a-fA-F]+$/.test(text.slice(2)) ? text.toLowerCase() : text;
}

/** A slippage percent ("1", "0.5") as LI.FI's fraction ("0.01", "0.005"), exactly: no float on the way. */
export function fraction(percent: string): string {
  const bad = (): Error => new Error(`slippage ${percent} is not a percent from 0 to 100`);
  const dot = percent.indexOf(".");
  const whole = dot < 0 ? percent : percent.slice(0, dot);
  const frac = dot < 0 ? "" : percent.slice(dot + 1);
  if (whole === "" || !/^\d+$/.test(whole) || !/^\d*$/.test(frac)) throw bad();
  if (BigInt(whole) > 0xffffffffn) throw bad();
  const wholeNum = Number(whole);
  if (wholeNum > 100 || (wholeNum === 100 && /[^0]/.test(frac))) throw bad();
  const digits = String(wholeNum).padStart(3, "0") + frac;
  const split = digits.length - 2 - frac.length;
  const int = digits.slice(0, split);
  const rest = digits.slice(split);
  let text = `${int.replace(/^0+/, "")}.${rest}`;
  text = text.replace(/0+$/, "").replace(/\.+$/, "");
  if (text === "") return "0";
  return text.startsWith(".") ? `0${text}` : text;
}

/** The LI.FI request for one module call, or why it cannot be made. */
export function requestFor(call: BackendCall): ProviderRequest {
  if (call.kind === "routes") throw new Error("LI.FI answers swap and bridge quotes only");
  const swap = call.kind === "swap";
  const fields = swap ? SWAP : BRIDGE;
  const object = call.body;
  if (!isRecord(object)) throw new Error("LI.FI request body is not an object");
  const unknown = Object.keys(object).find((key) => !fields.some(([name]) => name === key));
  if (unknown !== undefined) throw new Error(`LI.FI does not take the field ${unknown}`);
  const named = new Map<string, string>();
  for (const [field, query] of fields) {
    const text = scalar(object, field);
    if (text === undefined) continue;
    if (query !== null) named.set(query, text);
  }
  if (swap) {
    const chain = named.get("fromChain");
    if (chain !== undefined) named.set("toChain", chain);
  } else {
    const amount = scalar(object, "dstChainTokenOutAmount");
    if (amount !== undefined && amount !== "auto") throw new Error("LI.FI quotes from the input; dstChainTokenOutAmount is auto");
  }
  const query: Array<[string, string]> = [];
  for (const name of QUERY_ORDER) {
    const text = named.get(name);
    if (text === undefined) throw new Error(`LI.FI field ${moduleName(fields, name)} is missing`);
    query.push([name, name === "fromChain" || name === "toChain" ? chainId(text) : lowerIfEvm(text)]);
  }
  if (swap) {
    const slippage = scalar(object, "slippage");
    if (slippage !== undefined && slippage !== "auto") query.push(["slippage", fraction(slippage)]);
  }
  query.push(["order", "CHEAPEST"]);
  // NEAR Intents sends the input to a deposit address and keeps the recipient
  // off-chain, so nothing in the signed transaction says where the money
  // lands; hands/wallet refuses such a quote. Not asking for it lets LI.FI
  // offer a route the hand can check instead.
  query.push(["denyBridges", "near"]);
  return { path: "/v1/quote", query };
}

const U128_MAX = (1n << 128n) - 1n;

/** An EVM value (hex `0x...` or decimal, absent for none) as decimal wei. */
function wei(value: unknown): string {
  const bad = (): Error => new Error("LI.FI's EVM transaction value is not a number");
  let number: bigint;
  if (value === undefined || value === null) number = 0n;
  else if (typeof value === "string") {
    const text = value.trim();
    const hex = text.startsWith("0x") || text.startsWith("0X") ? text.slice(2) : undefined;
    if (hex !== undefined) {
      if (hex === "") number = 0n;
      else if (/^[0-9a-fA-F]+$/.test(hex)) number = BigInt(`0x${hex}`);
      else throw bad();
    } else if (/^\+?\d+$/.test(text)) number = BigInt(text);
    else throw bad();
  } else if (typeof value === "number" && Number.isInteger(value) && value >= 0) number = BigInt(value);
  else throw bad();
  if (number > U128_MAX) throw bad();
  return number.toString();
}

function isCanonicalBase64(text: string): boolean {
  return text.length % 4 === 0 && /^[A-Za-z0-9+/]*={0,2}$/.test(text);
}

/** LI.FI's quote as the envelope the wallet module signs from. */
export function translate(answer: unknown): Record<string, unknown> {
  if (!isRecord(answer)) throw new Error("LI.FI sent a quote that is not an object");
  const quote: Record<string, unknown> = { ...answer };
  const action = quote.action;
  const fromChain = isRecord(action) ? action.fromChainId : undefined;
  if (typeof fromChain !== "number" || !Number.isInteger(fromChain) || fromChain < 0) {
    throw new Error("LI.FI's quote does not say which chain it starts on");
  }
  const request = quote.transactionRequest;
  delete quote.transactionRequest;
  if (!isRecord(request)) throw new Error("LI.FI's quote carries no transaction");
  const text = (key: string): string | undefined => (typeof request[key] === "string" ? (request[key] as string).trim() : undefined);
  let tx: Record<string, unknown>;
  if (fromChain === LIFI_SOLANA) {
    const blob = text("data");
    if (blob === undefined) throw new Error("LI.FI's Solana transaction has no data");
    if (!isCanonicalBase64(blob)) throw new Error("LI.FI's Solana transaction is not base64");
    tx = { data: `0x${Buffer.from(blob, "base64").toString("hex")}` };
  } else {
    const to = text("to");
    if (to === undefined) throw new Error("LI.FI's EVM transaction has no `to`");
    tx = { to, data: text("data") ?? "0x", value: wei(request.value) };
  }
  quote.tx = tx;
  quote.provider = "lifi";
  return quote;
}

/** LI.FI's own message from an error body, cut short. Never the request, which carries addresses. */
export function errorMessage(status: number, body: string): string {
  let detail = "";
  try {
    const parsed: unknown = JSON.parse(body);
    if (isRecord(parsed) && typeof parsed.message === "string") detail = parsed.message;
  } catch {
    /* not JSON */
  }
  detail = cut(detail, ERROR_CHARS);
  return detail === "" ? `LI.FI answered HTTP ${status}` : `LI.FI answered HTTP ${status}: ${detail}`;
}

function baseUrl(env: Env): string {
  const value = env.TEENY_LIFI_API?.trim().replace(/\/+$/, "");
  return value ? value : DEFAULT_BASE;
}

async function fetchQuote(env: Env, deps: Deps, request: ProviderRequest): Promise<unknown> {
  const url = new URL(`${baseUrl(env)}${request.path}`);
  for (const [k, v] of request.query) url.searchParams.append(k, v);
  const headers: Record<string, string> = { "user-agent": "teeny-core" };
  const key = env.LIFI_API_KEY?.trim();
  if (key) headers["x-lifi-api-key"] = key;
  let response: Response;
  try {
    response = await deps.fetch(url, { headers, redirect: "error", signal: AbortSignal.timeout(TIMEOUT_MS) });
  } catch (error) {
    const timeout = error instanceof Error && (error.name === "TimeoutError" || error.name === "AbortError");
    throw new Error(`could not reach LI.FI (${timeout ? "timeout" : "connect"})`);
  }
  let body: string;
  try {
    body = await response.text();
  } catch {
    throw new Error("LI.FI sent a body that could not be read");
  }
  if (response.status < 200 || response.status >= 300) throw new Error(errorMessage(response.status, body));
  try {
    return JSON.parse(body);
  } catch {
    throw new Error("LI.FI sent something that is not JSON");
  }
}

/** One module call, answered from LI.FI. */
export async function answer(env: Env, deps: Deps, call: BackendCall): Promise<unknown> {
  return translate(await fetchQuote(env, deps, requestFor(call)));
}
