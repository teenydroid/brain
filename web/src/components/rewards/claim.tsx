"use client"

import { useWallet } from "@solana/wallet-adapter-react"
import { Connection, PublicKey, Transaction } from "@solana/web3.js"
import { ArrowUpRight } from "lucide-react"
import { useCallback, useEffect, useState } from "react"
import { Button } from "@/components/ui/button"
import {
  checkRoundAccounts,
  claimStatusPda,
  createAtaIdempotentInstruction,
  hexToBytes,
  newClaimInstruction,
  TOKEN_PROGRAM_ID,
  verifyClaimProof,
} from "@/lib/distributor"
import { findClaim, formatTiny, solscan, type Round } from "@/lib/drops"
import { rpcFor } from "@/lib/solana-rpc"
import { ConnectWallet } from "./wallet"

/** What the claim panel needs of a round: no entrant list, no draw. */
export type ClaimableRound = Pick<
  Round,
  "id" | "tag" | "cluster" | "mint" | "decimals" | "program_id" | "distributor" | "vault" | "merkle_root" | "clawback_at" | "claims"
>

/** Byte offset of `clawed_back` in a MerkleDistributor account (8-byte discriminator, then borsh fields). */
const CLAWED_BACK_OFFSET = 8 + 1 + 8 + 32 + 32 + 32 + 8 * 4 + 8 * 3 + 32 + 32

type State =
  | { step: "checking" }
  | { step: "lost" }
  | { step: "broken"; why: string }
  | { step: "claimed" }
  | { step: "expired" }
  | { step: "ready" }
  | { step: "sending" }
  | { step: "done"; signature: string }
  | { step: "failed"; why: string; retry: boolean }

/** One round, for the connected wallet: did it win, has it claimed, and the claim itself. */
function RoundClaim({ round, owner }: { round: ClaimableRound; owner: PublicKey }) {
  const { sendTransaction } = useWallet()
  const [state, setState] = useState<State>({ step: "checking" })
  const [vaultLeft, setVaultLeft] = useState<string>()
  const claim = findClaim(round as Round, owner.toBase58())

  useEffect(() => {
    let live = true
    const set = (s: State) => live && setState(s)
    ;(async () => {
      const connection = new Connection(rpcFor(round.cluster), "confirmed")
      if (round.vault) {
        connection
          .getTokenAccountBalance(new PublicKey(round.vault))
          .then((b) => live && setVaultLeft(formatTiny(BigInt(b.value.amount), round.decimals)))
          .catch(() => undefined)
      }
      if (!claim) return set({ step: "lost" })
      if (!round.merkle_root || !checkRoundAccounts(round)) return set({ step: "broken", why: "this round's published accounts don't match what the program derives." })
      if (!verifyClaimProof(round.merkle_root, claim.address, claim.amount_atomic, claim.proof)) {
        return set({ step: "broken", why: "your proof doesn't check against the round's root, so the program would refuse it." })
      }
      if (round.clawback_at && Date.parse(round.clawback_at) <= Date.now()) return set({ step: "expired" })
      const program = new PublicKey(round.program_id)
      const distributor = new PublicKey(round.distributor!)
      const [status, mint, dist] = await connection.getMultipleAccountsInfo([
        claimStatusPda(program, owner, distributor),
        new PublicKey(round.mint),
        distributor,
      ])
      if (status) return set({ step: "claimed" })
      if (!dist) return set({ step: "broken", why: "the round's distributor isn't on chain." })
      if (dist.data[CLAWED_BACK_OFFSET] === 1) return set({ step: "expired" })
      if (!mint || !mint.owner.equals(TOKEN_PROGRAM_ID)) {
        return set({ step: "broken", why: "the distributor program pays classic spl tokens only, and this round's mint is not one." })
      }
      set({ step: "ready" })
    })().catch(() => set({ step: "failed", why: "could not reach solana. try again in a minute.", retry: false }))
    return () => {
      live = false
    }
  }, [round, owner, claim])

  const send = useCallback(async () => {
    if (!claim || !round.distributor) return
    setState({ step: "sending" })
    try {
      const connection = new Connection(rpcFor(round.cluster), "confirmed")
      const mint = new PublicKey(round.mint)
      const tx = new Transaction().add(
        createAtaIdempotentInstruction(owner, owner, mint),
        newClaimInstruction({
          programId: new PublicKey(round.program_id),
          distributor: new PublicKey(round.distributor),
          mint,
          claimant: owner,
          amountUnlocked: BigInt(claim.amount_atomic),
          amountLocked: 0n,
          proof: claim.proof.map(hexToBytes),
        }),
      )
      const latest = await connection.getLatestBlockhash("confirmed")
      tx.feePayer = owner
      tx.recentBlockhash = latest.blockhash
      const signature = await sendTransaction(tx, connection)
      const result = await connection.confirmTransaction({ signature, ...latest }, "confirmed")
      if (result.value.err) throw new Error("the claim failed on chain")
      setState({ step: "done", signature })
    } catch (e) {
      setState({ step: "failed", why: e instanceof Error && e.message ? e.message : "the claim did not go through", retry: true })
    }
  }, [claim, owner, round, sendTransaction])

  return (
    <li className="flex flex-col gap-3 border-l-2 border-neon-cyan/30 pl-4">
      <p className="font-mono text-xs uppercase tracking-[0.2em] text-neon-cyan">
        {round.tag}
        {vaultLeft !== undefined ? <span className="ml-3 text-muted-foreground normal-case tracking-normal">{vaultLeft} $TINY still in the vault</span> : null}
      </p>
      {state.step === "checking" ? <p className="text-sm text-muted-foreground">checking…</p> : null}
      {state.step === "lost" ? <p className="text-sm text-muted-foreground">this wallet isn&apos;t one of the winners of {round.tag}.</p> : null}
      {claim && state.step !== "lost" ? (
        <p className="text-sm">
          you won <span className="text-neon-yellow">{formatTiny(BigInt(claim.amount_atomic), round.decimals)} $TINY</span>
          {claim.kind === "best_reply" ? " for the best reply" : " in the draw"}.
        </p>
      ) : null}
      {state.step === "broken" ? <p className="text-sm text-destructive">{state.why} nothing was sent. tell @teenydroid.</p> : null}
      {state.step === "claimed" ? <p className="text-sm text-muted-foreground">already claimed from this wallet.</p> : null}
      {state.step === "expired" ? <p className="text-sm text-muted-foreground">the claim window closed and the unclaimed tokens went back.</p> : null}
      {state.step === "ready" || state.step === "sending" || (state.step === "failed" && state.retry) ? (
        <div className="flex flex-col gap-2">
          <Button size="lg" className="w-fit" disabled={state.step === "sending"} onClick={send}>
            {state.step === "sending" ? "waiting for your wallet…" : "claim"}
          </Button>
          <p className="max-w-xl text-xs text-muted-foreground">
            you sign and you pay the network&apos;s fees: about 0.002 SOL, most of it rent for the record that you claimed
            and, if you don&apos;t hold $TINY yet, for your token account. have that much SOL in this wallet first.
          </p>
        </div>
      ) : null}
      {state.step === "failed" ? <p className="text-sm text-destructive">{state.why}</p> : null}
      {state.step === "done" ? (
        <a href={solscan("tx", state.signature, round.cluster)} target="_blank" rel="noreferrer" className="flex items-center gap-1 text-sm text-neon-cyan hover:text-neon-yellow">
          claimed. see it on solscan <ArrowUpRight className="size-4" />
        </a>
      ) : null}
    </li>
  )
}

/** "Did I win?": connect, then each round still paying out says what this wallet can claim. */
export function ClaimPanel({ rounds }: { rounds: ClaimableRound[] }) {
  const { publicKey } = useWallet()
  return (
    <div className="flex flex-col gap-5">
      <ConnectWallet />
      {publicKey ? (
        <ul className="flex flex-col gap-5">
          {rounds.map((round) => (
            <RoundClaim key={round.id} round={round} owner={publicKey} />
          ))}
        </ul>
      ) : null}
    </div>
  )
}
