/** What the wallet module asks its host for: one unsigned transaction per swap or bridge, and the routes. */
export type BackendCall =
  | { kind: "swap"; body: Record<string, unknown> }
  | { kind: "bridge"; body: Record<string, unknown> }
  | { kind: "routes" };

/** One GET to a provider: the path and its query, in order. */
export interface ProviderRequest {
  path: string;
  query: Array<[string, string]>;
}

export type Env = Readonly<Record<string, string | undefined>>;

export const ERROR_CHARS = 300;

export function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

export function cut(text: string, chars: number): string {
  return Array.from(text).slice(0, chars).join("");
}
