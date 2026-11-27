import { Cron } from "croner";
import { RpcError } from "../server/types.js";

export type Schedule =
  | { kind: "cron"; expr: string; tz: string }
  | { kind: "at"; at: string };

function validTz(tz: string): boolean {
  try {
    new Intl.DateTimeFormat("en-US", { timeZone: tz });
    return true;
  } catch {
    return false;
  }
}

/** Checks a schedule object from a caller and returns it in the stored shape. */
export function parseSchedule(raw: unknown): Schedule {
  if (typeof raw !== "object" || raw === null || Array.isArray(raw)) {
    throw new RpcError("schedule must be an object", -32602);
  }
  const s = raw as Record<string, unknown>;
  if (s.kind === "at") {
    if (typeof s.at !== "string" || Number.isNaN(Date.parse(s.at))) {
      throw new RpcError("schedule.at must be an RFC 3339 timestamp", -32602);
    }
    return { kind: "at", at: new Date(Date.parse(s.at)).toISOString() };
  }
  if (s.kind === "cron") {
    if (typeof s.expr !== "string" || s.expr.trim() === "") {
      throw new RpcError("schedule.expr must be a cron expression", -32602);
    }
    const tz = typeof s.tz === "string" && s.tz !== "" ? s.tz : "UTC";
    if (!validTz(tz)) throw new RpcError(`unknown time zone ${tz}`, -32602);
    const expr = s.expr.trim();
    try {
      new Cron(expr, { timezone: tz });
    } catch (error) {
      throw new RpcError(`bad cron expression ${expr}: ${(error as Error).message}`, -32602);
    }
    return { kind: "cron", expr, tz };
  }
  throw new RpcError("schedule.kind must be 'cron' or 'at'", -32602);
}

/** The first run strictly after FROM (epoch ms), or null when there is none. */
export function nextRunMs(schedule: Schedule, fromMs: number): number | null {
  if (schedule.kind === "at") {
    const at = Date.parse(schedule.at);
    return at > fromMs ? at : null;
  }
  const next = new Cron(schedule.expr, { timezone: schedule.tz }).nextRun(new Date(fromMs));
  return next ? next.getTime() : null;
}
