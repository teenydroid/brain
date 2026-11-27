import { accessSync, constants } from "node:fs";
import { delimiter, join } from "node:path";

const NAMES = ["chromium", "chromium-browser", "google-chrome", "google-chrome-stable", "chrome"];

/** The Chrome/Chromium binary OpenClaw's browser would use: $CHROME_BIN, else the first on PATH. */
export function findChrome(env: Readonly<Record<string, string | undefined>> = process.env): string | undefined {
  const exec = (p: string): boolean => {
    try {
      accessSync(p, constants.X_OK);
      return true;
    } catch {
      return false;
    }
  };
  if (env.CHROME_BIN) return exec(env.CHROME_BIN) ? env.CHROME_BIN : undefined;
  for (const dir of (env.PATH ?? "").split(delimiter).filter(Boolean)) {
    for (const name of NAMES) if (exec(join(dir, name))) return join(dir, name);
  }
  return undefined;
}
