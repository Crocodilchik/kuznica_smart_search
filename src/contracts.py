"""Search-side contract matching project-agent-roles.md; monetary amounts are kopecks."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid")


class HardFilters(Contract):
    os: str | None = None
    country: str | None = None
    devices: int | None = Field(default=None, gt=0, strict=True)
    max_total_price_kopecks: int | None = Field(default=None, ge=0, strict=True)
    budget_scope: Literal["primary", "bundle"] | None = None
    in_stock: bool | None = None

    @field_validator("country")
    @classmethod
    def normalize_country(cls, value: str | None) -> str | None:
        if value is not None:
            value = value.strip().upper()
            if len(value) != 2 or not value.isascii() or not value.isalpha():
                raise ValueError("country must be a two-letter ISO code")
        return value


class Preferences(Contract):
    preferred_brand: str | None = None
    target_price_kopecks: int | None = Field(default=None, ge=0, strict=True)


class QuerySpec(Contract):
    raw_query: str
    normalized_query: str
    intent: Literal["exact_product", "product_search", "alternatives", "complements", "solution"] = (
        "product_search"
    )
    sku: str | None = None
    category: str | None = None
    hard_filters: HardFilters = Field(default_factory=HardFilters)
    preferences: Preferences = Field(default_factory=Preferences)
    clarifying_question: str | None = None


class Product(Contract):
    product_id: str
    sku: str | None = None
    title: str
    category: str
    brand: str | None = None
    price_kopecks: int | None = Field(default=None, gt=0, strict=True)
    license_units: int | None = Field(default=None, gt=0, strict=True)
    in_stock: bool | None = None
    url: str | None = None
    attributes: dict[str, Any] = Field(default_factory=dict)
    category_id: str | None = None
    category_ids: list[str] = Field(default_factory=list)
    category_path: list[str] = Field(default_factory=list)
    family_id: str | None = None
    family_name: str | None = None
    description: str = ""
    search_text: str = ""
    os: list[str] = Field(default_factory=list)
    country: str | None = None
    license_unit_type: Literal["device", "user", "license"] | None = None
    min_purchase: int | None = Field(default=None, gt=0)
    license_type: str | None = None
    product_type: Literal["software", "hardware", "unknown"] = "unknown"
    source_currency: str | None = None
    source_date: str | None = None
    data_warnings: list[str] = Field(default_factory=list)


class SearchParams(Contract):
    query_spec: QuerySpec
    mode: Literal["sku", "bm25", "dense", "hybrid"] = "bm25"
    top_k: int = Field(default=10, ge=1, le=1000)


class SearchHit(Contract):
    product: Product
    rank: int = Field(ge=1)
    score: float | None = None
    branch_scores: dict[str, float] = Field(default_factory=dict)


class SearchResult(Contract):
    hits: list[SearchHit] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    status: Literal["ok", "empty", "partial", "unavailable"] = "ok"
    diagnostics: dict[str, Any] = Field(default_factory=dict)


class RecommendationDraft(Contract):
    anchor_id: str
    product: Product
    type: Literal["substitute", "cross_sell", "upsell"]
    claim: str


class RecommendationResult(Contract):
    recommendations: list[RecommendationDraft] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class Evidence(Contract):
    source_type: Literal["catalog", "vendor_document"]
    source_url: str | None = None
    catalog_field: str | None = None
    excerpt: str
    checked_at: datetime


class EvidenceResult(Contract):
    anchor_id: str
    product_id: str
    claim: str
    status: Literal["confirmed", "contradicted", "unknown"]
    evidence: list[Evidence] = Field(default_factory=list)
