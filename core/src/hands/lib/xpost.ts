// A post on X from a hand that holds no X key: through `hands/x post`, as a
// separate process, so the guard, the posting rules and DRY_RUN that hand keeps
// all apply, and the keys stay in the one hand that holds them. Used by
// `hands/firsts announce` and `hands/requests vote-open`. A port of
// hands/lib/teeny_hands/xpost.py.

import { spawn } from "node:child_process";
import { statSync } from "node:fs";
import { join } from "node:path";
import * as hand from "./hand.js";

type Json = Record<string, unknown>;

/**
 * `hands/x post` with {text, anchor}: its answer, or Refused / HandError as it
 * said. `anchor` names what the post is about; hands/x refuses a post without
 * one. A dry run answers `dry_run` and no id.
 */
export async function post(text: string, anchor: string): Promise<Json> {
  const exe = join(hand.root(), "hands", "x");
  let isFile = false;
  try {
    isFile = statSync(exe).isFile();
  } catch {
    isFile = false;
  }
  if (!isFile) throw new hand.HandError("hands/x is not installed");
  const done = await new Promise<{ stdout: string; code: number }>((resolve, reject) => {
    const child = spawn(exe, ["post"], { cwd: hand.root(), stdio: ["pipe", "pipe", "pipe"] });
    let stdout = "";
    const timer = setTimeout(() => {
      child.kill("SIGKILL");
      reject(new hand.HandError("hands/x post: TimeoutExpired"));
    }, 180_000);
    child.stdout.on("data", (chunk: Buffer) => (stdout += chunk.toString("utf8")));
    child.stderr.resume();
    child.stdin.on("error", () => {});
    child.on("error", (exc) => {
      clearTimeout(timer);
      reject(new hand.HandError(`hands/x post: ${(exc as NodeJS.ErrnoException).code === "ENOENT" ? "FileNotFoundError" : "OSError"}`));
    });
    child.on("close", (code) => {
      clearTimeout(timer);
      resolve({ stdout, code: code ?? 1 });
    });
    child.stdin.end(JSON.stringify({ text, anchor }));
  });
  let answer: Json;
  try {
    const lines = done.stdout.trim().split(/\r?\n/);
    answer = JSON.parse(lines[lines.length - 1] as string) as Json;
    if (typeof answer !== "object" || answer === null) throw new Error("not an object");
  } catch {
    throw new hand.HandError(`hands/x post answered no JSON (exit ${done.code})`);
  }
  if (done.code === 2) {
    const detail = Object.fromEntries(Object.entries(answer).filter(([k]) => k !== "ok" && k !== "refused"));
    throw new hand.Refused(`hands/x post refused it: ${answer.refused}`, detail);
  }
  if (done.code !== 0) throw new hand.HandError(`hands/x post: ${answer.error ?? "failed"}`);
  return answer;
}
