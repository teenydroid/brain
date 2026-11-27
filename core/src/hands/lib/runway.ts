// Runway as a sense (docs/roadmap.md, S2): how many days the wallet's USDC
// lasts at the current burn, and the tier that cuts optional spend when it is
// low. A port of hands/lib/teeny_hands/runway.py. Frozen (evals/frozen.txt),
// and the tiers are frozen config (evals/runway.toml, read from this
// checkout, never from the brain).
//
//   measure   USDC held over the last 7 days' cost a day
//   daily     measures, writes state/runway.json, and files one
//             `health · runway` stimulus a UTC day
//   limits    what the tier in force allows: `media` and `evolve_candidates`
//
// Off unless RUNWAY=1 in secrets/teeny.env: `daily` is skipped (exit 0) and
// `limits` cuts nothing. On, a runway not measured for STALE_DAYS counts as
// the `low` tier: an unread balance is not money.
//
// Only ever less: a tier lowers what Teeny may spend on optional things. It
// never lifts a cap, and nothing here spends or signs.
//
// STUB: the Python reads the wallet in-process (wallet.reads.balance and
// cost_parts). The wallet hand is not ported yet, so `measure` asks whatever
// `setWalletReads` installed, and throws HandError until one is. The wallet
// port installs the real one; until then the Python runway stays the one in use.

import { readFileSync, mkdirSync, renameSync, writeFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { parse as parseToml } from "smol-toml";
import * as hand from "./hand.js";
import * as stimuli from "./stimuli.js";
import { dumps } from "./py.js";

type Json = Record<string, unknown>;

export const TIERS_FILE = join(hand.codeRoot(), "evals", "runway.toml");
export const STALE_DAYS = 3;
export const BURN_DAYS = 7;
/** What "no tier" means for each thing a tier may set. */
export const UNCUT = { media: true, evolve_candidates: null as number | null };
const DAY = 86400;

export interface Tier {
  name: string;
  below_days: number;
  media: boolean;
  evolve_candidates: number | null;
}

/** The wallet reads the runway needs, as wallet/reads.py gives them. */
export interface WalletReads {
  balance(): Promise<Json> | Json;
  costParts(days: number): Promise<Json> | Json;
}

let wallet: WalletReads | null = null;
export function setWalletReads(reads: WalletReads | null): void {
  wallet = reads;
}

/** The tiers, lowest `below_days` first. A file that does not parse is an error, not an empty list: frozen config never fails open. */
export function tiers(path: string = TIERS_FILE): Tier[] {
  const data = parseToml(readFileSync(path, "utf8")) as Json;
  const out: Tier[] = [];
  for (const [name, raw] of Object.entries((data.tiers as Record<string, Json> | undefined) ?? {})) {
    const below = raw.below_days;
    if (typeof below !== "number" || below <= 0) throw new hand.HandError(`evals/runway.toml: tier ${name} needs below_days above zero`);
    const media = raw.media ?? true;
    const cands = raw.evolve_candidates ?? null;
    if (typeof media !== "boolean" || (cands !== null && (typeof cands !== "number" || !Number.isInteger(cands) || cands < 1))) {
      throw new hand.HandError(`evals/runway.toml: tier ${name} has a bad media or evolve_candidates`);
    }
    out.push({ name, below_days: below, media, evolve_candidates: cands as number | null });
  }
  return out.sort((a, b) => a.below_days - b.below_days);
}

/** The lowest tier the runway is under; null above them all. */
export function tierFor(days: number | null, table: readonly Tier[]): Tier | null {
  if (days === null) return null;
  return table.find((t) => days < t.below_days) ?? null;
}

const path = (): string => join(hand.stateDir(), "runway.json");

/** USDC held and the burn a day. Throws HandError when either cannot be read (an incomplete cost report counts as unread). */
export async function measure(): Promise<Json> {
  if (!wallet) throw new hand.HandError("the wallet reads are not available to the TypeScript runway yet");
  const balance = (await wallet.balance()) as { usdc: { raw: string | number }; evm?: { networks?: Record<string, { usdc?: { raw: string | number } }> } };
  let micro = Number(balance.usdc.raw);
  for (const network of Object.values(balance.evm?.networks ?? {})) {
    if (network.usdc && typeof network.usdc === "object") micro += Number(network.usdc.raw);
  }
  const parts = (await wallet.costParts(BURN_DAYS)) as {
    complete: boolean;
    models: { micro: number };
    x402: { micro: number };
    bills: { share_micro: number }[];
  };
  if (!parts.complete) throw new hand.HandError("a cost source could not be read in full");
  const spent = parts.models.micro + parts.x402.micro + parts.bills.reduce((n, b) => n + b.share_micro, 0);
  const burn = spent / BURN_DAYS;
  const days = burn <= 0 ? null : micro / burn;
  return { usdc_micro: micro, burn_micro_per_day: pyRound(burn), days: days === null ? null : Math.round(days * 10) / 10 };
}

/** Python's round(): half to even, to a whole number. */
function pyRound(x: number): number {
  const f = Math.floor(x);
  const d = x - f;
  if (d < 0.5) return f;
  if (d > 0.5) return f + 1;
  return f % 2 === 0 ? f : f + 1;
}

function cuts(tier: Pick<Tier, "media" | "evolve_candidates"> | null): string {
  if (tier === null) return "nothing is cut";
  const cut: string[] = [];
  if (!tier.media) cut.push("no pictures or clips");
  if (tier.evolve_candidates) cut.push(`at most ${tier.evolve_candidates} evolve candidate(s) a generation`);
  return cut.join(", ") || "nothing is cut";
}

/** Python's `{x:g}`: 10.0 is "10", 3.5 is "3.5". */
const g = (x: number): string => String(Number(x.toPrecision(6)));

/** Measures, saves, and files one `health · runway` stimulus a UTC day. */
export async function daily(): Promise<Json> {
  if (!hand.flag("RUNWAY", false)) return { skipped: "the runway sense is off (RUNWAY=1 in secrets/teeny.env turns it on)" };
  const table = tiers();
  const now = hand.now();
  const day = new Date(now * 1000).toISOString().slice(0, 10);
  let got: Json;
  try {
    got = await measure();
  } catch (exc) {
    if (!(exc instanceof hand.HandError)) throw exc;
    const state = load();
    const out: Json = { date: day, measured: false, error: exc.message.slice(0, 200) };
    const lim = limits();
    const text = lim.tier
      ? `Your runway could not be measured today (${out.error}). Until it is, you spend as the ${lim.tier} tier: ${cuts(lim as unknown as Tier)}.`
      : `Your runway could not be measured today (${out.error}).`;
    out.filed = stimuli.add("health", "runway", day, text, null, { measured: false, tier: lim.tier });
    out.last = state;
    return out;
  }
  const tier = tierFor(got.days as number | null, table);
  const state: Json = { ...got, tier: tier ? tier.name : null, at: now };
  const file = path();
  mkdirSync(dirname(file), { recursive: true });
  const tmp = file.replace(/\.[^./]*$/, "") + ".tmp";
  writeFileSync(tmp, dumps(state, { ensureAscii: true, sortKeys: true }));
  renameSync(tmp, file);
  const usdc = (got.usdc_micro as number) / 1e6;
  const burn = (got.burn_micro_per_day as number) / 1e6;
  let text =
    got.days === null
      ? `Runway: you hold ${usdc.toFixed(2)} USDC and nothing cost money this week, so it does not run down.`
      : `Runway: you hold ${usdc.toFixed(2)} USDC and running you cost ${burn.toFixed(2)} a day this week: ${g(got.days as number)} days at this rate.`;
  text += ` Tier: ${state.tier ?? "none"}; ${cuts(tier)}.`;
  const out: Json = { date: day, measured: true, ...state };
  const payload = Object.fromEntries(Object.entries(state).filter(([k]) => k !== "at"));
  out.filed = stimuli.add("health", "runway", day, text, null, payload);
  return out;
}

export function load(): Json | null {
  try {
    const state = JSON.parse(readFileSync(path(), "utf8")) as unknown;
    return typeof state === "object" && state !== null && !Array.isArray(state) ? (state as Json) : null;
  } catch {
    return null;
  }
}

export interface Limits {
  media: boolean;
  evolve_candidates: number | null;
  tier: string | null;
  days: number | null;
  why: string | null;
}

/** What the tier in force allows. Off: nothing is cut. On and stale or never measured: the `low` tier (else the highest tier there is). */
export function limits(): Limits {
  if (!hand.flag("RUNWAY", false)) return { ...UNCUT, tier: null, days: null, why: null };
  const table = tiers();
  const state = load();
  const fresh = state !== null && Number(state.at || 0) >= hand.now() - STALE_DAYS * DAY;
  let tier: Tier | null;
  let days: number | null;
  if (fresh) {
    tier = table.find((t) => t.name === state.tier) ?? null;
    days = (state.days as number | null | undefined) ?? null;
  } else {
    tier = table.find((t) => t.name === "low") ?? table[table.length - 1] ?? null;
    days = null;
  }
  if (tier === null) return { ...UNCUT, tier: null, days, why: null };
  const why = fresh && days !== null ? `runway ${g(days)} days, under ${g(tier.below_days)}` : `runway not measured in the last ${STALE_DAYS} days`;
  return { media: tier.media, evolve_candidates: tier.evolve_candidates, tier: tier.name, days, why };
}

/** `n` candidates, cut to the tier's most; and why, when cut. */
export function capCandidates(n: number): [number, string | null] {
  const lim = limits();
  const most = lim.evolve_candidates;
  if (most !== null && n > most) return [most, `${lim.tier} tier (${lim.why}): ${most} candidate(s), not ${n}`];
  return [n, null];
}

/** The line in the mind's beat header, or null when the sense is off. */
export function headerLine(): string | null {
  if (!hand.flag("RUNWAY", false)) return null;
  let lim: Limits;
  try {
    lim = limits();
  } catch (exc) {
    if (exc instanceof hand.HandError || (exc as NodeJS.ErrnoException).code === "ENOENT") return null;
    throw exc;
  }
  if (lim.tier === null) return lim.days === null ? null : `Runway: ${g(lim.days)} days at the current burn.`;
  return `Runway: ${lim.why}. You are on the ${lim.tier} tier: ${cuts(lim)}.`;
}
