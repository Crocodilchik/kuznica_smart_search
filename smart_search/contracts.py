"""Shared Pydantic contracts for all four development roles."""

from datetime import UTC, datetime
from typing import Annotated, Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

Intent = Literal["exact_product", "product_search", "alternatives", "complements", "solution"]
SearchMode = Literal["sku", "bm25", "dense", "hybrid"]
SearchStatus = Literal["ok", "partial", "empty", "clarification_needed", "unavailable"]
RelationType = Literal["substitute", "cross_sell", "upsell"]
PositiveInt = Annotated[int, Field(strict=True, gt=0)]
Kopecks = Annotated[int, Field(strict=True, ge=0)]
NonEmptyText = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]
MAX_QUERY_LENGTH = 1000
QueryText = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=MAX_QUERY_LENGTH)
]


class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid")


class SearchRequest(Contract):
    query: QueryText
    request_id: NonEmptyText = Field(default_factory=lambda: str(uuid4()))
    session_id: str | None = None
    previous_context: str | None = None


class HardFilters(Contract):
    os: str | None = None
    country: str | None = None
    devices: PositiveInt | None = None
    max_total_price_kopecks: Kopecks | None = None
    budget_scope: Literal["primary", "bundle"] | None = None
    in_stock: bool | None = None


class Preferences(Contract):
    preferred_brand: str | None = None
    target_price_kopecks: Kopecks | None = None


class QuerySpec(Contract):
    raw_query: NonEmptyText
    normalized_query: NonEmptyText
    intent: Intent = "product_search"
    sku: str | None = None
    category: str | None = None
    hard_filters: HardFilters = Field(default_factory=HardFilters)
    preferences: Preferences = Field(default_factory=Preferences)
    clarifying_question: str | None = None
    warnings: list[str] = Field(default_factory=list)


class SearchParams(Contract):
    query_spec: QuerySpec
    mode: SearchMode = "hybrid"
    top_k: PositiveInt = 12


class Product(Contract):
    product_id: NonEmptyText
    sku: str | None = None
    title: NonEmptyText
    category: NonEmptyText
    brand: str | None = None
    price_kopecks: Kopecks | None = None
    license_units: PositiveInt | None = None
    in_stock: bool | None = None
    url: str | None = None
    attributes: dict[str, str | int | float | bool | list[str] | None] = Field(default_factory=dict)


class SearchHit(Contract):
    product: Product
    rank: PositiveInt
    score: float | None = None


class SearchResult(Contract):
    hits: list[SearchHit] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    available: bool = True


class RecommendationDraft(Contract):
    anchor_id: NonEmptyText
    product: Product
    type: RelationType
    claim: NonEmptyText


class RecommendationResult(Contract):
    recommendations: list[RecommendationDraft] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class Evidence(Contract):
    source_type: Literal["vendor", "catalog"]
    source_url: str | None = None
    catalog_field: str | None = None
    excerpt: NonEmptyText
    checked_at: datetime


class EvidenceResult(Contract):
    anchor_id: NonEmptyText
    product_id: NonEmptyText
    claim: NonEmptyText
    status: Literal["confirmed", "contradicted", "unknown"]
    evidence: list[Evidence] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class Recommendation(RecommendationDraft):
    evidence: list[Evidence] = Field(default_factory=list)


class ValidationResult(Contract):
    products: list[Product] = Field(default_factory=list)
    recommendations: list[Recommendation] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    reason_codes: list[str] = Field(default_factory=list)
    needs_retry: bool = False


class SearchResponse(Contract):
    request_id: NonEmptyText
    status: SearchStatus
    products: list[Product] = Field(default_factory=list)
    recommendations: list[Recommendation] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    reason_codes: list[str] = Field(default_factory=list)
    clarifying_question: str | None = None
    intent: Intent | None = None
    retry_count: Annotated[int, Field(ge=0, le=1)] = 0
    steps: list[str] = Field(default_factory=list)


class SearchEvent(Contract):
    request_id: NonEmptyText
    normalized_query: str
    intent: Intent | None = None
    status: SearchStatus
    result_count: Annotated[int, Field(ge=0)]
    latency_ms: Annotated[float, Field(ge=0)]
    retry_count: Annotated[int, Field(ge=0, le=1)] = 0
    reason_codes: list[str] = Field(default_factory=list)
    category: str | None = None
    recommendation_count: Annotated[int, Field(ge=0)] = 0
    occurred_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class AnalyticsReport(Contract):
    total_searches: int
    empty_rate: float
    p95_latency_ms: float | None
    status_counts: dict[str, int]
    problem_categories: dict[str, int]
    reason_counts: dict[str, int]
