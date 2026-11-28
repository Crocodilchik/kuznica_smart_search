import { proxyBackendRequest } from "@/lib/search-proxy";

export const runtime = "nodejs";
export const maxDuration = 130;

export async function POST(request: Request): Promise<Response> {
  return proxyBackendRequest(request, {
    apiUrl: process.env.SMART_SEARCH_API_URL ?? "http://127.0.0.1:8000",
    path: "/v1/search",
    method: "POST",
  });
}
