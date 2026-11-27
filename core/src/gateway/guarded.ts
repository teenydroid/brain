// Refuses the core's own turns of a shell agent until the supervisor has proven that teeny-guardrails loaded in the running Gateway (its file carries that Gateway's pid).
// A shell agent's exec runs with OpenClaw's own check off (agents/definitions.ts), so the plugin is the
// only gate: a turn that would start without it does not start. The supervisor removes the file at every
// Gateway start and the plugin writes it once its hooks are in, so right after a restart a turn waits for
// it (up to waitMs) and then fails. Turns the Gateway starts itself (cron) are covered by the supervisor,
// which stops a Gateway that never writes the file.
import { AgentRunError, type AgentRunRequest, type AgentRunResult, type AgentRunner } from "./client.js";

export class GuardedRunner implements AgentRunner {
  constructor(
    private readonly inner: AgentRunner,
    private readonly opts: { proven: () => boolean; holdsShell: (agentId: string) => boolean; waitMs?: number; pollMs?: number },
  ) {}

  async run(req: AgentRunRequest): Promise<AgentRunResult> {
    if (this.opts.holdsShell(req.agentId)) {
      const deadline = Date.now() + (this.opts.waitMs ?? 30_000);
      while (!this.opts.proven()) {
        if (Date.now() >= deadline) {
          throw new AgentRunError("teeny-guardrails is not loaded on the Gateway; a shell agent does not run without it", "guardrails_not_loaded");
        }
        await new Promise((r) => setTimeout(r, this.opts.pollMs ?? 100));
      }
    }
    return this.inner.run(req);
  }
}
