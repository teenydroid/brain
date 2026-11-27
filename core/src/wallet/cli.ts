// Command-line entry for the wallet's one subcommand, `giveaway-tx`.
//
// Wiring for core/src/main.ts (P1 owns that file): before anything starts a
// server, hand the command over, exactly as spine/src/main.rs did:
//
//   if (argv[0] === "giveaway-tx") process.exit(await giveawayTxCli(argv.slice(1)));
//
// hands/giveaway/onchain.py runs `$OPENHUMAN_CORE_BIN giveaway-tx create|close`
// with the request on stdin, so OPENHUMAN_CORE_BIN becomes a wrapper that
// execs `node core/dist/main.js "$@"`.
import { COMMAND, giveawayTxMain } from "./giveaway.js";

export { COMMAND as GIVEAWAY_COMMAND, giveawayTxMain };

async function readStdin(stream: NodeJS.ReadableStream): Promise<string> {
  const chunks: Buffer[] = [];
  for await (const chunk of stream) chunks.push(Buffer.isBuffer(chunk) ? chunk : Buffer.from(chunk));
  return Buffer.concat(chunks).toString("utf8");
}

/** Runs `giveaway-tx ARGV...` against the real stdin/stdout/stderr; returns the exit code. */
export async function giveawayTxCli(argv: readonly string[]): Promise<number> {
  const input = await readStdin(process.stdin);
  const result = giveawayTxMain(argv, input);
  if (result.stdout) process.stdout.write(result.stdout);
  if (result.stderr) process.stderr.write(result.stderr);
  return result.code;
}
