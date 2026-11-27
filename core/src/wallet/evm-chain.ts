// EVM: balances, transfers, status, and signing a provider's transaction. viem
// only encodes and signs; every network call goes through the injectable fetch.
import { encodeFunctionData, isAddress, parseAbi, getAddress } from "viem";
import { privateKeyToAccount } from "viem/accounts";
import type { EvmRpc } from "./rpc.js";
import { withMutex } from "./util.js";

const ERC20 = parseAbi(["function transfer(address to, uint256 amount) returns (bool)", "function balanceOf(address owner) view returns (uint256)"]);

/** 0x and 40 hex digits; a mixed-case address must carry a valid EIP-55 checksum. */
export function isEvmAddress(text: string): boolean {
  return isAddress(text, { strict: true });
}

export function erc20TransferData(to: string, amount: bigint): `0x${string}` {
  return encodeFunctionData({ abi: ERC20, functionName: "transfer", args: [getAddress(to), amount] });
}

const hexToBig = (value: unknown): bigint => (typeof value === "string" ? BigInt(value) : 0n);

export async function ethBalance(rpc: EvmRpc, address: string): Promise<bigint> {
  return hexToBig(await rpc.call<string>("eth_getBalance", [address, "latest"]));
}

export interface EvmTx {
  to: string;
  value: bigint;
  data: string;
}

/** Sign (not send) TX from the key's own address on CHAINID, estimating gas and fees from the node. */
export async function signEvmTransaction(rpc: EvmRpc, privateKey: `0x${string}`, chainId: number, tx: EvmTx): Promise<`0x${string}`> {
  const account = privateKeyToAccount(privateKey);
  const from = account.address;
  const [nonceHex, gasHex, block, tipHex] = await Promise.all([
    rpc.call<string>("eth_getTransactionCount", [from, "pending"]),
    rpc.call<string>("eth_estimateGas", [{ from, to: tx.to, value: `0x${tx.value.toString(16)}`, data: tx.data }]),
    rpc.call<{ baseFeePerGas?: string }>("eth_getBlockByNumber", ["latest", false]),
    rpc.call<string>("eth_maxPriorityFeePerGas", []).catch(() => "0x59682f00"),
  ]);
  const base = hexToBig(block.baseFeePerGas);
  const tip = hexToBig(tipHex);
  const gas = (hexToBig(gasHex) * 12n) / 10n;
  return account.signTransaction({
    type: "eip1559",
    chainId,
    nonce: Number(hexToBig(nonceHex)),
    to: getAddress(tx.to),
    value: tx.value,
    data: tx.data as `0x${string}`,
    gas,
    maxFeePerGas: base * 2n + tip,
    maxPriorityFeePerGas: tip,
  });
}

/**
 * Reads the pending nonce, signs and broadcasts as one step per sender and chain, so two sends from the same
 * account in this process cannot both read the same nonce (one would be refused, or replace the other).
 */
export async function signAndBroadcast(rpc: EvmRpc, privateKey: `0x${string}`, chainId: number, tx: EvmTx): Promise<string> {
  const sender = privateKeyToAccount(privateKey).address.toLowerCase();
  return withMutex(`evm-send:${chainId}:${sender}`, async () => sendRaw(rpc, await signEvmTransaction(rpc, privateKey, chainId, tx)));
}

export async function sendRaw(rpc: EvmRpc, raw: string): Promise<string> {
  return rpc.call<string>("eth_sendRawTransaction", [raw]);
}

export async function erc20Balance(rpc: EvmRpc, token: string, owner: string): Promise<bigint> {
  const data = encodeFunctionData({ abi: ERC20, functionName: "balanceOf", args: [getAddress(owner)] });
  return hexToBig(await rpc.call<string>("eth_call", [{ to: token, data }, "latest"]));
}

export type TxState = "confirmed" | "failed" | "pending";

export async function receiptStatus(rpc: EvmRpc, hash: string): Promise<{ state: TxState; blockNumber?: string }> {
  const receipt = await rpc.call<{ status?: string; blockNumber?: string } | null>("eth_getTransactionReceipt", [hash]);
  if (!receipt) return { state: "pending" };
  return { state: receipt.status === "0x1" ? "confirmed" : "failed", ...(receipt.blockNumber ? { blockNumber: receipt.blockNumber } : {}) };
}
