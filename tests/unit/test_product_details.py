import pytest

from smart_search.contracts import (
    Product,
    ProductDetailsResult,
    Recommendation,
    SearchRequest,
    SearchResponse,
)
from smart_search.modes.demo import demo_services
from smart_search.product_details import chat_products, make_details, parse_reference
from smart_search.supervisor import build_search


def test_details_use_chat_order_and_skip_search():
    product = Product(product_id="p", title="Мышь Logitech M190", category="Мыши")
    recommended = Product(
        product_id="r",
        title="Мышь Logitech M185",
        category="Мыши",
        description="Беспроводная мышь.",
        url="https://store.softline.ru/example/",
    )
    result = SearchResponse(
        request_id="list",
        status="ok",
        products=[product, recommended],
        recommendations=[
            Recommendation(anchor_id="p", product=recommended, type="substitute", claim="Мышь")
        ],
    )

    def unexpected(*args):
        raise AssertionError("The details route must not run the ordinary search")

    runner = build_search(
        demo_services(
            understand=unexpected,
            search=unexpected,
            get_product_details=lambda target: ProductDetailsResult(
                candidates=[recommended] if target.value == "r" else [product]
            ),
        )
    )
    response = runner(
        SearchRequest(query="Расскажи подробнее про первый вариант", channel="chat"),
        shown_products=chat_products(result),
    )
    assert response.status == "ok"
    assert response.response_kind == "product_details"
    assert response.product_details.product_id == "r"
    assert response.product_details.url == recommended.url
    assert response.products == []
    relation = result.model_copy(update={"intent": "alternatives"})
    assert [item.product_id for item in chat_products(relation)] == ["r"]


@pytest.mark.parametrize(
    ("query", "expected"),
    [
        ("Подробнее про №2", 2),
        ("Расскажи о третьем варианте", 3),
        ("расскажи про второй вариант поподробнее", 2),
        ("подробнее о четвёртом", 4),
        ("А беспроводную до 5000?", None),
        ("Подбери замену первому", None),
        ("расскажи анекдот про программистов", None),
    ],
)
def test_route_does_not_capture_purchase_refinements(query, expected):
    assert parse_reference(query, [], allow_short=True) == expected


def test_details_use_structured_facts_and_reject_unsafe_links():
    product = Product(
        product_id="p",
        title="Мышь",
        category="Мыши",
        url="https://example.org@other.example/item",
        description="<script>bad()</script><p>Описание мыши</p>",
        attributes={"Интерфейс": ["USB"], "description_fields": {"Цвет корпуса": "Чёрный"}},
    )
    details = make_details(product)
    assert details.url is None
    assert details.summary == "Описание мыши"
    assert [fact.value for fact in details.facts] == ["USB", "Чёрный"]


def test_expired_reference_prompts_for_model():
    result = build_search(demo_services())(
        SearchRequest(query="Расскажи подробнее про первый вариант", channel="chat")
    )
    assert result.status == "clarification_needed"
    assert result.reason_codes == ["detail_reference_expired"]


def test_named_model_can_resolve_outside_chat():
    product = Product(
        product_id="a",
        title="Logitech M190",
        category="Мыши",
        description="Беспроводная мышь.",
        url="https://store.softline.ru/product/",
    )
    result = build_search(
        demo_services(
            get_product_details=lambda target: ProductDetailsResult(candidates=[product]),
        )
    )(SearchRequest(query="Расскажи подробнее про Logitech M190"))
    assert result.status == "ok"
    assert "details:by_name" in result.steps


def test_copied_numbered_card_title_resolves_directly_to_shown_details():
    pad = Product(
        product_id="493337",
        sku="CS-MPC-P07XS",
        title="Коврик для мыши Cactus Owl blue 220x180x2мм (CS-MPC-P07XS)",
        category="Прочие аксессуары",
        brand="Cactus",
        description="Коврик для компьютерной мыши Cactus Owl 220x180x2мм.",
        url="https://store.softline.ru/cactus/cs-mpc-p07xs-493337/",
    )
    shown = chat_products(SearchResponse(request_id="prev", status="ok", products=[pad]))

    def unexpected(*_args):
        raise AssertionError("Copied shown card must resolve without search")

    runner = build_search(
        demo_services(
            understand=unexpected,
            search=unexpected,
            get_product_details=lambda target: ProductDetailsResult(
                candidates=[pad] if target.value == "493337" else []
            ),
        )
    )
    response = runner(
        SearchRequest(
            query="3. Коврик для мыши Cactus Owl blue 220x180x2мм (CS-MPC-P07XS)",
            channel="chat",
        ),
        shown_products=shown,
    )
    assert response.status == "ok"
    assert response.response_kind == "product_details"
    assert response.product_details.product_id == "493337"


def test_unmatched_details_phrase_falls_through_to_understand():
    from smart_search.contracts import QuerySpec, SearchHit, SearchResult

    pad = Product(
        product_id="493337",
        sku="CS-MPC-P07XS",
        title="Коврик для мыши Cactus Owl blue 220x180x2мм (CS-MPC-P07XS)",
        category="Прочие аксессуары",
    )
    shown = chat_products(SearchResponse(request_id="prev", status="ok", products=[pad]))
    seen_contexts: list[str] = []

    def capture_understand(request: SearchRequest) -> QuerySpec:
        seen_contexts.append(request.previous_context or "")
        return QuerySpec(
            raw_query=request.query,
            normalized_query="Cactus Owl blue",
            intent="exact_product",
            sku="CS-MPC-P07XS",
        )

    runner = build_search(
        demo_services(
            understand=capture_understand,
            search=lambda params: SearchResult(hits=[SearchHit(product=pad, rank=1)]),
            get_product_details=lambda target: ProductDetailsResult(candidates=[]),
        )
    )
    response = runner(
        SearchRequest(
            query="расскажи подробнее про тот синий коврик совой",
            channel="chat",
        ),
        shown_products=shown,
    )
    assert response.status == "ok"
    assert response.response_kind == "search"
    assert [item.product_id for item in response.products] == ["493337"]
    assert seen_contexts and "Показанные карточки:" in seen_contexts[0]
    assert "CS-MPC-P07XS" in seen_contexts[0]
