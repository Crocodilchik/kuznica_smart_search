"use client";

import { useEffect, useRef, useState } from "react";
import type { FormEvent } from "react";
import type { Evidence, Product, Recommendation, SearchResponse, SearchStatus } from "@/types/search";

type UIConfig = {
  examples: string[];
  relation_labels: Record<string, string>;
  status_labels: Record<string, string>;
};

type ReadinessState = "checking" | "ready" | "unavailable";
type SearchExperienceProps = { config: UIConfig };
type ChatMessage = {
  id: string;
  role: "user" | "assistant";
  text: string;
  response?: SearchResponse;
};
type StoreProduct = {
  id: string;
  brand: string;
  sku?: string;
  title: string;
  image: string;
  price: number;
  url: string;
  reviews?: { rating: string; label: string };
};

const bannerSlides = [
  { image: "/softline-store/hero-01.png", alt: "Решения для резервного копирования Softline Cloud" },
  { image: "/softline-store/hero-02.png", alt: "Ideco — защита сетей и фильтрация трафика" },
  { image: "/softline-store/hero-03.png", alt: "Антивирус PRO32 со скидкой" },
  { image: "/softline-store/hero-04.png", alt: "Акции интернет-магазина Softline" },
  { image: "/softline-store/hero-05.png", alt: "VK WorkSpace для совместной работы" },
  { image: "/softline-store/hero-06.png", alt: "Виртуальное рабочее место с ChatGPT Plus" },
  { image: "/softline-store/hero-07.png", alt: "Astra Linux для бизнеса" },
];

const storeProducts: StoreProduct[] = [
  {
    id: "kaspersky-endpoint",
    brand: "«Лаборатория Касперского»",
    title: "Kaspersky Endpoint Security для бизнеса Стандартный, базовая лицензия на 1 год по количеству узлов",
    image: "/softline-store/kaspersky-endpoint.jpg",
    price: 3318.21,
    url: "https://store.softline.ru/kaspersky/kl4863lakfs-4982/",
    sku: "KL4863RAKFS",
    reviews: { rating: "4,5", label: "2 отзыва" },
  },
  {
    id: "microsoft-family",
    brand: "Программы и лицензии Microsoft Corporation",
    title: "Microsoft 365 для семьи (Family) 6GQ-00084: подписка на 6 пользователей, 1 год",
    image: "/softline-store/microsoft-family.png",
    price: 11247,
    url: "https://store.softline.ru/microsoft/6gq-00084-195981/",
    sku: "6GQ-00084",
    reviews: { rating: "4,8", label: "30 отзывов" },
  },
  {
    id: "cryptopro-workstation",
    brand: "КриптоПро",
    title: "КриптоПро CSP 5.0: годовая лицензия на 1 рабочее место",
    image: "/softline-store/cryptopro-workstation.png",
    price: 1850,
    url: "https://store.softline.ru/kripto-pro/-411620/",
    reviews: { rating: "5,0", label: "6 отзывов" },
  },
  {
    id: "microsoft-personal",
    brand: "Программы и лицензии Microsoft Corporation",
    title: "Microsoft 365 Персональный, Электронная подписка для 1 пользователя, на 1 год",
    image: "/softline-store/microsoft-personal.png",
    price: 9103,
    url: "https://store.softline.ru/microsoft/qq2-00004-195982/",
    sku: "QQ2-00004",
    reviews: { rating: "5,0", label: "25 отзывов" },
  },
  {
    id: "astra-linux",
    brand: "Группа Астра",
    title: "Astra Linux Special Edition 1.8, х86-64, «Орел», РУСБ.10015-10, способ передачи электронный, для рабочей станции, на срок действия исключительного права, с включенными обновлениями Тип 1 на 12 мес.",
    image: "/softline-store/astra-linux.png",
    price: 12000,
    url: "https://store.softline.ru/astralinux/os2200x8618digsktws01-so12-489919/",
    sku: "OS2200X8618DIGSKTWS01-SO12",
    reviews: { rating: "5,0", label: "4 отзыва" },
  },
  {
    id: "popular-2410137",
    brand: "КриптоПро",
    title: "КриптоПро CSP (бессрочная лицензия СКЗИ КриптоПро CSP 5.0), на одном рабочем месте",
    image: "/softline-store/popular-2410137.png",
    price: 3700,
    url: "https://store.softline.ru/kripto-pro/-410137/",
  },
  {
    id: "popular-2197958",
    brand: "КриптоПро",
    title: "КриптоПро CSP 5.0: серверная лицензия СКЗИ",
    image: "/softline-store/popular-2197958.png",
    price: 70000,
    url: "https://store.softline.ru/kripto-pro/-197958/",
  },
  {
    id: "popular-2473582",
    brand: "Программы и лицензии Microsoft Corporation",
    title: "Microsoft Office для дома и бизнеса 2024, Электронная лицензия на 1 ПК, бессрочная",
    image: "/softline-store/popular-2473582.jpg",
    price: 36574,
    url: "https://store.softline.ru/microsoft/ep2-06605-473582/",
    sku: "EP2-06605",
  },
  {
    id: "popular-2248970",
    brand: "«Лаборатория Касперского»",
    title: "Kaspersky Small Office Security новая лицензия на 5 ПК/мобильных устройств/пользователей на 1 год",
    image: "/softline-store/popular-2248970.png",
    price: 6739.2,
    url: "https://store.softline.ru/kaspersky/kl4141rcefs-248970/",
    sku: "KL4141RCEFS",
  },
  {
    id: "popular-2473568",
    brand: "Программы и лицензии Microsoft Corporation",
    title: "Microsoft Office для дома 2024, Электронная лицензия на 1 ПК, бессрочная",
    image: "/softline-store/popular-2473568.jpg",
    price: 16543,
    url: "https://store.softline.ru/microsoft/ep2-06797-473568/",
    sku: "EP2-06797",
  },
  {
    id: "popular-24950",
    brand: "«Лаборатория Касперского»",
    title: "Kaspersky Endpoint Security для бизнеса Стандартный продление лицензии на 1 год. Количество узлов",
    image: "/softline-store/popular-24950.jpg",
    price: 2322,
    url: "https://store.softline.ru/kaspersky/kl4863lakfr-4950/",
    sku: "KL4863RAKFR",
  },
  {
    id: "popular-25314",
    brand: "Доктор Веб",
    title: "Доктор Веб Комплект Dr.Web «Малый бизнес», Лицензия на 1 год коробочная версия",
    image: "/softline-store/popular-25314.jpg",
    price: 11330,
    url: "https://store.softline.ru/drweb/bbz-c-12m-5-a3-5314/",
    sku: "BBZ-*C-12M-5-A3",
  },
];

const storeSections = [
  {
    id: "categories",
    title: "Компьютеры и комплектующие",
    links: [
      { href: "https://store.softline.ru/computers/", title: "Компьютеры" },
      { href: "https://store.softline.ru/peripherals/", title: "Компьютерная периферия" },
      { href: "https://store.softline.ru/parts/", title: "Комплектующие 3D" },
      { href: "https://store.softline.ru/3d-oborudovanie/", title: "Оборудование" },
      { href: "https://store.softline.ru/accessories_comp/", title: "Аксессуары для компьютерной техники" },
    ],
  },
  {
    id: "office",
    title: "Офисные программы",
    links: [
      { href: "https://store.softline.ru/ofisnie-programmi/ms-office/", title: "Офисные пакеты Microsoft Office" },
      { href: "https://store.softline.ru/ofisnie-programmi/rossiyskie-ofisnoe-po-importozameshchenie/", title: "Российское офисное ПО" },
    ],
  },
  {
    id: "kaspersky",
    title: "«Лаборатория Касперского»",
    links: [
      { href: "https://store.softline.ru/kaspersky/", title: "Все продукты" },
      { href: "https://store.softline.ru/kaspersky/?filter[category][]=650", title: "Антивирусы для организаций" },
      { href: "https://store.softline.ru/kaspersky/?filter[category][]=3775", title: "Антивирусы для дома" },
      { href: "https://store.softline.ru/kaspersky/?filter[category][]=687", title: "Защита информации" },
      { href: "https://store.softline.ru/kaspersky/?filter[category][]=3656", title: "Сеть и интернет" },
    ],
  },
];

const storeBrands = [
  ["Microsoft", "microsoft", "microsoft.png"],
  ["Лаборатория Касперского", "kaspersky", "kaspersky.png"],
  ["КриптоПро", "kripto-pro", "cryptopro.png"],
  ["Группа Астра", "astralinux", "astra.png"],
  ["Adobe", "adobe", "adobe.png"],
  ["Apple", "apple", "apple.png"],
  ["Lenovo", "lenovo", "lenovo.png"],
  ["Samsung", "samsung", "samsung.png"],
];

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
    const existing = window.sessionStorage.getItem(storageKey);
    if (existing) return existing;
    const created = window.crypto.randomUUID();
    window.sessionStorage.setItem(storageKey, created);
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
        return location ? location + ": " + message : message;
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

function formatStorePrice(price: number): string {
  return new Intl.NumberFormat("ru-RU", {
    style: "currency",
    currency: "RUB",
    maximumFractionDigits: 2,
  }).format(price);
}

const catalogDetailLabels = ["Срок действия", "Тип лицензии", "Минимальная покупка", "Способ доставки", "Тип поставки"];

function catalogDetail(value: unknown): string {
  const values = Array.isArray(value) ? value : [value];
  return values
    .filter((item): item is string | number => typeof item === "string" || typeof item === "number")
    .map((item) => String(item).trim())
    .filter(Boolean)
    .join(", ");
}

function findProductImage(sku: Product["sku"]): string | undefined {
  const normalized = sku?.trim().toUpperCase();
  if (!normalized) return undefined;
  return storeProducts.find((product) => product.sku?.toUpperCase() === normalized)?.image;
}

function StoreIcon({ name }: { name: string }) {
  return (
    <svg className="storeIcon" aria-hidden="true">
      <use href={"/softline-store/store-icons.svg#" + name} />
    </svg>
  );
}

function SparklesIcon() {
  return (
    <svg className="sparklesIcon" viewBox="0 0 24 24" fill="none" aria-hidden="true">
      <path d="m12 2 1.9 6.1L20 10l-6.1 1.9L12 18l-1.9-6.1L4 10l6.1-1.9L12 2Z" />
      <path d="m19 15 .9 2.1L22 18l-2.1.9L19 21l-.9-2.1L16 18l2.1-.9L19 15Z" />
    </svg>
  );
}

function StockLabel({ inStock }: { inStock: boolean | null | undefined }) {
  if (inStock === true) return <span className="stock stockAvailable">В наличии</span>;
  if (inStock === false) return <span className="stock stockUnavailable">Нет в наличии</span>;
  return <span className="stock stockUnknown">Наличие неизвестно</span>;
}

function ProductCard({
  product,
  config,
  recommendation,
}: {
  product: Product;
  config: UIConfig;
  recommendation?: Recommendation;
}) {
  const link = safeHttpsUrl(product.url);
  const image = findProductImage(product.sku);
  const descriptionFields = product.attributes?.description_fields;
  const attributes = catalogDetailLabels
    .map((label) => {
      const detail = isRecord(descriptionFields) ? descriptionFields[label] : undefined;
      return [label, catalogDetail(detail) || catalogDetail(product.attributes?.[label])] as const;
    })
    .filter(([, value]) => value);
  const description = attributes.length === 0 ? product.description?.trim() : "";

  return (
    <article className="searchProductCard">
      {image && <img className="searchProductImage" src={image} alt="" loading="lazy" />}
      <p className="productByline">{product.brand || product.category}</p>
      <h3>{product.title}</h3>
      {product.sku && <p className="productMeta">Артикул: {product.sku}</p>}
      {(attributes.length > 0 || description || product.license_units != null) && (
        <details className="productDetails">
          <summary>Подробнее о товаре</summary>
          {description && <p>{description}</p>}
          <dl>
            {attributes.map(([label, value]) => (
              <div key={label}><dt>{label}</dt><dd>{value}</dd></div>
            ))}
            {product.license_units != null && (
              <div><dt>Единиц лицензии</dt><dd>{product.license_units}</dd></div>
            )}
          </dl>
        </details>
      )}
      {recommendation && (
        <div className="productRecommendation">
          <strong>{config.relation_labels[recommendation.type] ?? "Связанный товар"}</strong>
          <RecommendationDetails recommendation={recommendation} />
        </div>
      )}
      <div className="productPurchase">
        <p className="productPrice">{formatPrice(product.price_kopecks)}</p>
        {link && <a className="productLink" href={link} target="_blank" rel="noopener noreferrer">Открыть товар</a>}
        <StockLabel inStock={product.in_stock} />
      </div>
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
        {item.catalog_field && " · поле: " + item.catalog_field}
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
          <ul>
            {recommendation.evidence.map((item, index) => (
              <EvidenceItem key={item.checked_at + "-" + index} item={item} />
            ))}
          </ul>
        </details>
      )}
    </div>
  );
}

function ResultPanel({ response, config }: { response: SearchResponse; config: UIConfig }) {
  const statusLabel = config.status_labels[response.status] ?? "Результат поиска";
  return (
    <section className={"searchResultPanel status-" + response.status} aria-live="polite" aria-labelledby="result-heading">
      <div className="resultHeader">
        <div>
          <p className="eyebrow">Умный поиск Softline</p>
          <h2 id="result-heading">{statusLabel}</h2>
        </div>
        {response.status === "partial" && <span className="statusMark">Частичный результат</span>}
      </div>
      {response.status === "partial" && (
        <p className="statusNote">Подбор неполный. Показаны позиции, которые вернул поисковый сервис.</p>
      )}
      {response.status === "clarification_needed" && response.clarifying_question && (
        <div className="clarificationBox">
          <span className="clarificationIcon" aria-hidden="true">?</span>
          <div>
            <h3>Уточните запрос</h3>
            <p>{response.clarifying_question}</p>
            <span>Ответьте в строке поиска — предыдущий запрос будет передан как контекст.</span>
          </div>
        </div>
      )}
      {response.status === "empty" && response.products.length === 0 && response.recommendations.length === 0 && (
        <p className="emptyMessage">Подходящих товаров не найдено. Попробуйте описать задачу иначе.</p>
      )}
      {response.status === "unavailable" && (
        <div className="notice noticeError">
          <strong>Поисковая зависимость недоступна.</strong>
          <p>Попробуйте повторить запрос чуть позже.</p>
        </div>
      )}
      {response.warnings.length > 0 && (
        <details className="warningsBlock">
          <summary>Предупреждения ({response.warnings.length})</summary>
          <ul>{response.warnings.map((warning, index) => <li key={index + "-" + warning}>{warning}</li>)}</ul>
        </details>
      )}
      {response.products.length > 0 && (
        <div className="resultGroup">
          <div className="groupHeading"><h3>Товары</h3><span>{response.products.length}</span></div>
          <div className="searchProductGrid">
            {response.products.map((product) => (
              <ProductCard key={product.product_id} product={product} config={config} />
            ))}
          </div>
        </div>
      )}
      {response.recommendations.length > 0 && (
        <div className="resultGroup">
          <div className="groupHeading"><h3>Рекомендации</h3><span>{response.recommendations.length}</span></div>
          <div className="searchProductGrid">
            {response.recommendations.map((recommendation) => (
              <ProductCard
                key={recommendation.anchor_id + "-" + recommendation.product.product_id}
                product={recommendation.product}
                recommendation={recommendation}
                config={config}
              />
            ))}
          </div>
        </div>
      )}
      {response.reason_codes.length > 0 && (
        <details className="warningsBlock">
          <summary>Причины статуса</summary>
          <ul>{response.reason_codes.map((code) => <li key={code}>{code}</li>)}</ul>
        </details>
      )}
    </section>
  );
}

function StaticProductCard({
  product,
  onAdd,
  isFavorite,
  onToggleFavorite,
}: {
  product: StoreProduct;
  onAdd: () => void;
  isFavorite: boolean;
  onToggleFavorite: () => void;
}) {
  return (
    <article className="storeProductCard">
      <div className="storeProductImage">
        <img src={product.image} alt={product.title} loading="lazy" />
        <button
          className={isFavorite ? "favoriteButton favoriteButtonActive" : "favoriteButton"}
          type="button"
          aria-pressed={isFavorite}
          aria-label={(isFavorite ? "Убрать из избранного: " : "Добавить в избранное: ") + product.title}
          onClick={onToggleFavorite}
        >{isFavorite ? "♥" : <StoreIcon name="heart" />}</button>
      </div>
      <div className="storeProductCopy">
        <p className="storeProductBrand">{product.brand}</p>
        <h3><a href={product.url}>{product.title}</a></h3>
        {product.reviews && (
          <p className="storeProductReviews">
            {product.reviews.rating} <span aria-hidden="true">★</span> <small>{product.reviews.label}</small>
          </p>
        )}
        <p className="storeProductPrice">{formatStorePrice(product.price)}</p>
        <button className="addToCartButton" type="button" onClick={onAdd}>
          В корзину <span aria-hidden="true">→</span>
        </button>
      </div>
    </article>
  );
}

function getResponseText(response: SearchResponse): string {
  if (response.status === "clarification_needed") {
    return response.clarifying_question ?? "Помогу уточнить запрос. Расскажите немного подробнее.";
  }
  if (response.status === "unavailable") {
    return "Сейчас не удалось связаться с поисковым сервисом. Попробуйте ещё раз немного позже.";
  }
  if (response.status === "empty") {
    return "Пока не нашёл подходящие позиции. Укажите модель купленного товара и уточните, для какой задачи подбираете дополнение.";
  }
  if (response.recommendations.length > 0) {
    return "Подготовил рекомендации по вашему запросу. Ниже — подходящие товары и пояснения.";
  }
  return "Нашёл варианты в каталоге. Посмотрите товары ниже и уточните требования, если нужен более точный подбор.";
}

function ChatProducts({ response }: { response: SearchResponse }) {
  const items = [
    ...response.recommendations.map((recommendation) => ({
      product: recommendation.product,
      note: recommendation.claim,
      evidence: recommendation.evidence,
      key: recommendation.product.product_id,
    })),
    ...response.products.map((product) => ({
      product,
      note: "",
      evidence: [],
      key: product.product_id,
    })),
  ].filter((item, index, entries) => entries.findIndex((entry) => entry.key === item.key) === index).slice(0, 3);

  if (items.length === 0) return null;
  return (
    <div className="agentProducts">
      {items.map(({ product, note, evidence, key }) => {
        const link = safeHttpsUrl(product.url);
        const image = findProductImage(product.sku);
        return (
          <article className="agentProduct" key={key}>
            {image && <img src={image} alt="" loading="lazy" />}
            <div className="agentProductCopy">
              <p className="productByline">{product.brand || product.category}</p>
              <h4>{product.title}</h4>
              <p className="productPrice">{formatPrice(product.price_kopecks)}</p>
              <StockLabel inStock={product.in_stock} />
              {note && <span className="agentProductNote">{note}</span>}
              {evidence.length > 0 && (
                <details className="agentEvidence">
                  <summary>Основание рекомендации</summary>
                  <ul>
                    {evidence.map((item, index) => (
                      <EvidenceItem key={item.checked_at + "-" + index} item={item} />
                    ))}
                  </ul>
                </details>
              )}
              {link && <a className="productLink" href={link} target="_blank" rel="noopener noreferrer">Открыть товар</a>}
            </div>
          </article>
        );
      })}
    </div>
  );
}

function ChatMessageView({ message }: { message: ChatMessage }) {
  return (
    <div className={"agentMessage agentMessage-" + message.role}>
      {message.role === "assistant" && <span className="agentAvatar" aria-hidden="true"><SparklesIcon /></span>}
      <div className="agentBubble">
        <p>{message.text}</p>
        {message.response && (message.response.status === "ok" || message.response.status === "partial") && (
          <ChatProducts response={message.response} />
        )}
      </div>
    </div>
  );
}

export default function SearchExperience({ config }: SearchExperienceProps) {
  const [query, setQuery] = useState("");
  const [smartEnabled, setSmartEnabled] = useState(false);
  const [catalogQuery, setCatalogQuery] = useState("");
  const [sessionId, setSessionId] = useState("");
  const [readiness, setReadiness] = useState<ReadinessState>("checking");
  const [readinessMessage, setReadinessMessage] = useState("");
  const [retryReadiness, setRetryReadiness] = useState(0);
  const [loading, setLoading] = useState(false);
  const [response, setResponse] = useState<SearchResponse | null>(null);
  const [searchError, setSearchError] = useState("");
  const [previousContext, setPreviousContext] = useState<string | null>(null);
  const [activeSlide, setActiveSlide] = useState(0);
  const productRail = useRef<HTMLDivElement>(null);
  const [cartCount, setCartCount] = useState(0);
  const [favoriteIds, setFavoriteIds] = useState<string[]>([]);
  const [agentOpen, setAgentOpen] = useState(false);
  const [agentInput, setAgentInput] = useState("");
  const [agentLoading, setAgentLoading] = useState(false);
  const [agentContext, setAgentContext] = useState<string | null>(null);
  const agentInputRef = useRef<HTMLInputElement>(null);
  const conversationRef = useRef<HTMLDivElement>(null);
  const [agentMessages, setAgentMessages] = useState<ChatMessage[]>([
    {
      id: "welcome",
      role: "assistant",
      text: "Здравствуйте! Расскажите, что у вас уже есть и для какой задачи подбираете решение. Я предложу подходящие товары из каталога.",
    },
  ]);

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
        setReadinessMessage(errorMessage(data) ?? "Сервис поиска вернул HTTP " + result.status + ".");
      })
      .catch(() => {
        if (!active) return;
        setReadiness("unavailable");
        setReadinessMessage("Не удалось подключиться к поисковому сервису.");
      });
    return () => { active = false; };
  }, [retryReadiness]);

  useEffect(() => {
    if (!response && !catalogQuery) return;
    document.getElementById(response ? "smart-search-results" : "popular")?.scrollIntoView({
      behavior: window.matchMedia("(prefers-reduced-motion: reduce)").matches ? "auto" : "smooth",
      block: "start",
    });
  }, [response, catalogQuery]);

  useEffect(() => {
    if (agentOpen && conversationRef.current) {
      conversationRef.current.scrollTop = conversationRef.current.scrollHeight;
    }
  }, [agentMessages, agentLoading, agentOpen]);

  const agentDockRef = useRef<HTMLElement>(null);

  useEffect(() => {
    const dock = agentDockRef.current;
    const viewport = window.visualViewport;
    if (!agentOpen || !dock || !viewport) return;

    function updateViewport() {
      if (!dock || !viewport) return;
      const bottomInset = Math.max(0, document.documentElement.clientHeight - viewport.height - viewport.offsetTop);
      dock.style.setProperty("--agent-viewport-height", viewport.height + "px");
      dock.style.setProperty("--agent-viewport-inset", bottomInset + "px");
    }

    updateViewport();
    viewport.addEventListener("resize", updateViewport);
    viewport.addEventListener("scroll", updateViewport);
    return () => {
      viewport.removeEventListener("resize", updateViewport);
      viewport.removeEventListener("scroll", updateViewport);
      dock.style.removeProperty("--agent-viewport-height");
      dock.style.removeProperty("--agent-viewport-inset");
    };
  }, [agentOpen]);

  async function callSearch(queryText: string, context: string | null): Promise<SearchResponse> {
    const activeSessionId = sessionId || getSessionId();
    if (!sessionId) setSessionId(activeSessionId);
    const result = await fetch("/api/search", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({
        query: queryText,
        request_id: window.crypto.randomUUID(),
        session_id: activeSessionId,
        previous_context: context,
      }),
    });
    const data: unknown = await result.json().catch(() => null);
    if (!isSearchResponse(data)) {
      const message = errorMessage(data);
      throw new Error("HTTP " + result.status + (message ? ": " + message : ". Поисковый сервис вернул некорректный ответ."));
    }
    if (!result.ok && data.status !== "unavailable") {
      const message = errorMessage(data);
      throw new Error("HTTP " + result.status + (message ? ": " + message : ". Поиск не выполнен."));
    }
    return data;
  }

  async function submitSearch(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const trimmedQuery = query.trim();
    if (!trimmedQuery || loading) return;
    setSearchError("");
    setResponse(null);
    if (!smartEnabled) {
      setCatalogQuery(trimmedQuery);
      setPreviousContext(null);
      return;
    }
    setCatalogQuery("");
    if (readiness !== "ready") setRetryReadiness((attempt) => attempt + 1);

    setLoading(true);
    try {
      const result = await callSearch(trimmedQuery, previousContext);
      setResponse(result);
      setPreviousContext(result.status === "clarification_needed" ? trimmedQuery : null);
    } catch (error) {
      setSearchError(error instanceof Error ? error.message : "Не удалось выполнить поиск. Попробуйте ещё раз.");
    } finally {
      setLoading(false);
    }
  }

  async function submitAgentMessage(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const messageText = agentInput.trim();
    if (!messageText || agentLoading) return;
    setAgentInput("");
    setAgentOpen(true);
    setAgentMessages((messages) => [...messages, {
      id: window.crypto.randomUUID(),
      role: "user",
      text: messageText,
    }]);
    setAgentLoading(true);
    try {
      const result = await callSearch(messageText, agentContext);
      setAgentMessages((messages) => [...messages, {
        id: window.crypto.randomUUID(),
        role: "assistant",
        text: getResponseText(result),
        response: result,
      }]);
      if (result.status !== "unavailable") {
        setAgentContext([agentContext, messageText].filter(Boolean).join("\n").slice(-1000));
      }
    } catch (error) {
      setAgentMessages((messages) => [...messages, {
        id: window.crypto.randomUUID(),
        role: "assistant",
        text: error instanceof Error ? error.message : "Не получилось выполнить подбор. Попробуйте ещё раз.",
      }]);
    } finally {
      setAgentLoading(false);
    }
  }

  function choosePrompt(prompt: string) {
    setAgentInput(prompt);
    agentInputRef.current?.focus();
  }

  const visibleSlides = [bannerSlides[activeSlide], bannerSlides[(activeSlide + 1) % bannerSlides.length]];
  const catalogMatches = catalogQuery ? catalogQuery.toLocaleLowerCase("ru-RU").split(/\s+/) : [];
  const displayedProducts = storeProducts.filter((product) => {
    const searchable = [product.title, product.brand, product.sku].join(" ").toLocaleLowerCase("ru-RU");
    return catalogMatches.every((word) => searchable.includes(word));
  });

  return (
    <main className="storePage" id="top">
      <div className="topUtility">
        <div className="storeContainer utilityInner">
          <a className="storeLogo" href="#top" aria-label="Softline Store — главная">
            <img src="/softline-store/store-header-logo.svg" width="188" height="32" alt="Софтлайн — Цифровой актив" />
          </a>
          <span className="storeCity"><StoreIcon name="map-marker" /> Ростов-на-Дону</span>
          <div className="utilityLinks">
            <a href="https://softline.ru/">Softline.ru</a>
            <a href="https://store.softline.ru/writeorder/">Запрос цены</a>
            <a href="https://store.softline.ru/tenders/">Тендеры</a>
            <a href="https://store.softline.ru/b2b/">Для юр.лиц</a>
            <a href="https://store.softline.ru/delivery_info/">Оплата и доставка</a>
            <a href="https://store.softline.ru/contacts/">Контакты</a>
          </div>
        </div>
      </div>

      <header className="storeHeader">
        <div className="storeContainer headerMain">
          <a className="catalogLink" href="#categories"><StoreIcon name="menu" /> Каталог</a>
          <form className="headerSearch" onSubmit={submitSearch}>
            <label htmlFor="store-search" className="visuallyHidden">Поиск товаров и услуг</label>
            <input
              id="store-search"
              value={query}
              maxLength={1000}
              placeholder={smartEnabled ? "Опишите, что хотите подобрать" : "Продукт, производитель или артикул (SKU)"}
              onChange={(event) => setQuery(event.target.value)}
              required
            />
            <label className="smartSearchToggle">
              <input
                type="checkbox"
                checked={smartEnabled}
                disabled={loading}
                onChange={(event) => {
                  setSmartEnabled(event.target.checked);
                  setPreviousContext(null);
                }}
              />
              Умный поиск
            </label>
            <button className="plainSearchButton" type="submit" disabled={loading || !query.trim()} aria-label={loading ? "Выполняется поиск" : "Найти товар"}>
              {loading ? <span className="buttonSpinner" /> : <StoreIcon name="store-search" />}
            </button>
          </form>
          <div className="storeContacts">
            <a href="tel:88002000860">8 (800) 200-08-60</a>
            <a href="mailto:sales.r@softline.com">sales.r@softline.com</a>
          </div>
          <div className="headerActions">
            <a className="headerAction" href="https://store.softline.ru/compare/"><StoreIcon name="chart" /><small>Сравнение</small></a>
            <a className="headerAction" href="https://store.softline.ru/personal/"><StoreIcon name="store-user" /><small>Войти</small></a>
            <a className="headerAction" href="#popular"><StoreIcon name="heart" /><small>Избранное</small></a>
            <a className="headerAction" href="#popular" aria-label={"Корзина, товаров: " + cartCount}>
              <StoreIcon name="store-cart" /><small>Корзина {cartCount > 0 && <b aria-live="polite">{cartCount}</b>}</small>
            </a>
          </div>
        </div>
        <nav className="primaryNav" aria-label="Главная навигация">
          <div className="storeContainer navInner">
            <a href="#promotions"><StoreIcon name="fire" /> Акции</a>
            <a href="https://store.softline.ru/vendors/">Производители</a>
            <a href="https://store.softline.ru/ofisnie-programmi/">Офисные программы</a>
            <a href="https://store.softline.ru/antivirusi-bezopasnost/antivirusi-organizatsii/">Антивирусы для организаций</a>
            <a href="https://store.softline.ru/antivirusi-bezopasnost/bezopasnost-zaschita-informatsii/">Защита информации</a>
            <a href="https://store.softline.ru/computers/">Компьютеры</a>
            <a href="https://store.softline.ru/inferit-tech/">Инферит</a>
            <a href="https://store.softline.ru/blog/">Блог</a>
            <a href="https://store.softline.ru/ii/">ИИ</a>
          </div>
        </nav>
      </header>

      <div className="storeContainer">
        {searchError && (
          <section className="searchError noticeError" role="alert">
            <strong>Не удалось выполнить поиск</strong>
            <p>{searchError}</p>
            <span>Техническая ошибка не означает, что в каталоге нет товаров.</span>
          </section>
        )}

        {response && (
          <div id="smart-search-results">
            <ResultPanel response={response} config={config} />
          </div>
        )}

        <section className="storeHero" id="promotions" aria-label="Акции Softline">
          <div className="heroSlides">
            {visibleSlides.map((slide) => (
              <div className="heroSlide" key={slide.image}>
                <img src={slide.image} alt={slide.alt} />
                <span className="heroAd">Реклама</span>
              </div>
            ))}
          </div>
          <button
            type="button"
            className="sliderArrow sliderArrowPrev"
            aria-label="Предыдущий баннер"
            onClick={() => setActiveSlide((activeSlide + bannerSlides.length - 1) % bannerSlides.length)}
          >‹</button>
          <button
            type="button"
            className="sliderArrow sliderArrowNext"
            aria-label="Следующий баннер"
            onClick={() => setActiveSlide((activeSlide + 1) % bannerSlides.length)}
          >›</button>
        </section>

        <section className="popularSection" id="popular">
          <div className="sectionHeading">
            <h2>{catalogQuery ? "Результаты поиска" : "Популярные товары"}</h2>
            {catalogQuery ? (
              <button type="button" onClick={() => { setCatalogQuery(""); setQuery(""); }}>Сбросить поиск</button>
            ) : (
              <div className="productRailControls" aria-label="Прокрутка популярных товаров">
                <button type="button" aria-label="Предыдущие товары" onClick={() => productRail.current?.scrollBy({ left: -productRail.current.clientWidth })}>‹</button>
                <button type="button" aria-label="Следующие товары" onClick={() => productRail.current?.scrollBy({ left: productRail.current.clientWidth })}>›</button>
              </div>
            )}
          </div>
          {catalogQuery && displayedProducts.length === 0 && <p role="status">По запросу «{catalogQuery}» ничего не найдено в подборке витрины. Для поиска по каталогу включите «Умный поиск».</p>}
          <div className="storeProductGrid" key={catalogQuery} ref={productRail}>
            {displayedProducts.map((product) => (
              <StaticProductCard
                key={product.id}
                product={product}
                isFavorite={favoriteIds.includes(product.id)}
                onToggleFavorite={() => setFavoriteIds((ids) => (
                  ids.includes(product.id) ? ids.filter((id) => id !== product.id) : [...ids, product.id]
                ))}
                onAdd={() => setCartCount((count) => count + 1)}
              />
            ))}
          </div>
        </section>

        {storeSections.map((section) => (
          <section className="storeTopic" id={section.id} key={section.id}>
            <h2>{section.title}</h2>
            <ul>
              {section.links.map((link) => <li key={link.href}><a href={link.href}>{link.title}</a></li>)}
            </ul>
          </section>
        ))}

        <section className="storeTopic" aria-labelledby="brands-heading">
          <h2 id="brands-heading">Производители</h2>
          <ul className="brandRail">
            {storeBrands.map(([name, slug, image]) => (
              <li key={slug}>
                <a href={"https://store.softline.ru/" + slug + "/"}>
                  <img src={"/softline-store/brand-" + image} alt={name} loading="lazy" />
                </a>
              </li>
            ))}
          </ul>
        </section>

        <section className="storeTopic aboutCopy" id="about">
          <h2>О магазине Softline</h2>
          <p>Интернет-магазин Softline Store предлагает большой ассортимент лицензионного программного обеспечения и электроники от ведущих производителей. Здесь вы найдете все необходимое: от популярных офисных программ до систем безопасности и современной компьютерной техники.</p>
          <p>У нас вы можете выгодно купить продукцию известных брендов, включая Kaspersky, Microsoft, КриптоПро, Астра, Adobe, Инферит. Только оригинальные товары по ценам от производителя!</p>
          <p>Мы работаем как с бизнесом, так и с частными клиентами, которые ценят надежность и качество программных продуктов. Объясним и поможем — готовы ответить на любые вопросы до и после покупки! Присоединяйтесь к числу наших довольных клиентов! Мы уверены, что у нас вы найдете именно то, что искали.</p>
          <div className="aboutStats">
            <div><strong>Быстрая доставка</strong><span>Доставка электронных лицензий от 5 минут на почту. Курьерская доставка по всей России.</span></div>
            <div><strong>Счет на оплату без ожиданий</strong><span>Сформируйте счет прямо в корзине за пару кликов или попросите об этом менеджера.</span></div>
            <div><strong>Платите как удобно</strong><span>Банковский платеж, карта, SberPay, СБП или цифровой рубль.</span></div>
            <div><strong>Документы под ключ</strong><span>Закрывающие документы, ЭДО, возмещение НДС.</span></div>
          </div>
        </section>

      </div>

      <footer className="storeFooter" id="footer">
        <div className="storeContainer">
          <div className="footerMain">
            <div className="footerContacts">
              <a href="mailto:sales.r@softline.com">sales.r@softline.com</a>
              <a href="tel:88002000860">8 (800) 200-08-60</a>
              <address>ПАО «Софтлайн».<br />Юр. адрес: 119021, г. Москва, ул. Льва Толстого, дом 5, строение 1, этаж 3, помещение 1, комн. № 2, 2а (А-311)</address>
            </div>
            <nav className="footerLinks" aria-label="Информация о магазине">
            <div>
            <a href="https://store.softline.ru/catalog/">Каталог</a>
            <a href="https://store.softline.ru/pricelists/">Прайс-листы</a>
            <a href="https://store.softline.ru/vendors/">Производители</a>
            <a href="https://store.softline.ru/specials/">Специальные предложения</a>
            <a href="https://softline.ru/solutions/support/basic-support">Техподдержка</a>
            </div>
            <div>
            <a href="https://store.softline.ru/sale/">Распродажа</a>
            <a href="https://softline.ru/events">Семинары и вебинары</a>
            <a href="https://files.softline.com/fileStorage/partnerskaia-programma-dlia-vendorov.pdf">Стать партнером</a>
            <a href="https://vendors.softline.ru/">Вход в ЛК партнера</a>
            <a href="https://store.softline.ru/about/">О компании</a>
            </div>
            <div>
            <a href="https://softline.ru/about/vacancies">Вакансии</a>
            <a href="https://store.softline.ru/delivery_info/">Помощь</a>
            <a href="https://softline.com/">Группа компаний Softline</a>
            </div>
            </nav>
            <nav className="footerSocial" aria-label="Социальные сети">
              <a href="https://t.me/Softline">Telegram</a>
              <a href="https://rutube.ru/u/softline/">Rutube</a>
              <a href="https://vk.com/softlinecompany">ВКонтакте</a>
              <a href="https://max.ru/Softline">MAX</a>
            </nav>
          </div>
          <div className="footerLegal">
            <span className="storeCity"><StoreIcon name="map-marker" /> Ростов-на-Дону</span>
            <span>© 1993—2026 Softline</span>
            <a href="https://softline.ru/copy">Условия использования</a>
            <a href="https://store.softline.ru/privacy-policy/">Политика конфиденциальности</a>
            <a href="https://store.softline.ru/sale-rules/">Правила продажи</a>
            <strong>14+</strong>
          </div>
          <p className="footerNotice">На информационном ресурсе store.softline.ru применяются <a href="https://store.softline.ru/recomendations_rules/">рекомендательные технологии</a> (информационные технологии предоставления информации на основе сбора, систематизации и анализа сведений, относящихся к предпочтениям пользователей сети «Интернет», находящихся на территории Российской Федерации)</p>
        </div>
      </footer>

      <a className="feedbackTab" href="https://store.softline.ru/contacts/">Отзыв о сайте</a>

      <aside ref={agentDockRef} className="agentDock" aria-label="AI-агент по подбору товаров">
        {agentOpen && (
          <section className="agentWindow" aria-labelledby="agent-title">
            <header className="agentHeader">
              <div className="agentHeaderAvatar" aria-hidden="true"><SparklesIcon /></div>
              <div className="agentHeaderText">
                <strong id="agent-title">AI-помощник Softline</strong>
                <span className={"service-" + readiness} title={readinessMessage}><i /> {readiness === "ready" ? "Подбор по каталогу" : readiness === "checking" ? "Проверяем подключение" : "Сервис временно недоступен"}</span>
              </div>
              <button className="agentClose" type="button" aria-label="Свернуть помощника" onClick={() => setAgentOpen(false)}>−</button>
            </header>
            <div className="agentConversation" role="log" aria-live="polite" aria-relevant="additions text" ref={conversationRef}>
              {agentMessages.map((message) => <ChatMessageView key={message.id} message={message} />)}
              {agentLoading && (
                <div className="agentMessage agentMessage-assistant">
                  <span className="agentAvatar" aria-hidden="true"><SparklesIcon /></span>
                  <div className="agentTyping"><i /><i /><i /><span className="visuallyHidden">Ищу товары</span></div>
                </div>
              )}
            </div>
            {agentMessages.length === 1 && (
              <div className="agentSuggestions">
                <button type="button" onClick={() => choosePrompt("У меня есть старый ноутбук, помоги подобрать замену")}>Подобрать замену</button>
                <button type="button" onClick={() => choosePrompt("Купил ноутбук, подберите мышь и клавиатуру")}>Что докупить?</button>
              </div>
            )}
            <form className="agentComposer" onSubmit={submitAgentMessage}>
              <label className="visuallyHidden" htmlFor="agent-message">Сообщение AI-помощнику</label>
              <input
                id="agent-message"
                ref={agentInputRef}
                value={agentInput}
                maxLength={1000}
                placeholder="Напишите, что у вас есть…"
                onChange={(event) => setAgentInput(event.target.value)}
                disabled={agentLoading}
                required
              />
              <button type="submit" disabled={!agentInput.trim() || agentLoading} aria-label="Отправить сообщение">
                <span aria-hidden="true">↑</span>
              </button>
            </form>
            <p className="agentFootnote">Ответы основаны на данных поискового сервиса</p>
          </section>
        )}
        <button
          className={agentOpen ? "agentLauncher agentLauncherOpen" : "agentLauncher"}
          type="button"
          onClick={() => setAgentOpen((open) => !open)}
          aria-expanded={agentOpen}
          aria-label={agentOpen ? "Свернуть AI-помощника" : "Открыть AI-помощника"}
        >
          <span className="agentLauncherIcon"><SparklesIcon /></span>
          <span>{agentOpen ? "Свернуть" : "Помощь с подбором"}</span>
          {!agentOpen && <span className="agentLauncherBadge">AI</span>}
        </button>
      </aside>
    </main>
  );
}
