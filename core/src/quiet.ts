// Import this first (main.ts does). bigint-buffer, which @solana/web3.js pulls in, tries a native binding
// and says on every start that it will use pure JS instead: true, harmless, and in every log and every
// `teeny-core openclaw ...` a script runs. This drops that one line and nothing else; imports run in order,
// so it is in place before the wallet modules load.
const NOISE = /^bigint: Failed to load bindings, pure JS will be used/;
const warn = console.warn.bind(console);

export function isNoise(args: readonly unknown[]): boolean {
  return typeof args[0] === "string" && NOISE.test(args[0]);
}

console.warn = (...args: unknown[]): void => {
  if (!isNoise(args)) warn(...args);
};
