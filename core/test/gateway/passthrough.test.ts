import { describe, expect, it } from "vitest";
import { openclawEnv } from "../../src/gateway/passthrough.js";

describe("teeny-core openclaw", () => {
  it("points the CLI at the core's state and generated config, and passes no secret on", () => {
    const env = openclawEnv(
      { TEENY_ROOT: "/srv/teeny", TEENY_STATE: "/s", PATH: "/bin", HOME: "/h", OPENHUMAN_CORE_TOKEN: "no", LOCAL_OPENAI_API_KEY: "no", OPENCLAW_GATEWAY_TOKEN: "no" },
    );
    expect(env).toEqual({ PATH: "/bin", HOME: "/h", OPENCLAW_STATE_DIR: "/s/openclaw", OPENCLAW_CONFIG_PATH: "/s/openclaw/openclaw.json" });
  });
});
