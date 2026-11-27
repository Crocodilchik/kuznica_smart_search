export const SEARCH_TIMEOUT_MS = 120_000;
export const READINESS_TIMEOUT_MS = 5_000;

type ProxyOptions = {
  apiUrl: string;
  fetchImpl?: typeof fetch;
  timeoutMs?: number;
  path: "/v1/search" | "/health/ready";
  method: "POST" | "GET";
};

function jsonError(status: number, code: string, message: string): Response {
  return Response.json({ code, message }, { status, headers: { "cache-control": "no-store" } });
}

function proxyHeaders(upstream: Response): Headers {
  const headers = new Headers({ "cache-control": "no-store" });
  const contentType = upstream.headers.get("content-type");
  if (contentType) headers.set("content-type", contentType);
  return headers;
}

export async function proxyBackendRequest(
  request: Request | null,
  { apiUrl, fetchImpl = fetch, timeoutMs = SEARCH_TIMEOUT_MS, path, method }: ProxyOptions,
): Promise<Response> {
  const baseUrl = apiUrl.trim().replace(/\/+$/, "");
  if (!baseUrl) {
    return jsonError(503, "backend_url_missing", "Адрес поискового сервиса не настроен.");
  }

  let body: string | undefined;
  if (method === "POST") {
    if (!request) return jsonError(400, "request_body_missing", "Не удалось прочитать запрос.");
    body = await request.text();
  }

  try {
    const upstream = await fetchImpl(`${baseUrl}${path}`, {
      method,
      headers: method === "POST" ? { "content-type": "application/json" } : undefined,
      body,
      cache: "no-store",
      signal: AbortSignal.timeout(timeoutMs),
    });

    return new Response(upstream.body, {
      status: upstream.status,
      statusText: upstream.statusText,
      headers: proxyHeaders(upstream),
    });
  } catch (error) {
    const name = error instanceof Error ? error.name : "";
    if (name === "TimeoutError" || name === "AbortError") {
      const message =
        method === "POST"
          ? "Поиск выполняется слишком долго. Попробуйте повторить запрос."
          : "Проверка готовности поискового сервиса превысила время ожидания.";
      return jsonError(504, "backend_timeout", message);
    }

    const message =
      method === "POST"
        ? "Не удалось подключиться к поисковому сервису. Проверьте его доступность и попробуйте снова."
        : "Сервис поиска сейчас недоступен. Попробуйте проверить подключение ещё раз.";
    return jsonError(503, "backend_unavailable", message);
  }
}
