from functools import partial

import pytest

from smart_search.contracts import (
    HardFilters,
    Product,
    QuerySpec,
    SearchHit,
    SearchRequest,
    SearchResult,
    ValidationResult,
)
from smart_search.modes.demo import demo_services
from smart_search.supervisor import build_search


def request(query: str) -> SearchRequest:
    return SearchRequest(query=query, request_id="query-1")


def products(*identifiers: str) -> list[SearchHit]:
    return [
        SearchHit(product=Product(product_id=value, title=value, category="серверы"), rank=rank)
        for rank, value in enumerate(identifiers, 1)
    ]


def test_exact_sku_skips_understanding_and_needs_only_one_card():
    def must_not_run(_request):
        pytest.fail("An explicitly entered SKU must be routed directly")

    def search(params):
        assert params.mode == "sku"
        assert params.query_spec.sku == "458218"
        return SearchResult(hits=products("458218"))

    runner = build_search(demo_services(understand=must_not_run, search=search))
    response = runner(request("артикул: 458218"))

    assert response.status == "ok"
    assert [product.product_id for product in response.products] == ["458218"]
    assert response.retry_count == 0


def test_retry_merges_candidates_and_preserves_hard_filters():
    calls = []
    constraints = HardFilters(os="Windows", max_total_price_kopecks=300_000)

    def understand(item):
        return QuerySpec(raw_query=item.query, normalized_query="сервер", hard_filters=constraints)

    def search(params):
        calls.append(params)
        if len(calls) == 1:
            return SearchResult(hits=products("a", "b"))
        return SearchResult(hits=products("b", "c", "d"))

    def validate(spec, results, recommendations, evidence):
        return ValidationResult(products=[hit.product for hit in results.hits])

    runner = build_search(demo_services(understand=understand, search=search, validate=validate))
    response = runner(request("сервер до 3 000 рублей для Windows"))

    assert response.status == "ok"
    assert response.retry_count == 1
    assert len(calls) == 2 and calls[1].top_k > calls[0].top_k
    assert all(call.query_spec.hard_filters == constraints for call in calls)
    assert [item.product_id for item in response.products] == ["a", "b", "c", "d"]


def test_search_unavailable_differs_from_empty_and_failed_retry_keeps_products():
    def unavailable(_):
        return SearchResult(available=False, warnings=["search_unavailable"])

    empty = build_search(demo_services(search=lambda _: SearchResult()))
    interrupted = build_search(demo_services(search=unavailable))
    assert empty(request("сервер")).status == "empty"
    assert interrupted(request("сервер")).status == "unavailable"

    def failed_retry(params):
        if params.top_k > 12:
            raise TimeoutError
        return SearchResult(hits=products("a"))

    response = build_search(demo_services(search=failed_retry))(request("сервер"))
    assert response.status == "partial"
    assert response.retry_count == 1
    assert [product.product_id for product in response.products] == ["a"]
    assert "search_unavailable" in response.warnings


def test_exhausted_candidates_skip_same_search_and_recommendations():
    calls = []

    def search(params):
        calls.append(params.top_k)
        return SearchResult(hits=products("a"), diagnostics={"exhausted": True})

    response = build_search(demo_services(search=search))(request("сервер"))
    assert response.status == "partial"
    assert response.retry_count == 0
    assert calls == [12]


def test_exhausted_category_hint_can_widen_once_with_original_constraints():
    calls = []
    filters = HardFilters(in_stock=True, max_total_price_kopecks=300_000)

    def understand(item):
        return QuerySpec(
            raw_query=item.query,
            normalized_query="сервер",
            hard_filters=filters,
            category_hints=["backup", "virtualization"],
        )

    def search(params):
        calls.append(params)
        scoped = bool(params.query_spec.category_hints)
        return SearchResult(
            hits=products("a") if scoped else products("a", "b", "c", "d"),
            diagnostics={"exhausted": True, "category_hints_applied": scoped},
        )

    def validate(spec, results, recommendations, evidence):
        return ValidationResult(products=[hit.product for hit in results.hits])

    runner = build_search(demo_services(understand=understand, search=search, validate=validate))
    response = runner(request("сервер для резервного копирования"))
    assert response.retry_count == 1 and response.status == "ok"
    assert calls[0].query_spec.category_hints and not calls[1].query_spec.category_hints
    assert all(call.query_spec.hard_filters == filters for call in calls)
    assert "route:category_hint" in response.steps and "route:category_fallback" in response.steps


def test_clarification_skips_search():
    def unexpected(_):
        pytest.fail("Clarification must be returned before search")

    response = build_search(demo_services(search=unexpected))(
        request("чем заменить Windows Server?")
    )
    assert response.status == "clarification_needed"
    assert response.clarifying_question


def test_alternatives_use_product_reference_and_stay_in_own_section():
    response = build_search(demo_services())(request("Аналоги AnyDesk"))

    assert response.status == "partial"
    assert response.intent == "alternatives"
    assert response.recommendations
    assert all(item.type == "substitute" for item in response.recommendations)
    assert all(item.anchor_id != item.product.product_id for item in response.recommendations)


def test_complement_reaches_research_and_is_gated_by_evidence():
    services = demo_services()
    confirmed = build_search(services)(request("Что докупить к Astra Linux SE?"))
    assert confirmed.intent == "complements"
    assert [item.product.product_id for item in confirmed.recommendations] == ["458218"]
    assert confirmed.recommendations[0].evidence[0].source_url

    missing = build_search(demo_services(research=partial(services.research, confirmed=False)))(
        request("Что докупить к Astra Linux SE?")
    )
    assert missing.recommendations == []
    assert "evidence_unconfirmed" in missing.reason_codes


def test_analytics_failure_does_not_change_search_result():
    def unavailable(_event):
        raise OSError("store unavailable")

    runner = build_search(demo_services(), record_event=unavailable)
    response = runner(request("артикул: 458218"))

    assert response.status == "ok"
    assert response.products[0].product_id == "458218"


def test_unknown_license_capacity_cannot_pass_total_price_filter():
    response = build_search(demo_services())(request("антивирь для винды на 50 компов до 100к"))

    assert "license_capacity_unknown" in response.reason_codes
    assert all(item.license_units is not None for item in response.products)


def test_research_timeout_is_a_dependency_failure_for_explicit_complement_requests():
    def timeout(_draft):
        raise TimeoutError

    response = build_search(demo_services(research=timeout))(
        request("Что докупить к Astra Linux SE?")
    )

    assert response.status == "unavailable"
    assert response.products
    assert not response.recommendations
    assert "research_unavailable" in response.warnings


def test_understanding_timeout_ends_without_dropping_customer_constraints():
    def timeout(_request):
        raise TimeoutError

    def must_not_run(_params):
        pytest.fail("Without a safe interpretation, a budget may silently disappear")

    response = build_search(demo_services(understand=timeout, search=must_not_run))(
        request("Сервер до 300 тысяч")
    )

    assert response.status == "unavailable"
    assert "understanding_unavailable" in response.warnings


def test_supervisor_uses_deepseek_search_mode_and_relaxes_dense_threshold_on_retry():
    calls = []
    constraints = HardFilters(max_total_price_kopecks=1_000_000)

    def understand(item):
        return QuerySpec(
            raw_query=item.query,
            normalized_query="компьютерная мышь",
            search_mode="dense",
            score_threshold=0.4,
            hard_filters=constraints,
        )

    def search(params):
        calls.append(params)
        if len(calls) == 1:
            return SearchResult(hits=products("m1"), diagnostics={"exhausted": True})
        return SearchResult(hits=products("m1", "m2", "m3", "m4"))

    def validate(spec, results, recommendations, evidence):
        return ValidationResult(products=[hit.product for hit in results.hits])

    runner = build_search(demo_services(understand=understand, search=search, validate=validate))
    response = runner(request("хачу мышку да 10 тыщ рублей"))

    assert response.status == "ok"
    assert response.retry_count == 1
    assert calls[0].mode == "dense" and calls[0].query_spec.score_threshold == 0.4
    assert calls[1].mode == "hybrid" and calls[1].query_spec.score_threshold is None
    assert all(c.query_spec.hard_filters == constraints for c in calls)
    assert "route:threshold_fallback" in response.steps
    assert "route:hybrid_fallback" in response.steps


def test_retry_excludes_rejected_wrong_category_hits_ahead_of_fallback_results():
    seen_batches = []

    def understand(item):
        return QuerySpec(
            raw_query=item.query,
            normalized_query="коврик для мыши",
            category_hints=["82833"],
        )

    def search(params):
        if params.query_spec.category_hints:
            return SearchResult(
                hits=products(*(f"wrong-{i}" for i in range(100))),
                diagnostics={"category_hints_applied": True},
            )
        return SearchResult(
            hits=products("target-pad"), diagnostics={"category_hints_applied": False}
        )

    def validate(spec, results, recommendations, evidence):
        seen_batches.append([hit.product.product_id for hit in results.hits])
        return ValidationResult(
            products=[hit.product for hit in results.hits if hit.product.product_id == "target-pad"]
        )

    runner = build_search(demo_services(understand=understand, search=search, validate=validate))
    response = runner(request("Коврик для мыши"))
    assert response.retry_count == 1
    assert [item.product_id for item in response.products] == ["target-pad"]
    assert seen_batches[1] == ["target-pad"]
