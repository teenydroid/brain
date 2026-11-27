// A thin face on node:sqlite shaped like the sqlite3 module the Python hands
// used: autocommit, `?` parameters, rows you can read by column name, and an
// OperationalError whose message says "locked" when the database is.

import { DatabaseSync, type StatementSync } from "node:sqlite";

export type Param = string | number | bigint | boolean | null | undefined | Uint8Array;
export type Row = Record<string, unknown>;

/** sqlite3.Error: every failure from the engine. Python's own name is kept in the message prefix of warnings. */
export class SqliteError extends Error {
  override name = "OperationalError";
  constructor(message: string, readonly code?: string) {
    super(message);
  }
}

function bind(params: readonly Param[]): (string | number | bigint | null | Uint8Array)[] {
  return params.map((p) => (p === undefined ? null : typeof p === "boolean" ? (p ? 1 : 0) : p));
}

export interface RunResult {
  /** cursor.rowcount: rows an INSERT, UPDATE or DELETE touched. */
  rowcount: number;
  /** cursor.lastrowid */
  lastrowid: number;
}

export class Connection {
  private statements = new Map<string, StatementSync>();

  constructor(private readonly handle: DatabaseSync) {}

  private prepare(sql: string): StatementSync {
    let statement = this.statements.get(sql);
    if (!statement) {
      statement = this.handle.prepare(sql);
      this.statements.set(sql, statement);
    }
    return statement;
  }

  private wrap<T>(fn: () => T): T {
    try {
      return fn();
    } catch (exc) {
      if (exc instanceof SqliteError) throw exc;
      const message = exc instanceof Error ? exc.message : String(exc);
      const code = (exc as { code?: string }).code;
      throw new SqliteError(message, code);
    }
  }

  /** One statement that changes things (or any statement whose rows you do not want). */
  run(sql: string, ...params: Param[]): RunResult {
    return this.wrap(() => {
      const r = this.prepare(sql).run(...bind(params));
      return { rowcount: Number(r.changes), lastrowid: Number(r.lastInsertRowid) };
    });
  }

  /** Every row the statement returns, as objects keyed by column name. */
  all(sql: string, ...params: Param[]): Row[] {
    return this.wrap(() => (this.prepare(sql).all(...bind(params)) as Row[]).map((r) => ({ ...r })));
  }

  /** The first row, or undefined (fetchone() is None). */
  get(sql: string, ...params: Param[]): Row | undefined {
    return this.wrap(() => {
      const r = this.prepare(sql).get(...bind(params)) as Row | undefined;
      return r === undefined ? undefined : { ...r };
    });
  }

  /** Several `;`-separated statements, no parameters (executescript). */
  exec(script: string): void {
    this.wrap(() => this.handle.exec(script));
  }

  /** The same statement for each parameter list (executemany). */
  runMany(sql: string, lists: readonly (readonly Param[])[]): void {
    for (const params of lists) this.run(sql, ...params);
  }

  close(): void {
    this.statements.clear();
    try {
      this.handle.close();
    } catch {
      // closing twice is not an error here, as it is not in sqlite3
    }
  }
}

export function connect(path: string, timeoutMs: number): Connection {
  try {
    return new Connection(new DatabaseSync(path, { timeout: timeoutMs }));
  } catch (exc) {
    throw new SqliteError(exc instanceof Error ? exc.message : String(exc), (exc as { code?: string }).code);
  }
}
