// A read-through cache in state/hands.db, for the hands that came after
// hands/gh (which keeps its own `gh_cache`): one table per hand, keyed on a
// digest of the kind, the arguments and the hand's key (another key can see
// other things). A store that cannot be read or written is a miss, never a
// failed read; an error is never cached; writes never come near it.
// A port of hands/lib/teeny_hands/readcache.py; the keys it makes are the
// same bytes, so a cache the Python filled is a cache the port can read.

import { createHash } from "node:crypto";
import * as hand from "./hand.js";
import { SqliteError } from "./sqlite.js";
import { dumps } from "./py.js";

const MISS = Symbol("miss");
const TABLE = /^[a-z][a-z0-9_]*_cache$/;

const sha256 = (text: string): string => createHash("sha256").update(text, "utf8").digest("hex");

export class ReadCache {
  readonly schema: string;
  private readonly ttl: Record<string, number>;

  /** `table` (`<hand>_cache`), the secret whose digest scopes it, the TTL of each kind in seconds, and the setting that turns it off. */
  constructor(readonly table: string, readonly keySetting: string, ttl: Readonly<Record<string, number>>, readonly enabledSetting: string) {
    if (!TABLE.test(table)) throw new Error(`bad cache table name '${table}'`);
    this.ttl = { ...ttl };
    this.schema =
      `CREATE TABLE IF NOT EXISTS ${table} (key TEXT PRIMARY KEY, kind TEXT NOT NULL,` +
      " value TEXT NOT NULL, expires_at REAL NOT NULL);";
  }

  private key(kind: string, parts: readonly unknown[]): string {
    const scope = sha256(hand.setting(this.keySetting) ?? "").slice(0, 16);
    const raw = dumps([kind, scope, ...parts], { fallback: String });
    return `${kind}:${sha256(raw).slice(0, 32)}`;
  }

  private warn(message: string): void {
    process.stderr.write(`hands/${this.table.slice(0, -"_cache".length)}: ${message}\n`);
  }

  private read(key: string): unknown {
    try {
      const conn = hand.openDb(this.schema);
      try {
        const row = conn.get(`SELECT value FROM ${this.table} WHERE key = ? AND expires_at > ?`, key, hand.now());
        return row === undefined ? MISS : JSON.parse(row.value as string);
      } finally {
        conn.close();
      }
    } catch (exc) {
      if (exc instanceof SqliteError || exc instanceof SyntaxError) {
        this.warn(`cache unreadable (${exc instanceof SyntaxError ? "JSONDecodeError" : (exc as Error).name}); asking the service`);
        return MISS;
      }
      throw exc;
    }
  }

  private write(key: string, kind: string, value: unknown): void {
    try {
      const conn = hand.openDb(this.schema);
      try {
        const at = hand.now();
        conn.run(
          `INSERT OR REPLACE INTO ${this.table} (key, kind, value, expires_at) VALUES (?, ?, ?, ?)`,
          key, kind, dumps(value, { ensureAscii: false }), at + (this.ttl[kind] as number),
        );
        conn.run(`DELETE FROM ${this.table} WHERE expires_at <= ?`, at);
      } finally {
        conn.close();
      }
    } catch (exc) {
      if (exc instanceof SqliteError) {
        this.warn(`could not cache (${exc.name})`);
        return;
      }
      throw exc;
    }
  }

  /** `fetch()`'s answer, from the store while it is fresh. */
  async cached<T>(kind: string, parts: readonly unknown[], fetch: () => Promise<T> | T): Promise<T> {
    if (!hand.flag(this.enabledSetting, true)) return fetch();
    const key = this.key(kind, parts);
    const hit = this.read(key);
    if (hit !== MISS) return hit as T;
    const value = await fetch();
    this.write(key, kind, value);
    return value;
  }
}
