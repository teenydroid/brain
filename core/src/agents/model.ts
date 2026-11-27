// Model names. Callers say `glm-5.3-flash` (model_override) or
// `local-openai:glm-5.3-flash` (the stored provider strings); OpenClaw says
// `local-openai/glm-5.3-flash`.
import { PROVIDER_ID } from "../gateway/config.js";

export function toModelRef(name: string): string {
  const n = name.trim();
  if (n.startsWith(`${PROVIDER_ID}:`)) return `${PROVIDER_ID}/${n.slice(PROVIDER_ID.length + 1)}`;
  if (n.includes("/")) return n;
  return `${PROVIDER_ID}/${n}`;
}

export function bareModel(ref: string): string {
  const n = ref.trim();
  for (const sep of [`${PROVIDER_ID}:`, `${PROVIDER_ID}/`]) if (n.startsWith(sep)) return n.slice(sep.length);
  return n;
}
