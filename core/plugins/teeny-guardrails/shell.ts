// A small, strict reader for the command line the `exec` tool is given. It
// does not try to be a shell: it accepts the plain shapes an agent needs
// (words, quotes, pipes, `&&`, `;`, `2>&1`, `> file`) and refuses everything
// that could run something this plugin did not read: command and process
// substitution, backticks, variables, background jobs, here-documents,
// subshells, grouping, newlines. OpenHuman refused the same constructs
// ("command/process substitution, backticks and background (&) are not
// allowed"), and then checked every program against an allowlist.
//
// One exception: a double-quoted `"$(cat PATH)"` is read, because the mind is
// told to pass a JSON body that way (brain/agents/mind.md). It is only a read
// of a plain path, which is checked like any other file the command names.

export interface Word {
  /** The word with quotes removed. */
  text: string;
  /** Offset in the command string where the word starts. */
  start: number;
  /** Any quote or backslash was used in it. */
  quoted: boolean;
  /** An unquoted glob character (* ? [) is in it. */
  glob: boolean;
}

export interface Redirect {
  op: ">" | ">>" | "<";
  target: Word;
}

export interface Segment {
  words: Word[];
  redirects: Redirect[];
  /** Paths read by `"$(cat PATH)"` inside this segment. */
  catReads: string[];
  /** How the segment is joined to the next one: ";", "&&", "||", "|" or "" for the last. */
  joiner: string;
}

export type ParseResult = { ok: true; segments: Segment[] } | { ok: false; reason: string };

const FD_DUP = /^&[0-9]$/;

export function parseCommand(input: string): ParseResult {
  if (input.trim() === "") return { ok: false, reason: "the command is empty" };
  if (input.includes("\0")) return { ok: false, reason: "the command holds a NUL byte" };
  const segments: Segment[] = [];
  let seg: Segment = fresh();
  let word: { text: string; start: number; quoted: boolean; glob: boolean } | undefined;
  let i = 0;
  const n = input.length;

  function fresh(): Segment {
    return { words: [], redirects: [], catReads: [], joiner: "" };
  }
  function bad(reason: string): ParseResult {
    return { ok: false, reason };
  }
  function pushWord(): Word | undefined {
    if (!word) return undefined;
    const w: Word = { text: word.text, start: word.start, quoted: word.quoted, glob: word.glob };
    word = undefined;
    return w;
  }
  function endWord(): void {
    const w = pushWord();
    if (w) seg.words.push(w);
  }
  function startWord(): void {
    if (!word) word = { text: "", start: i, quoted: false, glob: false };
  }
  /** Reads a redirection target word starting at i (after spaces). */
  function readTarget(): Word | string {
    while (i < n && (input[i] === " " || input[i] === "\t")) i++;
    if (i >= n) return "a redirection has no target";
    const c = input[i] as string;
    if (";&|<>()`$\n{}".includes(c)) return "a redirection target must be a plain word";
    const start = i;
    let text = "";
    let quoted = false;
    while (i < n && !" \t;&|<>()`$\n{}".includes(input[i] as string)) {
      const ch = input[i] as string;
      if (ch === "'") {
        const end = input.indexOf("'", i + 1);
        if (end < 0) return "unclosed quote";
        text += input.slice(i + 1, end);
        quoted = true;
        i = end + 1;
      } else if (ch === '"') {
        const end = input.indexOf('"', i + 1);
        if (end < 0) return "unclosed quote";
        const inner = input.slice(i + 1, end);
        if (/[$`\\]/.test(inner)) return "a redirection target must be a plain word";
        text += inner;
        quoted = true;
        i = end + 1;
      } else if (ch === "\\") {
        return "a redirection target must be a plain word";
      } else {
        if ("*?[".includes(ch)) return "a redirection target may not be a glob";
        text += ch;
        i++;
      }
    }
    return { text, start, quoted, glob: false };
  }

  while (i < n) {
    const c = input[i] as string;
    if (c === "\n" || c === "\r") return bad("a newline would start a second command");
    if (c === " " || c === "\t") {
      endWord();
      i++;
      continue;
    }
    if (c === "'") {
      startWord();
      const end = input.indexOf("'", i + 1);
      if (end < 0) return bad("unclosed single quote");
      (word as { text: string }).text += input.slice(i + 1, end);
      (word as { quoted: boolean }).quoted = true;
      i = end + 1;
      continue;
    }
    if (c === '"') {
      startWord();
      (word as { quoted: boolean }).quoted = true;
      i++;
      let closed = false;
      while (i < n) {
        const ch = input[i] as string;
        if (ch === '"') {
          closed = true;
          i++;
          break;
        }
        if (ch === "`") return bad("backticks run a hidden command");
        if (ch === "\\") {
          const next = input[i + 1];
          if (next === undefined) return bad("a trailing backslash");
          if (next === "\n") return bad("a newline would start a second command");
          if ('"\\$`'.includes(next)) {
            (word as { text: string }).text += next;
          } else {
            (word as { text: string }).text += ch + next;
          }
          i += 2;
          continue;
        }
        if (ch === "$") {
          if (input[i + 1] === "(") {
            const close = input.indexOf(")", i + 2);
            const inner = close < 0 ? "" : input.slice(i + 2, close);
            const m = /^cat\s+([A-Za-z0-9_./~-]+)$/.exec(inner);
            if (!m) return bad("command substitution runs a hidden command (only \"$(cat PATH)\" is read)");
            seg.catReads.push(m[1] as string);
            // The substituted text is unknown; keep a marker so the word is never a program.
            (word as { text: string }).text += "\u0001";
            i = close + 1;
            continue;
          }
          return bad("variable and substitution syntax ($) is not read by this guard");
        }
        if (ch === "\n" || ch === "\r") return bad("a newline would start a second command");
        (word as { text: string }).text += ch;
        i++;
      }
      if (!closed) return bad("unclosed double quote");
      continue;
    }
    if (c === "\\") {
      const next = input[i + 1];
      if (next === undefined) return bad("a trailing backslash");
      if (next === "\n") return bad("a newline would start a second command");
      startWord();
      (word as { text: string }).text += next;
      (word as { quoted: boolean }).quoted = true;
      i += 2;
      continue;
    }
    if (c === "`") return bad("backticks run a hidden command");
    if (c === "$") return bad("variable and substitution syntax ($) is not read by this guard");
    if (c === "(" || c === ")") return bad("subshells and process substitution are not read by this guard");
    if (c === "{" || c === "}") return bad("grouping and brace expansion are not read by this guard");
    if (c === ";") {
      endWord();
      seg.joiner = ";";
      segments.push(seg);
      seg = fresh();
      i++;
      continue;
    }
    if (c === "|") {
      endWord();
      if (input[i + 1] === "&") return bad("|& is not read by this guard");
      if (input[i + 1] === "|") {
        seg.joiner = "||";
        i += 2;
      } else {
        seg.joiner = "|";
        i++;
      }
      segments.push(seg);
      seg = fresh();
      continue;
    }
    if (c === "&") {
      endWord();
      if (input[i + 1] === "&") {
        seg.joiner = "&&";
        segments.push(seg);
        seg = fresh();
        i += 2;
        continue;
      }
      return bad("a background job (&) can outlive the check");
    }
    if (c === ">" || c === "<") {
      // An fd number just before the operator ("2>") belongs to the redirection.
      let fd = "";
      if (word && /^[0-9]$/.test(word.text) && !word.quoted && word.start === i - 1) {
        fd = word.text;
        word = undefined;
      } else {
        endWord();
      }
      void fd;
      if (c === "<") {
        if (input[i + 1] === "<") return bad("here-documents are not read by this guard");
        if (input[i + 1] === "(") return bad("process substitution is not read by this guard");
        if (input[i + 1] === ">" || input[i + 1] === "&") return bad("this redirection is not read by this guard");
        i++;
        const t = readTarget();
        if (typeof t === "string") return bad(t);
        seg.redirects.push({ op: "<", target: t });
        continue;
      }
      if (input[i + 1] === "(") return bad("process substitution is not read by this guard");
      if (input[i + 1] === "&") {
        // 2>&1, >&2: an fd duplication, nothing is written to a file.
        const dup = input.slice(i + 1, i + 3);
        if (!FD_DUP.test(dup)) return bad("this redirection is not read by this guard");
        i += 3;
        if (i < n && !" \t;&|".includes(input[i] as string)) return bad("this redirection is not read by this guard");
        continue;
      }
      let op: ">" | ">>" = ">";
      i++;
      if (input[i] === ">") {
        op = ">>";
        i++;
      }
      if (input[i] === "|") return bad("this redirection is not read by this guard");
      const t = readTarget();
      if (typeof t === "string") return bad(t);
      seg.redirects.push({ op, target: t });
      continue;
    }
    startWord();
    if ("*?[".includes(c)) (word as { glob: boolean }).glob = true;
    (word as { text: string }).text += c;
    i++;
  }
  endWord();
  if (seg.words.length === 0 && seg.redirects.length === 0) {
    if (segments.length === 0) return bad("the command is empty");
    const last = segments[segments.length - 1] as Segment;
    if (last.joiner === ";") {
      // a trailing ";" is harmless
      return { ok: true, segments };
    }
    return bad(`the command ends after "${last.joiner}"`);
  }
  segments.push(seg);
  for (const s of segments) {
    if (s.words.length === 0) return bad("a command has an empty part");
  }
  return { ok: true, segments };
}

/** A path made safe to put inside single quotes for the shell. */
export function shellQuote(text: string): string {
  return /^[A-Za-z0-9_./:@%+=,-]+$/.test(text) ? text : `'${text.replace(/'/g, `'\\''`)}'`;
}
