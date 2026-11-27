// Teeny's Python reads OpenHuman's storage.db directly (bin/crons, health.py,
// pulse.py, catch_up.py, evolve.py). This writes the same `docs` rows, in the
// shape recorded in test/contract/_storage_db.json.
import { mkdirSync } from "node:fs";
import { dirname } from "node:path";
import { DatabaseSync } from "node:sqlite";
import type { Schedule } from "./schedule.js";

export interface JobBody {
  name: string;
  job_type: "agent" | "shell";
  expression: string | null;
  enabled: boolean;
  next_run_ms: number | null;
  schedule: Schedule;
  command?: string;
  agent_id?: string;
  prompt?: string;
  session_target?: string;
  delivery: { mode: "none" };
  delete_after_run?: boolean;
  created_ms: number;
}

export interface JobRow extends JobBody {
  id: string;
}

export interface RunBody {
  job_id: string;
  status: "running" | "ok" | "error";
  started_ms: number;
  finished_ms?: number;
  seq: number;
  output?: string;
}

export interface LeaseBody {
  holder: string;
  epoch: number;
  expires_ms: number;
  released: boolean;
}

/** tinyflows' Key::of(["cron", Key::of([job_id, occurrence_ms])]), as bin/lib/catch_up.py _lease_id. */
export function leaseId(jobId: string, occurrenceMs: number): string {
  const part = (t: string) => t.replaceAll("%", "%25").replaceAll("/", "%2F");
  return `cron/${part(`${part(jobId)}/${occurrenceMs}`)}`;
}

/** "sqlite:brain/storage.db" (OPENHUMAN_STORAGE_URL) to a path, relative to ROOT. */
export function storagePath(env: Readonly<Record<string, string | undefined>>, root: string): string {
  const url = env.OPENHUMAN_STORAGE_URL;
  const raw = url?.startsWith("sqlite:") ? url.slice("sqlite:".length).replace(/^\/\//, "") : undefined;
  const p = raw && raw !== "" ? raw : "brain/storage.db";
  return p.startsWith("/") ? p : `${root}/${p}`;
}

const HISTORY_PER_JOB = 200;

export class CronStore {
  private readonly db: DatabaseSync;

  constructor(path: string) {
    mkdirSync(dirname(path), { recursive: true });
    this.db = new DatabaseSync(path);
    this.db.exec("PRAGMA busy_timeout = 5000");
    this.db.exec("PRAGMA journal_mode = WAL");
    // The DDL the Python tests use; a store OpenHuman made already has it.
    this.db.exec(
      "create table if not exists docs (scope text default '', coll text, id text, created_ms integer default 0, updated_ms integer default 0, deleted integer default 0, body text)",
    );
    this.db.exec("create index if not exists docs_coll_id on docs (coll, id)");
  }

  close(): void {
    this.db.close();
  }

  private put(coll: string, id: string, body: unknown, now: number): void {
    const text = JSON.stringify(body);
    const hit = this.db.prepare("select 1 from docs where coll = ? and id = ?").get(coll, id);
    if (hit) {
      this.db.prepare("update docs set body = ?, updated_ms = ?, deleted = 0 where coll = ? and id = ?").run(text, now, coll, id);
    } else {
      this.db
        .prepare("insert into docs (scope, coll, id, created_ms, updated_ms, deleted, body) values ('', ?, ?, ?, ?, 0, ?)")
        .run(coll, id, now, now, text);
    }
  }

  putJob(id: string, body: JobBody, now: number): void {
    this.put("cron.jobs", id, { id, ...body }, now);
  }

  getJob(id: string): JobRow | null {
    const row = this.db
      .prepare("select id, body from docs where coll = 'cron.jobs' and id = ? and deleted = 0")
      .get(id) as { id: string; body: string } | undefined;
    return row ? this.parseJob(row) : null;
  }

  listJobs(): JobRow[] {
    const rows = this.db
      .prepare("select id, body from docs where coll = 'cron.jobs' and deleted = 0 order by created_ms, id")
      .all() as { id: string; body: string }[];
    return rows.flatMap((r) => {
      try {
        return [this.parseJob(r)];
      } catch {
        return []; // a row that is not a job body is none of ours
      }
    });
  }

  private parseJob(row: { id: string; body: string }): JobRow {
    const body = JSON.parse(row.body) as JobBody;
    if (typeof body.name !== "string") throw new Error("not a job");
    return { ...body, id: row.id };
  }

  removeJob(id: string, now: number): boolean {
    const r = this.db.prepare("update docs set deleted = 1, updated_ms = ? where coll = 'cron.jobs' and id = ? and deleted = 0").run(now, id);
    return Number(r.changes) > 0;
  }

  nextSeq(jobId: string): number {
    const row = this.db
      .prepare(
        "select max(json_extract(body, '$.seq')) as m from docs where coll = 'cron.runs' and json_valid(body) and json_extract(body, '$.job_id') = ?",
      )
      .get(jobId) as { m: number | null };
    return (row.m ?? 0) + 1;
  }

  putRun(id: string, body: RunBody, now: number): void {
    this.put("cron.runs", id, body, now);
  }

  /** The newest runs of a job, newest first. */
  runs(jobId: string, limit: number): (RunBody & { id: string })[] {
    const rows = this.db
      .prepare(
        "select id, body from docs where coll = 'cron.runs' and deleted = 0 and json_valid(body) and json_extract(body, '$.job_id') = ? order by json_extract(body, '$.seq') desc limit ?",
      )
      .all(jobId, limit) as { id: string; body: string }[];
    return rows.map((r) => ({ id: r.id, ...(JSON.parse(r.body) as RunBody) }));
  }

  /** Marks runs that never finished (the process died under them) as failed. */
  failInterrupted(now: number): number {
    const rows = this.db
      .prepare(
        "select id, body from docs where coll = 'cron.runs' and deleted = 0 and json_valid(body) and json_extract(body, '$.status') = 'running'",
      )
      .all() as { id: string; body: string }[];
    for (const r of rows) {
      const body = JSON.parse(r.body) as RunBody;
      this.put("cron.runs", r.id, { ...body, status: "error", finished_ms: now, output: "interrupted: the core stopped while this ran" }, now);
    }
    return rows.length;
  }

  pruneRuns(jobId: string): void {
    this.db
      .prepare(
        `delete from docs where coll = 'cron.runs' and json_valid(body) and json_extract(body, '$.job_id') = ?1
         and json_extract(body, '$.seq') <= (select max(json_extract(body, '$.seq')) from docs where coll = 'cron.runs' and json_valid(body) and json_extract(body, '$.job_id') = ?1) - ?2`,
      )
      .run(jobId, HISTORY_PER_JOB);
  }

  removeRuns(jobId: string): void {
    this.db
      .prepare("delete from docs where coll = 'cron.runs' and json_valid(body) and json_extract(body, '$.job_id') = ?")
      .run(jobId);
  }

  putLease(jobId: string, occurrenceMs: number, body: LeaseBody, now: number): void {
    this.put("tsd.leases", leaseId(jobId, occurrenceMs), body, now);
  }
}
