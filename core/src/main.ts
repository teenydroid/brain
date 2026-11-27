#!/usr/bin/env node
// teeny-core: the OpenClaw-backed core. P0 is only the scaffold: `serve`
// is not implemented yet (the RPC host lands in P1).

const USAGE = "usage: teeny-core serve";

export function run(argv: readonly string[]): number {
  const [command] = argv;
  if (command === "serve") {
    process.stderr.write("teeny-core: serve is not implemented yet\n");
    return 1;
  }
  process.stderr.write(`${USAGE}\n`);
  return 2;
}

if (import.meta.url === `file://${process.argv[1]}`) {
  process.exitCode = run(process.argv.slice(2));
}
