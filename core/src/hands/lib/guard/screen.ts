// The lexical screen: does this text contain a word the account does not
// touch? Deterministic, synchronous, no model. A port of
// hands/lib/teeny_hands/guard/screen.py (see lexicon.ts for why it lives here
// for now). Python's `re` and JS's RegExp are both backtrackers, so the
// patterns are used as written, with the `u` flag for Unicode classes.

import { IDIOMS, JUDGED_TERMS, LEXICON, SEXUAL_FAMILY, SEXUAL_FAMILY_TERM } from "./lexicon.js";

export const INBOUND = "inbound";
export const OUTBOUND = "outbound";
export const NARRATIVE = "narrative";
export const ARTICLE = "article";
export const DIRECTIONS = [INBOUND, OUTBOUND, NARRATIVE, ARTICLE] as const;

export interface ScreenMatch {
  category: string;
  /** The lexicon term that matched, for the log. Never the text around it. */
  term: string;
  judged: boolean;
}

const UNLEET: Record<string, string> = { "0": "o", "1": "i", "!": "i", "3": "e", "4": "a", "@": "a", "5": "s", $: "s", "7": "t" };
const ZERO_WIDTH = new RegExp(["\\u200b", "\\u200c", "\\u200d", "\\u2060", "\\ufeff"].join("|"), "gu");

const NOT_AFTER_ALNUM = "(?<![a-zA-Z0-9])";
const NOT_BEFORE_ALNUM = "(?![a-zA-Z0-9])";
const GAP = "[\\s\\-_.*]+";
const LOOSE_GAP = "[\\s\\-_.*]+(?:[a-z]+[\\s\\-_.*]+)?";

const isAsciiLower = (c: string): boolean => c >= "a" && c <= "z";
const escape = (c: string): string => c.replace(/[.*+?^${}()|[\]\\/]/g, "\\$&");

/**
 * One term as a pattern: each letter may repeat ("fuuuck"), a space or hyphen
 * matches any run of spacing or punctuation, and the whole must stand on its
 * own rather than sit inside a longer word.
 */
export function termPattern(term: string, looseGaps: boolean): RegExp {
  const lower = term.toLowerCase();
  const letters = [...lower].filter((c) => c !== " " && c !== "-");
  // A term that is one letter repeated ("xxx", "kkk") keeps its count.
  if (letters.length > 1 && new Set(letters).size === 1 && isAsciiLower(letters[0] as string)) {
    return new RegExp(`${NOT_AFTER_ALNUM}(?:${letters[0]}{${letters.length},})${NOT_BEFORE_ALNUM}`, "iu");
  }
  let body = "";
  let previous: string | null = null;
  for (const c of lower) {
    if (c === " " || c === "-") {
      if (previous !== " ") body += looseGaps ? LOOSE_GAP : GAP;
      previous = " ";
      continue;
    }
    // Consecutive identical letters collapse to one repeating class; a doubled vowel keeps asking for two.
    if (c === previous && isAsciiLower(c)) {
      const single = `${c}+`;
      if ("aeiou".includes(c) && body.endsWith(single)) body = body.slice(0, -1) + "{2,}";
      continue;
    }
    body += isAsciiLower(c) ? `${c}+` : escape(c);
    previous = c;
  }
  return new RegExp(`${NOT_AFTER_ALNUM}(?:${body})${NOT_BEFORE_ALNUM}`, "iu");
}

const idiomPatterns = IDIOMS.map((idiom) => new RegExp(termPattern(idiom, true).source, "giu"));

const compiled = LEXICON.map((entry) => ({
  category: entry.category,
  outboundOnly: entry.outboundOnly,
  inboundOnly: entry.inboundOnly,
  terms: entry.terms.map((term) => [term, termPattern(term, false)] as const),
}));

const shapes = SEXUAL_FAMILY.map(([category, pattern]) => [category, new RegExp(pattern, "iu")] as const);

/** The text as the screen reads it: folded, unspaced, idioms removed. Returns the plain reading and the leetspeak one. */
export function normaliseForScreen(text: string, idioms: boolean): [string, string] {
  let plain = text.normalize("NFKC").toLowerCase().replace(ZERO_WIDTH, "");
  if (idioms) for (const idiom of idiomPatterns) plain = plain.replace(idiom, " ");
  const leet = [...plain].map((c) => UNLEET[c] ?? c).join("");
  return [plain, leet];
}

/**
 * The prohibited term in the text, or null. The longest matching term wins,
 * and a term held on sight always wins over a judged one.
 */
export function screenText(text: string, direction: string): ScreenMatch | null {
  return screenTextWith(text, direction, true);
}

export function screenTextWith(text: string, direction: string, idioms = true): ScreenMatch | null {
  if (!(DIRECTIONS as readonly string[]).includes(direction)) throw new Error(`unknown direction '${direction}'`);
  if (!text.trim()) return null;
  const [plain, leet] = normaliseForScreen(text, idioms);
  let best: ScreenMatch | null = null;
  let judged: ScreenMatch | null = null;
  for (const { category, outboundOnly, inboundOnly, terms } of compiled) {
    if (outboundOnly && direction !== OUTBOUND) continue;
    if (inboundOnly && direction !== INBOUND) continue;
    for (const [term, pattern] of terms) {
      const soft = JUDGED_TERMS.has(term);
      const kept = soft ? judged : best;
      if (kept !== null && term.length <= kept.term.length) continue;
      if (pattern.test(plain) || pattern.test(leet)) {
        if (soft) judged = { category, term, judged: true };
        else best = { category, term, judged: false };
      }
    }
  }
  if (best === null) {
    for (const [category, shape] of shapes) {
      if (shape.test(plain) || shape.test(leet)) return { category, term: SEXUAL_FAMILY_TERM, judged: false };
    }
  }
  return best ?? judged;
}
