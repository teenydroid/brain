// Parity: run a hand's Python and its TypeScript port on the same argv, env,
// stdin, fixture state and mocked network, and compare what a caller can see:
// stdout (as JSON), the exit code, stderr, every file the hand wrote under
// state/ (hands.db compared table by table), and the requests the hand made.
//
//   import { expectParity } from "../parity.js";
//   await expectParity({
//     hand: "hn",
//     argv: ["top", "--max", "3"],
//     settings: { HN_API_BASE: "{{HTTP}}/hn", HN_SEARCH_BASE: "{{HTTP}}/algolia" },
//     routes: { "GET /hn/topstories.json": { json: [1, 2, 3] }, "GET /hn/item/1.json": { json: { id: 1 } } },
//   });
//
// Each side gets its own copy of the fixture root (state/ and secrets/ from
// `fixture`, then `files` and `settings` on top), its own fixture HTTP server
// and a pinned clock (TEENY_NOW). The base URLs the Python hands already
// honour (HN_API_BASE, CHAN_API_BASE, LISTEN_*_BASE, GITHUB_API_BASE, ...) are
// settings in secrets/teeny.env, so `settings` is how the network is pointed at
// the server; "{{HTTP}}" in a value becomes the server's URL. The core's RPC is
// the same server's /rpc, answered from `rpc`.
//
// The TypeScript side runs core/dist/hands/<hand>/cli.js when `npm run build`
// has made it, else a throwaway compile of src/hands. The Python side runs
// hands/<hand> as it stands, or `pythonCmd`.

import { type ChildProcess, spawn } from "node:child_process";
import { cpSync, existsSync, mkdirSync, mkdtempSync, readdirSync, readFileSync, rmSync, statSync, writeFileSync } from "node:fs";
import { createServer, type IncomingMessage, type ServerResponse } from "node:http";
import type { AddressInfo } from "node:net";
import { tmpdir } from "node:os";
import { dirname, join, relative } from "node:path";
import { DatabaseSync } from "node:sqlite";
import { expect } from "vitest";
import { CORE, REPO, T0, build } from "./lib/support.js";

export interface Route {
  status?: number;
  headers?: Record<string, string>;
  /** Sent as JSON. */
  json?: unknown;
  /** Sent as it is (XML, HTML, or bytes a hand must find unreadable). */
  body?: string;
  /** Wait this long before answering (ms), for timeout cases. */
  delayMs?: number;
}
export type Handler = Route | ((req: { method: string; path: string; query: string; headers: Record<string, string | string[] | undefined>; body: string }) => Route);

export interface ParityCase {
  /** The hand's name: hands/<name>, and core/dist/hands/<name>/cli.js. */
  hand: string;
  argv: string[];
  stdin?: string;
  /** A directory whose state/ and secrets/ seed each side's TEENY_ROOT. */
  fixture?: string;
  /** Files to write under the root, by relative path. */
  files?: Record<string, string>;
  /** secrets/teeny.env, KEY -> value; "{{HTTP}}" is the fixture server. */
  settings?: Record<string, string>;
  /** Extra process environment. */
  env?: Record<string, string>;
  /** The fixture server: "METHOD /path" (no query) -> a Route or a function of the request. Anything else answers 404 {}. */
  routes?: Record<string, Handler>;
  /** The core's answers, by method ("openhuman.cron_list"); the call fails with an RPC error when absent. */
  rpc?: Record<string, unknown>;
  /** TEENY_NOW; default 2026-09-14T07:00:00Z. */
  now?: number;
  /** Don't compare stderr (a library's own error text, a traceback). */
  ignoreStderr?: boolean;
  /** Also require the same bytes on stdout, not only the same JSON. A whole-number float prints as "1.0" in Python and "1" here, so this fails on one. */
  rawStdout?: boolean;
  /** Skip these state files (relative to the root) or request headers. */
  ignoreFiles?: RegExp[];
  ignoreHeaders?: string[];
  /** Rewrite an outcome before comparing, to blank out what is meant to differ (a random id). */
  normalise?: (outcome: Outcome) => Outcome;
  /** How to run each side; default `hands/<hand>` and the built cli.js. */
  pythonCmd?: string[];
  tsCmd?: string[];
}

export interface RecordedRequest {
  method: string;
  path: string;
  query: string;
  body: string;
  headers: Record<string, string>;
}

export interface Outcome {
  code: number | null;
  stdout: string;
  json: unknown;
  stderr: string;
  /** Relative path -> content (text) or a table dump for hands.db. */
  files: Record<string, unknown>;
  requests: RecordedRequest[];
}

export interface ParityResult {
  python: Outcome;
  ts: Outcome;
  diffs: string[];
}

const DEFAULT_IGNORED_HEADERS = ["host", "connection", "accept-encoding", "accept-language", "sec-fetch-mode", "content-length"];
const RUN_TIMEOUT_MS = 120_000;

/** Every file under `dir` (not directories), relative. */
function walk(dir: string, base = dir): string[] {
  if (!existsSync(dir)) return [];
  return readdirSync(dir).flatMap((name) => {
    const full = join(dir, name);
    return statSync(full).isDirectory() ? walk(full, base) : [relative(base, full)];
  });
}

/** A hands.db as {table: {sql, rows}}, rows in rowid order, so two files compare by what they hold. */
export function dumpDb(path: string): Record<string, { sql: string; rows: unknown[] }> {
  const db = new DatabaseSync(path, { readOnly: true });
  try {
    const out: Record<string, { sql: string; rows: unknown[] }> = {};
    const tables = db.prepare("SELECT name, sql FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%' ORDER BY name").all() as { name: string; sql: string }[];
    for (const { name, sql } of tables) {
      out[name] = { sql: sql.replace(/\s+/g, " ").replace(/IF NOT EXISTS /gi, ""), rows: db.prepare(`SELECT * FROM "${name}" ORDER BY rowid`).all().map((r) => ({ ...r })) };
    }
    const indexes = db.prepare("SELECT name, sql FROM sqlite_master WHERE type = 'index' AND sql IS NOT NULL ORDER BY name").all() as { name: string; sql: string }[];
    out["(indexes)"] = { sql: "", rows: indexes.map((i) => [i.name, i.sql.replace(/\s+/g, " ").replace(/IF NOT EXISTS /gi, "")]) };
    return out;
  } finally {
    db.close();
  }
}

function snapshot(root: string, ignore: RegExp[]): Record<string, unknown> {
  const files: Record<string, unknown> = {};
  for (const rel of walk(join(root, "state")).map((r) => join("state", r)).sort()) {
    if (/(-wal|-shm)$/.test(rel) || rel.startsWith(join("state", "locks") + "/")) continue;
    if (ignore.some((re) => re.test(rel))) continue;
    files[rel] = rel.endsWith("hands.db") ? dumpDb(join(root, rel)) : readFileSync(join(root, rel), "utf8");
  }
  return files;
}

async function side(c: ParityCase, command: string[]): Promise<Outcome> {
  const root = mkdtempSync(join(tmpdir(), "parity-"));
  const requests: RecordedRequest[] = [];
  const ignoredHeaders = new Set([...DEFAULT_IGNORED_HEADERS, ...(c.ignoreHeaders ?? []).map((h) => h.toLowerCase())]);
  const server = createServer((req: IncomingMessage, res: ServerResponse) => {
    const chunks: Buffer[] = [];
    req.on("data", (d: Buffer) => chunks.push(d));
    req.on("end", () => {
      const body = Buffer.concat(chunks).toString();
      const [path = "", query = ""] = (req.url ?? "").split("?", 2) as [string, string?];
      const method = req.method ?? "GET";
      const headers = Object.fromEntries(Object.entries(req.headers).filter(([k]) => !ignoredHeaders.has(k)).map(([k, v]) => [k, String(v)]));
      if (path === "/rpc" && method === "POST") {
        const call = JSON.parse(body) as { id: number; method: string };
        const known = c.rpc !== undefined && call.method in c.rpc;
        // urllib and fetch each add their own User-Agent and Accept to the core call; nothing reads them.
        const kept = Object.fromEntries(Object.entries(headers).filter(([k]) => k === "authorization" || k === "content-type"));
        requests.push({ method, path, query, body, headers: kept });
        res.writeHead(200, { "content-type": "application/json" });
        res.end(JSON.stringify(known ? { jsonrpc: "2.0", id: call.id, result: c.rpc![call.method] } : { jsonrpc: "2.0", id: call.id, error: { code: -32601, message: `no such method ${call.method}` } }));
        return;
      }
      requests.push({ method, path, query, body, headers });
      const entry = c.routes?.[`${method} ${path}`];
      const route: Route = entry === undefined ? { status: 404, json: {} } : typeof entry === "function" ? entry({ method, path, query, headers: req.headers, body }) : entry;
      const send = (): void => {
        const payload = route.json !== undefined ? JSON.stringify(route.json) : (route.body ?? "");
        res.writeHead(route.status ?? 200, { ...(route.json !== undefined ? { "content-type": "application/json" } : {}), ...route.headers });
        res.end(payload);
      };
      if (route.delayMs) setTimeout(send, route.delayMs);
      else send();
    });
  });
  await new Promise<void>((done) => server.listen(0, "127.0.0.1", done));
  const url = `http://127.0.0.1:${(server.address() as AddressInfo).port}`;
  try {
    if (c.fixture) cpSync(c.fixture, root, { recursive: true });
    mkdirSync(join(root, "secrets"), { recursive: true });
    for (const [rel, text] of Object.entries(c.files ?? {})) {
      mkdirSync(dirname(join(root, rel)), { recursive: true });
      writeFileSync(join(root, rel), text.split("{{HTTP}}").join(url));
    }
    const lines = Object.entries(c.settings ?? {}).map(([k, v]) => `${k}=${v.split("{{HTTP}}").join(url)}`);
    if (lines.length) writeFileSync(join(root, "secrets", "teeny.env"), lines.join("\n") + "\n", { flag: "a" });
    if (c.rpc) writeFileSync(join(root, "secrets", "core.token"), "parity-token\n");
    const env: Record<string, string> = {
      ...(process.env as Record<string, string>),
      TEENY_ROOT: root,
      TEENY_NOW: String(c.now ?? T0),
      ...(c.rpc ? { OPENHUMAN_CORE_URL: url, OPENHUMAN_CORE_TOKEN: "parity-token" } : {}),
      ...c.env,
    };
    delete env.DRY_RUN;
    const [cmd, ...args] = command;
    const child: ChildProcess = spawn(cmd as string, [...args, ...c.argv], { env, cwd: root, stdio: ["pipe", "pipe", "pipe"] });
    let stdout = "";
    let stderr = "";
    child.stdout!.on("data", (d: Buffer) => (stdout += d.toString()));
    child.stderr!.on("data", (d: Buffer) => (stderr += d.toString()));
    child.stdin!.on("error", () => {});
    child.stdin!.end(c.stdin ?? "");
    const code = await new Promise<number | null>((done) => {
      const timer = setTimeout(() => {
        child.kill("SIGKILL");
      }, RUN_TIMEOUT_MS);
      child.on("close", (status) => {
        clearTimeout(timer);
        done(status);
      });
    });
    let json: unknown = null;
    try {
      json = JSON.parse(stdout);
    } catch {
      json = null;
    }
    const outcome: Outcome = { code, stdout, json, stderr, files: snapshot(root, c.ignoreFiles ?? []), requests };
    return c.normalise ? c.normalise(outcome) : outcome;
  } finally {
    await new Promise<void>((done) => server.close(() => done()));
    server.closeAllConnections();
    rmSync(root, { recursive: true, force: true });
  }
}

function tsCommand(name: string): string[] {
  const built = join(CORE, "dist", "hands", name, "cli.js");
  const dir = existsSync(built) ? null : build();
  return [process.execPath, "--disable-warning=ExperimentalWarning", dir === null ? built : join(dir, "hands", name, "cli.js")];
}

/** Both sides, and where they differ (empty when they do not). */
export async function runParity(c: ParityCase): Promise<ParityResult> {
  const python = await side(c, c.pythonCmd ?? [join(REPO, "hands", c.hand)]);
  const ts = await side(c, c.tsCmd ?? tsCommand(c.hand));
  const diffs: string[] = [];
  const same = (a: unknown, b: unknown): boolean => JSON.stringify(a) === JSON.stringify(b);
  if (python.code !== ts.code) diffs.push(`exit code: python ${python.code}, ts ${ts.code}`);
  if (!same(python.json, ts.json)) diffs.push("stdout JSON differs");
  if (c.rawStdout && python.stdout !== ts.stdout) diffs.push("stdout bytes differ");
  if (!c.ignoreStderr && python.stderr !== ts.stderr) diffs.push("stderr differs");
  if (!same(python.files, ts.files)) diffs.push("state files differ");
  if (!same(python.requests, ts.requests)) diffs.push("the requests made differ");
  return { python, ts, diffs };
}

/** vitest assertion: the same outcome from both, with the differing parts shown side by side. */
export async function expectParity(c: ParityCase): Promise<ParityResult> {
  const result = await runParity(c);
  expect({ code: result.ts.code, json: result.ts.json }, `exit code and stdout of ${c.hand} ${c.argv.join(" ")}`).toEqual({ code: result.python.code, json: result.python.json });
  expect(result.ts.files, "files written under state/").toEqual(result.python.files);
  expect(result.ts.requests, "requests made to the network").toEqual(result.python.requests);
  if (!c.ignoreStderr) expect(result.ts.stderr, "stderr").toBe(result.python.stderr);
  if (c.rawStdout) expect(result.ts.stdout, "stdout bytes").toBe(result.python.stdout);
  return result;
}

