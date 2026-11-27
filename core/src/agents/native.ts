// The agents of playbooks that run natively (`run = "native"` in a playbook's front matter, docs/native-playbooks.md).
// One agent per playbook, `pb-<name>`, generated here from the playbook the way the others are generated from
// brain/agents/*.toml. It speaks in the act agent's voice (the same prompt, SOUL.md and IDENTITY.md), holds a
// shell and nothing else, and teeny-guardrails holds that shell to exactly the commands nativeSpec lists. Its
// memory is CortexDB's, recalled into each turn like any agent's (the recall policy is built from every def).
import { existsSync, readdirSync, readFileSync } from "node:fs";
import { join } from "node:path";
import { loadAgentDef, type AgentDef } from "./definitions.js";
import { loadPlaybook, nativeSpec } from "../playbooks/index.js";

export const NATIVE_ADDENDUM = [
  "# how you run a playbook natively",
  "",
  "In this turn you are not only drafting: you also hold a shell, and you run the playbook yourself. The message names the commands you may run, in order, and the one hand you act through. Your shell refuses any other command, and it counts your calls to the hand, so a second call in a run (or past the day's cap) is refused whatever you write. Where the instructions above say to answer with JSON only, that JSON is the body you give the hand, and your final answer is the one-line report the message asks for.",
].join("\n");

/** Whether a playbook's front matter asks to run natively (read leniently: a broken playbook is reported, not skipped). */
function wantsNative(text: string): boolean {
  const front = text.split("+++")[1] ?? "";
  return /^run\s*=\s*"native"\s*(#.*)?$/m.test(front);
}

export interface NativeLoad {
  defs: AgentDef[];
  /** Playbooks that say `run = "native"` and could not become an agent, with why. */
  errors: Record<string, string>;
}

/** Tool calls one run may make: every input, a helper's two steps, the hand's calls, and some room to read and retry. */
export function nativeMaxToolCalls(spec: { inputs: unknown[]; helpers: unknown[]; max_per_run: number }): number {
  return spec.inputs.length + spec.helpers.length * 2 + spec.max_per_run * 3 + 6;
}

export function loadNativeDefs(brainDir: string, root: string): NativeLoad {
  const dir = join(brainDir, "playbooks");
  const out: NativeLoad = { defs: [], errors: {} };
  if (!existsSync(dir)) return out;
  const actFile = join(brainDir, "agents", "act.toml");
  const act = existsSync(actFile) ? loadAgentDef(brainDir, actFile) : undefined;
  for (const file of readdirSync(dir).filter((f) => f.endsWith(".md")).sort()) {
    const name = file.slice(0, -3);
    if (!wantsNative(readFileSync(join(dir, file), "utf8"))) continue;
    try {
      const book = loadPlaybook(brainDir, name, { handsDir: join(root, "hands"), repoRoot: root });
      const spec = nativeSpec(book);
      out.defs.push({
        id: spec.agent,
        displayName: `Teeny (playbook ${name})`,
        whenToUse: `Runs the ${name} playbook natively, from cron. Not a chat delegate.`,
        temperature: act?.temperature,
        maxIterations: nativeMaxToolCalls(spec),
        tools: ["shell"],
        prompt: [act?.prompt ?? "", NATIVE_ADDENDUM].filter(Boolean).join("\n\n"),
        subagentAllowlist: [],
        browserViewOnly: false,
        // CortexDB recall supplies the long-term memory, at the act agent's budget.
        omitMemoryContext: act?.omitMemoryContext ?? false,
        ...(act?.memoryBlockMaxTokens !== undefined ? { memoryBlockMaxTokens: act.memoryBlockMaxTokens } : {}),
        native: spec,
      });
    } catch (e) {
      // Any failure (a playbook that breaks a rule, a checkout with no hands/) leaves the playbook without an agent
      // and says why; it never stops the core from booting. bin/seed-cron --check is what refuses it before a deploy.
      out.errors[name] = e instanceof Error ? e.message : String(e);
    }
  }
  return out;
}
