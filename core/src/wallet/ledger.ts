// The persistent spend ledger (OpenHuman's web3::wallet::ledger).
//
// Every execute writes one record BEFORE anything is signed (`pending`) and
// updates it afterwards (`broadcast` with the signature, or `in_doubt` with
// the error). A refused execute is recorded `refused` for the audit trail.
// The spend policy sums these records for its rolling caps, so a crash
// between the two writes leaves a `pending` record that still counts.
//
// Records are one JSON file (mode 0600), rewritten atomically; reserve() holds
// an in-process mutex and a cross-process lock file across read, decide and
// write, so two cores cannot both pass the same cap.
import { join } from "node:path";
import { readJsonFile, withFileLock, withMutex, writeFileAtomic } from "./util.js";

export type LedgerStatus = "pending" | "broadcast" | "in_doubt" | "refused";
export type LedgerKind = "transfer" | "program" | "swap" | "bridge" | "dapp";

export interface LedgerRecord {
  id: string;
  quoteId: string;
  createdAtMs: number;
  updatedAtMs: number;
  origin: string;
  originClass: string;
  policyApplied: boolean;
  chain: string;
  evmNetwork?: string;
  assetSymbol: string;
  tokenAddress?: string;
  toAddress: string;
  amountRaw: string;
  status: LedgerStatus;
  transactionHash?: string;
  error?: string;
  /** Absent for a transfer. */
  kind?: LedgerKind;
  /** Absent for the primary account. */
  accountIndex?: number;
}

export function countsAgainstCaps(status: LedgerStatus): boolean {
  return status !== "refused";
}

/** The amount as an integer; an unparseable amount reads as huge so it can only make a cap stricter. */
export function recordAmount(record: LedgerRecord): bigint {
  return /^\s*\d+\s*$/.test(record.amountRaw) ? BigInt(record.amountRaw.trim()) : (1n << 128n) - 1n;
}

export class SpendLedger {
  private readonly file: string;

  constructor(dir: string) {
    this.file = join(dir, "spend-ledger.json");
  }

  records(): LedgerRecord[] {
    const doc = readJsonFile<{ records: LedgerRecord[] }>(this.file);
    return doc?.records ?? [];
  }

  /** The newest LIMIT records, newest first. */
  recent(limit: number): LedgerRecord[] {
    return this.records().slice().reverse().slice(0, limit);
  }

  private write(records: LedgerRecord[]): void {
    writeFileAtomic(this.file, JSON.stringify({ records }, null, 1));
  }

  /** Insert or replace RECORD (by id). */
  async put(record: LedgerRecord): Promise<void> {
    await this.locked(() => {
      const all = this.records();
      const at = all.findIndex((r) => r.id === record.id);
      if (at >= 0) all[at] = record;
      else all.push(record);
      all.sort((a, b) => a.createdAtMs - b.createdAtMs);
      this.write(all);
    });
  }

  /**
   * Decide and reserve as one step. DECIDE sees every record and returns the
   * `pending` record to reserve, or throws to refuse. Nothing may be signed
   * before this returns.
   */
  async reserve(decide: (records: LedgerRecord[]) => LedgerRecord): Promise<LedgerRecord> {
    return this.locked(() => {
      const all = this.records();
      const record = decide(all);
      all.push(record);
      all.sort((a, b) => a.createdAtMs - b.createdAtMs);
      this.write(all);
      return record;
    });
  }

  private locked<T>(body: () => T): Promise<T> {
    return withMutex(this.file, () => withFileLock(this.file, body));
  }
}
