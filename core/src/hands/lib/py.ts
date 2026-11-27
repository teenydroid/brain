// The few Python behaviours the hands' contract leans on, so a port can say
// what the Python said: json.dumps's spacing and key order, int()'s
// forgiving parse, str.split()/strip()'s idea of whitespace and repr().

/** What Python's str.isspace() counts (JS's \s adds U+FEFF and lacks \x1c-\x1f, \x85). */
export const PY_SPACE = "\\t\\n\\v\\f\\r \\x1c-\\x1f\\x85\\xa0\\u1680\\u2000-\\u200a\\u2028\\u2029\\u202f\\u205f\\u3000";
const SPACE_RUN = new RegExp(`[${PY_SPACE}]+`, "u");
const SPACE_END = new RegExp(`[${PY_SPACE}]+$`, "u");
const SPACE_START = new RegExp(`^[${PY_SPACE}]+`, "u");

/** str.split() with no argument. */
export function pySplit(text: string): string[] {
  return text.split(SPACE_RUN).filter((part) => part !== "");
}
export function pyStrip(text: string): string {
  return text.replace(SPACE_START, "").replace(SPACE_END, "");
}
export function pyRstrip(text: string): string {
  return text.replace(SPACE_END, "");
}
export function pyLstrip(text: string): string {
  return text.replace(SPACE_START, "");
}

/**
 * int(text) as Python reads it: surrounding whitespace, one sign, digits with
 * single underscores between them. Anything else (5.0, "", "1e3") is null,
 * where Python raises ValueError.
 */
export function pyInt(text: string): number | null {
  const t = pyStrip(text);
  if (!/^[+-]?\d+(?:_\d+)*$/.test(t)) return null;
  const n = Number(t.replace(/_/g, ""));
  return Number.isSafeInteger(n) ? n : null;
}

/** float(text) as Python reads it, or null. */
export function pyFloat(text: string): number | null {
  const t = pyStrip(text).toLowerCase();
  if (/^[+-]?(?:inf|infinity)$/.test(t)) return t.startsWith("-") ? -Infinity : Infinity;
  if (t === "nan" || t === "+nan" || t === "-nan") return NaN;
  if (!/^[+-]?(?:\d+(?:_\d+)*)?(?:\.(?:\d+(?:_\d+)*)?)?(?:e[+-]?\d+(?:_\d+)*)?$/.test(t)) return null;
  if (!/\d/.test(t.split("e")[0] ?? "")) return null;
  return Number(t.replace(/_/g, ""));
}

/** repr() of a str, number, boolean or null, as the argparse messages quote them. */
export function pyRepr(value: unknown): string {
  if (value === null || value === undefined) return "None";
  if (value === true) return "True";
  if (value === false) return "False";
  if (typeof value === "number") return Number.isInteger(value) ? String(value) : String(value);
  if (typeof value === "string") {
    const quote = value.includes("'") && !value.includes('"') ? '"' : "'";
    let out = "";
    for (const ch of value) {
      if (ch === "\\") out += "\\\\";
      else if (ch === quote) out += "\\" + ch;
      else if (ch === "\n") out += "\\n";
      else if (ch === "\r") out += "\\r";
      else if (ch === "\t") out += "\\t";
      else if (ch < " " || ch === "\x7f") out += "\\x" + ch.charCodeAt(0).toString(16).padStart(2, "0");
      else out += ch;
    }
    return quote + out + quote;
  }
  return String(value);
}

export interface DumpsOptions {
  /** Python's default is true: every non-ASCII character as \uXXXX. */
  ensureAscii?: boolean;
  sortKeys?: boolean;
  /** json.dumps(default=): what to do with a value JSON cannot hold. */
  fallback?: (value: unknown) => unknown;
}

function quoteString(text: string, ensureAscii: boolean): string {
  const quoted = JSON.stringify(text);
  if (!ensureAscii) return quoted;
  // eslint-disable-next-line no-control-regex
  return quoted.replace(/[^\x00-\x7f]/g, (c) => "\\u" + c.charCodeAt(0).toString(16).padStart(4, "0"));
}

/**
 * json.dumps: ", " and ": " between items, and (with sortKeys) keys in
 * Python's code point order. A whole number prints without ".0", the one
 * difference a JS number cannot avoid; every JSON reader takes both.
 */
export function dumps(value: unknown, options: DumpsOptions = {}): string {
  const { ensureAscii = true, sortKeys = false, fallback } = options;
  const walk = (v: unknown): string => {
    if (v === null || v === undefined) return "null";
    if (typeof v === "string") return quoteString(v, ensureAscii);
    if (typeof v === "boolean") return v ? "true" : "false";
    if (typeof v === "number") {
      if (Number.isNaN(v)) return "NaN";
      if (!Number.isFinite(v)) return v > 0 ? "Infinity" : "-Infinity";
      return JSON.stringify(v);
    }
    if (typeof v === "bigint") return v.toString();
    if (Array.isArray(v)) return "[" + v.map(walk).join(", ") + "]";
    if (typeof v === "object") {
      const toJSON = (v as { toJSON?: () => unknown }).toJSON;
      if (typeof toJSON === "function") return walk(toJSON.call(v));
      let keys = Object.keys(v as object).filter((k) => (v as Record<string, unknown>)[k] !== undefined);
      if (sortKeys) keys = keys.sort(codePointOrder);
      return "{" + keys.map((k) => quoteString(k, ensureAscii) + ": " + walk((v as Record<string, unknown>)[k])).join(", ") + "}";
    }
    if (fallback) return walk(fallback(v));
    throw new TypeError(`Object of type ${typeof v} is not JSON serializable`);
  };
  return walk(value);
}

/** Python compares str by code point; JS's default compares UTF-16 units. */
export function codePointOrder(a: string, b: string): number {
  const x = Array.from(a);
  const y = Array.from(b);
  const n = Math.min(x.length, y.length);
  for (let i = 0; i < n; i++) {
    const d = (x[i] as string).codePointAt(0)! - (y[i] as string).codePointAt(0)!;
    if (d !== 0) return d;
  }
  return x.length - y.length;
}

/** time.sleep for code that must stay synchronous (the SQLite retries). */
export function sleepSync(seconds: number): void {
  Atomics.wait(new Int32Array(new SharedArrayBuffer(4)), 0, 0, Math.max(0, seconds * 1000));
}

const LINE_BREAK = new RegExp(`\\r\\n|[\\r\\n\\v\\f${"\\x1c-\\x1e"}\\x85\\u2028\\u2029]`, "u");

/** str.splitlines(): the line breaks Python knows (no trailing empty line for a final break). */
export function pySplitLines(text: string): string[] {
  const parts = text.split(LINE_BREAK);
  if (parts.length && parts[parts.length - 1] === "") parts.pop();
  return parts;
}
