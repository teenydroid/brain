// The config, credential, cost and readiness methods. Contract:
// core/test/contract/{config_*,get_config,inference_update_model_settings,
// auth_store_provider_credentials,cost_report,
// modules_browser_check_readiness}.json. (encrypt_secret is the wallet's: wallet/index.ts.)
import { RpcError, type CoreContext, type RpcMethods } from "./types.js";
import type { ModelRegistryEntry } from "../config/store.js";

function svc(ctx: CoreContext) {
  if (!ctx.services) throw new RpcError("core services are not available", -32603);
  return ctx.services;
}

function bool(p: Record<string, unknown>, key: string): boolean {
  if (typeof p[key] !== "boolean") throw new RpcError(`${key} must be a boolean`, -32602);
  return p[key] as boolean;
}

function string(p: Record<string, unknown>, key: string): string {
  const v = p[key];
  if (typeof v !== "string" || v === "") throw new RpcError(`${key} must be a non-empty string`, -32602);
  return v;
}

const MODEL_KEYS = ["default_model", "chat_provider", "reasoning_provider", "agentic_provider", "coding_provider", "vision_provider", "memory_provider"];

function registryOf(p: Record<string, unknown>): ModelRegistryEntry[] {
  const reg = p.model_registry;
  if (!Array.isArray(reg)) throw new RpcError("model_registry must be an array", -32602);
  return reg.map((e, i) => {
    const o = e as Record<string, unknown>;
    if (typeof o !== "object" || o === null || typeof o.id !== "string" || typeof o.provider !== "string" || typeof o.context_window !== "number") {
      throw new RpcError(`model_registry[${i}] needs id, provider and context_window`, -32602);
    }
    return o as ModelRegistryEntry;
  });
}

export const coreMethods: RpcMethods = {
  async config_update_agent_settings(p, ctx) {
    const secs = p.agent_timeout_secs;
    // The whole-second value is what gets stored, so that is what must be positive: 0.5 floors to 0.
    const whole = typeof secs === "number" && Number.isFinite(secs) ? Math.floor(secs) : Number.NaN;
    if (!(whole >= 1)) throw new RpcError("agent_timeout_secs must be a positive number", -32602);
    svc(ctx).store.setAgentTimeout(whole);
    svc(ctx).configChanged();
    return { agent_timeout_secs: whole };
  },

  async config_get_agent_settings(_p, ctx) {
    return { agent_timeout_secs: svc(ctx).store.agentTimeoutSecs };
  },

  async config_update_local_ai_settings(p, ctx) {
    const apiKey = p.api_key;
    if (apiKey !== undefined && typeof apiKey !== "string") throw new RpcError("api_key must be a string", -32602);
    svc(ctx).store.setLocalAi({
      runtimeEnabled: bool(p, "runtime_enabled"),
      optInConfirmed: bool(p, "opt_in_confirmed"),
      nativeToolCalling: bool(p, "native_tool_calling"),
      ...(apiKey ? { apiKey } : {}),
    });
    svc(ctx).configChanged();
    return { ok: true }; // the key is never echoed
  },

  async config_update_model_settings(p, ctx) {
    const models: Record<string, string> = {};
    for (const k of MODEL_KEYS) models[k] = string(p, k);
    svc(ctx).store.setModels(models);
    svc(ctx).configChanged();
    return { ok: true, default_model: models.default_model };
  },

  async inference_update_model_settings(p, ctx) {
    // The WHOLE registry: the write replaces it.
    svc(ctx).store.setModelRegistry(registryOf(p));
    svc(ctx).configChanged();
    return { ok: true };
  },

  async get_config(_p, ctx) {
    const s = svc(ctx).store.snapshot;
    // Never the key, the credentials or anything sealed.
    return {
      config: {
        default_model: s.models.default_model ?? null,
        model_registry: s.modelRegistry,
        models: s.models,
        local_ai: { runtime_enabled: s.localAi.runtimeEnabled, opt_in_confirmed: s.localAi.optInConfirmed, native_tool_calling: s.localAi.nativeToolCalling, has_api_key: Boolean(s.localAi.apiKeySealed) },
        agent: { agent_timeout_secs: s.agentTimeoutSecs },
      },
    };
  },

  async auth_store_provider_credentials(p, ctx) {
    const provider = string(p, "provider");
    const token = string(p, "token");
    svc(ctx).store.storeCredential(provider, token, p.setActive === true);
    return { ok: true, provider }; // the token is never echoed or logged
  },

  async cost_report(p, ctx) {
    const days = p.days;
    if (typeof days !== "number" || !Number.isFinite(days) || days <= 0) throw new RpcError("days must be a positive number", -32602);
    return svc(ctx).costLog.report(days);
  },

  // A shallow probe, not TinyComputer's disposable-session check: the Gateway
  // is up and a Chrome binary exists. It does not open a page.
  async modules_browser_check_readiness(_p, ctx) {
    const gateway = svc(ctx).gatewayRunning();
    const chrome = svc(ctx).findChrome();
    return {
      module_ready: gateway,
      chrome_ready: chrome !== undefined,
      ...(!gateway ? { error: "the OpenClaw gateway is not running" } : chrome === undefined ? { error: "no Chrome or Chromium binary found (set CHROME_BIN)" } : {}),
    };
  },
};
