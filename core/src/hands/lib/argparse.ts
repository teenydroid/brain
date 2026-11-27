// A port of the part of Python 3.11's argparse the hands use, so a ported hand
// accepts the argv its Python twin accepted (prefix abbreviations, --flag=value,
// "--", greedy positionals, subparsers) and says no in the same words, on stderr,
// with exit status 2 (which hand.run turns into the contract's error JSON).
//
// Not ported: mutually exclusive groups, parents, fromfile, REMAINDER,
// %(default)s expansion in help text. Help is laid out as argparse lays it out
// at COLUMNS (default 80); a help line wraps at word breaks only.

import { pyRepr, pyInt, pyFloat } from "./py.js";

// eslint-disable-next-line @typescript-eslint/no-explicit-any
export type Args = Record<string, any>;

/** A type function's way to say "not this type": argparse reports `invalid <name> value: 'x'`. */
export class ValueError extends Error {
  override name = "ValueError";
}
/** A type function's own message, reported as it is. */
export class ArgumentTypeError extends Error {
  override name = "ArgumentTypeError";
}
/** Python's SystemExit: what parse_args raises after printing usage or help. */
export class SystemExit extends Error {
  override name = "SystemExit";
  constructor(public readonly code: number) {
    super(`exit ${code}`);
  }
}

export type TypeFn = ((value: string) => unknown) & { pyName?: string };

/** `type=int` */
export const int: TypeFn = Object.assign(
  (value: string): number => {
    const n = pyInt(value);
    if (n === null) throw new ValueError(value);
    return n;
  },
  { pyName: "int" },
);
/** `type=float` */
export const float: TypeFn = Object.assign(
  (value: string): number => {
    const n = pyFloat(value);
    if (n === null) throw new ValueError(value);
    return n;
  },
  { pyName: "float" },
);

/**
 * The hands' `_bounded(low, high, what)`: an int in range, else
 * "<what> is <low>..<high>". Named `parse`, as the Python closure was, so a
 * non-number reads `invalid parse value: 'x'`.
 */
export function bounded(low: number, high: number, what: string): TypeFn {
  return Object.assign(
    (value: string): number => {
      const n = pyInt(value);
      if (n === null) throw new ValueError(value);
      if (!(low <= n && n <= high)) throw new ArgumentTypeError(`${what} is ${low}..${high}`);
      return n;
    },
    { pyName: "parse" },
  );
}

type Nargs = undefined | "?" | "*" | "+" | number;
type ActionKind = "store" | "store_true" | "store_false" | "append" | "count" | "help" | "parsers";

export interface ArgOptions {
  type?: TypeFn;
  default?: unknown;
  choices?: readonly unknown[];
  required?: boolean;
  action?: "store" | "store_true" | "store_false" | "append" | "count";
  nargs?: "?" | "*" | "+" | number;
  metavar?: string;
  dest?: string;
  help?: string;
  const?: unknown;
}

interface Action {
  kind: ActionKind;
  optionStrings: string[];
  dest: string;
  nargs: Nargs;
  type?: TypeFn;
  default: unknown;
  choices?: readonly unknown[] | Map<string, ArgumentParser>;
  required: boolean;
  metavar?: string;
  help?: string;
  const?: unknown;
  parsers?: SubParsers;
}

const SUPPRESS = "==SUPPRESS==";

function columns(): number {
  const n = Number(process.env.COLUMNS);
  return Number.isInteger(n) && n > 0 ? n : 80;
}

export class ArgumentParser {
  prog: string;
  description?: string;
  private actions: Action[] = [];
  private optionMap = new Map<string, Action>();
  private defaults: Args = {};
  private negativeLooksLikeOption = false;
  /** Set by add_subparsers on a parent, so `verb` names its parser in help. */
  summary?: string;

  constructor(options: { prog: string; description?: string; addHelp?: boolean }) {
    this.prog = options.prog;
    this.description = options.description;
    if (options.addHelp !== false) {
      this.register({ kind: "help", optionStrings: ["-h", "--help"], dest: SUPPRESS, nargs: 0 as Nargs, default: SUPPRESS,
        required: false, help: "show this help message and exit" });
    }
  }

  setDefaults(values: Args): void {
    Object.assign(this.defaults, values);
    for (const action of this.actions) if (action.dest in values) action.default = values[action.dest];
  }

  addArgument(...spec: (string | ArgOptions)[]): void {
    const last = spec[spec.length - 1];
    const options: ArgOptions = typeof last === "object" ? last : {};
    const names = spec.filter((s): s is string => typeof s === "string");
    const optional = names.length > 0 && (names[0] as string).startsWith("-");
    const kind = (options.action ?? "store") as ActionKind;
    let dest = options.dest;
    if (!dest) {
      if (optional) {
        const long = names.find((n) => n.startsWith("--")) ?? (names[0] as string);
        dest = long.replace(/^-+/, "").replace(/-/g, "_");
      } else dest = names[0] as string;
    }
    let nargs: Nargs = options.nargs;
    if (kind === "store_true" || kind === "store_false" || kind === "count") nargs = 0 as Nargs;
    const flagDefault = kind === "store_true" ? false : kind === "store_false" ? true : null;
    const action: Action = {
      kind, optionStrings: optional ? names : [], dest, nargs, type: options.type,
      default: options.default !== undefined ? options.default : (this.defaults[dest] ?? flagDefault),
      choices: options.choices, required: optional ? Boolean(options.required) : options.nargs === undefined || options.nargs === "+" || typeof options.nargs === "number",
      metavar: options.metavar, help: options.help, const: options.const,
    };
    if (!optional && options.required === false) action.required = false;
    this.register(action);
    if (optional) for (const n of names) if (/^-\d+$|^-\d*\.\d+$/.test(n)) this.negativeLooksLikeOption = true;
  }

  addSubparsers(options: { dest?: string; title?: string } = {}): SubParsers {
    const subs = new SubParsers(this, options.dest ?? SUPPRESS);
    this.register({ kind: "parsers", optionStrings: [], dest: options.dest ?? SUPPRESS, nargs: "parsers" as unknown as Nargs,
      default: null, choices: subs.map, required: false, parsers: subs });
    return subs;
  }

  private register(action: Action): void {
    this.actions.push(action);
    for (const name of action.optionStrings) this.optionMap.set(name, action);
  }

  // --- usage and help --------------------------------------------------------

  private metavarOf(action: Action): string {
    if (action.metavar) return action.metavar;
    if (action.choices) return "{" + [...(action.choices instanceof Map ? action.choices.keys() : action.choices)].join(",") + "}";
    if (action.optionStrings.length) return action.dest.toUpperCase();
    return action.dest;
  }

  private argsFormat(action: Action): string {
    const m = this.metavarOf(action);
    const n = action.nargs;
    if (action.kind === "parsers") return `${m} ...`;
    if (n === undefined) return m;
    if (n === "?") return `[${m}]`;
    if (n === "*") return action.optionStrings.length ? `[${m} ...]` : `[${m} ...]`;
    if (n === "+") return `${m} [${m} ...]`;
    if (typeof n === "number" && n > 0) return Array(n).fill(m).join(" ");
    return "";
  }

  private usageParts(): { optionals: string; positionals: string } {
    const opt: string[] = [];
    const pos: string[] = [];
    for (const a of this.actions) {
      if (a.optionStrings.length) {
        const first = a.optionStrings[0] as string;
        const part = a.nargs === 0 ? first : `${first} ${this.argsFormat(a)}`;
        opt.push(a.required ? part : `[${part}]`);
      } else pos.push(this.argsFormat(a));
    }
    return { optionals: opt.join(" "), positionals: pos.join(" ") };
  }

  formatUsage(): string {
    const prefix = "usage: ";
    const width = columns() - 2;
    const textWidth = Math.max(width, 11);
    const { optionals, positionals } = this.usageParts();
    let usage = [this.prog, optionals, positionals].filter(Boolean).join(" ");
    if (prefix.length + usage.length > textWidth) {
      const part = /\(.*?\)+(?=\s|$)|\[.*?\]+(?=\s|$)|\S+/g;
      const optParts = optionals.match(part) ?? [];
      const posParts = positionals.match(part) ?? [];
      const getLines = (parts: string[], indent: string, pre?: string): string[] => {
        const lines: string[] = [];
        let line: string[] = [];
        let lineLen = pre !== undefined ? pre.length - 1 : indent.length - 1;
        for (const p of parts) {
          if (lineLen + 1 + p.length > textWidth && line.length) {
            lines.push(indent + line.join(" "));
            line = [];
            lineLen = indent.length - 1;
          }
          line.push(p);
          lineLen += p.length + 1;
        }
        if (line.length) lines.push(indent + line.join(" "));
        if (pre !== undefined && lines.length) lines[0] = (lines[0] as string).slice(indent.length);
        return lines;
      };
      let lines: string[];
      if (prefix.length + this.prog.length <= 0.75 * textWidth) {
        const indent = " ".repeat(prefix.length + this.prog.length + 1);
        if (optParts.length) {
          lines = getLines([this.prog, ...optParts], indent, prefix);
          lines.push(...getLines(posParts, indent));
        } else if (posParts.length) lines = getLines([this.prog, ...posParts], indent, prefix);
        else lines = [this.prog];
      } else {
        const indent = " ".repeat(prefix.length);
        const parts = [...optParts, ...posParts];
        lines = getLines(parts, indent);
        if (lines.length > 1) {
          lines = [...getLines(optParts, indent), ...getLines(posParts, indent)];
        }
        lines = [this.prog, ...lines];
      }
      usage = lines.join("\n");
    }
    return `${prefix}${usage}\n`;
  }

  formatHelp(): string {
    const width = columns() - 2;
    const sections: string[] = [this.formatUsage()];
    if (this.description) sections.push("\n" + wrap(this.description, width).join("\n") + "\n");
    const entry = (inv: string, help: string | undefined, indent: number, helpPos: number): string => {
      const helpWidth = Math.max(width - helpPos, 11);
      const pad = " ".repeat(indent);
      if (!help) return `${pad}${inv}\n`;
      const lines = wrap(help, helpWidth);
      const first = inv.length <= helpPos - indent - 2 ? `${pad}${inv.padEnd(helpPos - indent)}${lines[0]}\n` : `${pad}${inv}\n${" ".repeat(helpPos)}${lines[0]}\n`;
      return first + lines.slice(1).map((l) => `${" ".repeat(helpPos)}${l}\n`).join("");
    };
    const rows: { inv: string; help?: string; indent: number }[] = [];
    const build = (wantPositional: boolean): void => {
      for (const a of this.actions) {
        if (!a.optionStrings.length !== wantPositional) continue;
        if (a.kind === "parsers") {
          rows.push({ inv: this.metavarOf(a), indent: 2 });
          for (const [name, p] of a.parsers!.map) rows.push({ inv: name, help: p.summary, indent: 4 });
        } else if (a.optionStrings.length) {
          const inv = a.nargs === 0 ? a.optionStrings.join(", ") : a.optionStrings.map((o) => `${o} ${this.argsFormat(a)}`).join(", ");
          rows.push({ inv, help: a.help, indent: 2 });
        } else rows.push({ inv: this.metavarOf(a), help: a.help, indent: 2 });
      }
    };
    const groups: [string, boolean][] = [["positional arguments:", true], ["options:", false]];
    const all: { title: string; rows: typeof rows }[] = [];
    for (const [title, pos] of groups) {
      rows.length = 0;
      build(pos);
      if (rows.length) all.push({ title, rows: [...rows] });
    }
    const longest = Math.max(0, ...all.flatMap((g) => g.rows.map((r) => r.inv.length + r.indent)));
    const helpPos = Math.min(longest + 2, 24);
    for (const g of all) sections.push("\n" + g.title + "\n" + g.rows.map((r) => entry(r.inv, r.help, r.indent, helpPos)).join(""));
    return sections.join("");
  }

  printUsage(stream: NodeJS.WritableStream = process.stderr): void {
    stream.write(this.formatUsage());
  }

  error(message: string): never {
    this.printUsage(process.stderr);
    process.stderr.write(`${this.prog}: error: ${message}\n`);
    throw new SystemExit(2);
  }

  // --- parsing -------------------------------------------------------------------

  parseArgs(argv: readonly string[]): Args {
    const [ns, extras] = this.parseKnownArgs([...argv]);
    if (extras.length) this.error(`unrecognized arguments: ${extras.join(" ")}`);
    return ns;
  }

  parseKnownArgs(argStrings: string[]): [Args, string[]] {
    const namespace: Args = {};
    for (const a of this.actions) if (a.dest !== SUPPRESS && !(a.dest in namespace) && a.default !== SUPPRESS) namespace[a.dest] = a.default;
    for (const [k, v] of Object.entries(this.defaults)) if (!(k in namespace)) namespace[k] = v;
    return this.parseInto(namespace, argStrings);
  }

  private optionTuples(arg: string): [Action, string, string | null][] {
    const out: [Action, string, string | null][] = [];
    if (arg.startsWith("--")) {
      const [prefix, explicit] = arg.includes("=") ? [arg.slice(0, arg.indexOf("=")), arg.slice(arg.indexOf("=") + 1)] : [arg, null];
      for (const [name, action] of this.optionMap) if (name.startsWith(prefix)) out.push([action, name, explicit]);
    } else if (arg[0] === "-" && arg[1] !== "-") {
      const short = arg.slice(0, 2);
      const explicit = arg.slice(2);
      for (const [name, action] of this.optionMap) {
        if (name === short) out.push([action, name, explicit]);
        else if (name.startsWith(arg)) out.push([action, name, null]);
      }
    } else this.error(`unexpected option string: ${arg}`);
    return out;
  }

  private parseOptional(arg: string): [Action | null, string, string | null] | null {
    if (!arg || arg[0] !== "-") return null;
    const known = this.optionMap.get(arg);
    if (known) return [known, arg, null];
    if (arg.length === 1) return null;
    if (arg.includes("=")) {
      const at = arg.indexOf("=");
      const act = this.optionMap.get(arg.slice(0, at));
      if (act) return [act, arg.slice(0, at), arg.slice(at + 1)];
    }
    const tuples = this.optionTuples(arg);
    if (tuples.length > 1) this.error(`ambiguous option: ${arg} could match ${tuples.map((t) => t[1]).join(", ")}`);
    if (tuples.length === 1) return tuples[0] as [Action, string, string | null];
    if (/^-\d+$|^-\d*\.\d+$/.test(arg) && !this.negativeLooksLikeOption) return null;
    if (arg.includes(" ")) return null;
    return [null, arg, null];
  }

  private nargsPattern(action: Action): string {
    const n = action.nargs;
    let p: string;
    if (action.kind === "parsers") p = "(-*A[-AO]*)";
    else if (n === undefined) p = "(-*A-*)";
    else if (n === "?") p = "(-*A?-*)";
    else if (n === "*") p = "(-*[A-]*)";
    else if (n === "+") p = "(-*A[A-]*)";
    else p = `(-*${Array(n as number).fill("A").join("-*")}-*)`;
    return action.optionStrings.length ? p.replace(/-\*/g, "").replace(/-/g, "") : p;
  }

  private matchArgument(action: Action, pattern: string): number {
    const m = new RegExp("^" + this.nargsPattern(action)).exec(pattern);
    if (!m) {
      const n = action.nargs;
      const msg = n === undefined ? "expected one argument" : n === "?" ? "expected at most one argument" : n === "+" ? "expected at least one argument"
        : `expected ${n} argument${n === 1 ? "" : "s"}`;
      this.argError(action, msg);
    }
    return (m as RegExpExecArray)[1]!.length;
  }

  private actionName(action: Action): string {
    if (action.optionStrings.length) return action.optionStrings.join("/");
    if (action.metavar) return action.metavar;
    if (action.dest !== SUPPRESS) return action.dest;
    if (action.choices) return "{" + [...(action.choices instanceof Map ? action.choices.keys() : action.choices)].join(",") + "}";
    return "";
  }

  private argError(action: Action | null, message: string): never {
    this.error(action ? `argument ${this.actionName(action)}: ${message}` : message);
  }

  private convert(action: Action, text: string): unknown {
    if (!action.type) return text;
    try {
      return action.type(text);
    } catch (exc) {
      if (exc instanceof ArgumentTypeError) this.argError(action, exc.message);
      if (exc instanceof ValueError || exc instanceof TypeError) {
        this.argError(action, `invalid ${action.type.pyName ?? action.type.name ?? "type"} value: ${pyRepr(text)}`);
      }
      throw exc;
    }
  }

  private checkChoice(action: Action, value: unknown): void {
    const choices = action.choices;
    if (!choices) return;
    const names = choices instanceof Map ? [...choices.keys()] : [...choices];
    if (!names.some((c) => c === value)) {
      this.argError(action, `invalid choice: ${pyRepr(value)} (choose from ${names.map(pyRepr).join(", ")})`);
    }
  }

  private values(action: Action, strings: string[]): unknown {
    let args = strings;
    if (action.kind !== "parsers") {
      const at = args.indexOf("--");
      if (at >= 0) args = [...args.slice(0, at), ...args.slice(at + 1)];
    }
    const n = action.nargs;
    if (action.kind === "parsers") {
      const value = args.map((s) => this.convert(action, s));
      this.checkChoice(action, value[0]);
      return value;
    }
    if (!args.length && n === "?") {
      const v = action.optionStrings.length ? action.const : action.default;
      if (typeof v === "string") {
        const c = this.convert(action, v);
        this.checkChoice(action, c);
        return c;
      }
      return v ?? null;
    }
    if (!args.length && n === "*" && !action.optionStrings.length) return action.default ?? [];
    if (args.length === 1 && (n === undefined || n === "?")) {
      const c = this.convert(action, args[0] as string);
      this.checkChoice(action, c);
      return c;
    }
    const list = args.map((s) => this.convert(action, s));
    for (const v of list) this.checkChoice(action, v);
    return list;
  }

  private apply(action: Action, namespace: Args, value: unknown, option: string | null, extras: string[]): void {
    switch (action.kind) {
      case "store": namespace[action.dest] = value; break;
      case "store_true": namespace[action.dest] = true; break;
      case "store_false": namespace[action.dest] = false; break;
      case "count": namespace[action.dest] = ((namespace[action.dest] as number | null) ?? 0) + 1; break;
      case "append": namespace[action.dest] = [...((namespace[action.dest] as unknown[] | null) ?? []), value]; break;
      case "help":
        process.stdout.write(this.formatHelp());
        throw new SystemExit(0);
      case "parsers": {
        const list = value as string[];
        const name = list[0] as string;
        const rest = list.slice(1);
        if (action.dest !== SUPPRESS) namespace[action.dest] = name;
        const parser = action.parsers!.map.get(name);
        if (!parser) this.argError(action, `unknown parser ${pyRepr(name)} (choices: ${[...action.parsers!.map.keys()].join(", ")})`);
        const [sub, left] = (parser as ArgumentParser).parseKnownArgs(rest);
        Object.assign(namespace, sub);
        extras.push(...left);
        break;
      }
    }
    void option;
  }

  private parseInto(namespace: Args, argStrings: string[]): [Args, string[]] {
    const extras: string[] = [];
    const optionIndex = new Map<number, [Action | null, string, string | null]>();
    let pattern = "";
    let rest = false;
    argStrings.forEach((arg, i) => {
      if (rest) pattern += "A";
      else if (arg === "--") {
        pattern += "-";
        rest = true;
      } else {
        const t = this.parseOptional(arg);
        if (t === null) pattern += "A";
        else {
          optionIndex.set(i, t);
          pattern += "O";
        }
      }
    });

    const positionals = this.actions.filter((a) => !a.optionStrings.length);
    const seen = new Set<Action>();

    const take = (action: Action, strings: string[], option: string | null): void => {
      seen.add(action);
      const value = this.values(action, strings);
      this.apply(action, namespace, value, option, extras);
    };

    const consumePositionals = (start: number): number => {
      const selected = pattern.slice(start);
      let counts: number[] = [];
      for (let i = positionals.length; i > 0; i--) {
        const re = new RegExp("^" + positionals.slice(0, i).map((a) => this.nargsPattern(a)).join(""));
        const m = re.exec(selected);
        if (m) {
          counts = m.slice(1).map((g) => g.length);
          break;
        }
      }
      let at = start;
      counts.forEach((count, i) => {
        const strings = argStrings.slice(at, at + count);
        at += count;
        take(positionals[i] as Action, strings, null);
      });
      positionals.splice(0, counts.length);
      return at;
    };

    const consumeOptional = (start: number): number => {
      let [action, option, explicit] = optionIndex.get(start) as [Action | null, string, string | null];
      const tuples: [Action, string[], string][] = [];
      let stop: number;
      for (;;) {
        if (action === null) {
          extras.push(argStrings[start] as string);
          return start + 1;
        }
        if (explicit !== null) {
          const count = this.matchArgument(action, "A");
          if (count === 0 && option[1] !== "-" && explicit !== "") {
            tuples.push([action, [], option]);
            const next = (option[0] as string) + (explicit[0] as string);
            const more = explicit.slice(1) || null;
            const found = this.optionMap.get(next);
            if (found) {
              action = found;
              option = next;
              explicit = more;
            } else this.argError(action, `ignored explicit argument ${pyRepr(explicit)}`);
          } else if (count === 1) {
            stop = start + 1;
            tuples.push([action, [explicit], option]);
            break;
          } else this.argError(action, `ignored explicit argument ${pyRepr(explicit)}`);
        } else {
          const from = start + 1;
          const count = this.matchArgument(action, pattern.slice(from));
          stop = from + count;
          tuples.push([action, argStrings.slice(from, stop), option]);
          break;
        }
      }
      for (const [a, strings, opt] of tuples) take(a, strings, opt);
      return stop;
    };

    const maxOption = Math.max(-1, ...optionIndex.keys());
    let start = 0;
    while (start <= maxOption) {
      const nextOption = Math.min(...[...optionIndex.keys()].filter((i) => i >= start));
      if (start !== nextOption) {
        const end = consumePositionals(start);
        if (end > start) {
          start = end;
          continue;
        }
        start = end;
      }
      if (!optionIndex.has(start)) {
        extras.push(...argStrings.slice(start, nextOption));
        start = nextOption;
      }
      start = consumeOptional(start);
    }
    const stop = consumePositionals(start);
    extras.push(...argStrings.slice(stop));

    const missing: string[] = [];
    for (const action of this.actions) {
      if (seen.has(action)) continue;
      if (action.required) missing.push(this.actionName(action));
      else if (typeof action.default === "string" && action.dest !== SUPPRESS && namespace[action.dest] === action.default) {
        namespace[action.dest] = this.convert(action, action.default);
      }
    }
    if (missing.length) this.error(`the following arguments are required: ${missing.join(", ")}`);
    return [namespace, extras];
  }
}

export class SubParsers {
  readonly map = new Map<string, ArgumentParser>();
  constructor(private readonly parent: ArgumentParser, readonly dest: string) {}
  /** `add_parser(name, help=...)`: its prog is the parent's, then the name. */
  addParser(name: string, options: { help?: string } = {}): ArgumentParser {
    const parser = new ArgumentParser({ prog: `${this.parent.prog} ${name}` });
    parser.summary = options.help;
    this.map.set(name, parser);
    return parser;
  }
}

function wrap(text: string, width: number): string[] {
  const words = text.split(/\s+/).filter(Boolean);
  const lines: string[] = [];
  let line = "";
  for (const w of words) {
    if (line && line.length + 1 + w.length > width) {
      lines.push(line);
      line = w;
    } else line = line ? `${line} ${w}` : w;
  }
  if (line) lines.push(line);
  return lines.length ? lines : [""];
}
