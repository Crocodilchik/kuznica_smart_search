"""LangGraph routing, validation and response assembly."""

import logging
import re
import sqlite3
from collections.abc import Callable
from time import perf_counter
from typing import TypedDict

from langgraph.graph import END, START, StateGraph

from smart_search.contracts import (
    EvidenceResult,
    HardFilters,
    QuerySpec,
    RecommendationResult,
    SearchEvent,
    SearchMode,
    SearchParams,
    SearchRequest,
    SearchResponse,
    SearchResult,
    SearchStatus,
    ValidationResult,
)
from smart_search.events import EventDeliveryError
from smart_search.tools import SearchRunner, Services

LOGGER = logging.getLogger(__name__)
SKU_QUERY = re.compile(r"(?:sku|артикул)(?:\s*[:№#]\s*|\s+)([\w./-]+)", re.IGNORECASE)
RELATION_BY_INTENT = {"alternatives": "substitute", "complements": "cross_sell"}
DEPENDENCY_FAILURES = (TimeoutError, ConnectionError)
RECOMMENDATION_FAILURES = {
    "recommendation_unavailable",
    "research_unavailable",
    "recommendation_validation_unavailable",
}


class AgentState(TypedDict, total=False):
    request: SearchRequest
    spec: QuerySpec
    search_result: SearchResult
    recommendation_result: RecommendationResult
    evidence: list[EvidenceResult]
    validation: ValidationResult
    retry_count: int
    warnings: list[str]
    steps: list[str]


def _step(state: AgentState, step: str) -> list[str]:
    return [*state.get("steps", []), step]


def _merge_search(previous: SearchResult, current: SearchResult) -> SearchResult:
    hits = {}
    for hit in [*previous.hits, *current.hits]:
        hits.setdefault(hit.product.product_id, hit)
    return SearchResult(
        hits=[hit.model_copy(update={"rank": rank}) for rank, hit in enumerate(hits.values(), 1)],
        warnings=list(dict.fromkeys([*previous.warnings, *current.warnings])),
        available=previous.available or current.available,
    )


def _result_count(spec: QuerySpec, validation: ValidationResult) -> int:
    relation = RELATION_BY_INTENT.get(spec.intent)
    if relation:
        return len(
            {
                item.product.product_id
                for item in validation.recommendations
                if item.type == relation
            }
        )
    return len({item.product_id for item in validation.products})


def _status(state: AgentState) -> SearchStatus:
    spec = state.get("spec")
    if spec is None or "validation_unavailable" in state.get("warnings", []):
        return "unavailable"
    if spec.clarifying_question:
        return "clarification_needed"
    if not state["search_result"].available:
        return "unavailable"
    validation = state["validation"]
    count = _result_count(spec, validation)
    recommendation_needed = spec.intent in RELATION_BY_INTENT
    recommendation_warnings = {
        *state.get("warnings", []),
        *state.get("recommendation_result", RecommendationResult()).warnings,
    }
    if not count and recommendation_needed and RECOMMENDATION_FAILURES & recommendation_warnings:
        return "unavailable"
    if not count:
        return "empty"
    if count < (1 if spec.sku else 4):
        return "partial"
    return "ok"


def build_search(
    services: Services,
    *,
    record_event: Callable[[SearchEvent], None] | None = None,
    search_mode: SearchMode = "hybrid",
) -> SearchRunner:
    """Compile the workflow once and return a synchronous request handler."""

    def understand(state: AgentState) -> AgentState:
        item = state["request"]
        sku_match = SKU_QUERY.fullmatch(item.query)
        if sku_match:
            sku = sku_match.group(1)
            spec = QuerySpec(
                raw_query=item.query, normalized_query=sku, intent="exact_product", sku=sku
            )
            return {"spec": spec, "steps": _step(state, "route:sku")}
        try:
            return {"spec": services.understand(item), "steps": _step(state, "understand")}
        except DEPENDENCY_FAILURES:
            return {"warnings": ["understanding_unavailable"], "steps": _step(state, "understand")}

    def search(state: AgentState) -> AgentState:
        spec = state["spec"]
        retry = state["retry_count"]
        # For a relation request this search locates the reference product.
        # Purchase filters still apply to the final products and recommendations.
        lookup = spec
        if spec.intent in RELATION_BY_INTENT:
            lookup = spec.model_copy(update={"hard_filters": HardFilters()})
        mode = "sku" if spec.sku else search_mode
        params = SearchParams(query_spec=lookup, mode=mode, top_k=30 if retry else 12)
        try:
            current = services.search(params)
        except DEPENDENCY_FAILURES:
            current = SearchResult(available=False, warnings=["search_unavailable"])
        if not current.hits and {"search_unavailable", "search_timeout"}.intersection(
            current.warnings
        ):
            current = current.model_copy(update={"available": False})
        previous = state.get("search_result")
        merged = _merge_search(previous if previous is not None else current, current)
        return {"search_result": merged, "steps": _step(state, f"search:{mode}")}

    def validate_primary(state: AgentState) -> AgentState:
        if not state["search_result"].available:
            return {"validation": ValidationResult()}
        try:
            result = services.validate(
                state["spec"], state["search_result"], RecommendationResult(), []
            )
            return {"validation": result, "steps": _step(state, "validate:products")}
        except DEPENDENCY_FAILURES:
            return {
                "validation": ValidationResult(),
                "warnings": [*state.get("warnings", []), "validation_unavailable"],
            }

    def recommend(state: AgentState) -> AgentState:
        spec = state["spec"]
        anchors = state["validation"].products
        if spec.intent in RELATION_BY_INTENT:
            anchors = [hit.product for hit in state["search_result"].hits[:1]]
        if not anchors or spec.sku:
            return {"recommendation_result": RecommendationResult()}
        try:
            result = services.recommend(spec, anchors[:3])
        except DEPENDENCY_FAILURES:
            result = RecommendationResult(warnings=["recommendation_unavailable"])
        return {"recommendation_result": result, "steps": _step(state, "recommend")}

    def research(state: AgentState) -> AgentState:
        evidence = []
        warnings = list(state.get("warnings", []))
        for draft in state["recommendation_result"].recommendations[:8]:
            try:
                evidence.append(services.research(draft))
            except DEPENDENCY_FAILURES:
                warnings.append("research_unavailable")
        return {"evidence": evidence, "warnings": warnings, "steps": _step(state, "research")}

    def validate_recommendations(state: AgentState) -> AgentState:
        try:
            result = services.validate(
                state["spec"],
                state["search_result"],
                state["recommendation_result"],
                state["evidence"],
            )
            return {"validation": result, "steps": _step(state, "validate:recommendations")}
        except DEPENDENCY_FAILURES:
            return {
                "warnings": [*state.get("warnings", []), "recommendation_validation_unavailable"]
            }

    def after_understand(state: AgentState) -> str:
        return "done" if "spec" not in state or state["spec"].clarifying_question else "search"

    def after_primary(state: AgentState) -> str:
        if not state["search_result"].available or "validation_unavailable" in state.get(
            "warnings", []
        ):
            return "done"
        return "recommend"

    def after_recommend(state: AgentState) -> str:
        return (
            "research"
            if state["recommendation_result"].recommendations
            else after_validation(state)
        )

    def after_validation(state: AgentState) -> str:
        spec, validation = state["spec"], state["validation"]
        if spec.sku or state["retry_count"]:
            return "done"
        if _status(state) == "unavailable":
            return "done"
        if validation.needs_retry or _result_count(spec, validation) < 4:
            return "retry"
        return "done"

    def retry(state: AgentState) -> AgentState:
        return {"retry_count": 1, "steps": _step(state, "retry")}

    graph = StateGraph(AgentState)
    graph.add_node("understand", understand)
    graph.add_node("search", search)
    graph.add_node("validate_primary", validate_primary)
    graph.add_node("recommend", recommend)
    graph.add_node("research", research)
    graph.add_node("validate_recommendations", validate_recommendations)
    graph.add_node("retry", retry)
    graph.add_edge(START, "understand")
    graph.add_conditional_edges("understand", after_understand, {"search": "search", "done": END})
    graph.add_edge("search", "validate_primary")
    graph.add_conditional_edges(
        "validate_primary", after_primary, {"recommend": "recommend", "done": END}
    )
    graph.add_conditional_edges(
        "recommend", after_recommend, {"research": "research", "retry": "retry", "done": END}
    )
    graph.add_edge("research", "validate_recommendations")
    graph.add_conditional_edges(
        "validate_recommendations", after_validation, {"retry": "retry", "done": END}
    )
    graph.add_edge("retry", "search")
    compiled = graph.compile()

    def run(request: SearchRequest) -> SearchResponse:
        start = perf_counter()
        state = compiled.invoke({"request": request, "retry_count": 0})
        spec = state.get("spec")
        validation = state.get("validation", ValidationResult())
        recommendation_result = state.get("recommendation_result", RecommendationResult())
        warnings = [
            *state.get("warnings", []),
            *(spec.warnings if spec else []),
            *state.get("search_result", SearchResult()).warnings,
            *recommendation_result.warnings,
            *validation.warnings,
            *(warning for item in state.get("evidence", []) for warning in item.warnings),
        ]
        status = _status(state)
        reasons = list(validation.reason_codes)
        if status == "partial":
            reasons.append("not_enough_products")
        if status == "empty":
            reasons.append("no_suitable_products")
        response = SearchResponse(
            request_id=request.request_id,
            status=status,
            products=validation.products,
            recommendations=validation.recommendations,
            warnings=list(dict.fromkeys(warnings)),
            reason_codes=list(dict.fromkeys(reasons)),
            clarifying_question=spec.clarifying_question if spec else None,
            intent=spec.intent if spec else None,
            retry_count=state["retry_count"],
            steps=state.get("steps", []),
        )
        if record_event is not None:
            try:
                record_event(
                    SearchEvent(
                        request_id=request.request_id,
                        normalized_query=spec.normalized_query if spec else "",
                        intent=response.intent,
                        status=response.status,
                        result_count=len(response.products),
                        latency_ms=(perf_counter() - start) * 1000,
                        retry_count=response.retry_count,
                        reason_codes=list(
                            dict.fromkeys([*response.reason_codes, *response.warnings])
                        ),
                        category=spec.category if spec else None,
                        recommendation_count=len(response.recommendations),
                    )
                )
            except EventDeliveryError as error:
                LOGGER.warning("Search event delivery failed (%s)", error)
            except (OSError, sqlite3.Error) as error:
                LOGGER.warning("Search event delivery failed (%s)", type(error).__name__)
        return response

    return run
