"""Typed function contract between Supervisor and its search backend."""

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Protocol

from smart_search.contracts import (
    EvidenceResult,
    Product,
    ProductDetailsResult,
    ProductDetailsTarget,
    QuerySpec,
    RecommendationDraft,
    RecommendationResult,
    SearchParams,
    SearchRequest,
    SearchResponse,
    SearchResult,
    ShownProduct,
    ValidationResult,
)


class SearchRunner(Protocol):
    def __call__(
        self,
        request: SearchRequest,
        *,
        shown_products: Sequence[ShownProduct] = (),
        reference_unavailable: bool = False,
    ) -> SearchResponse: ...


@dataclass(frozen=True)
class Services:
    understand: Callable[[SearchRequest], QuerySpec]
    search: Callable[[SearchParams], SearchResult]
    recommend: Callable[[QuerySpec, list[Product]], RecommendationResult]
    research: Callable[[RecommendationDraft], EvidenceResult]
    validate: Callable[
        [QuerySpec, SearchResult, RecommendationResult, list[EvidenceResult]], ValidationResult
    ]
    research_batch: (
        Callable[[QuerySpec, list[Product], list[RecommendationDraft]], list[EvidenceResult]] | None
    ) = None
    check_ready: Callable[[], None] | None = None
    get_product_details: Callable[[ProductDetailsTarget], ProductDetailsResult] | None = None
