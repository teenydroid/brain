// The RPC contract: what the Python and bash callers send and read, recorded in
// test/contract/<method>.json from their call sites. Later phases test the TS
// core against it with assertMatchesContract.
import { readdirSync, readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

export interface FieldSpec {
  type: string;
  required: boolean;
  note?: string;
}

export interface ShapeSpec {
  type: string;
  fields?: Record<string, FieldSpec>;
  items?: ShapeSpec;
  reader?: string;
  example?: unknown;
  result_item?: Record<string, FieldSpec>;
}

export interface Contract {
  method: string;
  call_sites: string[];
  params: { fields: Record<string, FieldSpec>; example: Record<string, unknown> };
  response: ShapeSpec;
  timeout?: string;
  notes?: string;
  errors?: string[];
}

export const CONTRACT_DIR = join(dirname(fileURLToPath(import.meta.url)), "contract");

export function methodNames(): string[] {
  return readdirSync(CONTRACT_DIR)
    .filter((f) => f.endsWith(".json") && !f.startsWith("_"))
    .map((f) => f.slice(0, -".json".length))
    .sort();
}

export function loadContract(method: string): Contract {
  const name = method.replace(/^openhuman\./, "");
  return JSON.parse(readFileSync(join(CONTRACT_DIR, `${name}.json`), "utf8")) as Contract;
}

export function typeOf(value: unknown): string {
  if (value === null) return "null";
  if (Array.isArray(value)) return "array";
  return typeof value;
}

function typeOk(spec: string, value: unknown): boolean {
  if (spec === "any") return true;
  return spec.split("|").includes(typeOf(value));
}

function check(path: string, spec: ShapeSpec, value: unknown, errors: string[]): void {
  if (spec.type !== "any" && !typeOk(spec.type, value)) {
    errors.push(`${path}: expected ${spec.type}, got ${typeOf(value)}`);
    return;
  }
  if (spec.fields && typeOf(value) === "object") {
    const obj = value as Record<string, unknown>;
    for (const [key, f] of Object.entries(spec.fields)) {
      if (!(key in obj) || obj[key] === undefined) {
        if (f.required) errors.push(`${path}.${key}: required, missing`);
        continue;
      }
      if (!typeOk(f.type, obj[key])) errors.push(`${path}.${key}: expected ${f.type}, got ${typeOf(obj[key])}`);
    }
  }
  if (spec.items && Array.isArray(value)) {
    value.forEach((item, i) => check(`${path}[${i}]`, spec.items as ShapeSpec, item, errors));
  }
}

/** Problems with RESPONSE against the contract of METHOD; empty when it matches. */
export function contractErrors(method: string, response: unknown): string[] {
  const contract = loadContract(method);
  const errors: string[] = [];
  // A {logs, result} envelope is unwrapped by every client, so it is accepted here too.
  let body = response;
  if (typeOf(body) === "object") {
    const o = body as Record<string, unknown>;
    if ("logs" in o && "result" in o) body = o.result;
  }
  check("response", contract.response, body, errors);
  return errors;
}

/** Throws unless RESPONSE has the shape the callers of METHOD read. */
export function assertMatchesContract(method: string, response: unknown): void {
  const errors = contractErrors(method, response);
  if (errors.length > 0) {
    throw new Error(`${method} does not match its contract:\n  ${errors.join("\n  ")}`);
  }
}

/** Problems with PARAMS against the contract's params: required keys present, types right. */
export function paramsErrors(method: string, params: unknown): string[] {
  const contract = loadContract(method);
  const errors: string[] = [];
  check("params", { type: "object", fields: contract.params.fields }, params, errors);
  return errors;
}

export function assertParamsMatchContract(method: string, params: unknown): void {
  const errors = paramsErrors(method, params);
  if (errors.length > 0) throw new Error(`${method} params do not match: ${errors.join("; ")}`);
}
