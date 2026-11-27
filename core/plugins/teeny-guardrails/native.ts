// What a native playbook's run may do in the world, counted in code. bin/act kept three things in shell: the
// day's budget (state/act/<name>/<date>), the one-run-at-a-time lock (state/locks/act-<name>) and the run log
// (state/act/<name>/runs.jsonl). A native run is an agent turn, so the same three live here, under the same
// paths: a playbook switched between bin/act and native keeps its day's count, and bin/act and a native run
// of one playbook cannot overlap. before_tool_call calls `begin` and `reserve`; agent_end calls `end`.
import { appendFileSync, existsSync, mkdirSync, readFileSync, renameSync, rmSync, statSync, writeFileSync } from "node:fs";
import { hostname } from "node:os";
import { join } from "node:path";
import type { NativePolicy } from "./policy.js";

/** A lock with no live owner is taken over; one whose owner cannot be told (no file) is stale after this long, as in bin/act. */
const STALE_NO_OWNER_MS = 60 * 60 * 1000;
/** A native run that never ended (the gateway lost its agent_end) holds its lock no longer than this. */
const MAX_RUN_MS = 45 * 60 * 1000;
const MAX_RUNS_KEPT = 1024;

interface RunState {
  playbook: string;
  /** Acting-hand calls this run has been allowed. */
  acted: number;
  startedMs: number;
  /** Day reservations by the exact command that made them (the UTC day they were taken on, how many, and whether a refusal may give them back). */
  pending: Map<string, { day: string; calls: number; refundable: boolean }>;
}

export type Refusal = string | undefined;

export class NativeState {
  private readonly runs = new Map<string, RunState>();

  constructor(
    private readonly stateDir: string,
    private readonly now: () => number = Date.now,
    private readonly pid: number = process.pid,
    private readonly host: string = process.env.HOSTNAME || hostname(),
  ) {}

  private today(): string {
    return new Date(this.now()).toISOString().slice(0, 10);
  }
  private countFile(playbook: string, day: string = this.today()): string {
    return join(this.stateDir, "act", playbook, day);
  }
  private lockDir(playbook: string): string {
    return join(this.stateDir, "locks", `act-${playbook}`);
  }

  /** The actions this playbook has taken today (bin/act's `taken`). */
  taken(playbook: string, day: string = this.today()): number {
    let text: string;
    try {
      text = readFileSync(this.countFile(playbook, day), "utf8");
    } catch (e) {
      // No file is an unused day; a file that cannot be read is not known to be unused.
      return (e as NodeJS.ErrnoException).code === "ENOENT" ? 0 : Number.POSITIVE_INFINITY;
    }
    const n = Number.parseInt(text.trim(), 10);
    return Number.isFinite(n) && n >= 0 ? n : Number.POSITIVE_INFINITY;
  }

  private writeCount(playbook: string, n: number, day: string = this.today()): void {
    const file = this.countFile(playbook, day);
    mkdirSync(join(this.stateDir, "act", playbook), { recursive: true, mode: 0o700 });
    const tmp = `${file}.${this.pid}.tmp`;
    writeFileSync(tmp, `${n}\n`, { mode: 0o600 });
    renameSync(tmp, file);
  }

  /** A process's start time from /proc/PID/stat, as bin/act's start_of reads it ("-" when it cannot be read, which bin/act treats as no start time). */
  private startOf(pid: number): string {
    try {
      const stat = readFileSync(`/proc/${pid}/stat`, "utf8");
      return stat.slice(stat.lastIndexOf(")") + 2).split(" ")[19] || "-";
    } catch {
      return "-";
    }
  }

  private ownerOf(dir: string): { pid: number; host: string; run?: string } | undefined {
    try {
      const [pid, host, , run] = readFileSync(join(dir, "owner"), "utf8").trim().split(/\s+/);
      const n = Number.parseInt(pid ?? "", 10);
      return Number.isFinite(n) ? { pid: n, host: host ?? "", ...(run ? { run } : {}) } : undefined;
    } catch {
      return undefined;
    }
  }

  private stale(dir: string, playbook: string): boolean {
    const owner = this.ownerOf(dir);
    if (!owner) {
      try {
        return this.now() - statSync(dir).mtimeMs > STALE_NO_OWNER_MS;
      } catch {
        return true;
      }
    }
    if (owner.host !== this.host) return true;
    if (owner.pid === this.pid) {
      // Ours: held only by a run still going here.
      const mine = owner.run ? this.runs.get(owner.run) : undefined;
      return !mine || mine.playbook !== playbook || this.now() - mine.startedMs > MAX_RUN_MS;
    }
    try {
      process.kill(owner.pid, 0);
      return false;
    } catch (e) {
      return (e as NodeJS.ErrnoException).code !== "EPERM";
    }
  }

  /** Takes the playbook's lock for this run. Undefined when it is held (by this run, or now), else why not. */
  begin(n: NativePolicy, runKey: string): Refusal {
    if (this.runs.has(runKey)) return undefined;
    const dir = this.lockDir(n.playbook);
    mkdirSync(join(this.stateDir, "locks"), { recursive: true, mode: 0o700 });
    for (let attempt = 0; attempt < 2; attempt++) {
      try {
        mkdirSync(dir, { mode: 0o700 });
        try {
          // pid, host and start time are the three fields bin/act's lock_stale reads; the run key follows.
          writeFileSync(join(dir, "owner"), `${this.pid} ${this.host} ${this.startOf(this.pid)} ${runKey}\n`, { mode: 0o600 });
        } catch (w) {
          // ENOENT means the directory is gone: another process has since reclaimed the path, and that lock is not ours to remove.
          if ((w as NodeJS.ErrnoException).code !== "ENOENT") rmSync(dir, { recursive: true, force: true });
          throw w;
        }
        this.keep(runKey, { playbook: n.playbook, acted: 0, startedMs: this.now(), pending: new Map() });
        return undefined;
      } catch (e) {
        if ((e as NodeJS.ErrnoException).code !== "EEXIST") throw e;
        if (attempt === 0 && this.stale(dir, n.playbook)) {
          const old = this.ownerOf(dir)?.run;
          if (old) this.runs.delete(old);
          rmSync(dir, { recursive: true, force: true });
          continue;
        }
        return `${n.playbook} is already running (its lock is held): one run of a playbook at a time`;
      }
    }
    return `${n.playbook} is already running`;
  }

  private keep(runKey: string, run: RunState): void {
    this.runs.delete(runKey);
    this.runs.set(runKey, run);
    if (this.runs.size > MAX_RUNS_KEPT) {
      const oldest = this.runs.keys().next().value;
      if (oldest !== undefined) this.runs.delete(oldest);
    }
  }

  /** Counts `calls` acting-hand calls of `command` against the run and the day. Undefined when allowed, else why not. */
  reserve(n: NativePolicy, runKey: string, calls: number, command: string, refundable = true): Refusal {
    const run = this.runs.get(runKey);
    if (!run) return `${n.playbook}: this run holds no lock`;
    if (run.acted + calls > n.maxPerRun) {
      return `${n.playbook} allows ${n.maxPerRun} call${n.maxPerRun === 1 ? "" : "s"} to hands/${n.hand?.name ?? "its hand"} in one run and this run has made ${run.acted}: end the run`;
    }
    const taken = this.taken(n.playbook);
    if (!Number.isFinite(taken)) return `${n.playbook}: today's count could not be read, so no action is allowed: end the run`;
    if (taken + calls > n.maxPerDay) {
      return `${n.playbook} has taken its ${n.maxPerDay} action${n.maxPerDay === 1 ? "" : "s"} for today (${taken} of ${n.maxPerDay}): end the run`;
    }
    const day = this.today();
    this.writeCount(n.playbook, taken + calls, day);
    run.acted += calls;
    const before = run.pending.get(command);
    run.pending.set(command, { day, calls: (before?.calls ?? 0) + calls, refundable: refundable && (before?.refundable ?? true) });
    return undefined;
  }

  /**
   * The hand answered `exitCode` for `command`. Code 2 is a rule refusing (nothing was done), so that day's
   * reservation is given back, as bin/act spent the budget only on a done action. The run's own count stays: the
   * per-run cap counts calls made. Any other code keeps the reservation (an error may have acted).
   */
  settle(n: NativePolicy, runKey: string, command: string, exitCode: number | undefined): void {
    const run = this.runs.get(runKey);
    const held = run?.pending.get(command);
    if (!run || held === undefined) return;
    run.pending.delete(command);
    if (exitCode !== 2 || !held.refundable) return;
    // Back on the day it was taken from, even if midnight UTC has passed since.
    const now = this.taken(n.playbook, held.day);
    if (Number.isFinite(now)) this.writeCount(n.playbook, Math.max(0, now - held.calls), held.day);
  }

  /** The run is over: its record to state/act/<name>/runs.jsonl (what bin/health reads) and its lock released. */
  end(runKey: string, outcome: { ok: boolean; error?: string }): void {
    const run = this.runs.get(runKey);
    if (!run) return;
    this.runs.delete(runKey);
    const dir = this.lockDir(run.playbook);
    if (this.ownerOf(dir)?.run === runKey) rmSync(dir, { recursive: true, force: true });
    try {
      const at = new Date(this.now()).toISOString().replace(/\.\d+Z$/, "Z");
      const line = outcome.ok
        ? { at, ok: true, playbook: run.playbook, native: true, acted: run.acted }
        : { at, ok: false, playbook: run.playbook, native: true, error: (outcome.error ?? "the run failed").slice(0, 500), failure: "playbook", acted: run.acted };
      mkdirSync(join(this.stateDir, "act", run.playbook), { recursive: true, mode: 0o700 });
      appendFileSync(join(this.stateDir, "act", run.playbook, "runs.jsonl"), `${JSON.stringify(line)}\n`, { mode: 0o600 });
    } catch {
      // A record that cannot be written never fails a run.
    }
  }

  /** Test aid: whether the lock directory exists. */
  locked(playbook: string): boolean {
    return existsSync(this.lockDir(playbook));
  }
}

/** The exit code an exec tool result carries, when it says one (OpenClaw's exec result details). */
export function exitCodeOf(result: unknown): number | undefined {
  const seen = new Set<unknown>();
  const walk = (v: unknown, depth: number): number | undefined => {
    if (typeof v !== "object" || v === null || depth > 4 || seen.has(v)) return undefined;
    seen.add(v);
    for (const key of ["exitCode", "exit_code", "code"]) {
      const c = (v as Record<string, unknown>)[key];
      if (typeof c === "number" && Number.isInteger(c)) return c;
    }
    for (const child of Object.values(v)) {
      const found = walk(child, depth + 1);
      if (found !== undefined) return found;
    }
    return undefined;
  };
  return walk(result, 0);
}
