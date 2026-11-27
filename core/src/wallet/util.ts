import { randomBytes } from "node:crypto";
import { chmodSync, closeSync, existsSync, fsyncSync, mkdirSync, openSync, readFileSync, renameSync, rmSync, statSync, writeSync } from "node:fs";
import { dirname, join } from "node:path";

export const DAY_MS = 24 * 60 * 60 * 1000;
export const WEEK_MS = 7 * DAY_MS;

export function str(params: Record<string, unknown>, key: string): string | undefined {
  const value = params[key];
  return typeof value === "string" ? value : undefined;
}

export function requireStr(params: Record<string, unknown>, key: string): string {
  const value = str(params, key);
  if (value === undefined || value.trim() === "") throw new Error(`invalid params: ${key} is required`);
  return value;
}

export function requireConfirmed(params: Record<string, unknown>): void {
  if (params.confirmed !== true) {
    throw new Error("invalid params: confirmed must be true");
  }
}

/** A non-negative integer from a number or decimal string, as bigint; undefined when it is not one. */
export function bigintOf(value: unknown): bigint | undefined {
  if (typeof value === "number") return Number.isSafeInteger(value) && value >= 0 ? BigInt(value) : undefined;
  if (typeof value === "string" && /^\d+$/.test(value.trim())) return BigInt(value.trim());
  return undefined;
}

export function hexToBytes(hex: string): Uint8Array {
  const text = hex.startsWith("0x") || hex.startsWith("0X") ? hex.slice(2) : hex;
  if (text.length % 2 !== 0 || !/^[0-9a-fA-F]*$/.test(text)) throw new Error("not hex");
  return new Uint8Array(Buffer.from(text, "hex"));
}

export function bytesToHex(bytes: Uint8Array): string {
  return Buffer.from(bytes).toString("hex");
}

export function nowMs(): number {
  return Date.now();
}

export function newId(prefix = ""): string {
  const b = randomBytes(16);
  b[6] = (b[6]! & 0x0f) | 0x40;
  b[8] = (b[8]! & 0x3f) | 0x80;
  const h = b.toString("hex");
  return `${prefix}${h.slice(0, 8)}-${h.slice(8, 12)}-${h.slice(12, 16)}-${h.slice(16, 20)}-${h.slice(20)}`;
}

/** Writes FILE atomically (unique temp, fsync, rename) with mode 0600, creating its directory 0700. */
export function writeFileAtomic(file: string, data: string): void {
  const dir = dirname(file);
  mkdirSync(dir, { recursive: true, mode: 0o700 });
  const temp = join(dir, `.${randomBytes(6).toString("hex")}.tmp`);
  const fd = openSync(temp, "w", 0o600);
  try {
    writeSync(fd, data);
    fsyncSync(fd);
  } finally {
    closeSync(fd);
  }
  renameSync(temp, file);
  chmodSync(file, 0o600);
}

export function readJsonFile<T>(file: string): T | undefined {
  if (!existsSync(file)) return undefined;
  const text = readFileSync(file, "utf8");
  if (text.trim() === "") return undefined;
  return JSON.parse(text) as T;
}

/**
 * A cross-process advisory lock: an exclusively created sidecar file.
 * Retries for a few seconds; a lock older than 60s is taken as stale.
 */
export async function withFileLock<T>(file: string, body: () => Promise<T> | T): Promise<T> {
  const lock = `${file}.lock`;
  mkdirSync(dirname(lock), { recursive: true, mode: 0o700 });
  const deadline = Date.now() + 10_000;
  for (;;) {
    try {
      const fd = openSync(lock, "wx", 0o600);
      closeSync(fd);
      break;
    } catch (error) {
      if ((error as NodeJS.ErrnoException).code !== "EEXIST") throw error;
      try {
        if (Date.now() - statSync(lock).mtimeMs > 60_000) rmSync(lock, { force: true });
      } catch {
        /* raced with a release */
      }
      if (Date.now() > deadline) throw new Error("could not take the wallet lock");
      await new Promise((resolve) => setTimeout(resolve, 20));
    }
  }
  try {
    return await body();
  } finally {
    rmSync(lock, { force: true });
  }
}

/** An in-process mutex keyed by name. */
const chains = new Map<string, Promise<unknown>>();
export async function withMutex<T>(name: string, body: () => Promise<T>): Promise<T> {
  const previous = chains.get(name) ?? Promise.resolve();
  const run = previous.then(body, body);
  chains.set(name, run.catch(() => undefined));
  return run;
}
