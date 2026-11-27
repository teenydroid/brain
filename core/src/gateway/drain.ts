// Lets a Gateway restart wait for the turns in flight instead of killing them. A restart
// that arrives mid-turn (bin/spine sends settings in a row; an operator changes a model)
// once ended an evolve generation. The runner counts what is running; quiesce() holds new
// turns at the door, waits for the running ones to finish (or a deadline), and release()
// lets the held ones through once the Gateway is back.
import type { AgentRunRequest, AgentRunResult, AgentRunner } from "./client.js";

export class DrainingRunner implements AgentRunner {
  private inflight = 0;
  private gate: Promise<void> | undefined;
  private open: (() => void) | undefined;
  private idle: (() => void) | undefined;

  constructor(private readonly inner: AgentRunner) {}

  get running(): number {
    return this.inflight;
  }

  async run(req: AgentRunRequest): Promise<AgentRunResult> {
    while (this.gate) await this.gate;
    this.inflight += 1;
    try {
      return await this.inner.run(req);
    } finally {
      this.inflight -= 1;
      if (this.inflight === 0) this.idle?.();
    }
  }

  /** Stop admitting turns and wait up to maxMs for the running ones. Resolves true when none is left. */
  async quiesce(maxMs: number): Promise<boolean> {
    if (!this.gate) this.gate = new Promise<void>((r) => (this.open = r));
    if (this.inflight === 0) return true;
    return new Promise<boolean>((resolve) => {
      const timer = setTimeout(() => {
        this.idle = undefined;
        resolve(false);
      }, maxMs);
      this.idle = () => {
        clearTimeout(timer);
        this.idle = undefined;
        resolve(true);
      };
    });
  }

  release(): void {
    this.open?.();
    this.gate = undefined;
    this.open = undefined;
  }
}
