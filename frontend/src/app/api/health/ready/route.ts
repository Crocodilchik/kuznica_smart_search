import { READINESS_TIMEOUT_MS, proxyBackendRequest } from "@/lib/search-proxy";

export const runtime = "nodejs";

export async function GET(): Promise<Response> {
  return proxyBackendRequest(null, {
    apiUrl: process.env.SMART_SEARCH_API_URL ?? "http://127.0.0.1:8000",
    path: "/health/ready",
    method: "GET",
    timeoutMs: READINESS_TIMEOUT_MS,
  });
}
