"""Typed function contract between Supervisor and its search backend."""

from collections.abc import Callable
from dataclasses import dataclass

from smart_search.contracts import (
    EvidenceResult,
    Product,
    QuerySpec,
    RecommendationDraft,
    RecommendationResult,
    SearchParams,
    SearchRequest,
    SearchResponse,
    SearchResult,
    ValidationResult,
)

SearchRunner = Callable[[SearchRequest], SearchResponse]


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
