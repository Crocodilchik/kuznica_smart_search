"""Tests for multi-direction recommendation planning and interface-based catalog research."""

from src.contracts import Product, RecommendationDraft
from src.recommendation import Plan
from src.research import Batch, Check, Quote, research_batch


def test_plan_allows_up_to_four_directions():
    plan = Plan.model_validate(
        {
            "directions": [
                {
                    "anchor_id": "1",
                    "category_id": f"c{i}",
                    "query": f"item {i}",
                    "title_terms": [f"item{i}"],
                    "reason": "Compatible accessory",
                }
                for i in range(4)
            ]
        }
    )
    assert len(plan.directions) == 4


def test_research_batch_confirms_matching_standard_interfaces():
    anchor = Product(
        product_id="mon-1",
        title="Монитор MSI 272QPF E20 27.0-inch черный",
        category="Мониторы",
        url="https://store.example/mon-1",
        attributes={"Интерфейсы": ["HDMI", "DisplayPort", "Audio Mini-jack 3.5 мм"]},
    )
    cable = Product(
        product_id="cab-1",
        title="Кабель Ningbo DisplayPort (m) DisplayPort (m) 3м",
        category="Кабели",
        url="https://store.example/cab-1",
    )
    draft = RecommendationDraft(
        anchor_id="mon-1",
        product=cable,
        type="cross_sell",
        claim="Совместим с товаром «Монитор MSI 272QPF E20 27.0-inch черный».",
    )

    def fake_complete_json(system_prompt, user_prompt, response_model):
        assert "DisplayPort" in user_prompt
        return Batch(
            checks=[
                Check(
                    anchor_id="mon-1",
                    product_id="cab-1",
                    status="confirmed",
                    quotes=[
                        Quote(
                            product_id="mon-1",
                            field="attributes.Интерфейсы",
                            excerpt="DisplayPort",
                        ),
                        Quote(
                            product_id="cab-1",
                            field="title",
                            excerpt="DisplayPort",
                        ),
                    ],
                )
            ]
        )

    results = research_batch(
        [draft],
        anchors=[anchor],
        query="посоветуй что нибудь к MSI 272QPF E20",
        complete_json=fake_complete_json,
    )
    assert len(results) == 1
    assert results[0].status == "confirmed"
    assert [e.catalog_field for e in results[0].evidence] == [
        "mon-1.attributes.Интерфейсы",
        "cab-1.title",
    ]
