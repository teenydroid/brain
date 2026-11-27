import { randomBytes } from "node:crypto";
import { chmodSync, closeSync, existsSync, fsyncSync, linkSync, mkdirSync, openSync, readFileSync, renameSync, rmSync, statSync, writeSync } from "node:fs";
import { basename, dirname, join } from "node:path";

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
    // writeSync may take only part of the buffer; never rename a short file over good state.
    const bytes = Buffer.from(data, "utf8");
    let written = 0;
    while (written < bytes.length) {
      const n = writeSync(fd, bytes, written, bytes.length - written);
      if (n <= 0) throw new Error("short write to the wallet state file");
      written += n;
    }
    fsyncSync(fd);
  } catch (error) {
    try {
      closeSync(fd);
    } catch {
      /* already closed */
    }
    rmSync(temp, { force: true });
    throw error;
  }
  closeSync(fd);
  try {
    renameSync(temp, file);
  } catch (error) {
    rmSync(temp, { force: true });
    throw error;
  }
  chmodSync(file, 0o600);
  syncDir(dir);
}

/** Makes a rename or create in DIR durable (fsync of the directory itself). */
function syncDir(dir: string): void {
  let fd: number | undefined;
  try {
    fd = openSync(dir, "r");
    fsyncSync(fd);
  } catch {
    /* some filesystems refuse a directory fsync; the file itself is already synced */
  } finally {
    if (fd !== undefined) closeSync(fd);
  }
}

export function readJsonFile<T>(file: string): T | undefined {
  if (!existsSync(file)) return undefined;
  const text = readFileSync(file, "utf8");
  // A file that exists but holds nothing, or not an object, is damage: reading it as
  // "no history" would let a cap forget what was spent and then overwrite the file.
  if (text.trim() === "") throw new Error(`${basename(file)} is empty; restore it before the wallet can use it`);
  const parsed: unknown = JSON.parse(text);
  if (typeof parsed !== "object" || parsed === null || Array.isArray(parsed)) {
    throw new Error(`${basename(file)} is not a JSON object; restore it before the wallet can use it`);
  }
  return parsed as T;
}

/**
 * A cross-process advisory lock: an exclusively created sidecar file holding a
 * unique token. Retries for a few seconds; a lock older than 60s is taken as
 * stale. A stale lock is reclaimed by moving it aside (an atomic rename) and
 * checking that what was moved is the lock that was judged stale; a lock that
 * turns out to be someone's fresh one is linked straight back. The holder
 * releases only a lock that still carries its own token.
 */
export async function withFileLock<T>(file: string, body: () => Promise<T> | T): Promise<T> {
  const lock = `${file}.lock`;
  mkdirSync(dirname(lock), { recursive: true, mode: 0o700 });
  const token = randomBytes(16).toString("hex");
  const deadline = Date.now() + 10_000;
  for (;;) {
    try {
      const fd = openSync(lock, "wx", 0o600);
      try {
        writeSync(fd, token);
      } finally {
        closeSync(fd);
      }
      break;
    } catch (error) {
      if ((error as NodeJS.ErrnoException).code !== "EEXIST") throw error;
      reclaimIfStale(lock);
      if (Date.now() > deadline) throw new Error("could not take the wallet lock");
      await new Promise((resolve) => setTimeout(resolve, 20));
    }
  }
  try {
    return await body();
  } finally {
    try {
      if (readFileSync(lock, "utf8") === token) rmSync(lock, { force: true });
    } catch {
      /* already gone */
    }
  }
}

function reclaimIfStale(lock: string): void {
  try {
    const seen = statSync(lock);
    if (Date.now() - seen.mtimeMs <= 60_000) return;
    const aside = `${lock}.${randomBytes(6).toString("hex")}.stale`;
    renameSync(lock, aside);
    try {
      const moved = statSync(aside);
      if (moved.ino === seen.ino && moved.dev === seen.dev) return; // the stale lock itself: drop it
      // We moved a different (fresh) lock: put it back unless somebody has already taken the name.
      try {
        linkSync(aside, lock);
      } catch {
        /* the name is taken again; that holder owns it now */
      }
    } finally {
      rmSync(aside, { force: true });
    }
  } catch {
    /* raced with a release */
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
