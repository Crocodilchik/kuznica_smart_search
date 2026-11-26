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
