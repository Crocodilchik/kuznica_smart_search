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


@pytest.mark.parametrize(
    ("query", "expected"),
    [
        ("Подробнее про №2", 2),
        ("Расскажи о третьем варианте", 3),
        ("подробнее о четвёртом", 4),
        ("А беспроводную до 5000?", None),
        ("Подбери замену первому", None),
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
