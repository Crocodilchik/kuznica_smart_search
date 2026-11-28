import assert from "node:assert/strict";
import test from "node:test";
import { READINESS_TIMEOUT_MS, SEARCH_TIMEOUT_MS, proxyBackendRequest } from "../src/lib/search-proxy.ts";

test("search and readiness timeouts stay within the configured limits", () => {
  assert.equal(SEARCH_TIMEOUT_MS, 120_000);
  assert.ok(READINESS_TIMEOUT_MS <= 120_000);
});

const upstreamPayloads = [
  [200, { status: "ok", products: [{ product_id: "1" }] }],
  [200, { status: "partial", products: [{ product_id: "1" }] }],
  [200, { status: "empty", products: [] }],
  [200, { status: "clarification_needed", clarifying_question: "Сколько устройств?" }],
  [503, { status: "unavailable", warnings: ["index unavailable"] }],
  [422, { detail: [{ loc: ["body", "query"], msg: "Field required" }] }],
  [503, { code: "backend_not_configured", message: "Set backend module." }],
];

for (const [status, payload] of upstreamPayloads) {
  test(`preserves upstream HTTP ${status} status and response body`, async () => {
    const fetchImpl = async (_url, options) => {
      assert.equal(options.method, "POST");
      assert.equal(options.body, JSON.stringify({ query: "сервер" }));
      assert.equal(options.headers["content-type"], "application/json");
      return Response.json(payload, { status });
    };

    const response = await proxyBackendRequest(
      new Request("http://node.local/api/search", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ query: "сервер" }),
      }),
      { apiUrl: "http://api:8000/", fetchImpl, path: "/v1/search", method: "POST" },
    );

    assert.equal(response.status, status);
    assert.deepEqual(await response.json(), payload);
    assert.equal(response.headers.get("cache-control"), "no-store");
  });
}

test("returns a distinct timeout response", async () => {
  const fetchImpl = async (_url, options) => {
    assert.ok(options.signal instanceof AbortSignal);
    return await new Promise((_resolve, reject) => {
      options.signal.addEventListener("abort", () => reject(options.signal.reason), { once: true });
    });
  };
  const response = await proxyBackendRequest(null, {
    apiUrl: "http://api:8000",
    fetchImpl,
    timeoutMs: 1,
    path: "/health/ready",
    method: "GET",
  });
  assert.equal(response.status, 504);
  assert.equal((await response.json()).code, "backend_timeout");
});

test("readiness uses the backend readiness route without a request body", async () => {
  const response = await proxyBackendRequest(null, {
    apiUrl: "http://api:8000",
    fetchImpl: async (url, options) => {
      assert.equal(url, "http://api:8000/health/ready");
      assert.equal(options.method, "GET");
      assert.equal(options.body, undefined);
      return Response.json({ status: "ready", mode: "prod" });
    },
    path: "/health/ready",
    method: "GET",
  });
  assert.equal(response.status, 200);
  assert.deepEqual(await response.json(), { status: "ready", mode: "prod" });
});

test("returns a distinct network error response", async () => {
  const response = await proxyBackendRequest(null, {
    apiUrl: "http://api:8000",
    fetchImpl: async () => {
      throw new TypeError("connection refused");
    },
    path: "/health/ready",
    method: "GET",
  });
  assert.equal(response.status, 503);
  assert.equal((await response.json()).code, "backend_unavailable");
});
