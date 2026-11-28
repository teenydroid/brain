// How the core reaches the openclaw package: one place that knows the binary.
import { existsSync, readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

/** The installed package's directory: openclaw's `exports` hide package.json, so look for it in node_modules going up. */
function packageDir(): string {
  let dir = dirname(fileURLToPath(import.meta.url));
  for (;;) {
    const candidate = join(dir, "node_modules", "openclaw");
    if (existsSync(join(candidate, "openclaw.mjs"))) return candidate;
    const parent = dirname(dir);
    if (parent === dir) throw new Error("the openclaw package is not installed");
    dir = parent;
  }
}

/** The command and leading args that run `openclaw`; OPENCLAW_BIN overrides (tests, a global install). */
export function openclawCommand(env: Readonly<Record<string, string | undefined>> = process.env): { cmd: string; args: string[] } {
  if (env.OPENCLAW_BIN) return { cmd: env.OPENCLAW_BIN, args: [] };
  return { cmd: process.execPath, args: [join(packageDir(), "openclaw.mjs")] };
}

/** The pinned openclaw's version (its package.json), which an official plugin installed beside it must match. */
export function openclawVersion(): string {
  const pkg = JSON.parse(readFileSync(join(packageDir(), "package.json"), "utf8")) as { version?: unknown };
  if (typeof pkg.version !== "string" || !pkg.version) throw new Error("the openclaw package has no version");
  return pkg.version;
}
