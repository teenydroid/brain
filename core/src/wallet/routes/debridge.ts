// deBridge: the wallet module's swap and bridge quotes straight from
// deBridge's public API (a port of spine/src/debridge.rs). Returns deBridge's
// JSON unchanged; hands/wallet reads the estimation to check the quote.
//
//   Routes             GET /v1.0/supported-chains-info
//   Swap               GET /v1.0/chain/transaction   (one chain)
//   Bridge             GET /v1.0/dln/order/create-tx (DLN)
//
// Only the query fields named below are forwarded. `TEENY_DEBRIDGE_API`
// overrides the base URL (a test double); `DEBRIDGE_API_KEY`, when set, goes
// as `accesstoken`. Nothing here signs or moves funds. No log line carries an
// address or the key.
import { trackSecret } from "../errors.js";
import type { Deps } from "../endpoints.js";
import { type BackendCall, ERROR_CHARS, type Env, type ProviderRequest, cut, isRecord } from "./types.js";

const DEFAULT_BASE = "https://dln.debridge.finance";
const TIMEOUT_MS = 30_000;

const SWAP_FIELDS = ["chainId", "tokenIn", "tokenInAmount", "tokenOut", "tokenOutRecipient", "senderAddress", "slippage"];
const BRIDGE_FIELDS = [
  "srcChainId",
  "srcChainTokenIn",
  "srcChainTokenInAmount",
  "dstChainId",
  "dstChainTokenOut",
  "dstChainTokenOutAmount",
  "dstChainTokenOutRecipient",
  "srcChainOrderAuthorityAddress",
  "dstChainOrderAuthorityAddress",
];

/** The named fields of BODY as query pairs, in the listed order. A string or whole number is forwarded as written. */
function query(body: unknown, fields: string[], required: string[]): Array<[string, string]> {
  if (!isRecord(body)) throw new Error("deBridge request body is not an object");
  const out: Array<[string, string]> = [];
  for (const field of fields) {
    const value = body[field];
    let text: string;
    if (value === undefined || value === null) continue;
    if (typeof value === "string") text = value.trim();
    else if (typeof value === "number" && Number.isSafeInteger(value)) text = String(value);
    else throw new Error(`deBridge field ${field} is not a string or a whole number`);
    if (text === "") continue;
    out.push([field, text]);
  }
  for (const field of required) {
    if (!out.some(([name]) => name === field)) throw new Error(`deBridge field ${field} is missing`);
  }
  return out;
}

/** The deBridge request for one module call, or why it cannot be made. */
export function requestFor(call: BackendCall): ProviderRequest {
  switch (call.kind) {
    case "routes":
      return { path: "/v1.0/supported-chains-info", query: [] };
    case "swap":
      return {
        path: "/v1.0/chain/transaction",
        query: query(call.body, SWAP_FIELDS, ["chainId", "tokenIn", "tokenInAmount", "tokenOut", "tokenOutRecipient"]),
      };
    case "bridge":
      return {
        path: "/v1.0/dln/order/create-tx",
        query: query(call.body, BRIDGE_FIELDS, [
          "srcChainId",
          "srcChainTokenIn",
          "srcChainTokenInAmount",
          "dstChainId",
          "dstChainTokenOut",
          "dstChainTokenOutRecipient",
        ]),
      };
  }
}

/** deBridge's own message from an error body (`errorMessage`, else `message`), cut short. */
export function errorMessage(status: number, body: string): string {
  let detail = "";
  try {
    const parsed: unknown = JSON.parse(body);
    if (isRecord(parsed)) {
      const found = parsed.errorMessage ?? parsed.message;
      if (typeof found === "string") detail = found;
    }
  } catch {
    /* not JSON */
  }
  detail = cut(detail, ERROR_CHARS);
  return detail === "" ? `deBridge answered HTTP ${status}` : `deBridge answered HTTP ${status}: ${detail}`;
}

function baseUrl(env: Env): string {
  const value = env.TEENY_DEBRIDGE_API?.trim().replace(/\/+$/, "");
  return value ? value : DEFAULT_BASE;
}

async function fetchAnswer(env: Env, deps: Deps, request: ProviderRequest): Promise<unknown> {
  const pairs = [...request.query];
  const key = env.DEBRIDGE_API_KEY?.trim();
  const url = new URL(`${baseUrl(env)}${request.path}`);
  if (key) {
    if (url.protocol !== "https:") throw new Error("deBridge: DEBRIDGE_API_KEY is only sent over https; fix TEENY_DEBRIDGE_API or unset the key");
    trackSecret(key);
    pairs.push(["accesstoken", key]);
  }
  for (const [k, v] of pairs) url.searchParams.append(k, v);
  let response: Response;
  try {
    response = await deps.fetch(url, { headers: { "user-agent": "teeny-core" }, redirect: "error", signal: AbortSignal.timeout(TIMEOUT_MS) });
  } catch (error) {
    const timeout = error instanceof Error && (error.name === "TimeoutError" || error.name === "AbortError");
    throw new Error(`could not reach deBridge (${timeout ? "timeout" : "connect"})`);
  }
  let body: string;
  try {
    body = await response.text();
  } catch {
    throw new Error("deBridge sent a body that could not be read");
  }
  if (response.status < 200 || response.status >= 300) throw new Error(errorMessage(response.status, body));
  try {
    return JSON.parse(body);
  } catch {
    throw new Error("deBridge sent something that is not JSON");
  }
}

/** One module call, answered from deBridge. */
export async function answer(env: Env, deps: Deps, call: BackendCall): Promise<unknown> {
  return fetchAnswer(env, deps, requestFor(call));
}
