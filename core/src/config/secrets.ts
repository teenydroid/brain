// The master key and the secrets encrypted under it. bin/spine mints
// secrets/openhuman.key (64 hex characters) and points
// OPENHUMAN_KEYRING_MASTER_KEY_FILE at it; every secret the core stores is
// sealed with AES-256-GCM under a key derived from that file, so a store
// without its key file is unreadable. Nothing here logs or returns the key.
import { createCipheriv, createDecipheriv, createHash, randomBytes } from "node:crypto";
import { readFileSync } from "node:fs";
import { join } from "node:path";

const PREFIX = "enc2:";

type Env = Readonly<Record<string, string | undefined>>;

export function masterKeyFile(env: Env, secretsDir: string): string {
  return env.OPENHUMAN_KEYRING_MASTER_KEY_FILE || join(secretsDir, "openhuman.key");
}

export class Sealer {
  private readonly key: Buffer;

  constructor(masterKey: string) {
    const trimmed = masterKey.trim();
    if (!trimmed) throw new Error("the master key is empty");
    this.key = createHash("sha256").update("teeny-core/secret/v1\0").update(trimmed).digest();
  }

  static fromFile(path: string): Sealer {
    let text: string;
    try {
      text = readFileSync(path, "utf8");
    } catch {
      throw new Error(`no master key at ${path}`);
    }
    return new Sealer(text);
  }

  seal(plaintext: string): string {
    const nonce = randomBytes(12);
    const cipher = createCipheriv("aes-256-gcm", this.key, nonce);
    const body = Buffer.concat([cipher.update(plaintext, "utf8"), cipher.final()]);
    return PREFIX + Buffer.concat([nonce, cipher.getAuthTag(), body]).toString("base64");
  }

  open(sealed: string): string {
    if (!sealed.startsWith(PREFIX)) throw new Error("not a sealed secret");
    const raw = Buffer.from(sealed.slice(PREFIX.length), "base64");
    if (raw.length < 28) throw new Error("sealed secret is truncated");
    const decipher = createDecipheriv("aes-256-gcm", this.key, raw.subarray(0, 12));
    decipher.setAuthTag(raw.subarray(12, 28));
    try {
      return Buffer.concat([decipher.update(raw.subarray(28)), decipher.final()]).toString("utf8");
    } catch {
      throw new Error("sealed secret does not open under this master key");
    }
  }
}
