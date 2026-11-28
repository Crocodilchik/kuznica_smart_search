"""Recommendation and catalog evidence for the integrated search pipeline."""

import os
from datetime import UTC, datetime

from pydantic import ValidationError

from smart_search.catalog_backend import category_names, search_rich
from smart_search.contracts import (
    Evidence,
    EvidenceResult,
    Product,
    QuerySpec,
    RecommendationDraft,
    RecommendationResult,
)
from smart_search.llm.exceptions import LLMError
from smart_search.model_client import complete_json
from src import contracts as native
from src.recommendation import recommend as plan_recommendations
from src.recommendation_graph import RecommendationGraph
from src.research import research as explicit_compatibility
from src.research import research_batch as check_quotes


def _planner() -> str:
    return os.environ.get("SMART_SEARCH_RECOMMENDATION_PLANNER", "llm").strip().casefold()


def _claim(relation: str, anchor: Product, product: Product) -> str:
    if relation == "cross_sell":
        return f"Совместим с товаром «{anchor.title}»."
    if _same_family(anchor, product):
        return f"Вариант из того же семейства «{anchor.family_name}»."
    return f"Выполняет ту же функцию, что и «{anchor.title}»."


def _same_family(anchor: Product, product: Product) -> bool:
    return bool(
        anchor.family_id
        and anchor.family_name
        and anchor.category_id
        and (anchor.family_id, anchor.family_name, anchor.category_id)
        == (product.family_id, product.family_name, product.category_id)
    )


def recommend(spec: QuerySpec, anchors: list[Product]) -> RecommendationResult:
    if not anchors:
        return RecommendationResult()
    planner = _planner()
    if planner not in {"llm", "graph"}:
        return RecommendationResult(
            warnings=["recommendation_unavailable", "invalid_recommendation_planner"]
        )
    query = native.QuerySpec.model_validate(
        spec.model_dump(
            exclude={
                "category_hints",
                "warnings",
                "search_mode",
                "score_threshold",
                "product_type_hint",
                "required_terms",
            }
        )
    )
    cards = [native.Product.model_validate(anchor.model_dump()) for anchor in anchors]
    warnings = []
    try:
        categories = category_names()
        if planner == "llm":
            try:
                result = plan_recommendations(
                    query,
                    cards,
                    search=search_rich,
                    categories=categories,
                    complete_json=complete_json,
                )
            except (LLMError, ValidationError):
                warnings.append("recommendation_planner_fallback")
                planner = "graph"
        if planner == "graph":
            result = plan_recommendations(
                query,
                cards,
                search=search_rich,
                categories=categories,
                graph=RecommendationGraph.load(),
            )
    except OSError:
        return RecommendationResult(warnings=[*warnings, "recommendation_unavailable"])
    by_id = {anchor.product_id: anchor for anchor in anchors}
    output = RecommendationResult.model_validate(result.model_dump())
    for draft in output.recommendations:
        draft.claim = _claim(draft.type, by_id[draft.anchor_id], draft.product)
    output.warnings = [*warnings, *output.warnings]
    if "recommendation_search_unavailable" in output.warnings:
        output.warnings.append("recommendation_unavailable")
    return output


def research(draft: RecommendationDraft) -> EvidenceResult:
    """A draft alone lacks the anchor snapshot needed to verify the pair."""
    return EvidenceResult(
        anchor_id=draft.anchor_id,
        product_id=draft.product.product_id,
        claim=draft.claim,
        status="unknown",
    )


def research_batch(
    spec: QuerySpec, anchors: list[Product], drafts: list[RecommendationDraft]
) -> list[EvidenceResult]:
    """Check explicit catalog facts first. Send remaining pairs in one structured call."""
    by_id = {anchor.product_id: anchor for anchor in anchors}
    results = [research(draft) for draft in drafts]
    pending = []
    for index, draft in enumerate(drafts):
        anchor = by_id.get(draft.anchor_id)
        if (
            anchor is None
            or anchor.product_id == draft.product.product_id
            or draft.type not in {"substitute", "cross_sell"}
            or draft.claim != _claim(draft.type, anchor, draft.product)
        ):
            continue
        if draft.type == "substitute" and _same_family(anchor, draft.product):
            checked_at = datetime.now(UTC)
            results[index].status = "confirmed"
            results[index].evidence = [
                Evidence(
                    source_type="catalog",
                    source_url=card.url,
                    catalog_field=f"{card.product_id}.family_name",
                    excerpt=card.family_name,
                    checked_at=checked_at,
                )
                for card in (anchor, draft.product)
            ]
            continue
        native_draft = native.RecommendationDraft.model_validate(draft.model_dump())
        checked = explicit_compatibility(
            native_draft, anchor=native.Product.model_validate(anchor.model_dump())
        )
        results[index] = EvidenceResult.model_validate(checked.model_dump())
        # Conflicting explicit fields yield unknown with evidence and must stay unknown.
        if not checked.evidence:
            pending.append((index, native_draft))
    if pending and _planner() == "llm":
        try:
            verified = check_quotes(
                [draft for _, draft in pending],
                anchors=[native.Product.model_validate(card.model_dump()) for card in anchors],
                query=spec.raw_query,
                complete_json=complete_json,
            )
        except (LLMError, ValidationError):
            for index, _ in pending:
                results[index].warnings.append("research_unavailable")
        else:
            for (index, _), checked in zip(pending, verified, strict=True):
                results[index] = EvidenceResult.model_validate(checked.model_dump())
    return results
