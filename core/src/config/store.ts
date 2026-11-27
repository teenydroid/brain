// The core's own settings, the ones bin/spine sets over RPC at every boot:
// the agent turn budget, the model and the endpoint key, the model registry,
// and provider credentials. One JSON file in state/, mode 0600; keys and
// tokens are stored sealed (config/secrets.ts), never in clear.
import { chmodSync, mkdirSync, readFileSync, renameSync, writeFileSync } from "node:fs";
import { dirname, join } from "node:path";
import type { Sealer } from "./secrets.js";

/** OpenHuman's own default: a cron agent turn stopped at 120 s until bin/spine raised it. */
export const DEFAULT_AGENT_TIMEOUT_SECS = 120;

export interface ModelRegistryEntry {
  id: string;
  provider: string;
  context_window: number;
  [key: string]: unknown;
}

export interface StoredSettings {
  agentTimeoutSecs: number;
  localAi: { runtimeEnabled: boolean; optInConfirmed: boolean; nativeToolCalling: boolean; apiKeySealed?: string };
  models: Record<string, string>;
  modelRegistry: ModelRegistryEntry[];
  credentials: Record<string, { tokenSealed: string; active: boolean }>;
}

const EMPTY: StoredSettings = {
  agentTimeoutSecs: DEFAULT_AGENT_TIMEOUT_SECS,
  localAi: { runtimeEnabled: false, optInConfirmed: false, nativeToolCalling: true },
  models: {},
  modelRegistry: [],
  credentials: {},
};

export class SettingsStore {
  private data: StoredSettings;
  private readonly file: string;

  constructor(stateDir: string, private readonly sealer: Sealer | undefined) {
    this.file = join(stateDir, "core-settings.json");
    this.data = this.read();
  }

  private read(): StoredSettings {
    try {
      const parsed = JSON.parse(readFileSync(this.file, "utf8")) as Partial<StoredSettings>;
      return {
        ...structuredClone(EMPTY),
        ...parsed,
        localAi: { ...EMPTY.localAi, ...parsed.localAi },
      };
    } catch {
      return structuredClone(EMPTY);
    }
  }

  private save(): void {
    mkdirSync(dirname(this.file), { recursive: true });
    const tmp = `${this.file}.tmp`;
    writeFileSync(tmp, JSON.stringify(this.data, null, 2), { mode: 0o600 });
    chmodSync(tmp, 0o600);
    renameSync(tmp, this.file);
  }

  private need(): Sealer {
    if (!this.sealer) throw new Error("no master key: cannot store or read a secret");
    return this.sealer;
  }

  get snapshot(): Readonly<StoredSettings> {
    return this.data;
  }

  get agentTimeoutSecs(): number {
    return this.data.agentTimeoutSecs;
  }

  setAgentTimeout(secs: number): void {
    this.data.agentTimeoutSecs = secs;
    this.save();
  }

  setLocalAi(update: {
    runtimeEnabled: boolean;
    optInConfirmed: boolean;
    nativeToolCalling: boolean;
    apiKey?: string;
  }): void {
    const sealed = update.apiKey ? this.need().seal(update.apiKey) : this.data.localAi.apiKeySealed;
    this.data.localAi = {
      runtimeEnabled: update.runtimeEnabled,
      optInConfirmed: update.optInConfirmed,
      nativeToolCalling: update.nativeToolCalling,
      ...(sealed ? { apiKeySealed: sealed } : {}),
    };
    this.save();
  }

  /** The endpoint key in clear, for the Gateway's environment only. */
  localAiApiKey(): string | undefined {
    const sealed = this.data.localAi.apiKeySealed;
    return sealed ? this.need().open(sealed) : undefined;
  }

  setModels(models: Record<string, string>): void {
    this.data.models = { ...models };
    this.save();
  }

  setModelRegistry(registry: ModelRegistryEntry[]): void {
    this.data.modelRegistry = registry.map((e) => ({ ...e }));
    this.save();
  }

  storeCredential(provider: string, token: string, active: boolean): void {
    this.data.credentials[provider] = { tokenSealed: this.need().seal(token), active };
    this.save();
  }

  credential(provider: string): string | undefined {
    const c = this.data.credentials[provider];
    return c ? this.need().open(c.tokenSealed) : undefined;
  }
}
