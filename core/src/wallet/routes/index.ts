// The router: picks the provider per call, as spine/src/wallet_backend.rs did.
//
// hands/wallet (convert.py) names the provider by tagging the one free-text
// field each body carries, the field that holds no address or amount:
//
//   Call    Field                     Tagged           Forwarded
//   swap    slippage                  lifi:1           1
//   bridge  dstChainTokenOutAmount    debridge:auto    auto
//
// The tag comes off before the provider sees the body. An unknown tag is
// refused. An untagged call (buy-tiny, the module's own tools) and the routes
// list go to deBridge as they always have, since those callers read
// deBridge's shape. A call routed to the wrong provider cannot slip through:
// hands/wallet refuses an answer that is not the provider's own shape (a
// LI.FI answer says `"provider": "lifi"`).
import type { Deps } from "../endpoints.js";
import * as debridge from "./debridge.js";
import * as lifi from "./lifi.js";
import type { BackendCall, Env } from "./types.js";

export type Provider = "lifi" | "debridge";

export function route(call: BackendCall): { provider: Provider; call: BackendCall } {
  if (call.kind === "routes") return { provider: "debridge", call };
  const field = call.kind === "swap" ? "slippage" : "dstChainTokenOutAmount";
  const value = call.body[field];
  const colon = typeof value === "string" ? value.indexOf(":") : -1;
  if (typeof value !== "string" || colon < 0) return { provider: "debridge", call };
  const tag = value.slice(0, colon);
  const rest = value.slice(colon + 1);
  let provider: Provider;
  if (tag === "lifi") provider = "lifi";
  else if (tag === "debridge") provider = "debridge";
  else throw new Error(`no swap or bridge provider is called ${JSON.stringify(tag)}`);
  return { provider, call: { kind: call.kind, body: { ...call.body, [field]: rest } } };
}

/** One module call, answered by whichever provider it names. */
export async function backendAnswer(env: Env, deps: Deps, call: BackendCall): Promise<unknown> {
  const routed = route(call);
  return routed.provider === "lifi" ? lifi.answer(env, deps, routed.call) : debridge.answer(env, deps, routed.call);
}

export { debridge, lifi };
export type { BackendCall };
