// An HTTP(S) request whose connection goes to addresses the caller already
// vetted, whatever DNS says now. This closes the gap between "the name
// resolved to a public address" (the guard) and "the request connected to
// that address" (the fetch): the TLS handshake and the Host header still use
// the original name, but the socket is only ever opened to a vetted address.
import http from "node:http";
import https from "node:https";
import type { LookupFunction } from "node:net";

const MAX_BYTES = 256 * 1024 + 1;

/** A `lookup` that answers only with ADDRESSES, for any name. */
export function pinnedLookup(addresses: readonly string[]): LookupFunction {
  return (_hostname, options, callback) => {
    const wanted = addresses.map((address) => ({ address, family: address.includes(":") ? 6 : 4 }));
    if (options.all) (callback as unknown as (e: null, a: typeof wanted) => void)(null, wanted);
    else callback(null, wanted[0]!.address, wanted[0]!.family);
  };
}

/** The response, with the body cut at 256 KiB plus one byte (enough to know it was cut). Redirects are not followed. */
export function pinnedFetch(url: string, init: RequestInit, addresses: readonly string[]): Promise<Response> {
  const target = new URL(url);
  if (addresses.length === 0) return Promise.reject(new Error("no vetted address to connect to"));
  const client = target.protocol === "https:" ? https : target.protocol === "http:" ? http : undefined;
  if (!client) return Promise.reject(new Error("unsupported scheme"));
  const headers: Record<string, string> = {};
  new Headers(init.headers).forEach((value, key) => {
    headers[key] = value;
  });
  // The request goes to the vetted URL's authority, and TLS is checked against its name: a caller-supplied Host
  // that names anything else would reach another virtual host on the same address.
  if (headers.host !== undefined && headers.host.toLowerCase() !== target.host.toLowerCase()) {
    return Promise.reject(new Error("the Host header does not match the request URL"));
  }
  headers.host = target.host;
  return new Promise((resolve, reject) => {
    const request = client.request(
      target,
      {
        method: init.method ?? "GET",
        headers,
        lookup: pinnedLookup(addresses),
        agent: false,
        ...(target.protocol === "https:" && !/^[\d.]+$|^\[/.test(target.hostname) ? { servername: target.hostname } : {}),
        ...(init.signal ? { signal: init.signal } : {}) },
      (response) => {
        const chunks: Buffer[] = [];
        let size = 0;
        response.on("data", (chunk: Buffer) => {
          if (size >= MAX_BYTES) return;
          chunks.push(chunk);
          size += chunk.length;
          if (size >= MAX_BYTES) response.destroy();
        });
        let finished = false;
        const done = (): void => {
          if (finished) return;
          finished = true;
          try {
            resolve(buildResponse());
          } catch (error) {
            reject(error);
          }
        };
        const buildResponse = (): Response => {
          const out = new Headers();
          for (const [key, value] of Object.entries(response.headers)) {
            for (const v of Array.isArray(value) ? value : value === undefined ? [] : [value]) out.append(key, v);
          }
          const status = response.statusCode ?? 0;
          const nullBody = status === 101 || status === 204 || status === 205 || status === 304;
          const body = Buffer.concat(chunks).subarray(0, MAX_BYTES);
          // Response() refuses a status outside 200-599; that must be a rejection, not an exception in an event handler.
          if (!Number.isInteger(status) || status < 200 || status > 599) throw new Error(`the server answered an unsupported HTTP status (${status})`);
          return new Response(nullBody ? null : new Uint8Array(body), { status, headers: out });
        };
        response.on("end", done);
        response.on("close", done);
        response.on("error", reject);
      },
    );
    request.on("error", reject);
    if (typeof init.body === "string") request.write(init.body);
    request.end();
  });
}
