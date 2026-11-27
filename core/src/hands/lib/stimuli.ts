// The one inbox Teeny's mind reads (docs/architecture.md, "One mind"). A port
// of hands/lib/teeny_hands/stimuli.py: every surface writes what happened here
// instead of each running its own scoped, amnesiac drafting turn. `bin/tick`
// drains a batch into the `teeny:mind` thread.
//
// A stimulus is unique on (surface, kind, ref): adding the same event twice is
// a no-op, so a surface can re-scan freely.
//
//   pending  waiting for a tick
//   taken    in a tick's batch (`tick` names it); put back by `release` when
//            the tick fails, or by the next `take` once it has been out an hour
//   done     the tick that read it settled
//   filed    for another reader, never the mind's (surface `studio`)
//
// Follow-ups are Teeny's own reminders, kept in `follow_ups` until they come
// due, then raised as a `self` stimulus: pending, raised, dropped, resolved.
//
// Text is stored capped and never trusted: it is what strangers wrote, read by
// a turn that has no acting tools.

import { randomBytes } from "node:crypto";
import * as hand from "./hand.js";
import type { Connection, Row } from "./sqlite.js";
import { dumps, pyRstrip, pyStrip } from "./py.js";

type Json = Record<string, unknown>;

export const SURFACES = [
  "x", "moltbook", "reddit", "gh", "mail", "telegram", "builder", "operator", "nudge", "hand", "self", "world", "health", "wallet", "hn",
  "host", "studio", "rent",
] as const;
/** `filed`: a stimulus for another reader than the mind (FILED_SURFACES), never taken into a batch. */
export const STATUSES = ["pending", "taken", "done", "filed"] as const;
/** Surfaces the mind writes and something else reads. */
export const FILED_SURFACES: readonly string[] = ["studio"];
/** One stimulus's text, beyond which it is cut. */
export const MAX_TEXT_CHARS = 4000;
/** A nudge's text: a habit's prompt and what its playbook's inputs read. */
export const NUDGE_TEXT_CHARS = 16000;
/** A batch out this long belonged to a tick that died. */
export const TAKEN_TIMEOUT_SECS = 3600;
/** How far ahead a follow-up may be set. */
export const MAX_FOLLOW_UP_SECS = 90 * 86400;
/** Surfaces a follow-up's ref may be prefixed with ("x:111" is "111"). */
const REF_SURFACES = ["x", "moltbook", "reddit", "mail", "telegram", "gh", "hn"];
/** Taken ahead of anything older: what a hand did with the mind's last answer. */
export const FIRST: readonly string[] = ["hand"];

export const SCHEMA = `
CREATE TABLE IF NOT EXISTS stimuli (
    id         TEXT PRIMARY KEY,
    surface    TEXT NOT NULL,
    kind       TEXT NOT NULL,
    ref        TEXT NOT NULL,
    who        TEXT,
    text       TEXT NOT NULL,
    payload    TEXT NOT NULL DEFAULT '{}',
    at         REAL NOT NULL,
    status     TEXT NOT NULL,
    tick       TEXT,
    taken_at   REAL,
    updated_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS stimuli_status ON stimuli (status, at);
CREATE TABLE IF NOT EXISTS follow_ups (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    due_at     REAL NOT NULL,
    about      TEXT NOT NULL,
    ref        TEXT,
    status     TEXT NOT NULL,
    created_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS follow_ups_due ON follow_ups (status, due_at);
CREATE TABLE IF NOT EXISTS mind_notes (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    note       TEXT NOT NULL,
    who        TEXT,
    tick       TEXT,
    at         REAL NOT NULL
);
`;

export function db(): Connection {
  return hand.openDb(SCHEMA);
}

/** A connection for one call, closed after it (Python left them to its GC). */
function using<T>(fn: (conn: Connection) => T): T {
  const conn = db();
  try {
    return fn(conn);
  } finally {
    conn.close();
  }
}

/** Whether surfaces feed this inbox (TEENY_INBOX=mind, the default) or the core's inbound queue. */
export function toMind(): boolean {
  return pyStrip(hand.setting("TEENY_INBOX", "mind")).toLowerCase() !== "core";
}

function clip(text: string, limit: number = MAX_TEXT_CHARS): string {
  const t = pyStrip(text);
  const chars = Array.from(t);
  return chars.length <= limit ? t : pyRstrip(chars.slice(0, limit).join("")) + " [...cut]";
}

function row(r: Row): Json {
  const out: Json = {};
  for (const k of ["id", "surface", "kind", "ref", "who", "text", "at"]) out[k] = r[k];
  out.payload = JSON.parse((r.payload as string) || "{}");
  return out;
}

/** Adds one event; false when it was already there. */
export function add(surface: string, kind: string, ref: string, text: string, who: string | null = null, payload: Json | null = null, at: number | null = null): boolean {
  if (!(SURFACES as readonly string[]).includes(surface)) throw new hand.HandError(`surface is one of ${SURFACES.join(", ")}`);
  for (const [name, value] of [["kind", kind], ["ref", ref]] as const) {
    if (typeof value !== "string" || !pyStrip(value)) throw new hand.HandError(`a stimulus needs a ${name}`);
  }
  if (typeof text !== "string" || !pyStrip(text)) throw new hand.HandError("a stimulus needs text");
  const now = hand.now();
  return using((conn) =>
    hand.transaction(conn, () => {
      const cur = conn.run(
        "INSERT OR IGNORE INTO stimuli (id, surface, kind, ref, who, text, payload, at, status, updated_at) VALUES (?,?,?,?,?,?,?,?,?, ?)",
        `${surface}:${kind}:${ref}`, surface, pyStrip(kind), pyStrip(ref), who,
        clip(text, surface === "nudge" ? NUDGE_TEXT_CHARS : MAX_TEXT_CHARS),
        dumps({ ...(payload ?? {}) }, { ensureAscii: false }), at ?? now,
        FILED_SURFACES.includes(surface) ? "filed" : "pending", now,
      );
      return cur.rowcount === 1;
    }),
  );
}

/**
 * Puts a settled stimulus back to pending, in its old place (its `at`), with
 * new details. False when it is not there or not settled.
 */
export function again(surface: string, kind: string, ref: string, payload: Json): boolean {
  const now = hand.now();
  return using((conn) =>
    hand.transaction(conn, () => {
      const cur = conn.run(
        "UPDATE stimuli SET status = 'pending', tick = NULL, taken_at = NULL, payload = ?, updated_at = ? WHERE id = ? AND status = 'done'",
        dumps({ ...payload }, { ensureAscii: false }), now, `${surface}:${kind}:${ref}`,
      );
      return cur.rowcount === 1;
    }),
  );
}

/**
 * The oldest pending stimuli, up to `maxItems` and about `maxChars` of text
 * (always at least one), marked taken under a new tick id. The surfaces in
 * FIRST come before the rest. Those of a (surface, kind) in `leave` stay
 * pending in their place, for a later take.
 */
export function take(maxItems = 20, maxChars = 30_000, leave: Iterable<readonly [string, string]> = []): Json {
  const left = new Set([...leave].map(([s, k]) => `${s}\u0000${k}`));
  const now = hand.now();
  const tick = randomBytes(6).toString("hex");
  return using((conn) =>
    hand.transaction(conn, () => {
      conn.run(
        "UPDATE stimuli SET status = 'pending', tick = NULL, taken_at = NULL, updated_at = ? WHERE status = 'taken' AND taken_at < ?",
        now, now - TAKEN_TIMEOUT_SECS,
      );
      const rows = conn
        .all(
          `SELECT * FROM stimuli WHERE status = 'pending' ORDER BY surface IN (${FIRST.map(() => "?").join(",")}) DESC, at, rowid`,
          ...FIRST,
        )
        .filter((r) => !left.has(`${r.surface}\u0000${r.kind}`))
        .slice(0, maxItems);
      const picked: Row[] = [];
      let used = 0;
      for (const r of rows) {
        const size = Array.from(r.text as string).length;
        if (picked.length && used + size > maxChars) break;
        picked.push(r);
        used += size;
      }
      conn.runMany("UPDATE stimuli SET status = 'taken', tick = ?, taken_at = ?, updated_at = ? WHERE id = ?", picked.map((r) => [tick, now, now, r.id as string]));
      return { tick: picked.length ? tick : null, stimuli: picked.map(row) };
    }),
  );
}

function finish(tick: string, status: "done" | "pending"): number {
  if (typeof tick !== "string" || !tick) throw new hand.HandError("needs a tick id");
  const now = hand.now();
  return using((conn) =>
    hand.transaction(conn, () => {
      const cur =
        status === "done"
          ? conn.run("UPDATE stimuli SET status = 'done', updated_at = ? WHERE status = 'taken' AND tick = ?", now, tick)
          : conn.run(
              "UPDATE stimuli SET status = 'pending', tick = NULL, taken_at = NULL, updated_at = ? WHERE status = 'taken' AND tick = ?",
              now, tick,
            );
      return cur.rowcount;
    }),
  );
}

/** The tick read its batch: done for good. */
export const settle = (tick: string): number => finish(tick, "done");
/** The tick failed: its batch goes back to pending, in its old place. */
export const release = (tick: string): number => finish(tick, "pending");

/** What is waiting, oldest first, without taking it. */
export function pending(maxItems = 200): Json[] {
  return using((conn) => conn.all("SELECT * FROM stimuli WHERE status = 'pending' ORDER BY at, rowid LIMIT ?", maxItems).map(row));
}

export function counts(): Record<string, number> {
  return using((conn) => {
    const got = new Map(conn.all("SELECT status, COUNT(*) AS n FROM stimuli GROUP BY status").map((r) => [r.status as string, Number(r.n)]));
    return Object.fromEntries(STATUSES.map((s) => [s, got.get(s) ?? 0]));
  });
}

/**
 * The newest stimuli, any status. With `chars`, each text is cut to that
 * length and the payload left out: small enough to be a playbook's input.
 */
export function recent(maxItems = 50, chars: number | null = null): { stimuli: Json[] } {
  return using((conn) => {
    const out: Json[] = conn.all("SELECT * FROM stimuli ORDER BY at DESC, rowid DESC LIMIT ?", maxItems).map((r) => ({ ...row(r), status: r.status }));
    if (chars !== null) {
      for (const item of out) {
        delete (item as Json).payload;
        item.text = Array.from(item.text as string).slice(0, chars).join("");
      }
    }
    return { stimuli: out };
  });
}

// --- follow-ups -------------------------------------------------------------------------

/** A ref as the thing itself: trimmed, any `surface:` prefix gone, so "x:111" and "111" are the same post. */
export function bareRef(ref: string): string {
  const r = pyStrip(ref);
  const at = r.indexOf(":");
  if (at < 0) return r;
  const head = r.slice(0, at);
  const rest = pyStrip(r.slice(at + 1));
  return REF_SURFACES.includes(pyStrip(head).toLowerCase()) && rest ? rest : r;
}

/** Teeny's own reminder, raised as a stimulus once `dueAt` passes. */
export function followUp(dueAt: number, about: string, ref: string | null = null): Json {
  const now = hand.now();
  if (typeof about !== "string" || !pyStrip(about)) throw new hand.HandError("a follow-up needs `about`");
  if (typeof dueAt !== "number" || dueAt <= now) throw new hand.HandError("a follow-up is due in the future");
  if (dueAt - now > MAX_FOLLOW_UP_SECS) throw new hand.HandError(`a follow-up is due within ${Math.floor(MAX_FOLLOW_UP_SECS / 86400)} days`);
  const cleanRef = typeof ref === "string" && pyStrip(ref) ? pyStrip(ref) : null;
  return using((conn) =>
    hand.transaction(conn, () => {
      const cur = conn.run("INSERT INTO follow_ups (due_at, about, ref, status, created_at) VALUES (?,?,?, 'pending', ?)", dueAt, clip(about), cleanRef, now);
      const out: Json = { id: cur.lastrowid, due_at: dueAt, about: pyStrip(about) };
      if (cleanRef) out.ref = cleanRef;
      return out;
    }),
  );
}

/** Every follow-up now due becomes a `self` stimulus, once. */
export function raiseDue(): number {
  const now = hand.now();
  return using((conn) =>
    hand.transaction(conn, () => {
      const due = conn.all("SELECT * FROM follow_ups WHERE status = 'pending' AND due_at <= ? ORDER BY due_at", now);
      for (const r of due) {
        conn.run(
          "INSERT OR IGNORE INTO stimuli (id, surface, kind, ref, who, text, payload, at, status, updated_at) VALUES (?, 'self', 'follow_up', ?, NULL, ?, ?, ?, 'pending', ?)",
          `self:follow_up:${r.id}`, String(r.id), `you said you would come back to this: ${r.about}`,
          dumps({ ref: r.ref, set_at: r.created_at }, { ensureAscii: false }), r.due_at as number, now,
        );
        conn.run("UPDATE follow_ups SET status = 'raised' WHERE id = ?", r.id as number);
      }
      return due.length;
    }),
  );
}

function waitingOn(conn: Connection, refs: Iterable<string>): Row[] {
  const wanted = new Set([...refs].filter((r) => typeof r === "string" && pyStrip(r)).map(bareRef));
  if (!wanted.size) return [];
  return conn
    .all("SELECT id, about, ref FROM follow_ups WHERE status = 'pending' AND ref IS NOT NULL")
    .filter((r) => wanted.has(bareRef(r.ref as string)));
}

function close(conn: Connection, rows: Row[], status: string): void {
  conn.runMany("UPDATE follow_ups SET status = ? WHERE id = ? AND status = 'pending'", rows.map((r) => [status, r.id as number]));
}

/** Clears waiting follow-ups, by id or by ref, so they never come due. The ids dropped. */
export function dropFollowUp(id: number | null = null, ref: string | null = null): number[] {
  if (id === null && !(typeof ref === "string" && pyStrip(ref))) throw new hand.HandError("dropping a follow-up needs its id or ref");
  return using((conn) =>
    hand.transaction(conn, () => {
      const rows = id !== null ? conn.all("SELECT id FROM follow_ups WHERE id = ? AND status = 'pending'", id) : waitingOn(conn, [ref as string]);
      close(conn, rows, "dropped");
      return rows.map((r) => r.id as number);
    }),
  );
}

/** The thing these refs name was just answered: every waiting follow-up about it is resolved, except the ids in `keep`. */
export function resolve(refs: Iterable<string>, keep: Iterable<number> = []): Json[] {
  const spare = new Set(keep);
  return using((conn) =>
    hand.transaction(conn, () => {
      const rows = waitingOn(conn, refs).filter((r) => !spare.has(r.id as number));
      close(conn, rows, "resolved");
      return rows.map((r) => ({ id: r.id, about: r.about, ref: r.ref }));
    }),
  );
}

export function followUps(status = "pending"): { follow_ups: Json[] } {
  return using((conn) => ({
    follow_ups: conn.all("SELECT id, due_at, about, ref, status FROM follow_ups WHERE status = ? ORDER BY due_at", status),
  }));
}

// --- notes ------------------------------------------------------------------------------

/** Something Teeny chose to remember. */
export function note(text: string, who: string | null = null, tick: string | null = null): Json {
  if (typeof text !== "string" || !pyStrip(text)) throw new hand.HandError("a note needs text");
  return using((conn) =>
    hand.transaction(conn, () => ({ id: conn.run("INSERT INTO mind_notes (note, who, tick, at) VALUES (?,?,?,?)", clip(text), who, tick, hand.now()).lastrowid })),
  );
}

export function notes(since = 0, who: string | null = null, maxItems = 200): { notes: Json[] } {
  return using((conn) => ({
    notes: who
      ? conn.all("SELECT id, note, who, at FROM mind_notes WHERE at >= ? AND who = ? COLLATE NOCASE ORDER BY at LIMIT ?", since, who, maxItems)
      : conn.all("SELECT id, note, who, at FROM mind_notes WHERE at >= ? ORDER BY at LIMIT ?", since, maxItems),
  }));
}
