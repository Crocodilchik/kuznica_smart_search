import json

import httpx
import pytest
from fastapi.testclient import TestClient

from smart_search import catalog_backend
from smart_search.events import publisher
from smart_search.interfaces.api import app
from smart_search.llm import DeepSeekProvider, LLMConfigurationError, LLMFactory
from smart_search.runtime import _configured_runner
from softline_search.catalog import Catalog, build_catalog
from softline_search.retrieval import build_index


@pytest.fixture
def pipeline(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    catalog = tmp_path / "catalog"
    index = tmp_path / "qdrant"
    for key, value in {
        "SMART_SEARCH_MODE": "prod",
        "SMART_SEARCH_BACKEND_MODULE": "smart_search.backend",
        "SMART_SEARCH_RETRIEVAL_MODE": "bm25",
        "SMART_SEARCH_RECOMMENDATION_PLANNER": "graph",
        "SOFTLINE_CATALOG_DIR": str(catalog),
        "SOFTLINE_INDEX_DIR": str(index),
        "QDRANT_URL": "",
        "QDRANT_API_KEY": "",
        "SOFTLINE_DENSE_MODEL": "",
        "LLM_PROVIDER": "mock",
    }.items():
        monkeypatch.setenv(key, value)
    feed = tmp_path / "feed.xml"
    feed.write_text(
        """<yml_catalog date="2026-11-25"><shop><categories>
        <category id="82080">ИБП</category><category id="82014">Сменные батареи для ИБП</category>
        </categories><offers>
        <offer id="10" available="true"><name>ИБП Alpha</name><categoryId>82080</categoryId>
        <url>https://store.example/10</url><vendorCode>UPS-A10</vendorCode></offer>
        <offer id="20" available="true"><name>Сменная батарея Plus</name><categoryId>82014</categoryId>
        <url>https://store.example/20</url><param name="Совместимые модели">UPS-A10</param></offer>
        <offer id="30" available="true"><name>Сменная батарея Basic</name><categoryId>82014</categoryId>
        <url>https://store.example/30</url><description>Поддерживает только ИБП Beta.</description></offer>
        </offers></shop></yml_catalog>""",
        encoding="utf-8",
    )
    build_catalog(feed, catalog, tmp_path / "reports")
    build_index(Catalog(catalog), index)
    events = []

    class Recorder:
        publish = staticmethod(events.append)

    monkeypatch.setattr(publisher, "broker_event_publisher", Recorder)
    _configured_runner.cache_clear()
    catalog_backend.close()
    try:
        with TestClient(app) as client:
            yield client, events
    finally:
        catalog_backend.close()
        _configured_runner.cache_clear()


def test_relation_by_sku_reaches_batch_evidence_validation_and_analytics(pipeline):
    client, events = pipeline
    assert client.get("/health/ready").status_code == 200
    response = client.post("/v1/search", json={"query": "Что докупить к UPS-A10?"})
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "partial" and body["retry_count"] == 0
    assert "search:sku" in body["steps"] and "research" in body["steps"]
    assert [r["product"]["product_id"] for r in body["recommendations"]] == ["20"]
    evidence = body["recommendations"][0]["evidence"][0]
    assert evidence["catalog_field"] == "20.attributes.Совместимые модели"
    assert evidence["excerpt"] == "UPS-A10"
    assert "evidence_unconfirmed" in body["reason_codes"]
    assert len(events) == 1
    assert events[0].request_id == body["request_id"] and events[0].recommendation_count == 1


def test_structured_provider_plans_catalog_search_and_research_checks_quotes(pipeline, monkeypatch):
    client, _ = pipeline
    monkeypatch.setenv("SMART_SEARCH_RECOMMENDATION_PLANNER", "llm")
    calls = []

    def reply(request):
        messages = json.loads(request.content)["messages"]
        user = json.loads(messages[-1]["content"])
        if "directions" in messages[0]["content"] and "anchors" in user:
            calls.append("plan")
            output = {
                "directions": [
                    {
                        "anchor_id": "10",
                        "category_id": "82014",
                        "query": "батарея",
                        "title_terms": ["батарея"],
                        "reason": "Проверить замену батареи.",
                    }
                ]
            }
        else:
            calls.append("research")
            assert user["pairs"][0]["product_id"] == "30"
            output = {
                "checks": [
                    {
                        "anchor_id": "10",
                        "product_id": "30",
                        "status": "confirmed",
                        "quotes": [
                            {"product_id": "10", "field": "title", "excerpt": "ИБП Alpha"},
                            {
                                "product_id": "30",
                                "field": "description",
                                "excerpt": "Вымышленная цитата",
                            },
                        ],
                    }
                ]
            }
        return httpx.Response(200, json={"choices": [{"message": {"content": json.dumps(output)}}]})

    def provider():
        return DeepSeekProvider(
            api_key="test",
            base_url="https://model.invalid",
            model="test",
            http_client=httpx.AsyncClient(transport=httpx.MockTransport(reply)),
        )

    monkeypatch.setattr(LLMFactory, "create", staticmethod(provider))
    response = client.post("/v1/search", json={"query": "Что докупить к UPS-A10?"})
    assert response.status_code == 200
    assert [r["product"]["product_id"] for r in response.json()["recommendations"]] == ["20"]
    assert {"plan", "research"} == set(calls)


def test_missing_model_credentials_preserve_verified_catalog_pair(pipeline, monkeypatch):
    client, _ = pipeline
    monkeypatch.setenv("SMART_SEARCH_RECOMMENDATION_PLANNER", "llm")

    def missing_credentials():
        raise LLMConfigurationError("missing credentials")

    monkeypatch.setattr(LLMFactory, "create", staticmethod(missing_credentials))
    response = client.post("/v1/search", json={"query": "Что докупить к UPS-A10?"})
    assert response.status_code == 200
    result = response.json()
    assert "recommendation_planner_fallback" in result["warnings"]
    assert "research_unavailable" in result["warnings"]
    assert [r["product"]["product_id"] for r in result["recommendations"]] == ["20"]
