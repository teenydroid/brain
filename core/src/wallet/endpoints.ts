import { pinnedFetch } from "./pinned-fetch.js";

// Chains, clusters and their endpoints. The cluster and the RPC URLs come from
// the boot environment (bin/lib/wallet_config.py writes them); an endpoint URL
// can carry a provider's API key, so no error here ever names one.
export type Env = Readonly<Record<string, string | undefined>>;

export interface EvmNetworkInfo {
  /** OpenHuman's evmNetwork id. */
  id: string;
  chainId: number;
  native: string;
  usdc: string;
  rpcEnv: string;
  rpcDefault: string;
  explorer: string;
}

export const EVM_NETWORKS: readonly EvmNetworkInfo[] = [
  {
    id: "base_mainnet",
    chainId: 8453,
    native: "ETH",
    usdc: "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913",
    rpcEnv: "OPENHUMAN_WALLET_RPC_BASE",
    rpcDefault: "https://mainnet.base.org",
    explorer: "https://basescan.org",
  },
  {
    id: "arbitrum_one",
    chainId: 42161,
    native: "ETH",
    usdc: "0xaf88d065e77c8cC2239327C5EDb3A432268e5831",
    rpcEnv: "OPENHUMAN_WALLET_RPC_ARBITRUM",
    rpcDefault: "https://arb1.arbitrum.io/rpc",
    explorer: "https://arbiscan.io",
  },
  {
    id: "ethereum_mainnet",
    chainId: 1,
    native: "ETH",
    usdc: "0xA0b86991c6218b36c1d19D4a2E9Eb0cE3606eB48",
    rpcEnv: "OPENHUMAN_WALLET_RPC_EVM",
    rpcDefault: "https://ethereum-rpc.publicnode.com",
    explorer: "https://etherscan.io",
  },
  {
    id: "optimism_mainnet",
    chainId: 10,
    native: "ETH",
    usdc: "0x0b2C639c533813f4Aa9D7837CAf62653d097Ff85",
    rpcEnv: "OPENHUMAN_WALLET_RPC_OPTIMISM",
    rpcDefault: "https://mainnet.optimism.io",
    explorer: "https://optimistic.etherscan.io",
  },
];

export function evmNetwork(id: string | undefined): EvmNetworkInfo | undefined {
  return EVM_NETWORKS.find((n) => n.id === id);
}

export function evmNetworkByChainId(chainId: number | string): EvmNetworkInfo | undefined {
  return EVM_NETWORKS.find((n) => String(n.chainId) === String(chainId));
}

export function evmRpcUrl(env: Env, network: EvmNetworkInfo): string {
  return env[network.rpcEnv]?.trim() || network.rpcDefault;
}

export const SOLANA_MAINNET_USDC = "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v";
export const SOLANA_DEVNET_USDC = "4zMMC9srt5Ri5X14GAgXhaHii3GnPAEERYPJgZJDncDU";

export type SolanaCluster = "mainnet" | "devnet" | "testnet";

/** devnet until WALLET_CLUSTER=mainnet says otherwise (bin/lib/wallet_config.py exports it). */
export function solanaCluster(env: Env): SolanaCluster {
  const raw = (env.OPENHUMAN_SOLANA_CLUSTER ?? "").trim().toLowerCase();
  if (raw === "mainnet" || raw === "mainnet-beta") return "mainnet";
  if (raw === "testnet") return "testnet";
  return "devnet";
}

export function solanaUsdcMint(cluster: SolanaCluster): string | undefined {
  return cluster === "mainnet" ? SOLANA_MAINNET_USDC : cluster === "devnet" ? SOLANA_DEVNET_USDC : undefined;
}

const SOLANA_DEFAULTS: Record<SolanaCluster, string> = {
  mainnet: "https://api.mainnet-beta.solana.com",
  devnet: "https://api.devnet.solana.com",
  testnet: "https://api.testnet.solana.com",
};

export function solanaRpcUrl(env: Env): string {
  return env.OPENHUMAN_WALLET_RPC_SOLANA?.trim() || SOLANA_DEFAULTS[solanaCluster(env)];
}

export function btcApiUrl(env: Env): string {
  return (env.OPENHUMAN_WALLET_RPC_BTC?.trim() || "https://blockstream.info/api").replace(/\/+$/, "");
}

export function solanaExplorerTx(sig: string, cluster: SolanaCluster): string {
  return `https://explorer.solana.com/tx/${sig}${cluster === "mainnet" ? "" : `?cluster=${cluster}`}`;
}

/** Everything a handler needs from outside: HTTP, DNS and the clock, injectable for tests. */
export interface Deps {
  fetch: typeof fetch;
  now: () => number;
  /** Every address HOST resolves to, for the x402 destination guard. */
  resolve: (host: string) => Promise<string[]>;
  /** A request whose connection goes only to the given, already vetted addresses (see pinned-fetch.ts). */
  pinnedFetch: (url: string, init: RequestInit, addresses: readonly string[]) => Promise<Response>;
}

export const defaultDeps: Deps = {
  pinnedFetch,
  fetch: (...args) => globalThis.fetch(...args),
  now: () => Date.now(),
  resolve: async (host) => {
    const { lookup } = await import("node:dns/promises");
    return (await lookup(host, { all: true })).map((a) => a.address);
  },
};
