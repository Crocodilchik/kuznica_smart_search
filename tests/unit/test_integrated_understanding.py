import pytest

from smart_search.contracts import SearchRequest
from smart_search.llm.exceptions import LLMUnavailableError
from smart_search.understanding import understand


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "mock")


def test_slang_and_kopecks_survive_without_a_model():
    request = SearchRequest(query="Российский антивирь для винды на 50 компов в наличии до 100к")
    spec = understand(request)
    assert spec.raw_query == request.query
    assert spec.category == "endpoint_security"
    assert spec.intent == "product_search"
    assert spec.hard_filters.model_dump() == {
        "os": "Windows",
        "country": "RU",
        "devices": 50,
        "max_total_price_kopecks": 10_000_000,
        "budget_scope": "primary",
        "in_stock": True,
    }
    assert not spec.warnings


@pytest.mark.parametrize(
    ("text", "intent", "sku"),
    [
        ("AL16-03SWS701-0100", "exact_product", "AL16-03SWS701-0100"),
        ("Нужна замена артикулу Ac-17/A", "alternatives", "Ac-17/A"),
        ("Что докупить к AL16-03SWS701-0100?", "complements", "AL16-03SWS701-0100"),
        ("КриптоПро CSP 5.0", "product_search", None),
    ],
)
def test_relation_preserves_anchor_sku_and_version(text, intent, sku):
    spec = understand(SearchRequest(query=text))
    assert (spec.intent, spec.sku) == (intent, sku)
    assert (sku or "5.0") in spec.normalized_query


def test_continuation_keeps_context_and_does_not_turn_device_count_into_price():
    spec = understand(SearchRequest(previous_context="Антивирус под Windows", query="До 50 ПК"))
    assert spec.raw_query == "До 50 ПК"
    assert spec.hard_filters.os == "Windows"
    assert spec.hard_filters.devices == 50
    assert spec.hard_filters.max_total_price_kopecks is None


def test_soft_target_and_bundle_ceiling_use_exact_decimal_kopecks():
    spec = understand(
        SearchRequest(query="Комплект ИБП, желательно около 2,5 тыс., всего до 3 200,45 руб.")
    )
    assert spec.hard_filters.budget_scope == "bundle"
    assert spec.hard_filters.max_total_price_kopecks == 320045
    assert spec.preferences.target_price_kopecks == 250000


def test_ambiguous_negated_constraint_needs_clarification():
    spec = understand(SearchRequest(query="Антивирус не для Windows"))
    assert spec.hard_filters.os is None
    assert spec.clarifying_question


def test_explicit_server_category_overrides_backup_use_case():
    spec = understand(SearchRequest(query="Сервер для резервного копирования до 300 тысяч"))
    assert spec.category == "server_hardware"
    assert spec.category_hints == []
    assert spec.hard_filters.max_total_price_kopecks == 30_000_000


def test_ambiguous_use_cases_limit_only_the_first_search():
    spec = understand(SearchRequest(query="Резервное копирование и виртуализация"))
    assert spec.category is None
    assert spec.category_hints == ["backup", "virtualization"]


def test_model_category_is_a_verified_hint(monkeypatch):
    def complete(system_prompt, user_prompt, schema):
        return schema(
            raw_query="text",
            normalized_query="сетевой мониторинг",
            category="Net",
            category_hints=["bad"],
        )

    monkeypatch.setattr("smart_search.understanding.complete_json", complete)
    monkeypatch.setattr(
        "smart_search.understanding.resolve_category",
        lambda category: ["123"] if category == "Net" else [],
    )
    spec = understand(SearchRequest(query="Нужно видеть кто отключился от сети в филиалах"))
    assert spec.category is None
    assert spec.category_hints == ["123"]


def test_price_is_a_filter_and_product_version_remains_searchable():
    spec = understand(SearchRequest(query="КриптоПро CSP 5.0 до 1200.50 руб."))
    assert spec.normalized_query == "КриптоПро CSP 5.0"
    assert spec.hard_filters.max_total_price_kopecks == 120050


def test_model_can_resolve_complex_search_without_overwriting_known_filters(monkeypatch):
    def complete(system_prompt, user_prompt, schema):
        return schema.model_validate(
            {
                "raw_query": "modified",
                "normalized_query": "сетевой мониторинг",
                "intent": "product_search",
                "category": "fabricated-id",
                "hard_filters": {"max_total_price_kopecks": 999999999},
            }
        )

    monkeypatch.setattr("smart_search.understanding.complete_json", complete)
    request = SearchRequest(
        query="Нужно видеть кто отключился от сети в филиалах до 9 тысяч рублей"
    )
    spec = understand(request)
    assert spec.normalized_query == "сетевой мониторинг"
    assert spec.raw_query == request.query
    assert spec.category is None
    assert spec.hard_filters.max_total_price_kopecks == 900000
    assert not spec.warnings


def test_complex_fallback_preserves_hard_constraints(monkeypatch):
    def unavailable(*args):
        raise LLMUnavailableError("offline")

    monkeypatch.setattr("smart_search.understanding.complete_json", unavailable)
    spec = understand(
        SearchRequest(query="Хотим видеть кто отключился от сети под Linux до 4 тыс.")
    )
    assert spec.hard_filters.os == "Linux"
    assert spec.hard_filters.max_total_price_kopecks == 400000
    assert spec.warnings
