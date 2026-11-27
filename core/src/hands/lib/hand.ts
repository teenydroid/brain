// The contract every hand keeps (docs/hands.md), and what they share. A port
// of hands/lib/teeny_hands/hand.py: same outcomes, same exit codes, same
// files, same messages.
//
// A hand is `hands/<name> <verb> [--flag value ...]`, with an optional JSON
// body on stdin. It prints exactly one JSON object and exits:
//
//   0  done              {"ok": true, ...}
//   2  refused by a rule {"ok": false, "refused": "<why>", ...}
//   1  error             {"ok": false, "error": "<what>"}
//
// Everything a hand needs that is not in its arguments comes from the
// checkout (cron shell jobs run with an allowlisted environment, no TEENY_*):
//
//   secrets/teeny.env   the hands' keys and switches (KEY=VALUE)
//   secrets/core.token  the core's RPC bearer
//   state/core.url      where the core answers
//   state/hands.db      every rule that spans calls is a transaction here
//   state/locks/        flock files for what must not run twice at once
//
// TEENY_ROOT moves all of it (the tests point it at a scratch directory).
//
// What differs from the Python, and cannot not: handlers are async (the
// network is), `lock` takes the function to run under it, a generic failure
// reads `Error: message` where Python said `ValueError: message`, and a
// whole-number float prints without ".0". Everything else is the same.

import { spawn } from "node:child_process";
import { mkdirSync, readFileSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { type Args, ArgumentParser, SystemExit } from "./argparse.js";
import { dumps, pyInt, pySplitLines, pyStrip, sleepSync } from "./py.js";
import { Connection, SqliteError, connect } from "./sqlite.js";

export { Connection, SqliteError };

// --- outcomes -----------------------------------------------------------------

export type Detail = Record<string, unknown>;

/** A rule said no. Exit 2, with the reason and any detail for the caller. */
export class Refused extends Error {
  override name = "Refused";
  constructor(readonly reason: string, readonly detail: Detail = {}) {
    super(reason);
  }
}

/**
 * Something went wrong that is not a rule: exit 1. `detail` is what else the
 * caller is told beside the message (a verb that did part of its work says how
 * far it got).
 */
export class HandError extends Error {
  override name = "HandError";
  constructor(message: string, readonly detail: Detail = {}) {
    super(message);
  }
}

export class CoreError extends HandError {
  override name = "CoreError";
}

// --- paths and secrets --------------------------------------------------------------

/** The checkout this code runs from (core/src/hands/lib and core/dist/hands/lib both sit four levels down), whatever TEENY_ROOT says. */
export function codeRoot(): string {
  return resolve(dirname(fileURLToPath(import.meta.url)), "..", "..", "..", "..");
}

/** The checkout the state lives in: TEENY_ROOT, else the one this code runs from. */
export function root(): string {
  return process.env.TEENY_ROOT || codeRoot();
}

export function stateDir(): string {
  const path = join(root(), "state");
  mkdirSync(path, { recursive: true });
  return path;
}

export function secretsDir(): string {
  return join(root(), "secrets");
}

let envCache: Record<string, string> | null = null;

/** Forget what secrets/teeny.env said (the tests change it between cases). */
export function resetSecrets(): void {
  envCache = null;
}

export function parseEnvFile(text: string): Record<string, string> {
  const values: Record<string, string> = {};
  for (const raw of pySplitLines(text)) {
    const line = pyStrip(raw);
    if (!line || line.startsWith("#") || !line.includes("=")) continue;
    const at = line.indexOf("=");
    let key = pyStrip(line.slice(0, at));
    if (key.startsWith("export ")) key = pyStrip(key.slice("export ".length));
    let value = pyStrip(line.slice(at + 1));
    if (value.length >= 2 && value[0] === value[value.length - 1] && (value[0] === '"' || value[0] === "'")) {
      value = value.slice(1, -1);
    }
    values[key] = value;
  }
  return values;
}

function readText(path: string): string | null {
  try {
    return readFileSync(path, "utf8");
  } catch (exc) {
    if ((exc as NodeJS.ErrnoException).code === "ENOENT") return null;
    throw exc;
  }
}

/** secrets/teeny.env, read once per process. Missing is empty, not an error. */
export function secrets(): Readonly<Record<string, string>> {
  if (envCache === null) {
    const text = readText(join(secretsDir(), "teeny.env"));
    envCache = text === null ? {} : parseEnvFile(text);
  }
  return envCache;
}

/**
 * A switch or a key from secrets/teeny.env. The process environment does not
 * override it: a job's environment is not the operator's to trust, and a
 * model that can set variables must not be able to change a rule.
 */
export function setting(name: string): string | null;
export function setting(name: string, fallback: string): string;
export function setting(name: string, fallback: string | null): string | null;
export function setting(name: string, fallback: string | null = null): string | null {
  const value = secrets()[name];
  return value !== undefined && value !== "" ? value : fallback;
}

export function require_(name: string): string {
  const value = setting(name);
  if (!value) throw new HandError(`${name} is not set in secrets/teeny.env`);
  return value;
}
export { require_ as require };

export function flag(name: string, fallback: boolean): boolean {
  const value = setting(name);
  if (value === null) return fallback;
  return ["1", "true", "yes", "on"].includes(pyStrip(value).toLowerCase());
}

/**
 * On unless secrets/teeny.env says DRY_RUN=0. The environment can only force
 * it on (DRY_RUN=1 for a rehearsal), never off.
 */
export function dryRun(): boolean {
  const forced = ["1", "true", "yes", "on"].includes(pyStrip(process.env.DRY_RUN ?? "").toLowerCase());
  return forced || flag("DRY_RUN", true);
}

// --- state --------------------------------------------------------------------------------

/** state/hands.db, autocommit with explicit transactions (`transaction`). */
export function db(): Connection {
  const conn = connect(join(stateDir(), "hands.db"), 10_000);
  conn.run("PRAGMA busy_timeout=10000");
  // Switching a fresh file to WAL needs an exclusive lock, and SQLite answers
  // "database is locked" to the losers at once rather than waiting out the
  // busy timeout. Many hands start at the same minute (cron), so retry that
  // one statement briefly instead of failing a job over it.
  for (let attempt = 0; attempt < 50; attempt++) {
    try {
      conn.run("PRAGMA journal_mode=WAL");
      break;
    } catch (exc) {
      if (!(exc instanceof SqliteError) || !exc.message.includes("locked") || attempt === 49) throw exc;
      sleepSync(0.02 * (attempt + 1));
    }
  }
  conn.run("PRAGMA foreign_keys=ON");
  return conn;
}

/**
 * state/hands.db with `schema` in place, retrying a "database is locked" from
 * another hand creating its own tables at the same moment.
 */
export function openDb(schema: string): Connection {
  for (let attempt = 0; attempt < 20; attempt++) {
    let conn: Connection | null = null;
    try {
      conn = db();
      conn.exec(schema);
      return conn;
    } catch (exc) {
      conn?.close();
      if (!(exc instanceof SqliteError) || !exc.message.includes("locked") || attempt === 19) throw exc;
      sleepSync(0.05 * (attempt + 1));
    }
  }
  throw new Error("unreachable");
}

/**
 * Adds each of `columns` (name -> declaration, nullable or with a default)
 * that `table` lacks. Another hand adding the same column at the same moment
 * is not an error.
 */
export function addColumns(conn: Connection, table: string, columns: Readonly<Record<string, string>>): void {
  const have = new Set(conn.all(`PRAGMA table_info(${table})`).map((row) => row.name));
  for (const [name, declaration] of Object.entries(columns)) {
    if (have.has(name)) continue;
    try {
      conn.run(`ALTER TABLE ${table} ADD COLUMN ${name} ${declaration}`);
    } catch (exc) {
      if (!(exc instanceof SqliteError) || !exc.message.includes("duplicate column")) throw exc;
    }
  }
}

/** A whole-number setting, clamped to [low, high]; the default when unset or unreadable, so a typo cannot lift a cap. */
export function number(name: string, fallback: number, low: number, high: number): number {
  const raw = setting(name);
  let value = fallback;
  if (raw !== null) {
    const parsed = pyInt(raw);
    if (parsed === null) return fallback;
    value = parsed;
  }
  return Math.max(low, Math.min(value, high));
}

/** The start of the UTC day `at` (default now) falls in. */
export function dayStart(at?: number): number {
  const moment = new Date((at === undefined ? now() : at) * 1000);
  return Date.UTC(moment.getUTCFullYear(), moment.getUTCMonth(), moment.getUTCDate()) / 1000;
}

/** Seconds since the epoch. TEENY_NOW pins it, for tests of time rules. */
export function now(): number {
  const pinned = process.env.TEENY_NOW;
  if (pinned) {
    const n = Number(pyStrip(pinned));
    if (Number.isNaN(n) && pyStrip(pinned).toLowerCase() !== "nan") throw new Error(`could not convert string to float: ${JSON.stringify(pinned)}`);
    return n;
  }
  return Date.now() / 1000;
}

/**
 * BEGIN IMMEDIATE: the write lock is taken up front, so a read-then-write rule
 * (a claim, a gap check) cannot interleave with another process's. Rolls back
 * if `fn` throws.
 */
export function transaction<T>(conn: Connection, fn: (conn: Connection) => T): T {
  conn.run("BEGIN IMMEDIATE");
  let out: T;
  try {
    out = fn(conn);
  } catch (exc) {
    conn.run("ROLLBACK");
    throw exc;
  }
  conn.run("COMMIT");
  return out;
}

/**
 * An exclusive flock on state/locks/<name>.lock, held while `fn` runs. Waits
 * up to `timeout` seconds, then throws HandError rather than queueing forever.
 *
 * Node has no flock(2), and the Python hands (and a mixed fleet while hands
 * are being ported) lock the same files, so the lock is taken by util-linux's
 * flock(1) in a child that holds it until we close its stdin. If we die, the
 * pipe closes and the lock goes with us.
 */
export async function lock<T>(name: string, fn: () => Promise<T> | T, timeout = 120): Promise<T> {
  const locks = join(stateDir(), "locks");
  mkdirSync(locks, { recursive: true });
  const child = spawn("flock", ["-x", "-w", String(timeout), join(locks, `${name}.lock`), "sh", "-c", "echo held; cat >/dev/null"], {
    stdio: ["pipe", "pipe", "ignore"],
  });
  child.stdin.on("error", () => {});
  const held = await new Promise<boolean>((done, fail) => {
    let out = "";
    child.stdout.on("data", (chunk: Buffer) => {
      out += chunk.toString();
      if (out.includes("held")) done(true);
    });
    child.on("error", (exc) => fail(new HandError(`could not take the ${name} lock: ${exc.message}`)));
    child.on("close", () => done(false));
  });
  if (!held) throw new HandError(`timed out waiting for the ${name} lock`);
  try {
    return await fn();
  } finally {
    const closed = new Promise<void>((done) => child.on("close", () => done()));
    child.stdin.end();
    await closed;
  }
}

// --- the core -----------------------------------------------------------------------------

export interface CoreOptions {
  timeout?: number;
}

/** One JSON-RPC call to the running core, as bin/oh makes it. */
export async function core(method: string, params: Record<string, unknown> = {}, options: CoreOptions = {}): Promise<unknown> {
  if (!method.startsWith("openhuman.")) method = `openhuman.${method}`;
  let url = process.env.OPENHUMAN_CORE_URL;
  if (!url) {
    const text = readText(join(stateDir(), "core.url"));
    url = text === null ? "http://127.0.0.1:7788" : pyStrip(text);
  }
  let token = process.env.OPENHUMAN_CORE_TOKEN;
  if (!token) {
    const text = readText(join(secretsDir(), "core.token"));
    if (text === null) throw new CoreError("no core token at secrets/core.token");
    token = pyStrip(text);
  }
  const body = dumps({ jsonrpc: "2.0", id: 1, method, params });
  let payload: Record<string, unknown>;
  let response: Response;
  try {
    response = await fetch(`${url.replace(/\/+$/, "")}/rpc`, {
      method: "POST",
      body,
      headers: { "content-type": "application/json", authorization: `Bearer ${token}` },
      signal: AbortSignal.timeout((options.timeout ?? 300) * 1000),
    });
    if (!response.ok) throw new CoreError(`${method}: core answered HTTP ${response.status}`);
    const text = await response.text();
    payload = text ? (JSON.parse(text) as Record<string, unknown>) : {};
  } catch (exc) {
    if (exc instanceof CoreError) throw exc;
    if (exc instanceof SyntaxError) throw new Error(`JSONDecodeError: ${exc.message}`);
    throw new CoreError(`${method}: core unreachable at ${url}`);
  }
  const error = payload.error;
  if (error) {
    const message = typeof error === "object" && error !== null ? ((error as Record<string, unknown>).message ?? error) : error;
    throw new CoreError(`${method}: ${typeof message === "string" ? message : dumps(message)}`);
  }
  const result = payload.result;
  if (result && typeof result === "object" && !Array.isArray(result) && "logs" in result && "result" in result) {
    return (result as Record<string, unknown>).result;
  }
  return result ?? null;
}

// --- the command line -------------------------------------------------------------------------

export type Verb = (args: Args, body: unknown) => Promise<Record<string, unknown>> | Record<string, unknown>;

/** The JSON body on stdin, or null when there is none. */
export async function readBody(): Promise<unknown> {
  if (process.stdin.isTTY) return null;
  const chunks: Buffer[] = [];
  for await (const chunk of process.stdin) chunks.push(chunk as Buffer);
  const text = Buffer.concat(chunks).toString("utf8");
  if (!pyStrip(text)) return null;
  try {
    return JSON.parse(text);
  } catch (exc) {
    throw new HandError(`stdin is not JSON: ${exc instanceof Error ? exc.message : String(exc)}`);
  }
}

export function emit(obj: Record<string, unknown>): void {
  process.stdout.write(dumps(obj, { ensureAscii: false, sortKeys: true }) + "\n");
}

/**
 * Parse, dispatch to the verb's handler (`setDefaults({handler})`), and turn
 * its outcome into the contract's JSON and exit code. Returns the code.
 */
export async function run(name: string, parser: ArgumentParser, argv: readonly string[]): Promise<number> {
  let args: Args;
  try {
    args = parser.parseArgs(argv);
  } catch (exc) {
    if (!(exc instanceof SystemExit)) throw exc;
    emit({ ok: false, error: `bad arguments to hands/${name}` });
    return exc.code ? 1 : 0;
  }
  const handler = args.handler as Verb | undefined | null;
  if (!handler) {
    parser.printUsage(process.stderr);
    emit({ ok: false, error: `hands/${name} needs a verb` });
    return 1;
  }
  try {
    const result = { ...(await handler(args, args.takes_body ? await readBody() : null)) };
    if (!("ok" in result)) result.ok = true;
    emit(result);
    return 0;
  } catch (exc) {
    if (exc instanceof Refused) {
      emit({ ok: false, refused: exc.reason, ...exc.detail });
      return 2;
    }
    if (exc instanceof HandError) {
      emit({ ok: false, error: exc.message, ...exc.detail });
      return 1;
    }
    if (process.env.TEENY_HANDS_DEBUG) console.error(exc);
    const label = exc instanceof Error ? exc.constructor.name : "Error";
    emit({ ok: false, error: `${label}: ${exc instanceof Error ? exc.message : String(exc)}` });
    return 1;
  }
}

/**
 * The last line of a hand's cli.ts: `await main("hn", parser(), process.argv.slice(2))`.
 * Runs, lets stdout drain, and exits with the contract's code (open sockets
 * and caches never hold a finished hand up).
 */
export async function main(name: string, parser: ArgumentParser, argv: readonly string[]): Promise<never> {
  const code = await run(name, parser, argv);
  await new Promise<void>((done) => process.stdout.write("", () => done()));
  process.exit(code);
}
