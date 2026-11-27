// One line of text, safe to keep and to publish: no URL (and so no query
// string), no address, nothing that looks like a key, and not too long.
// A port of hands/lib/teeny_hands/scrub.py.

import { pyRstrip, pySplit } from "./py.js";

export const MAX_CHARS = 300;

// \b in Python is Unicode-aware: a letter, digit or underscore of any script.
const W = "[\\p{L}\\p{N}_]";
const URL = new RegExp(`(?<!${W})[a-z][a-z0-9+.-]*://\\S+`, "giu");
const HOST = new RegExp(`(?<!${W})\\d{1,3}(?:\\.\\d{1,3}){3}(?::\\d+)?(?!${W})`, "gu");
const KEYISH = new RegExp(`(?<!${W})(?:sk|pk|ghp|gho|github_pat|xox[a-z]?|AKIA)[-_A-Za-z0-9]{6,}|[A-Za-z0-9_-]{32,}`, "gu");

export function scrub(text: unknown, maxChars: number = MAX_CHARS): string {
  let line = pySplit(String(text)).join(" ");
  line = line.replace(URL, "[url]").replace(HOST, "[host]").replace(KEYISH, "[redacted]");
  const chars = Array.from(line);
  return chars.length <= maxChars ? line : pyRstrip(chars.slice(0, maxChars).join("")) + "...";
}
