export type SearchStatus =
  | "ok"
  | "partial"
  | "empty"
  | "clarification_needed"
  | "unavailable";

export type SearchIntent =
  | "exact_product"
  | "product_search"
  | "alternatives"
  | "complements"
  | "solution";

export type Product = {
  product_id: string;
  title: string;
  category: string;
  sku?: string | null;
  brand?: string | null;
  price_kopecks?: number | null;
  in_stock?: boolean | null;
  url?: string | null;
  description?: string | null;
  attributes?: Record<string, unknown>;
  license_units?: number | null;
};

export type Evidence = {
  source_type: "vendor" | "catalog";
  source_url?: string | null;
  catalog_field?: string | null;
  excerpt: string;
  checked_at: string;
};

export type Recommendation = {
  anchor_id: string;
  product: Product;
  type: "substitute" | "cross_sell" | "upsell";
  claim: string;
  evidence: Evidence[];
};

export type SearchResponse = {
  request_id: string;
  status: SearchStatus;
  intent?: SearchIntent | null;
  products: Product[];
  recommendations: Recommendation[];
  warnings: string[];
  reason_codes: string[];
  clarifying_question?: string | null;
};

export type ApiError = {
  code?: string;
  message?: string;
  detail?: unknown;
};
