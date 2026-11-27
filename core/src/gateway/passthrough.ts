// `teeny-core openclaw ARGS...`: the bundled OpenClaw CLI against the core's own
// state and generated config, so bin/doctor and an operator run
// `openclaw security audit` on exactly what the Gateway runs on, with the
// version the core pins and no global install.
import { spawn } from "node:child_process";
import { join } from "node:path";
import { resolvePaths } from "../config/paths.js";
import { openclawCommand } from "./cli.js";

type Env = Readonly<Record<string, string | undefined>>;

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
