// `teeny-core openclaw ARGS...`: the bundled OpenClaw CLI against the core's own
// state and generated config, so bin/doctor and an operator run
// `openclaw security audit` on exactly what the Gateway runs on, with the
// version the core pins and no global install.
import { spawn } from "node:child_process";
import { randomBytes } from "node:crypto";
import { chmodSync, existsSync, readFileSync, writeFileSync } from "node:fs";
import { join } from "node:path";
import { resolvePaths } from "../config/paths.js";
import { openclawCommand } from "./cli.js";

type Env = Readonly<Record<string, string | undefined>>;

/** Where the Gateway's shared token lives: beside its config, readable by this user only. */
export const gatewayTokenFile = (openclawDir: string): string => join(openclawDir, "gateway.token");

/**
 * The Gateway's shared token, created on first use and kept. The generated config
 * names this file as a SecretRef (`gateway.auth.token`), so the Gateway reads it and
 * so does every `openclaw` CLI run on that config (the core's cron calls, bin/openclaw
 * for bin/crons and the rest): none of them needs it in an environment, and the
 * Gateway's own environment, which its exec tool hands to every command, never has it.
 */
export function ensureGatewayToken(openclawDir: string): string {
  const file = gatewayTokenFile(openclawDir);
  if (existsSync(file)) {
    const token = readFileSync(file, "utf8").trim();
    if (token.length >= 32) return token;
  }
  const token = randomBytes(32).toString("hex");
  writeFileSync(file, `${token}\n`, { mode: 0o600 });
  chmodSync(file, 0o600);
  return token;
}

export function openclawEnv(env: Env, cwd?: string): Record<string, string> {
  const paths = resolvePaths(env, cwd);
  const out: Record<string, string> = {};
  for (const k of ["PATH", "HOME", "LANG", "TMPDIR", "NODE_OPTIONS"]) if (env[k]) out[k] = env[k] as string;
  out.OPENCLAW_STATE_DIR = paths.openclawDir;
  out.OPENCLAW_CONFIG_PATH = join(paths.openclawDir, "openclaw.json");
  return out;
}

/** Runs the OpenClaw CLI with inherited stdio; resolves with its exit code. */
export function openclawPassthrough(args: readonly string[], env: Env = process.env): Promise<number> {
  return new Promise((resolve) => {
    let command: { cmd: string; args: string[] };
    try {
      command = openclawCommand(env);
    } catch (e) {
      process.stderr.write(`teeny-core: ${e instanceof Error ? e.message : String(e)}\n`);
      resolve(127);
      return;
    }
    const child = spawn(command.cmd, [...command.args, ...args], { env: openclawEnv(env), stdio: "inherit" });
    child.on("error", (e) => {
      process.stderr.write(`teeny-core: could not run openclaw: ${e.message}\n`);
      resolve(127);
    });
    child.on("exit", (code, signal) => resolve(code ?? (signal ? 128 : 1)));
  });
}
