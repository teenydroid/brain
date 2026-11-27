// What the hand-framework tests share: a scratch TEENY_ROOT per test, a pinned
// clock, a one-off build of src/hands for the tests that run a real process,
// and a tiny JSON-RPC server.

import { execFileSync, spawnSync } from "node:child_process";
import { mkdirSync, mkdtempSync, rmSync, writeFileSync } from "node:fs";
import { createServer, type IncomingMessage, type Server, type ServerResponse } from "node:http";
import type { AddressInfo } from "node:net";
import { tmpdir } from "node:os";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { afterEach, beforeEach } from "vitest";
import * as hand from "../../../src/hands/lib/hand.js";

export const CORE = resolve(dirname(fileURLToPath(import.meta.url)), "..", "..", "..");
export const REPO = resolve(CORE, "..");
/** 2026-09-14T07:00:00Z, as the Python tests pin it. */
export const T0 = 1_789_369_200.0;

export interface Scratch {
  root: string;
  secrets(text: string): void;
  at(offset: number): void;
}

const KEYS = ["TEENY_ROOT", "TEENY_NOW", "DRY_RUN", "OPENHUMAN_CORE_URL", "OPENHUMAN_CORE_TOKEN", "TEENY_HANDS_DEBUG"];

/** A scratch TEENY_ROOT (with secrets/) per test, the env cache cleared, and the environment put back after. */
export function scratch(options: { now?: number | null } = {}): Scratch {
  const scope = {} as Scratch;
  let saved: Record<string, string | undefined> = {};
  beforeEach(() => {
    saved = Object.fromEntries(KEYS.map((k) => [k, process.env[k]]));
    for (const k of KEYS) delete process.env[k];
    scope.root = mkdtempSync(join(tmpdir(), "hands-"));
    mkdirSync(join(scope.root, "secrets"));
    process.env.TEENY_ROOT = scope.root;
    if (options.now !== null) process.env.TEENY_NOW = String(options.now ?? T0);
    hand.resetSecrets();
    scope.secrets = (text: string) => {
      writeFileSync(join(scope.root, "secrets", "teeny.env"), text);
      hand.resetSecrets();
    };
    scope.at = (offset: number) => {
      process.env.TEENY_NOW = String(T0 + offset);
    };
  });
  afterEach(() => {
    for (const k of KEYS) {
      if (saved[k] === undefined) delete process.env[k];
      else process.env[k] = saved[k];
    }
    hand.resetSecrets();
    rmSync(scope.root, { recursive: true, force: true });
  });
  return scope;
}

let built: string | null = null;

/** src/hands compiled once into a temp dir, for tests that run a real node process. Returns the dir holding hands/lib/*.js. */
export function build(): string {
  if (built) return built;
  const out = mkdtempSync(join(tmpdir(), "hands-build-"));
  const tsc = join(CORE, "node_modules", ".bin", "tsc");
  execFileSync(tsc, ["-p", join(CORE, "test", "hands", "tsconfig.json"), "--outDir", out], { cwd: CORE, stdio: "pipe" });
  // The compiled files import the packages the core has installed.
  execFileSync("ln", ["-s", join(CORE, "node_modules"), join(out, "node_modules")]);
  writeFileSync(join(out, "package.json"), '{"type":"module"}');
  built = out;
  return out;
}

export function pythonAvailable(): boolean {
  return spawnSync("python3", ["-I", "-c", "import sys; assert sys.version_info[:2] == (3, 11)"]).status === 0;
}

export interface RpcServer {
  url: string;
  calls: { authorization: string | undefined; body: Record<string, unknown> }[];
  close(): Promise<void>;
}

/** A JSON-RPC-ish server on 127.0.0.1 that answers every POST with `reply`. */
export async function serveRpc(reply: unknown): Promise<RpcServer> {
  const calls: RpcServer["calls"] = [];
  const server: Server = createServer((req: IncomingMessage, res: ServerResponse) => {
    const chunks: Buffer[] = [];
    req.on("data", (c: Buffer) => chunks.push(c));
    req.on("end", () => {
      calls.push({ authorization: req.headers.authorization, body: JSON.parse(Buffer.concat(chunks).toString()) });
      const data = JSON.stringify(reply);
      res.writeHead(200, { "content-length": Buffer.byteLength(data) });
      res.end(data);
    });
  });
  await new Promise<void>((done) => server.listen(0, "127.0.0.1", done));
  return {
    url: `http://127.0.0.1:${(server.address() as AddressInfo).port}`,
    calls,
    close: () => new Promise<void>((done) => server.close(() => done())),
  };
}

/** Runs `fn` with process.stdout/stderr captured; returns what was written. */
export async function capture<T>(fn: () => Promise<T>): Promise<{ result: T; stdout: string; stderr: string }> {
  let stdout = "";
  let stderr = "";
  const out = process.stdout.write.bind(process.stdout);
  const err = process.stderr.write.bind(process.stderr);
  process.stdout.write = ((chunk: string | Uint8Array, ...rest: unknown[]) => {
    stdout += String(chunk);
    const cb = rest.find((r) => typeof r === "function") as (() => void) | undefined;
    cb?.();
    return true;
  }) as typeof process.stdout.write;
  process.stderr.write = ((chunk: string | Uint8Array) => {
    stderr += String(chunk);
    return true;
  }) as typeof process.stderr.write;
  try {
    return { result: await fn(), stdout, stderr };
  } finally {
    process.stdout.write = out;
    process.stderr.write = err;
  }
}
