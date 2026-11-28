"""LangGraph routing, validation and response assembly."""

import logging
import re
import sqlite3
from collections.abc import Callable, Sequence
from time import perf_counter
from typing import TypedDict

from langgraph.graph import END, START, StateGraph

from smart_search.contracts import (
    EvidenceResult,
    HardFilters,
    Product,
    ProductDetailsTarget,
    QuerySpec,
    RecommendationResult,
    SearchEvent,
    SearchHit,
    SearchMode,
    SearchParams,
    SearchRequest,
    SearchResponse,
    SearchResult,
    SearchStatus,
    ShownProduct,
    ValidationResult,
)
from smart_search.events import EventDeliveryError
from smart_search.product_details import chat_products, make_details, parse_reference
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
    anchors: list[Product]
    evidence: list[EvidenceResult]
    validation: ValidationResult
    retry_count: int
    warnings: list[str]
    steps: list[str]
    shown_products: Sequence[ShownProduct]
    reference_unavailable: bool
    details_response: SearchResponse


def _step(state: AgentState, step: str) -> list[str]:
    return [*state.get("steps", []), step]


def _merge_search(
    previous: SearchResult,
    current: SearchResult,
    *,
    kept_previous_hits: list[SearchHit] | None = None,
) -> SearchResult:
    hits = {}
    base_hits = previous.hits if kept_previous_hits is None else kept_previous_hits
    for hit in [*base_hits, *current.hits]:
        hits.setdefault(hit.product.product_id, hit)
    return SearchResult(
        hits=[hit.model_copy(update={"rank": rank}) for rank, hit in enumerate(hits.values(), 1)],
        warnings=list(dict.fromkeys([*previous.warnings, *current.warnings])),
        available=previous.available or current.available,
        diagnostics=current.diagnostics,
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
        *(warning for item in state.get("evidence", []) for warning in item.warnings),
    }
    if not count and recommendation_needed and RECOMMENDATION_FAILURES & recommendation_warnings:
        return "unavailable"
    if not count:
        return "empty"
    if count < (1 if spec.intent == "exact_product" and spec.sku else 4):
        return "partial"
    return "ok"


def build_search(
    services: Services,
    *,
    record_event: Callable[[SearchEvent], None] | None = None,
    search_mode: SearchMode = "hybrid",
) -> SearchRunner:
    """Compile the workflow once and return a synchronous request handler."""

    def route_details(state: AgentState) -> AgentState:
        request = state["request"]
        shown = state.get("shown_products", ())
        reference = parse_reference(request.query, shown, allow_short=request.channel == "chat")
        if reference is None:
            return {}
        answer = SearchResponse(
            request_id=request.request_id,
            status="clarification_needed",
            response_kind="product_details",
            steps=["route:product_details"],
        )
        if isinstance(reference, int):
            if state.get("reference_unavailable"):
                answer.status = "unavailable"
                answer.reason_codes = ["detail_session_unavailable"]
                return {"details_response": answer}
            if not 1 <= reference <= len(shown):
                answer.reason_codes = [
                    "detail_position_invalid" if shown else "detail_reference_expired"
                ]
                answer.clarifying_question = (
                    f"В списке {len(shown)} поз. Укажите номер показанной карточки."
                    if shown
                    else "Список товаров недоступен. Назовите модель или артикул."
                )
                return {"details_response": answer}
            reference = ProductDetailsTarget(kind="id", value=shown[reference - 1].product_id)
        if isinstance(reference, list):
            selected = reference
        elif services.get_product_details is None:
            answer.status = "unavailable"
            answer.reason_codes = ["detail_tool_unavailable"]
            return {"details_response": answer}
        else:
            answer.steps.append(f"details:by_{reference.kind}")
            try:
                selected = services.get_product_details(reference).candidates
            except DEPENDENCY_FAILURES:
                answer.status = "unavailable"
                answer.reason_codes = ["detail_catalog_unavailable"]
                return {"details_response": answer}
            if len(selected) == 1:
                answer.product_details = make_details(selected[0])
                if not answer.product_details.url:
                    answer.reason_codes.append("detail_link_missing")
                if not (answer.product_details.summary or answer.product_details.facts):
                    answer.reason_codes.append("detail_description_missing")
                answer.status = "partial" if answer.reason_codes else "ok"
                return {"details_response": answer}
            if not selected and reference.kind in {"id", "sku"}:
                answer.status = "empty"
                answer.reason_codes = ["detail_product_not_found"]
                return {"details_response": answer}
        if selected:
            items = "; ".join(
                f"{card.title} (артикул {card.sku})" if card.sku else card.title
                for card in selected
            )
            answer.reason_codes = ["detail_model_ambiguous"]
            answer.clarifying_question = f"Нашёл несколько предложений: {items}. Укажите артикул."
            return {"details_response": answer}
        return {}

    def understand(state: AgentState) -> AgentState:
        item = state["request"]
        sku_match = SKU_QUERY.fullmatch(item.query)
        if sku_match:
            sku = sku_match.group(1)
            spec = QuerySpec(
                raw_query=item.query, normalized_query=sku, intent="exact_product", sku=sku
            )
            return {"spec": spec, "steps": _step(state, "route:sku")}
        shown = state.get("shown_products", ())
        if shown:
            cards_text = "Показанные карточки: " + "; ".join(
                f"{idx}. {card.title}" + (f" [sku: {card.sku}]" if card.sku else "")
                for idx, card in enumerate(shown, 1)
            )
            combined_context = (
                f"{item.previous_context}\n{cards_text}" if item.previous_context else cards_text
            )
            item = item.model_copy(update={"previous_context": combined_context})
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
        previous = state.get("search_result")
        steps = state.get("steps", [])
        if retry and previous is not None and previous.diagnostics.get("category_hints_applied"):
            lookup = lookup.model_copy(update={"category_hints": []})
            steps = [*steps, "route:category_fallback"]
        mode = "sku" if spec.sku else (spec.search_mode or search_mode)
        if retry and not spec.sku:
            if lookup.score_threshold is not None:
                lookup = lookup.model_copy(update={"score_threshold": None})
                steps = [*steps, "route:threshold_fallback"]
            if mode == "dense":
                mode = "hybrid"
                steps = [*steps, "route:hybrid_fallback"]
        params = SearchParams(query_spec=lookup, mode=mode, top_k=30 if retry else 12)
        try:
            current = services.search(params)
        except DEPENDENCY_FAILURES:
            current = SearchResult(available=False, warnings=["search_unavailable"])
        if not current.hits and {"search_unavailable", "search_timeout"}.intersection(
            current.warnings
        ):
            current = current.model_copy(update={"available": False})
        if current.diagnostics.get("category_hints_applied") is True:
            steps = [*steps, "route:category_hint"]
        if previous is not None:
            # Keep only validated hits (or top anchors on relations) ahead of retry results so
            # 100 rejected wrong-category hits cannot crowd out fresh retry matches past [:100].
            if spec.intent in RELATION_BY_INTENT:
                kept = previous.hits[:3]
            else:
                kept_ids = {
                    product.product_id
                    for product in state.get("validation", ValidationResult()).products
                }
                kept = [hit for hit in previous.hits if hit.product.product_id in kept_ids]
            merged = _merge_search(previous, current, kept_previous_hits=kept)
        else:
            merged = current
        return {"search_result": merged, "steps": [*steps, f"search:{mode}"]}

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
        if not anchors or (spec.intent == "exact_product" and spec.sku):
            return {"recommendation_result": RecommendationResult()}
        try:
            result = services.recommend(spec, anchors[:3])
        except DEPENDENCY_FAILURES:
            result = RecommendationResult(warnings=["recommendation_unavailable"])
        return {
            "recommendation_result": result,
            "anchors": anchors[:3],
            "steps": _step(state, "recommend"),
        }

    def research(state: AgentState) -> AgentState:
        evidence = []
        warnings = list(state.get("warnings", []))
        drafts = state["recommendation_result"].recommendations[:8]
        if services.research_batch is not None:
            try:
                evidence = services.research_batch(state["spec"], state["anchors"], drafts)
            except DEPENDENCY_FAILURES:
                warnings.append("research_unavailable")
        else:
            for draft in drafts:
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
        if state["spec"].intent in {"alternatives", "complements", "solution"}:
            return "recommend"
        return after_validation(state)

    def after_recommend(state: AgentState) -> str:
        return (
            "research"
            if state["recommendation_result"].recommendations
            else after_validation(state)
        )

    def after_validation(state: AgentState) -> str:
        spec, validation = state["spec"], state["validation"]
        if (spec.intent == "exact_product" and spec.sku) or state["retry_count"]:
            return "done"
        if _status(state) == "unavailable":
            return "done"
        diagnostics = state["search_result"].diagnostics
        can_relax = bool(
            diagnostics.get("category_hints_applied")
            or spec.score_threshold is not None
            or spec.search_mode == "dense"
        )
        if diagnostics.get("exhausted") is True and not can_relax:
            return "done"
        if validation.needs_retry or _result_count(spec, validation) < 4:
            return "retry"
        return "done"

    def retry(state: AgentState) -> AgentState:
        return {"retry_count": 1, "steps": _step(state, "retry")}

    graph = StateGraph(AgentState)
    graph.add_node("route_details", route_details)
    graph.add_node("understand", understand)
    graph.add_node("search", search)
    graph.add_node("validate_primary", validate_primary)
    graph.add_node("recommend", recommend)
    graph.add_node("research", research)
    graph.add_node("validate_recommendations", validate_recommendations)
    graph.add_node("retry", retry)
    graph.add_edge(START, "route_details")
    graph.add_conditional_edges(
        "route_details",
        lambda state: "done" if "details_response" in state else "understand",
        {"understand": "understand", "done": END},
    )
    graph.add_conditional_edges("understand", after_understand, {"search": "search", "done": END})
    graph.add_edge("search", "validate_primary")
    graph.add_conditional_edges(
        "validate_primary", after_primary, {"recommend": "recommend", "retry": "retry", "done": END}
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

    def run(
        request: SearchRequest,
        *,
        shown_products: Sequence[ShownProduct] = (),
        reference_unavailable: bool = False,
    ) -> SearchResponse:
        start = perf_counter()
        state = compiled.invoke(
            {
                "request": request,
                "retry_count": 0,
                "shown_products": shown_products,
                "reference_unavailable": reference_unavailable,
            }
        )
        if details := state.get("details_response"):
            LOGGER.info(
                "product_details.completed status=%s step=%s reason=%s latency_ms=%.1f",
                details.status,
                details.steps[-1],
                ",".join(details.reason_codes),
                (perf_counter() - start) * 1000,
            )
            return details
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
        if request.channel == "chat":
            response.chat_product_ids = [item.product_id for item in chat_products(response)]
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
