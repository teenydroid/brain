// hands/lib/teeny_hands/rest.py has no test of its own in Python (hands/moltbook
// and hands/mail exercise it); these pin the promises its docstring makes.

import { createServer, type IncomingMessage, type Server, type ServerResponse } from "node:http";
import type { AddressInfo } from "node:net";
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { HandError } from "../../../src/hands/lib/hand.js";
import { RestApi, RestError, TooLarge, quote, urlencode } from "../../../src/hands/lib/rest.js";

const KEY = "sk-live-0123456789abcdef";
let server: Server;
let base: string;
let seen: { method: string; url: string; headers: IncomingMessage["headers"]; body: string }[];
let answer: (req: IncomingMessage, res: ServerResponse) => void;

beforeEach(async () => {
  seen = [];
  answer = (_req, res) => res.end("{}");
  server = createServer((req, res) => {
    const chunks: Buffer[] = [];
    req.on("data", (c: Buffer) => chunks.push(c));
    req.on("end", () => {
      seen.push({ method: req.method ?? "", url: req.url ?? "", headers: req.headers, body: Buffer.concat(chunks).toString() });
      answer(req, res);
    });
  });
  await new Promise<void>((done) => server.listen(0, "127.0.0.1", done));
  base = `http://127.0.0.1:${(server.address() as AddressInfo).port}`;
});
afterEach(() => new Promise<void>((done) => (server.listening ? server.close(() => done()) : done())));

const api = (key: string | null = KEY): RestApi => new RestApi("svc", `${base}/`, key, "teeny-test/1");

describe("a call", () => {
  it("sends the key, the agent, the query and a JSON body, and reads the JSON back", async () => {
    answer = (_req, res) => res.writeHead(200, { "x-thing": "yes" }).end('{"a": [1, 2]}');
    const [parsed, headers] = await api().exchange("POST", "/v1/things", { query: [["q", "a b&c"], ["n", "é"]], body: { x: 1 }, headers: { "X-Extra": "1" } });
    expect(parsed).toEqual({ a: [1, 2] });
    expect(headers.get("x-thing")).toBe("yes");
    const [req] = seen;
    expect(req?.url).toBe("/v1/things?q=a+b%26c&n=%C3%A9");
    expect(req?.headers.authorization).toBe(`Bearer ${KEY}`);
    expect(req?.headers["user-agent"]).toBe("teeny-test/1");
    expect(req?.headers.accept).toBe("application/json");
    expect(req?.headers["content-type"]).toBe("application/json");
    expect(req?.headers["x-extra"]).toBe("1");
    expect(req?.body).toBe('{"x":1}');
  });

  it("sends no key at all with auth off, and refuses a call with no key to send", async () => {
    await api().call("GET", "/open", { auth: false });
    expect(seen[0]?.headers.authorization).toBeUndefined();
    await expect(api(null).call("GET", "/x")).rejects.toThrow("svc: no API key");
    await expect(api(null).call("GET", "/x")).rejects.toBeInstanceOf(HandError);
    expect(seen).toHaveLength(1);
  });

  it("takes an empty answer as {} and a JSON null as an answer", async () => {
    answer = (_req, res) => res.writeHead(204).end();
    expect(await api().call("DELETE", "/x")).toEqual({});
    answer = (_req, res) => res.end("null");
    expect(await api().call("GET", "/x")).toBeNull();
  });

  it("says something unreadable is unreadable", async () => {
    answer = (_req, res) => res.end("<html>");
    await expect(api().call("GET", "/x")).rejects.toThrow("svc sent something unreadable");
  });
});

describe("a failure", () => {
  it("names the status and the service's own short message, and cuts the key out of it", async () => {
    answer = (_req, res) => res.writeHead(401).end(JSON.stringify({ error: `bad key ${KEY} sorry`.padEnd(400, "!") }));
    const failure = await api().call("GET", "/x").catch((e: unknown) => e);
    expect(failure).toBeInstanceOf(RestError);
    const err = failure as RestError;
    expect(err.status).toBe(401);
    expect(err.message.startsWith("svc answered 401: bad key [the key] sorry")).toBe(true);
    expect(err.message).not.toContain(KEY);
    expect(err.message.length).toBe("svc answered 401: ".length + 200);
  });

  it("answers a bare status when the service gave no message", async () => {
    answer = (_req, res) => res.writeHead(500).end("oops");
    await expect(api().call("GET", "/x")).rejects.toThrow("svc answered 500");
  });

  it("reads Retry-After from the header or the body on a 429, and only on a 429", async () => {
    answer = (_req, res) => res.writeHead(429, { "retry-after": "17" }).end("{}");
    expect(((await api().call("GET", "/x").catch((e: unknown) => e)) as RestError).retryAfter).toBe(17);
    answer = (_req, res) => res.writeHead(429).end('{"retry_after_minutes": 2}');
    expect(((await api().call("GET", "/x").catch((e: unknown) => e)) as RestError).retryAfter).toBe(120);
    answer = (_req, res) => res.writeHead(503, { "retry-after": "9" }).end("{}");
    expect(((await api().call("GET", "/x").catch((e: unknown) => e)) as RestError).retryAfter).toBeNull();
  });

  it("never follows a redirect, so the key goes nowhere else", async () => {
    answer = (req, res) => {
      if (req.url === "/start") res.writeHead(301, { location: "/elsewhere" }).end();
      else res.end("{}");
    };
    const failure = (await api().call("GET", "/start").catch((e: unknown) => e)) as RestError;
    expect(failure.message).toBe("svc answered a redirect (301); not followed with the key");
    expect(failure.status).toBe(301);
    expect(seen.map((r) => r.url)).toEqual(["/start"]);
  });

  it("does not name the URL when the service cannot be reached", async () => {
    // A port that was open a moment ago and is not now (fetch refuses port 9 and its kin outright).
    const closed = (server.address() as AddressInfo).port;
    await new Promise<void>((done) => server.close(() => done()));
    const dead = new RestApi("svc", `http://127.0.0.1:${closed}/secret/path`, KEY, "a");
    const failure = (await dead.call("GET", "/x?token=1").catch((e: unknown) => e)) as RestError;
    expect(failure.message).toBe("could not reach svc (ConnectionRefusedError)");
    expect(failure.status).toBeNull();
    expect(failure.message).not.toMatch(/127|secret|token/);
  });

  it("stops reading past a limit", async () => {
    answer = (_req, res) => res.end(JSON.stringify({ pad: "x".repeat(5000) }));
    const failure = (await api().request("GET", "/x", { limit: 100 }).catch((e: unknown) => e)) as RestError;
    expect(failure).toBeInstanceOf(TooLarge);
    expect(failure.message).toBe("svc answered more than 100 bytes");
    const [status, parsed] = await api().request("GET", "/x", { limit: 100_000 });
    expect(status).toBe(200);
    expect((parsed as { pad: string }).pad).toHaveLength(5000);
  });
});

describe("quoting", () => {
  it("makes one path segment of anything", () => {
    expect(quote("a/b c?d#e")).toBe("a%2Fb%20c%3Fd%232e".replace("%232e", "%23e"));
    expect(quote(42)).toBe("42");
    expect(quote("~-_.")).toBe("~-_.");
    expect(quote("!'()*")).toBe("%21%27%28%29%2A");
  });

  it("encodes a query as urlencode does", () => {
    expect(urlencode([["a b", "c/d"], ["é", "~"]])).toBe("a+b=c%2Fd&%C3%A9=~");
  });
});
