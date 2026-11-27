// A JSON-over-HTTPS client with a bearer key, for the hands whose service is
// a plain REST API (hands/moltbook, hands/mail, hands/hn, hands/chan ...).
// A port of hands/lib/teeny_hands/rest.py, with the same promises:
//
// - Nothing that leaves it names a URL, a header or the key. An error says
//   which status the service answered and the service's own short message,
//   because a URL can carry a query the model wrote and a header carries the
//   key. fetch's errors are not chained: their message names the URL.
// - It never follows a redirect (`redirect: "manual"`): the Authorization
//   header would go wherever a 3xx points, so a redirect is an error naming
//   its status only.
// - A 429 is a RestError with `retryAfter` (seconds) read from the
//   Retry-After header or the body; the hand turns it into a refusal with the
//   wait, and never retries a write itself.
// - A service that authenticates some other way calls with `auth: false` and
//   its own `headers`; `exchange` also hands back the response headers. Neither
//   ever reaches a message.

import { HandError } from "./hand.js";

export const TIMEOUT_SECONDS = 30;
const MESSAGE_CHARS = 200;

/**
 * A failed call. `status` is the HTTP status, or null when the service was not
 * reached or answered something unreadable; `body` is its parsed JSON error
 * body when it sent one (never echoed whole to a caller).
 */
export class RestError extends HandError {
  override name = "RestError";
  constructor(
    message: string,
    readonly status: number | null = null,
    readonly retryAfter: number | null = null,
    readonly body: unknown = null,
  ) {
    super(message);
  }
}

/** An answer longer than the caller's `limit`; the rest was not read. */
export class TooLarge extends RestError {
  override name = "TooLarge";
}

export type Query = ReadonlyArray<readonly [string, string]>;

export interface CallOptions {
  query?: Query;
  body?: unknown;
  /** False sends no key at all (Moltbook's registration). */
  auth?: boolean;
  headers?: Readonly<Record<string, string>>;
}

export interface RequestOptions extends CallOptions {
  limit?: number | null;
}

function parse(body: Uint8Array): unknown {
  try {
    return JSON.parse(new TextDecoder("utf-8", { fatal: true }).decode(body));
  } catch {
    return undefined;
  }
}

function isMapping(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

/** The service's own error text, clipped. The KEY is cut out first: a key echoed across the clip would leave a piece no later scrub matches. */
function message(parsed: unknown, key: string | null): string | null {
  if (!isMapping(parsed)) return null;
  for (const field of ["error", "message", "detail"]) {
    const value = parsed[field];
    if (typeof value === "string" && value.trim()) {
      let text = value.trim();
      if (key) text = text.split(key).join("[the key]");
      return Array.from(text).slice(0, MESSAGE_CHARS).join("");
    }
  }
  return null;
}

function retryAfter(headers: Headers | null, parsed: unknown): number | null {
  const raw = headers?.get("retry-after");
  if (raw) {
    const n = Number(raw.trim());
    if (raw.trim() !== "" && !Number.isNaN(n)) return Math.max(0, n);
  }
  if (isMapping(parsed)) {
    for (const [key, scale] of [["retry_after_seconds", 1], ["retry_after_minutes", 60]] as const) {
      const value = parsed[key];
      if (typeof value === "number") return Math.max(0, value * scale);
    }
  }
  return null;
}

/** The Python exception name urllib's reason would have had, so a message reads the same. */
function reasonName(exc: unknown): string {
  const code = (exc as { cause?: { code?: string } }).cause?.code;
  if (exc instanceof DOMException && (exc.name === "TimeoutError" || exc.name === "AbortError")) return "TimeoutError";
  switch (code) {
    case "ECONNREFUSED": return "ConnectionRefusedError";
    case "ECONNRESET": return "ConnectionResetError";
    case "ENOTFOUND":
    case "EAI_AGAIN": return "gaierror";
    case "ETIMEDOUT":
    case "UND_ERR_CONNECT_TIMEOUT":
    case "UND_ERR_HEADERS_TIMEOUT":
    case "UND_ERR_BODY_TIMEOUT": return "TimeoutError";
    case "EHOSTUNREACH": return "OSError";
    case "ERR_INVALID_URL": return "ValueError";
    default: return exc instanceof TypeError && /invalid url/i.test(exc.message) ? "ValueError" : "OSError";
  }
}

async function readBounded(response: Response, limit: number | null): Promise<Uint8Array | "toolarge"> {
  if (!response.body) return new Uint8Array();
  const reader = response.body.getReader();
  const parts: Uint8Array[] = [];
  let size = 0;
  try {
    for (;;) {
      const { done, value } = await reader.read();
      if (done) break;
      parts.push(value);
      size += value.length;
      // With a LIMIT, one byte more than it at most is ever read.
      if (limit !== null && size > limit) {
        await reader.cancel();
        return "toolarge";
      }
    }
  } finally {
    reader.releaseLock();
  }
  const out = new Uint8Array(size);
  let at = 0;
  for (const part of parts) {
    out.set(part, at);
    at += part.length;
  }
  return out;
}

/** One path segment, so nothing a caller passes can add a segment. */
export function quote(part: unknown): string {
  return encodeURIComponent(String(part)).replace(/[!'()*]/g, (c) => "%" + c.charCodeAt(0).toString(16).toUpperCase());
}

/** urllib.parse.urlencode: quote_plus on keys and values. */
export function urlencode(query: Query): string {
  const plus = (s: string): string => quote(s).replace(/%20/g, "+").replace(/~/g, "~");
  return query.map(([k, v]) => `${plus(k)}=${plus(v)}`).join("&");
}

/** One service: `name` for messages, `base` (no trailing slash), the key. */
export class RestApi {
  private readonly base: string;

  constructor(readonly name: string, base: string, private readonly key: string | null, private readonly agent: string) {
    this.base = base.replace(/\/+$/, "");
  }

  toString(): string {
    return `RestApi(${this.name})`;
  }

  /** One request; its JSON (an empty answer is {}). */
  async call(method: string, path: string, options: CallOptions = {}): Promise<unknown> {
    return (await this.request(method, path, options))[1];
  }

  /** `call`, and the response's headers. */
  async exchange(method: string, path: string, options: CallOptions = {}): Promise<[unknown, Headers]> {
    const [, parsed, headers] = await this.request(method, path, options);
    return [parsed, headers];
  }

  /** `exchange`, with the status the service answered first (a 2xx: anything else is a RestError). */
  async request(method: string, path: string, options: RequestOptions = {}): Promise<[number, unknown, Headers]> {
    const { query = [], body, auth = true, headers, limit = null } = options;
    let url = this.base + path;
    if (query.length) url += "?" + urlencode(query);
    const sent: Record<string, string> = { accept: "application/json", "user-agent": this.agent };
    for (const [name, value] of Object.entries(headers ?? {})) sent[name.toLowerCase()] = value;
    if (auth) {
      if (!this.key) throw new HandError(`${this.name}: no API key`);
      sent.authorization = `Bearer ${this.key}`;
    }
    let data: string | undefined;
    if (body !== undefined && body !== null) {
      data = JSON.stringify(body);
      sent["content-type"] = "application/json";
    }
    let response: Response;
    let bytes: Uint8Array | "toolarge";
    try {
      response = await fetch(url, { method, headers: sent, body: data, redirect: "manual", signal: AbortSignal.timeout(TIMEOUT_SECONDS * 1000) });
      bytes = await readBounded(response, limit);
    } catch (exc) {
      throw new RestError(`could not reach ${this.name} (${reasonName(exc)})`);
    }
    if (bytes === "toolarge") throw new TooLarge(`${this.name} answered more than ${limit} bytes`, response.status);
    const status = response.status;
    if (status >= 300) {
      const parsed = parse(bytes);
      if (status < 400) throw new RestError(`${this.name} answered a redirect (${status}); not followed with the key`, status);
      const text = message(parsed === undefined ? null : parsed, this.key);
      throw new RestError(
        text ? `${this.name} answered ${status}: ${text}` : `${this.name} answered ${status}`,
        status,
        status === 429 ? retryAfter(response.headers, parsed) : null,
        parsed === undefined ? null : parsed,
      );
    }
    if (bytes.length === 0) return [status, {}, response.headers];
    const parsed = parse(bytes);
    // JSON's own null is an answer (Hacker News says "no such item" so).
    if (parsed === undefined || (parsed === null && new TextDecoder().decode(bytes).trim() !== "null")) {
      throw new RestError(`${this.name} sent something unreadable`);
    }
    return [status, parsed, response.headers];
  }
}
