// A ledger of model calls, one JSON line per agent run, so cost_report can
// answer `{totals}` for the last N days (the Gateway keeps its own usage; this
// is what the core saw come back from each run).
import { appendFileSync, existsSync, mkdirSync, readFileSync } from "node:fs";
import { dirname } from "node:path";

export interface CostRow {
  ts: number;
  agent: string;
  model?: string;
  costUsd?: number;
  tokens?: number;
}

export class CostLog {
  constructor(private readonly file: string) {}

  record(row: CostRow): void {
    mkdirSync(dirname(this.file), { recursive: true });
    appendFileSync(this.file, `${JSON.stringify(row)}\n`, { mode: 0o600 });
  }

  rows(sinceMs: number): CostRow[] {
    if (!existsSync(this.file)) return [];
    const out: CostRow[] = [];
    for (const line of readFileSync(this.file, "utf8").split("\n")) {
      if (!line.trim()) continue;
      try {
        const row = JSON.parse(line) as CostRow;
        if (typeof row.ts === "number" && row.ts >= sinceMs) out.push(row);
      } catch {
        // a torn line is skipped, never fatal
      }
    }
    return out;
  }

  report(days: number, now: number = Date.now()): { totals: Record<string, number>; days: number } {
    const rows = this.rows(now - days * 86_400_000);
    let cost = 0;
    let unpriced = 0;
    for (const r of rows) {
      if (typeof r.costUsd === "number") cost += r.costUsd;
      else unpriced += 1;
    }
    // OpenClaw reports one costUsd per run (recorded, else estimated from the model's price): it is all
    // counted as charged; there is no separate estimated figure.
    return { days, totals: { cost_usd: cost, calls: rows.length, unpriced_calls: unpriced, charged_usd: cost, estimated_usd: 0 } };
  }
}
