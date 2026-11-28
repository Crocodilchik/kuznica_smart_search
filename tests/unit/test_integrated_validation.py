from datetime import UTC, datetime

import pytest

from smart_search import catalog_backend
from smart_search.contracts import (
    Evidence,
    EvidenceResult,
    HardFilters,
    Product,
    QuerySpec,
    RecommendationDraft,
    RecommendationResult,
    SearchHit,
    SearchParams,
    SearchResult,
)
from smart_search.llm.exceptions import LLMUnavailableError
from smart_search.validation import RerankedSelection, validate


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "mock")


def product(product_id, **values):
    return Product(product_id=product_id, title=product_id, category="software", **values)


def check(products, *, spec=None, drafts=(), evidence=()):
    return validate(
        spec or QuerySpec(raw_query="ПО", normalized_query="ПО"),
        SearchResult(hits=[SearchHit(product=p, rank=i) for i, p in enumerate(products, 1)]),
        RecommendationResult(recommendations=list(drafts)),
        list(evidence),
    )


def test_rgb_mouse_excludes_unrelated_products_and_unconfirmed_lighting(monkeypatch):
    monkeypatch.setattr("smart_search.validation.resolve_category", lambda _: ["82833"])
    spec = QuerySpec(
        raw_query="хочу мышку настольную купить с ргб подсветкой",
        normalized_query="мышь RGB",
        category="82833",
    )
    products = [
        Product(product_id="scanner", title="Настольный сканер RGB", category="Сканеры"),
        Product(
            product_id="keyboard", title="Клавиатура RGB", category="Периферия", category_id="82833"
        ),
        Product(
            product_id="mouse", title="Мышь проводная", category="Периферия", category_id="82833"
        ),
        Product(product_id="lit", title="Мышь с RGB", category="Периферия", category_id="82833"),
    ]
    result = check(products, spec=spec)
    assert [item.product_id for item in result.products] == ["lit"]
    assert {"category_mismatch", "product_type_mismatch", "rgb_unconfirmed"} <= set(
        result.reason_codes
    )


def test_strict_os_country_stock_license_metric_and_price():
    filters = HardFilters(
        os="Windows", country="RU", in_stock=True, devices=50, max_total_price_kopecks=5_000
    )
    attrs = {
        "os": ["Windows"],
        "country": "RU",
        "in_stock": True,
        "license_units": 50,
        "license_unit_type": "device",
        "price_kopecks": 2500,
    }
    common = product("ok", **attrs)
    invalid = [
        common.model_copy(update={"product_id": f"bad-{i}", **changes})
        for i, changes in enumerate(
            [
                {"os": []},
                {"country": None},
                {"in_stock": None},
                {"license_unit_type": "user"},
                {"min_purchase": 3},
                {"price_kopecks": None},
            ]
        )
    ]
    spec = QuerySpec(raw_query="ПО", normalized_query="ПО", hard_filters=filters)
    result = check([common, common, *invalid], spec=spec)
    assert [p.product_id for p in result.products] == ["ok"]
    assert result.needs_retry
    assert "budget_exceeded" in result.reason_codes
    assert "price_unknown" in result.reason_codes


def test_explicitly_combinable_packs_cover_devices_and_obey_total_budget():
    spec = QuerySpec(
        raw_query="ПО",
        normalized_query="ПО",
        hard_filters=HardFilters(devices=41, max_total_price_kopecks=7500000),
    )
    pack = product(
        "pack",
        license_units=10,
        license_unit_type="device",
        price_kopecks=1500000,
        attributes={"license_stackable": True},
    )
    result = check(
        [
            pack,
            pack.model_copy(update={"product_id": "unknown", "attributes": {}}),
            pack.model_copy(update={"product_id": "costly", "price_kopecks": 1500001}),
        ],
        spec=spec,
    )
    assert [p.product_id for p in result.products] == ["pack"]


def test_exact_sku_ignores_unrelated_hits_and_does_not_retry():
    spec = QuerySpec(
        raw_query="sku A-42", normalized_query="A-42", sku="A-42", intent="exact_product"
    )
    result = check([product("A-42"), product("p", sku="a-42")], spec=spec)
    assert [p.product_id for p in result.products] == ["p"]
    assert not result.needs_retry


def test_explicit_product_version_must_match():
    spec = QuerySpec(raw_query="КриптоПро CSP 5.0", normalized_query="КриптоПро CSP 5.0")
    good = product("good").model_copy(update={"title": "КриптоПро CSP 5.0"})
    bad = product("bad").model_copy(update={"title": "КриптоПро CSP 5.1"})
    assert check([good, bad], spec=spec).products == [good]


def proof(draft, status="confirmed", **source):
    return EvidenceResult(
        anchor_id=draft.anchor_id,
        product_id=draft.product.product_id,
        claim=draft.claim,
        status=status,
        evidence=[
            Evidence(
                source_type="vendor",
                excerpt="Documentation of this model.",
                checked_at=datetime.now(UTC),
                **source,
            )
        ],
    )


def test_alternative_is_eligible_even_if_source_exceeds_budget():
    spec = QuerySpec(
        raw_query="замена",
        normalized_query="Ac-1",
        intent="alternatives",
        sku="Ac-1",
        hard_filters=HardFilters(max_total_price_kopecks=10000, budget_scope="primary"),
    )
    source = product("source", sku="Ac-1", price_kopecks=20000)
    draft = RecommendationDraft(
        anchor_id="source",
        product=product("alt", price_kopecks=10000),
        type="substitute",
        claim="Same function",
    )
    confirmed = proof(draft, source_url="https://vendor.test/model")
    result = check([source], spec=spec, drafts=[draft], evidence=[confirmed])
    assert not result.products
    assert [r.product.product_id for r in result.recommendations] == ["alt"]
    assert result.needs_retry


@pytest.mark.parametrize("issue", ["contradicted", "invalid_url", "wrong_claim", "unknown_anchor"])
def test_matching_confirmed_traceable_evidence_is_mandatory(issue):
    draft = RecommendationDraft(
        anchor_id="a", product=product("b"), type="cross_sell", claim="Compatible"
    )
    positive = proof(draft, source_url="https://vendor.test/model")
    if issue == "invalid_url":
        evidence = [proof(draft, source_url="file:///local")]
    elif issue == "wrong_claim":
        evidence = [positive.model_copy(update={"claim": "Different edition"})]
    elif issue == "contradicted":
        evidence = [positive, proof(draft, "contradicted", source_url="https://vendor.test/model")]
    else:
        evidence = [positive]
    result = check(
        [product("other" if issue == "unknown_anchor" else "a")], drafts=[draft], evidence=evidence
    )
    assert not result.recommendations


def test_bundle_budget_includes_all_parts_offered_for_the_same_anchor():
    spec = QuerySpec(
        raw_query="комплект",
        normalized_query="комплект",
        intent="solution",
        hard_filters=HardFilters(max_total_price_kopecks=10000, budget_scope="bundle"),
    )
    anchor = product("a", price_kopecks=6000)
    drafts = [
        RecommendationDraft(
            anchor_id="a",
            product=product(key, price_kopecks=3000),
            type="cross_sell",
            claim="Compatible",
        )
        for key in ("b", "c")
    ]
    result = check(
        [anchor],
        spec=spec,
        drafts=drafts,
        evidence=[proof(d, source_url="https://vendor.test/model") for d in drafts],
    )
    assert [r.product.product_id for r in result.recommendations] == ["b"]
    assert "budget_exceeded" in result.reason_codes


def test_keyboard_min_price_and_rgb_exclude_mice_and_cheap_items():
    spec = QuerySpec(
        raw_query="хочу клавиатуру от 10 тыс с ргб",
        normalized_query="клавиатура RGB",
        product_type_hint="клавиатура",
        required_terms=["RGB"],
        hard_filters=HardFilters(min_total_price_kopecks=1_000_000),
    )
    products = [
        Product(
            product_id="mouse-rgb",
            title="Мышь игровая RGB",
            category="Клавиатуры, мыши, комплекты",
            price_kopecks=1_500_000,
        ),
        Product(
            product_id="kb-cheap",
            title="Клавиатура механическая RGB",
            category="Клавиатуры, мыши, комплекты",
            price_kopecks=800_000,
        ),
        Product(
            product_id="kb-no-rgb",
            title="Клавиатура проводная",
            category="Клавиатуры, мыши, комплекты",
            price_kopecks=1_200_000,
        ),
        Product(
            product_id="kb-ok",
            title="Клавиатура механическая с RGB подсветкой",
            category="Клавиатуры, мыши, комплекты",
            price_kopecks=1_250_000,
        ),
    ]
    result = check(products, spec=spec)
    assert [item.product_id for item in result.products] == ["kb-ok"]
    assert {"product_type_mismatch", "price_below_minimum", "rgb_unconfirmed"} <= set(
        result.reason_codes
    )


def test_laptop_rule_rejects_tables_stands_mice_and_ram_for_laptops():
    spec = QuerySpec(
        raw_query="ноутбук до 100 тыс",
        normalized_query="ноутбук",
        product_type_hint="ноутбук",
        hard_filters=HardFilters(max_total_price_kopecks=10_000_000),
    )
    items = [
        Product(
            product_id="table",
            title="Стол для ноутбука Buro BU-807",
            category="Подставки для ноутбуков",
            price_kopecks=169_400,
        ),
        Product(
            product_id="stand",
            title="Подставка для ноутбука Deepcool",
            category="Подставки для ноутбуков",
            price_kopecks=289_400,
        ),
        Product(
            product_id="mouse",
            title="Мышь Оклик 980GMW для ноутбука",
            category="Клавиатуры, мыши, комплекты",
            price_kopecks=209_200,
        ),
        Product(
            product_id="ram",
            title="Оперативная память Crucial Laptop DDR5",
            category="Оперативная память",
            price_kopecks=1_700_000,
        ),
        Product(
            product_id="numpad",
            title="Числовой блок Оклик K604W для ноутбука",
            category="Клавиатуры, мыши, комплекты",
            price_kopecks=140_600,
        ),
        Product(
            product_id="real-laptop",
            title="Ноутбук LENOVO IdeaPad Slim 3",
            category="Ноутбуки",
            price_kopecks=5_938_900,
        ),
    ]
    result = check(items, spec=spec)
    assert [item.product_id for item in result.products] == ["real-laptop"]
    assert "product_type_mismatch" in result.reason_codes


def test_llm_rerank_filters_mismatches_and_reorders_candidates(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "deepseek")
    captured = {}

    def fake_complete_json(system_prompt, user_prompt, response_model):
        captured["prompt"] = user_prompt
        return RerankedSelection(relevant_ids=["l2", "l1", "nonexistent"])

    monkeypatch.setattr("smart_search.validation.complete_json", fake_complete_json)

    spec = QuerySpec(raw_query="ноутбук до 100 тыс", normalized_query="ноутбук")
    items = [
        Product(product_id="mini-pc", title="ПК INFERIT Micro", category="МиниПК"),
        Product(product_id="l1", title="Ноутбук Acer Aspire 3", category="Ноутбуки"),
        Product(product_id="l2", title="Ноутбук LENOVO IdeaPad Slim 3", category="Ноутбуки"),
    ]
    result = check(items, spec=spec)
    assert [item.product_id for item in result.products] == ["l2", "l1"]
    assert "llm_relevance_mismatch" in result.reason_codes
    assert not result.warnings
    assert "mini-pc" in captured["prompt"] and "Ноутбуки" in captured["prompt"]


def test_llm_mode_skips_brittle_text_regex_and_passes_specs_to_deepseek(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "deepseek")
    captured = {}

    def fake_complete_json(system_prompt, user_prompt, response_model):
        captured["prompt"] = user_prompt
        return RerankedSelection(relevant_ids=["mon27", "psu-plat"])

    monkeypatch.setattr("smart_search.validation.complete_json", fake_complete_json)

    # Literal regex would have rejected "27.0-inch" for required_terms=["27 дюймов"]
    # and rejected Gooxi PSU without "Platinum" in title.
    spec = QuerySpec(
        raw_query="монитор 27 дюймов",
        normalized_query="монитор 27 дюймов",
        product_type_hint="монитор",
        required_terms=["27 дюймов"],
    )
    items = [
        Product(
            product_id="mon27",
            title="Монитор ACER V277Gbi 27.0-inch черный",
            category="Мониторы",
            description='27" LCD TFT LED, 1920 x 1080',
        ),
        Product(
            product_id="psu-plat",
            title="Блок питания Gooxi GC2000PMT",
            category="Блоки питания",
            attributes={"Сертификаты": ["80 PLUS Platinum"]},
        ),
    ]
    result = check(items, spec=spec)
    assert [item.product_id for item in result.products] == ["mon27", "psu-plat"]
    assert "80 PLUS Platinum" in captured["prompt"]
    assert '27\\" LCD TFT LED' in captured["prompt"]


def test_llm_rerank_fallback_preserves_deterministic_candidates_on_error(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "deepseek")

    def broken(*_args, **_kwargs):
        raise LLMUnavailableError("timeout")

    monkeypatch.setattr("smart_search.validation.complete_json", broken)
    spec = QuerySpec(
        raw_query="принтер",
        normalized_query="принтер",
        required_terms=["лазерный"],
    )
    items = [
        Product(product_id="bad", title="Кабель USB", category="Кабели"),
        *[
            Product(product_id=f"p{i}", title=f"Принтер лазерный {i}", category="Принтеры")
            for i in range(15)
        ],
    ]
    result = check(items, spec=spec)
    assert len(result.products) == 12
    assert all(p.product_id != "bad" for p in result.products)
    assert "validation_rerank_fallback" in result.warnings
    assert "feature_unconfirmed" in result.reason_codes


def test_catalog_backend_expands_pool_for_product_search_only(monkeypatch):
    from types import SimpleNamespace

    seen_top_k = []

    def fake_rich(payload):
        seen_top_k.append((payload["mode"], payload["top_k"]))
        return SimpleNamespace(hits=[], warnings=[], status="empty", diagnostics={})

    monkeypatch.setattr(catalog_backend, "search_rich", fake_rich)
    catalog_backend.search(
        SearchParams(
            query_spec=QuerySpec(raw_query="ноутбук", normalized_query="ноутбук"),
            mode="dense",
            top_k=12,
        )
    )
    catalog_backend.search(
        SearchParams(
            query_spec=QuerySpec(
                raw_query="12345", normalized_query="12345", intent="exact_product", sku="12345"
            ),
            mode="sku",
            top_k=12,
        )
    )
    assert seen_top_k == [("dense", 100), ("sku", 12)]


def test_relation_validation_clears_anchor_and_unrelated_products_after_research():
    anchor = Product(product_id="mon1", title="Монитор AOC 24B30HM2", category="Мониторы")
    other = Product(product_id="mon2", title="Монитор AOC 24B30H3", category="Мониторы")
    cable = Product(product_id="cab1", title="Кабель HDMI", category="Кабели")
    draft = RecommendationDraft(
        anchor_id="mon1",
        product=cable,
        type="cross_sell",
        claim="Совместим с товаром «Монитор AOC 24B30HM2».",
    )
    confirmed = proof(draft, source_url="https://store.softline.ru/cab1")
    spec = QuerySpec(
        raw_query="Что посоветуешь к Монитор AOC 24B30HM2",
        normalized_query="Монитор AOC 24B30HM2",
        intent="complements",
    )

    with_recs = check([anchor, other], spec=spec, drafts=[draft], evidence=[confirmed])
    assert with_recs.products == []
    assert [r.product.product_id for r in with_recs.recommendations] == ["cab1"]

    rejected = check(
        [anchor, other],
        spec=spec,
        drafts=[draft],
        evidence=[proof(draft, status="unknown", source_url="https://store.softline.ru/cab1")],
    )
    assert rejected.products == []
    assert rejected.recommendations == []

    without_research = check([anchor, other], spec=spec, drafts=[draft], evidence=[])
    assert [p.product_id for p in without_research.products] == ["mon1", "mon2"]
