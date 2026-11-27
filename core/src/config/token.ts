// The RPC bearer: the environment wins, then secrets/core.token, exactly as
// bin/lib/teeny.sh core_token does. Never printed.
import { readFileSync } from "node:fs";
import { join } from "node:path";

export function resolveToken(env: Readonly<Record<string, string | undefined>>, secretsDir: string): string {
  const fromEnv = env.OPENHUMAN_CORE_TOKEN?.replace(/\s+/g, "");
  if (fromEnv) return fromEnv;
  try {
    const fromFile = readFileSync(join(secretsDir, "core.token"), "utf8").replace(/\s+/g, "");
    if (fromFile) return fromFile;
  } catch {
    // fall through to the error
  }
  throw new Error(`no core token: set OPENHUMAN_CORE_TOKEN or write ${join(secretsDir, "core.token")}`);
}
