"use client";

import { FormEvent, useEffect, useState } from "react";
import type { Evidence, Product, Recommendation, SearchResponse, SearchStatus } from "@/types/search";

type UIConfig = {
  examples: string[];
  relation_labels: Record<string, string>;
  status_labels: Record<string, string>;
};

type ReadinessState = "checking" | "ready" | "unavailable";

type SearchExperienceProps = { config: UIConfig };

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null;
}

function isSearchResponse(value: unknown): value is SearchResponse {
  if (!isRecord(value)) return false;
  const statuses: SearchStatus[] = ["ok", "partial", "empty", "clarification_needed", "unavailable"];
  return (
    typeof value.request_id === "string" &&
    typeof value.status === "string" &&
    statuses.includes(value.status as SearchStatus) &&
    Array.isArray(value.products) &&
    Array.isArray(value.recommendations)
  );
}

function getSessionId(): string {
  const storageKey = "softline-smart-search-session";
  try {
    const existing = window.localStorage.getItem(storageKey);
    if (existing) return existing;
    const created = window.crypto.randomUUID();
    window.localStorage.setItem(storageKey, created);
    return created;
  } catch {
    return window.crypto.randomUUID();
  }
}

function errorMessage(value: unknown): string | null {
  if (!isRecord(value)) return null;
  if (typeof value.message === "string" && value.message.trim()) return value.message;
  if (typeof value.detail === "string" && value.detail.trim()) return value.detail;
  if (Array.isArray(value.detail)) {
    const messages = value.detail
      .map((item) => {
        if (!isRecord(item)) return null;
        const location = Array.isArray(item.loc) ? item.loc.filter((part) => part !== "body").join(" → ") : "";
        const message = typeof item.msg === "string" ? item.msg : "Некорректное значение";
        return location ? `${location}: ${message}` : message;
      })
      .filter((item): item is string => Boolean(item));
    if (messages.length) return messages.join("; ");
  }
  return null;
}

function safeHttpsUrl(value: string | null | undefined): string | null {
  if (!value) return null;
  try {
    const url = new URL(value);
    return url.protocol === "https:" && url.hostname && !url.username && !url.password ? url.toString() : null;
  } catch {
    return null;
  }
}

function formatPrice(priceKopecks: number | null | undefined): string {
  if (typeof priceKopecks !== "number" || !Number.isSafeInteger(priceKopecks) || priceKopecks <= 0) {
    return "Цена по запросу";
  }
  return new Intl.NumberFormat("ru-RU", { style: "currency", currency: "RUB", maximumFractionDigits: 2 })
    .format(priceKopecks / 100);
}

function displayValue(value: unknown): string | null {
  if (value === null || value === undefined || value === "") return null;
  if (typeof value === "string" || typeof value === "number" || typeof value === "boolean") return String(value);
  try {
    return JSON.stringify(value);
  } catch {
    return null;
  }
}

function StockLabel({ inStock }: { inStock: boolean | null | undefined }) {
  if (inStock === true) return <span className="stock stockAvailable">В наличии по данным каталога</span>;
  if (inStock === false) return <span className="stock stockUnavailable">Нет в наличии</span>;
  return <span className="stock stockUnknown">Наличие неизвестно</span>;
}

function ProductCard({ product, config, recommendation }: {
  product: Product;
  config: UIConfig;
  recommendation?: Recommendation;
}) {
  const link = safeHttpsUrl(product.url);
  const attributes = Object.entries(product.attributes ?? {})
    .map(([label, value]) => [label, displayValue(value)] as const)
    .filter((entry): entry is readonly [string, string] => Boolean(entry[1]))
    .slice(0, 8);

  return (
    <article className="productCard">
      <div className="productTopline">
        <span className="categoryPill">{recommendation ? config.relation_labels[recommendation.type] ?? "Связанный товар" : product.category}</span>
        <StockLabel inStock={product.in_stock} />
      </div>
      <h3>{product.title}</h3>
      {(product.brand || product.sku) && (
        <p className="productMeta">
          {product.brand && <span>{product.brand}</span>}
          {product.brand && product.sku && <span aria-hidden="true"> · </span>}
          {product.sku && <span>Артикул: {product.sku}</span>}
        </p>
      )}
      <p className="productPrice">{formatPrice(product.price_kopecks)}</p>
      {product.license_units != null && <p className="productMeta">Единиц лицензии: {product.license_units}</p>}
      {product.description?.trim() && <p className="productDescription">{product.description}</p>}
      {attributes.length > 0 && (
        <dl className="attributesList">
          {attributes.map(([label, value]) => (
            <div key={label}>
              <dt>{label}</dt>
              <dd>{value}</dd>
            </div>
          ))}
        </dl>
      )}
      {recommendation && <RecommendationDetails recommendation={recommendation} />}
      {link && (
        <a className="productLink" href={link} target="_blank" rel="noopener noreferrer">
          Открыть карточку <span aria-hidden="true">↗</span>
        </a>
      )}
    </article>
  );
}

function EvidenceItem({ item }: { item: Evidence }) {
  const sourceUrl = safeHttpsUrl(item.source_url);
  return (
    <li className="evidenceItem">
      <span>{item.excerpt}</span>
      <span className="evidenceMeta">
        {item.source_type === "catalog" ? "Данные каталога" : "Источник производителя"}
        {item.catalog_field && ` · поле: ${item.catalog_field}`}
        {sourceUrl && (
          <>
            {" · "}
            <a href={sourceUrl} target="_blank" rel="noopener noreferrer">Источник ↗</a>
          </>
        )}
      </span>
    </li>
  );
}

function RecommendationDetails({ recommendation }: { recommendation: Recommendation }) {
  return (
    <div className="recommendationDetails">
      <p>{recommendation.claim}</p>
      {recommendation.evidence.length > 0 && (
        <details>
          <summary>Основания рекомендации ({recommendation.evidence.length})</summary>
          <ul>{recommendation.evidence.map((item, index) => <EvidenceItem key={`${item.checked_at}-${index}`} item={item} />)}</ul>
        </details>
      )}
    </div>
  );
}

function ResultPanel({ response, config }: { response: SearchResponse; config: UIConfig }) {
  const statusLabel = config.status_labels[response.status] ?? "Результат поиска";
  return (
    <section className={`resultPanel status-${response.status}`} aria-live="polite" aria-labelledby="result-heading">
      <div className="resultHeader">
        <div>
          <p className="eyebrow">Результат поиска</p>
          <h2 id="result-heading">{statusLabel}</h2>
        </div>
        {response.status === "partial" && <span className="statusMark">Частичный результат</span>}
      </div>

      {response.status === "partial" && (
        <p className="statusNote">Подбор неполный. Показаны только позиции, которые вернул поисковый сервис.</p>
      )}
      {response.status === "clarification_needed" && response.clarifying_question && (
        <div className="clarificationBox">
          <span className="clarificationIcon" aria-hidden="true">?</span>
          <div>
            <h3>Уточните запрос</h3>
            <p>{response.clarifying_question}</p>
            <span>Ответьте в поле выше — предыдущий запрос будет передан как контекст.</span>
          </div>
        </div>
      )}
      {response.status === "empty" && response.products.length === 0 && response.recommendations.length === 0 && (
        <p className="emptyMessage">Подходящих товаров не найдено. Попробуйте изменить запрос или выбрать пример.</p>
      )}
      {response.status === "unavailable" && (
        <div className="notice noticeError">
          <strong>Поисковая зависимость недоступна.</strong>
          <p>Сейчас показаны только данные, которые сервис успел получить.</p>
        </div>
      )}
      {response.warnings.length > 0 && (
        <details className="warningsBlock">
          <summary>Предупреждения ({response.warnings.length})</summary>
          <ul>{response.warnings.map((warning, index) => <li key={`${index}-${warning}`}>{warning}</li>)}</ul>
        </details>
      )}
      {response.products.length > 0 && (
        <div className="resultGroup">
          <div className="groupHeading"><h3>Товары</h3><span>{response.products.length}</span></div>
          <div className="productGrid">{response.products.map((product) => <ProductCard key={product.product_id} product={product} config={config} />)}</div>
        </div>
      )}
      {response.recommendations.length > 0 && (
        <div className="resultGroup">
          <div className="groupHeading"><h3>Рекомендации</h3><span>{response.recommendations.length}</span></div>
          <div className="productGrid">{response.recommendations.map((recommendation) => (
            <ProductCard key={`${recommendation.anchor_id}-${recommendation.product.product_id}`} product={recommendation.product} recommendation={recommendation} config={config} />
          ))}</div>
        </div>
      )}
      {response.reason_codes.length > 0 && (
        <details className="warningsBlock"><summary>Причины статуса</summary><ul>{response.reason_codes.map((code) => <li key={code}>{code}</li>)}</ul></details>
      )}
    </section>
  );
}

export default function SearchExperience({ config }: SearchExperienceProps) {
  const [query, setQuery] = useState("");
  const [sessionId, setSessionId] = useState("");
  const [readiness, setReadiness] = useState<ReadinessState>("checking");
  const [readinessMessage, setReadinessMessage] = useState("");
  const [retryReadiness, setRetryReadiness] = useState(0);
  const [loading, setLoading] = useState(false);
  const [response, setResponse] = useState<SearchResponse | null>(null);
  const [searchError, setSearchError] = useState("");
  const [previousContext, setPreviousContext] = useState<string | null>(null);

  useEffect(() => setSessionId(getSessionId()), []);

  useEffect(() => {
    let active = true;
    setReadiness("checking");
    setReadinessMessage("");
    fetch("/api/health/ready", { cache: "no-store", signal: AbortSignal.timeout(8_000) })
      .then(async (result) => {
        const data: unknown = await result.json().catch(() => null);
        if (!active) return;
        if (result.ok) {
          setReadiness("ready");
          return;
        }
        setReadiness("unavailable");
        setReadinessMessage(errorMessage(data) ?? `Сервис поиска вернул HTTP ${result.status}.`);
      })
      .catch(() => {
        if (!active) return;
        setReadiness("unavailable");
        setReadinessMessage("Не удалось подключиться к поисковому сервису.");
      });
    return () => { active = false; };
  }, [retryReadiness]);

  async function submitSearch(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const trimmedQuery = query.trim();
    if (!trimmedQuery || loading || readiness !== "ready") return;

    setLoading(true);
    setSearchError("");
    setResponse(null);
    const activeSessionId = sessionId || getSessionId();
    setSessionId(activeSessionId);
    try {
      const result = await fetch("/api/search", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({
          query: trimmedQuery,
          request_id: window.crypto.randomUUID(),
          session_id: activeSessionId,
          previous_context: previousContext,
        }),
      });
      const data: unknown = await result.json().catch(() => null);
      if (!isSearchResponse(data)) {
        const message = errorMessage(data);
        throw new Error(`HTTP ${result.status}${message ? `: ${message}` : ". Поисковый сервис вернул некорректный ответ."}`);
      }
      if (!result.ok && data.status !== "unavailable") {
        const message = errorMessage(data);
        throw new Error(`HTTP ${result.status}${message ? `: ${message}` : ". Поиск не выполнен."}`);
      }
      setResponse(data);
      setPreviousContext(data.status === "clarification_needed" ? trimmedQuery : null);
    } catch (error) {
      setSearchError(error instanceof Error ? error.message : "Не удалось выполнить поиск. Попробуйте ещё раз.");
    } finally {
      setLoading(false);
    }
  }

  return (
    <main className="pageShell">
      <header className="siteHeader">
        <a className="brand" href="/" aria-label="Умный поиск Softline — главная">
          <span className="brandMark" aria-hidden="true">s</span>
          <span><strong>softline</strong><small>умный поиск</small></span>
        </a>
        <div className={`serviceState service-${readiness}`} aria-live="polite">
          <span className="serviceDot" />
          {readiness === "checking" && "Проверяем сервис"}
          {readiness === "ready" && "Поиск доступен"}
          {readiness === "unavailable" && "Сервис недоступен"}
        </div>
      </header>

      <section className="hero">
        <div className="heroCopy">
          <p className="eyebrow">Подбор IT-товаров</p>
          <h1>Найдите нужное<br /><span>простыми словами</span></h1>
          <p className="heroDescription">Опишите задачу, назовите категорию или введите артикул — поиск подберёт товары и покажет обоснованные рекомендации.</p>
        </div>
        <div className="heroOrb" aria-hidden="true"><span>⌕</span></div>
      </section>

      <section className="searchPanel" aria-labelledby="search-heading">
        <h2 id="search-heading">Что вы ищете?</h2>
        <form onSubmit={submitSearch}>
          <label className="visuallyHidden" htmlFor="query">Запрос для поиска</label>
          <div className="inputFrame">
            <span className="inputIcon" aria-hidden="true">⌕</span>
            <textarea
              id="query"
              value={query}
              maxLength={1000}
              rows={2}
              placeholder="Например: сервер до 300 тысяч для офиса"
              onChange={(event) => setQuery(event.target.value)}
              disabled={loading}
              required
            />
            <span className="charCount">{query.length}/1000</span>
          </div>
          <div className="searchActions">
            <div className="readinessHint" role={readiness === "unavailable" ? "alert" : undefined}>
              {readiness === "checking" && "Подключаемся к сервису…"}
              {readiness === "unavailable" && <><span>{readinessMessage}</span><button type="button" className="textButton" onClick={() => setRetryReadiness((value) => value + 1)}>Проверить снова</button></>}
            </div>
            <button className="searchButton" type="submit" disabled={loading || !query.trim() || readiness !== "ready"}>
              {loading ? <><span className="buttonSpinner" />Ищем товары…</> : <>Найти товары <span aria-hidden="true">→</span></>}
            </button>
          </div>
        </form>
        <div className="examplesBlock">
          <p>Попробуйте, например</p>
          <div className="exampleList">
            {config.examples.map((example) => (
              <button key={example} type="button" className="exampleChip" disabled={loading} onClick={() => setQuery(example)}>{example}</button>
            ))}
          </div>
        </div>
      </section>

      {searchError && (
        <section className="notice noticeError searchError" role="alert">
          <span className="noticeIcon" aria-hidden="true">!</span>
          <div><h2>Не удалось выполнить поиск</h2><p>{searchError}</p><span>Техническая ошибка не означает, что в каталоге нет товаров.</span></div>
        </section>
      )}
      {response && <ResultPanel response={response} config={config} />}
      {!response && !searchError && !loading && (
        <section className="trustNote">
          <span aria-hidden="true">✳</span>
          <p>Рекомендации сопровождаются объяснением и основаниями из каталога или источников производителя.</p>
        </section>
      )}

      <footer className="siteFooter">
        <span>Умный поиск Softline</span>
        <span>Сведения о товарах предоставляются поисковым сервисом</span>
      </footer>
    </main>
  );
}
