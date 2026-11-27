// Where the core finds the checkout, its brain, state and secrets. The names
// are the ones bin/lib/teeny.sh exports, so `bin/spine` needs no new variable.
import { join, resolve } from "node:path";

export interface CorePaths {
  root: string;
  brainDir: string;
  stateDir: string;
  secretsDir: string;
  /** state/openclaw: OPENCLAW_STATE_DIR, the generated config, the agents' workspaces. */
  openclawDir: string;
}

type Env = Readonly<Record<string, string | undefined>>;

export function resolvePaths(env: Env, cwd: string = process.cwd()): CorePaths {
  const root = resolve(env.TEENY_ROOT || cwd);
  const stateDir = resolve(env.TEENY_STATE || join(root, "state"));
  return {
    root,
    brainDir: resolve(env.OPENHUMAN_WORKSPACE || join(root, "brain")),
    stateDir,
    secretsDir: resolve(env.TEENY_SECRETS || join(root, "secrets")),
    openclawDir: join(stateDir, "openclaw"),
  };
}

export interface ServeSettings {
  host: string;
  port: number;
}

export function serveSettings(env: Env): ServeSettings {
  const port = Number(env.OPENHUMAN_CORE_PORT || 7788);
  if (!Number.isInteger(port) || port < 0 || port > 65535) {
    throw new Error(`OPENHUMAN_CORE_PORT is not a port: ${env.OPENHUMAN_CORE_PORT}`);
  }
  return { host: env.OPENHUMAN_CORE_HOST || "127.0.0.1", port };
}
