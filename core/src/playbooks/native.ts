// What a playbook that runs natively may do, as data for the pieces that enforce it: the agent generated for
// it (agents/native.ts) and the policy teeny-guardrails holds that agent to. The message the agent is given is
// built in Python (bin/lib/native_playbook.py), where seed-cron needs it; this computes the same allowlist from
// the same parse, and test/playbooks/native.test.ts compares the two.

import type { Playbook } from "./load.js";

export interface NativeSpec {
  /** The agent that runs it: `pb-<playbook>`. */
  agent: string;
  playbook: string;
  surface: string;
  max_per_day: number;
  max_per_run: number;
  /** Each input's argv: the only commands of the playbook's inputs the shell may run. */
  inputs: string[][];
  /** The one hand it acts through, and the verbs of it that count as an action. */
  hand: { name: string; verbs: string[] } | null;
  /** Media helpers beside the inputs: made by hands/image, uploaded by hands/x. */
  helpers: string[][];
}

export const nativeAgentId = (name: string): string => `pb-${name}`;

export function nativeSpec(book: Playbook): NativeSpec {
  let hand: NativeSpec["hand"] = null;
  if (book.output.startsWith("hand:")) {
    const [name, verb] = book.output.slice("hand:".length).trim().split(/\s+/) as [string, string];
    hand = { name, verbs: [verb] };
  } else if (book.output !== "none") {
    hand = { name: "x", verbs: [book.output] };
  }
  const helpers: string[][] = [];
  const add = (argv: string[]): void => {
    if (!helpers.some((h) => h.join("\0") === argv.join("\0"))) helpers.push(argv);
  };
  if (book.media.includes("image")) {
    add(["hands/image", "generate"]);
    add(["hands/x", "upload"]);
  }
  if (book.media.includes("video")) {
    add(["hands/video", "generate"]);
    add(["hands/x", "upload"]);
  }
  return {
    agent: nativeAgentId(book.name),
    playbook: book.name,
    surface: book.surface,
    max_per_day: book.max_per_day,
    max_per_run: book.max_per_run,
    inputs: book.inputs.map((i) => i.argv),
    hand,
    helpers,
  };
}
