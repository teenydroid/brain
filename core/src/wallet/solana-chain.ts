// Solana: balances, transfers, status, and signing a provider's transaction.
import {
  ASSOCIATED_TOKEN_PROGRAM_ID,
  TOKEN_PROGRAM_ID,
  createAssociatedTokenAccountIdempotentInstruction,
  createTransferCheckedInstruction,
  getAssociatedTokenAddressSync,
} from "@solana/spl-token";
import { type Keypair, PublicKey, SystemProgram, Transaction, VersionedTransaction } from "@solana/web3.js";
import type { SolanaRpc } from "./rpc.js";

export const LAMPORTS_DECIMALS = 9;
export const USDC_DECIMALS = 6;

export function isSolanaAddress(text: string): boolean {
  try {
    const key = new PublicKey(text);
    return key.toBase58() === text;
  } catch {
    return false;
  }
}

export async function solBalance(rpc: SolanaRpc, address: string): Promise<bigint> {
  const answer = await rpc.callExact<{ value: string | number }>("getBalance", [address, { commitment: "confirmed" }], ["value"]);
  return BigInt(answer.value);
}

async function latestBlockhash(rpc: SolanaRpc): Promise<string> {
  const answer = await rpc.call<{ value: { blockhash: string } }>("getLatestBlockhash", [{ commitment: "confirmed" }]);
  return answer.value.blockhash;
}

/** A signed legacy transfer of SOL (no MINT) or an SPL token (USDC, 6 decimals) to TO. Returns wire bytes. */
export async function buildSignedTransfer(
  rpc: SolanaRpc,
  payer: Keypair,
  to: string,
  amount: bigint,
  mint?: string,
): Promise<Uint8Array> {
  const recipient = new PublicKey(to);
  const tx = new Transaction({ feePayer: payer.publicKey, recentBlockhash: await latestBlockhash(rpc) });
  if (mint === undefined) {
    if (amount > 0xffffffffffffffffn) throw new Error("amount does not fit in a u64");
    tx.add(SystemProgram.transfer({ fromPubkey: payer.publicKey, toPubkey: recipient, lamports: amount }));
  } else {
    const mintKey = new PublicKey(mint);
    const source = getAssociatedTokenAddressSync(mintKey, payer.publicKey, false, TOKEN_PROGRAM_ID, ASSOCIATED_TOKEN_PROGRAM_ID);
    const destination = getAssociatedTokenAddressSync(mintKey, recipient, true, TOKEN_PROGRAM_ID, ASSOCIATED_TOKEN_PROGRAM_ID);
    tx.add(
      createAssociatedTokenAccountIdempotentInstruction(payer.publicKey, destination, recipient, mintKey, TOKEN_PROGRAM_ID, ASSOCIATED_TOKEN_PROGRAM_ID),
      createTransferCheckedInstruction(source, mintKey, destination, payer.publicKey, amount, USDC_DECIMALS, [], TOKEN_PROGRAM_ID),
    );
  }
  tx.sign(payer);
  return new Uint8Array(tx.serialize());
}

/** Broadcast a signed transaction; returns the signature the node reports. */
export async function sendTransaction(rpc: SolanaRpc, wire: Uint8Array): Promise<string> {
  return rpc.call<string>("sendTransaction", [Buffer.from(wire).toString("base64"), { encoding: "base64", preflightCommitment: "confirmed" }]);
}

/**
 * Sign a provider's transaction (LI.FI or deBridge, hex of the wire bytes):
 * the signer must be its fee payer and its first required signer. The
 * blockhash is refreshed first, since a quote can sit for a while.
 */
export async function signProviderTransaction(rpc: SolanaRpc, signer: Keypair, wire: Uint8Array): Promise<Uint8Array> {
  const tx = VersionedTransaction.deserialize(wire);
  const message = tx.message;
  const payer = message.staticAccountKeys[0];
  if (!payer || !payer.equals(signer.publicKey)) throw new Error("the quote's transaction does not name the signing account as its fee payer");
  if (message.header.numRequiredSignatures !== 1) {
    throw new Error("the quote's transaction needs signers other than the signing account");
  }
  message.recentBlockhash = await latestBlockhash(rpc);
  tx.sign([signer]);
  return tx.serialize();
}

export type TxState = "confirmed" | "failed" | "pending";

export async function signatureStatus(rpc: SolanaRpc, signature: string): Promise<{ state: TxState; slot?: number }> {
  const answer = await rpc.call<{ value: Array<{ slot?: number; err?: unknown; confirmationStatus?: string } | null> }>("getSignatureStatuses", [
    [signature],
    { searchTransactionHistory: true },
  ]);
  const status = answer.value[0];
  if (!status) return { state: "pending" };
  if (status.err !== null && status.err !== undefined) return { state: "failed", ...(status.slot !== undefined ? { slot: status.slot } : {}) };
  const done = status.confirmationStatus === "confirmed" || status.confirmationStatus === "finalized";
  return { state: done ? "confirmed" : "pending", ...(status.slot !== undefined ? { slot: status.slot } : {}) };
}
