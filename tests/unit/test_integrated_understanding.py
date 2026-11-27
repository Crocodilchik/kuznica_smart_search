import pytest

from smart_search.contracts import SearchRequest
from smart_search.llm.exceptions import LLMUnavailableError
from smart_search.understanding import understand


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "mock")


def test_slang_and_kopecks_survive_in_fallback_without_a_model():
    request = SearchRequest(query="Российский антивирь для винды на 50 компов в наличии до 100к")
    spec = understand(request)
    assert spec.raw_query == request.query
    assert spec.category is None
    assert spec.intent == "product_search"
    assert spec.hard_filters.model_dump() == {
        "os": "Windows",
        "country": "RU",
        "devices": 50,
        "min_total_price_kopecks": None,
        "max_total_price_kopecks": 10_000_000,
        "budget_scope": "primary",
        "in_stock": True,
    }
    assert spec.warnings == ["understanding_llm_fallback"]


@pytest.mark.parametrize("repeat", [1, 2])
def test_monitor_slang_keeps_the_budget_out_of_the_search_text(repeat):
    request = SearchRequest(query=" ".join(["хочу моник купит до 30 тыс"] * repeat))
    spec = understand(request)
    assert spec.raw_query == request.query
    assert spec.category is None
    assert spec.product_type_hint == "монитор"
    assert spec.normalized_query == "монитор"
    assert spec.hard_filters.max_total_price_kopecks == 3_000_000
    assert spec.warnings == ["understanding_llm_fallback"]


def test_rgb_mouse_fallback_captures_type_and_feature_without_category_lock():
    spec = understand(SearchRequest(query="хочу мышку настольную купить с ргб подсветкой"))
    assert spec.category is None
    assert spec.product_type_hint == "мышь"
    assert spec.required_terms == ["RGB"]
    assert spec.normalized_query == "мышь RGB"


def test_min_price_floor_is_extracted_in_fallback_without_catching_units():
    spec = understand(SearchRequest(query="хочу клавиатуру от 10 тыс с ргб"))
    assert spec.category is None
    assert spec.product_type_hint == "клавиатура"
    assert spec.required_terms == ["RGB"]
    assert spec.hard_filters.min_total_price_kopecks == 1_000_000
    assert spec.hard_filters.max_total_price_kopecks is None

    unit_spec = understand(SearchRequest(query="лицензия от 2 шт."))
    assert unit_spec.hard_filters.min_total_price_kopecks is None


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


def test_deepseek_controls_short_and_slang_queries_directly(monkeypatch):
    calls = []

    def complete(system_prompt, user_prompt, schema):
        calls.append(user_prompt)
        return schema.model_validate(
            {
                "raw_query": "хачу мышку да 10 тыщ рублей",
                "normalized_query": "компьютерная мышь",
                "intent": "product_search",
                "search_mode": "dense",
                "score_threshold": 0.35,
                "product_type_hint": "мышь",
                "hard_filters": {"max_total_price_kopecks": 1_000_000, "budget_scope": "primary"},
            }
        )

    monkeypatch.setattr("smart_search.understanding.complete_json", complete)
    spec = understand(SearchRequest(query="хачу мышку да 10 тыщ рублей"))
    assert len(calls) == 1
    assert spec.category is None
    assert spec.normalized_query == "компьютерная мышь"
    assert spec.search_mode == "dense"
    assert spec.score_threshold == 0.35
    assert spec.product_type_hint == "мышь"
    assert spec.hard_filters.max_total_price_kopecks == 1_000_000
    assert not spec.warnings


def test_deepseek_can_choose_bm25_and_threshold_is_ignored_for_bm25(monkeypatch):
    def complete(system_prompt, user_prompt, schema):
        return schema.model_validate(
            {
                "raw_query": "КриптоПро CSP 5.0",
                "normalized_query": "КриптоПро CSP 5.0",
                "intent": "product_search",
                "search_mode": "bm25",
                "score_threshold": 0.4,
            }
        )

    monkeypatch.setattr("smart_search.understanding.complete_json", complete)
    spec = understand(SearchRequest(query="КриптоПро CSP 5.0"))
    assert spec.search_mode == "bm25"
    assert spec.score_threshold is None
    assert spec.normalized_query == "КриптоПро CSP 5.0"


def test_deepseek_constraints_override_regex_while_missing_fields_backfill(monkeypatch):
    def complete(system_prompt, user_prompt, schema):
        return schema.model_validate(
            {
                "raw_query": "modified",
                "normalized_query": "сетевой мониторинг",
                "intent": "product_search",
                "search_mode": "hybrid",
                "category": "fabricated-id",
                "hard_filters": {"min_total_price_kopecks": 200000},
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
    assert spec.hard_filters.min_total_price_kopecks == 200000
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


def test_non_monetary_units_are_never_parsed_as_price_and_type_hints_populate(monkeypatch):
    monkeypatch.setattr(
        "smart_search.understanding.resolve_type_category_hints",
        lambda t: ["74887"] if t == "блок питания" else [],
    )
    spec = understand(SearchRequest(query="хочу золотой блок питания от 750 ватт"))
    assert spec.hard_filters.min_total_price_kopecks is None
    assert spec.product_type_hint == "блок питания"
    assert spec.category_hints == ["74887"]

    mon_spec = understand(SearchRequest(query="монитор от 27 дюймов до 45 тыс"))
    assert mon_spec.hard_filters.min_total_price_kopecks is None
    assert mon_spec.hard_filters.max_total_price_kopecks == 4_500_000
