import { spawnSync } from "node:child_process";
import { mkdirSync, mkdtempSync, readFileSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";
import { DatabaseSync } from "node:sqlite";
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { createCron, type Cron } from "../../src/cron/index.js";
import { nextRunMs, parseSchedule } from "../../src/cron/schedule.js";
import { leaseId } from "../../src/cron/store.js";
import type { CoreContext } from "../../src/server/types.js";
import { assertMatchesContract, loadContract } from "../contract.js";

const REPO = resolve(import.meta.dirname, "../../..");
let dir: string;
let ctx: CoreContext;
let cron: Cron;
let clock: number;

function makeCtx(extra: Record<string, string> = {}): CoreContext {
  return {
    env: { HOME: "/home/fake", PATH: "/usr/bin:/bin", LANG: "C.UTF-8", OPENHUMAN_STORAGE_URL: `sqlite:${dir}/storage.db`, OPENHUMAN_ACTION_DIR: dir, ...extra },
    root: dir,
    stateDir: join(dir, "state"),
    log: () => {},
  };
}
const call = (m: string, p: Record<string, unknown> = {}) => cron.methods[m]!(p, ctx);
const shellJob = (command: string, extra: Record<string, unknown> = {}) => ({
  name: "teeny.t", job_type: "shell", command, schedule: { kind: "cron", expr: "*/5 * * * *", tz: "UTC" }, delivery: { mode: "none" }, ...extra,
});
function rows(coll: string): { id: string; updated_ms: number; body: Record<string, unknown> }[] {
  const db = new DatabaseSync(join(dir, "storage.db"), { readOnly: true });
  try {
    return (db.prepare("select id, updated_ms, body from docs where coll = ? and deleted = 0").all(coll) as { id: string; updated_ms: number; body: string }[]).map((r) => ({ ...r, body: JSON.parse(r.body) }));
  } finally {
    db.close();
  }
}
async function fireNow(): Promise<void> {
  clock += 10 * 60_000;
  await cron.tick();
  await cron.idle();
}

beforeEach(() => {
  dir = mkdtempSync(join(tmpdir(), "cron-"));
  clock = Date.parse("2026-11-27T10:00:00Z");
  ctx = makeCtx();
});
afterEach(async () => {
  await cron?.stop();
  rmSync(dir, { recursive: true, force: true });
});

describe("schedule math", () => {
  it("honours Asia/Kolkata (UTC+5:30)", () => {
    const s = parseSchedule({ kind: "cron", expr: "30 6 * * *", tz: "Asia/Kolkata" });
    // 06:30 IST is 01:00 UTC.
    expect(new Date(nextRunMs(s, Date.parse("2026-11-27T00:00:00Z"))!).toISOString()).toBe("2026-11-27T01:00:00.000Z");
    expect(new Date(nextRunMs(s, Date.parse("2026-11-27T01:00:00Z"))!).toISOString()).toBe("2026-11-28T01:00:00.000Z");
  });
  it("rejects bad expressions, zones and kinds", () => {
    expect(() => parseSchedule({ kind: "cron", expr: "nope", tz: "UTC" })).toThrow();
    expect(() => parseSchedule({ kind: "cron", expr: "* * * * *", tz: "Mars/Base" })).toThrow();
    expect(() => parseSchedule({ kind: "weekly" })).toThrow();
  });
  it("an at schedule has no next run once it has passed", () => {
    const s = parseSchedule({ kind: "at", at: "2026-11-27T10:00:00Z" });
    expect(nextRunMs(s, Date.parse("2026-11-27T09:00:00Z"))).toBe(Date.parse("2026-11-27T10:00:00Z"));
    expect(nextRunMs(s, Date.parse("2026-11-27T10:00:00Z"))).toBeNull();
  });
});

describe("rpc methods match the contract", () => {
  beforeEach(() => {
    cron = createCron({ now: () => clock });
  });
  it("cron_add / cron_list / cron_update / cron_remove / cron_run", async () => {
    const example = loadContract("cron_add").params.example;
    const added = (await call("cron_add", example)) as Record<string, unknown>;
    assertMatchesContract("cron_add", added);
    expect(added.enabled).toBe(true);
    expect(added.next_run).toBe("2026-11-27T10:05:00.000Z");
    const listed = await call("cron_list");
    assertMatchesContract("cron_list", listed);
    expect(listed).toHaveLength(1);
    assertMatchesContract("cron_update", await call("cron_update", { job_id: added.id, patch: { schedule: { kind: "cron", expr: "30 6 * * *", tz: "UTC" }, enabled: true } }));
    const after = (await call("cron_list")) as Record<string, unknown>[];
    expect((after[0]!.schedule as { expr: string }).expr).toBe("30 6 * * *");
    expect(after[0]!.next_run).toBe("2026-11-28T06:30:00.000Z");
    assertMatchesContract("cron_remove", await call("cron_remove", { job_id: added.id }));
    expect(await call("cron_list")).toEqual([]);
    await expect(call("cron_remove", { job_id: added.id })).rejects.toThrow();
  });
  it("an agent job carries agent_id, prompt and an isolated session", async () => {
    const j = (await call("cron_add", { name: "mind.nap", job_type: "agent", agent_id: "mind", prompt: "wake up", session_target: "isolated", schedule: { kind: "at", at: "2026-11-27T12:00:00Z" }, delivery: { mode: "none" }, delete_after_run: true })) as Record<string, unknown>;
    assertMatchesContract("cron_add", j);
    expect(j).toMatchObject({ job_type: "agent", agent_id: "mind", prompt: "wake up", session_target: "isolated", delete_after_run: true });
  });
  it("refuses delivery other than none, and an agent job without a prompt", async () => {
    await expect(call("cron_add", shellJob("true", { delivery: { mode: "announce" } }))).rejects.toThrow();
    await expect(call("cron_add", { name: "x", job_type: "agent", agent_id: "a", schedule: { kind: "cron", expr: "* * * * *" }, delivery: { mode: "none" } })).rejects.toThrow();
  });
  it("cron_run is accepted with a contract-shaped call", async () => {
    const j = (await call("cron_add", shellJob("true"))) as { id: string };
    await call("cron_run", { job_id: j.id });
    await cron.idle();
    expect(rows("cron.runs")).toHaveLength(1);
  });
});

describe("firing", () => {
  it("never fires a disabled job", async () => {
    cron = createCron({ now: () => clock });
    const j = (await call("cron_add", shellJob("echo hi"))) as { id: string };
    await call("cron_update", { job_id: j.id, patch: { enabled: false } });
    cron.start(ctx);
    await fireNow();
    expect(rows("cron.runs")).toHaveLength(0);
    expect(rows("cron.jobs")[0]!.body.next_run_ms).toBeNull();
    await call("cron_update", { job_id: j.id, patch: { enabled: true } });
    await fireNow();
    expect(rows("cron.runs")).toHaveLength(1);
  });

  it("runs a shell job with exactly HOME, PATH and LANG, in the action dir", async () => {
    cron = createCron({ now: () => clock });
    const out = join(dir, "env.txt");
    mkdirSync(join(dir, "act"));
    ctx = makeCtx({ OPENHUMAN_ACTION_DIR: join(dir, "act"), SECRET_TOKEN: "leak" });
    await call("cron_add", shellJob(`env | sort > ${out}; pwd >> ${out}`));
    cron.start(ctx);
    await fireNow();
    const lines = readFileSync(out, "utf8").trim().split("\n");
    const names = lines.filter((l) => l.includes("=")).map((l) => l.split("=")[0]).filter((n) => !["PWD", "SHLVL", "_", "OLDPWD"].includes(n!));
    expect(names).toEqual(["HOME", "LANG", "PATH"]);
    expect(lines).toContain("HOME=/home/fake");
    expect(lines).not.toContain("SECRET_TOKEN=leak");
    expect(lines.at(-1)).toBe(join(dir, "act"));
  });

  it("kills a shell job past the timeout, children included, and records an error", async () => {
    cron = createCron({ now: () => clock, shellTimeoutSecs: 0.3 });
    const marker = join(dir, "child-survived");
    await call("cron_add", shellJob(`(sleep 1; touch ${marker}) & sleep 30`));
    cron.start(ctx);
    await fireNow();
    const run = rows("cron.runs")[0]!.body;
    expect(run.status).toBe("error");
    expect(run.output).toBe("job timed out after 0.3s");
    await new Promise((r) => setTimeout(r, 1300));
    expect(() => readFileSync(marker)).toThrow();
  });

  it("writes run, job and lease rows in the fixture shape", async () => {
    cron = createCron({ now: () => clock });
    const j = (await call("cron_add", shellJob("echo out; echo err >&2; exit 3"))) as { id: string };
    cron.start(ctx);
    const slot = rows("cron.jobs")[0]!.body.next_run_ms as number;
    await fireNow();
    const job = rows("cron.jobs")[0]!;
    expect(job.id).toBe(j.id);
    expect(job.body).toMatchObject({ name: "teeny.t", job_type: "shell", expression: "*/5 * * * *", enabled: true, command: "echo out; echo err >&2; exit 3" });
    expect(job.body.schedule).toEqual({ kind: "cron", expr: "*/5 * * * *", tz: "UTC" });
    expect(job.body.next_run_ms).toBeGreaterThan(slot);
    const run = rows("cron.runs")[0]!.body;
    expect(run).toMatchObject({ job_id: j.id, status: "error", seq: 1 });
    expect(typeof run.started_ms).toBe("number");
    expect(typeof run.finished_ms).toBe("number");
    expect(run.output).toBe("status=exit status: 3\nstdout:\nout\nstderr:\nerr");
    const lease = rows("tsd.leases").find((r) => r.id === leaseId(j.id, slot))!;
    expect(lease.body).toMatchObject({ released: true, epoch: 1 });
    expect(lease.body.holder).toBeTypeOf("string");
    const listed = (await call("cron_list")) as Record<string, unknown>[];
    expect(listed[0]).toMatchObject({ last_status: "error" });
    await fireNow();
    expect(rows("cron.runs").map((r) => r.body.seq).sort()).toEqual([1, 2]);
  });

  it("runs agent jobs through the injected runner with an isolated session key", async () => {
    const seen: string[][] = [];
    cron = createCron({ now: () => clock, runAgent: async (a, p, k) => (seen.push([a, p, k]), "done") });
    await call("cron_add", { name: "teeny.thinker", job_type: "agent", agent_id: "mind", prompt: "think", session_target: "isolated", schedule: { kind: "cron", expr: "*/5 * * * *", tz: "UTC" }, delivery: { mode: "none" } });
    cron.start(ctx);
    await fireNow();
    expect(seen).toHaveLength(1);
    expect(seen[0]!.slice(0, 2)).toEqual(["mind", "think"]);
    expect(seen[0]![2]).toMatch(/^cron:teeny\.thinker:run-/);
    expect(rows("cron.runs")[0]!.body).toMatchObject({ status: "ok", output: "done" });
  });

  it("fails an agent run when no runner is set, or the runner throws", async () => {
    cron = createCron({ now: () => clock });
    await call("cron_add", { name: "teeny.a", job_type: "agent", agent_id: "mind", prompt: "p", schedule: { kind: "cron", expr: "*/5 * * * *" }, delivery: { mode: "none" } });
    cron.start(ctx);
    await fireNow();
    expect(rows("cron.runs")[0]!.body.status).toBe("error");
    cron.setAgentRunner(async () => { throw new Error("boom"); });
    await fireNow();
    expect(rows("cron.runs").map((r) => r.body.output)).toContain("boom");
  });

  it("deletes a one-off at job after it runs, and skips slots missed while down", async () => {
    cron = createCron({ now: () => clock });
    await call("cron_add", shellJob("true", { name: "mind.once", schedule: { kind: "at", at: "2026-11-27T10:03:00Z" }, delete_after_run: true }));
    await call("cron_add", shellJob("true", { name: "teeny.daily" }));
    await cron.stop();
    clock += 3 * 3600_000; // the box was down for hours
    cron = createCron({ now: () => clock });
    cron.start(ctx);
    const daily = rows("cron.jobs").find((r) => r.body.name === "teeny.daily")!;
    expect(daily.body.next_run_ms as number).toBeGreaterThan(clock);
    await cron.tick();
    await cron.idle();
    const names = rows("cron.runs").map((r) => r.body.job_id);
    expect(names).toHaveLength(1); // only the one-off wake
    expect(rows("cron.jobs").map((r) => r.body.name)).toEqual(["teeny.daily"]);
  });

  it("marks a run a dead process left open as an error at start", async () => {
    cron = createCron({ now: () => clock });
    const j = (await call("cron_add", shellJob("true"))) as { id: string };
    await cron.stop();
    const db = new DatabaseSync(join(dir, "storage.db"));
    db.prepare("insert into docs (coll, id, body) values ('cron.runs', 'r1', ?)").run(JSON.stringify({ job_id: j.id, status: "running", started_ms: clock, seq: 1 }));
    db.close();
    cron = createCron({ now: () => clock });
    cron.start(ctx);
    expect(rows("cron.runs")[0]!.body.status).toBe("error");
  });
});

const py = spawnSync("python3", ["--version"]).status === 0;
describe.skipIf(!py)("the Python readers on a storage.db this code wrote", () => {
  it("health.py, catch_up.py and bin/crons read it", async () => {
    cron = createCron({ now: () => clock });
    await call("cron_add", shellJob("exit 1", { name: "teeny.fails" }));
    await call("cron_add", shellJob("true", { name: "teeny.off" }));
    const off = ((await call("cron_list")) as { id: string; name: string }[]).find((j) => j.name === "teeny.off")!;
    await call("cron_update", { job_id: off.id, patch: { enabled: false } });
    cron.start(ctx);
    await fireNow();
    const env = { ...process.env, TEENY_STORAGE: join(dir, "storage.db"), PYTHONDONTWRITEBYTECODE: "1" };
    const script = [
      "import sys, json, time",
      `sys.path.insert(0, ${JSON.stringify(join(REPO, "bin/lib"))})`,
      "import health, catch_up",
      "conn_ok = catch_up.storage().is_file()",
      "errs = health.cron_errors(time.time() - 3600 * 24 * 365 * 5)",
      "stale = health.stale_crons(time.time() + 7 * 86400)",
      "print(json.dumps({'ok': conn_ok, 'errs': errs, 'stale': [s['name'] for s in stale], 'live': catch_up.live_job_ids()}))",
    ].join("\n");
    const r = spawnSync("python3", ["-I", "-c", script], { env, encoding: "utf8" });
    expect(r.stderr).toBe("");
    const out = JSON.parse(r.stdout) as { ok: boolean; errs: Record<string, number>; stale: string[]; live: string[] };
    expect(out.ok).toBe(true);
    expect(out.errs).toEqual({ "teeny.fails": 1 });
    expect(out.stale).toEqual(["teeny.fails"]); // a week on, the enabled job is stale; the disabled one is not
    expect(out.live).toEqual([]); // the lease was released

    if (spawnSync("sqlite3", ["-version"]).status === 0) {
      const crons = spawnSync(join(REPO, "bin/crons"), [], { env: { ...env, TEENY_ROOT: REPO }, encoding: "utf8" });
      expect(crons.stdout).toContain("teeny.fails");
      expect(crons.stdout).toMatch(/teeny\.off\s+shell\s+\*\/5 \* \* \* \*\s+UTC\s+no/);
      expect(crons.stdout).toContain("error");
    }
  });
});
