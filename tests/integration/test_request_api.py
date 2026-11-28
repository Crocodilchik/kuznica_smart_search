from fastapi.testclient import TestClient

from smart_search.analytics import local_event_store
from smart_search.contracts import SearchRequest
from smart_search.events import publisher
from smart_search.interfaces.api import app
from smart_search.runtime import run_search


def test_post_search_accepts_free_text_and_separates_analytics(prod_backend, monkeypatch):
    class RecordingPublisher:
        def __init__(self):
            self.events = []

        def publish(self, event):
            self.events.append(event)

    broker = RecordingPublisher()
    monkeypatch.setattr(publisher, "broker_event_publisher", lambda: broker)

    client = TestClient(app)
    query = "  Монитор 27 дюймов для переговорной  "
    response = client.post("/v1/search", json={"query": query, "request_id": "client-7"})

    assert response.status_code == 200
    body = response.json()
    assert body["request_id"] == "client-7"
    assert body["status"] == "partial"
    assert [item["product_id"] for item in body["products"]] == ["catalog-monitor"]
    assert prod_backend.received == [query.strip()]
    assert [(event.request_id, event.status) for event in broker.events] == [
        ("client-7", "partial")
    ]
    assert not local_event_store().path.exists()

    monkeypatch.setenv("SMART_SEARCH_MODE", "test")
    assert local_event_store().read() == []
    assert run_search(SearchRequest(query="Артикул: 458218")).status == "ok"
    assert len(local_event_store().read()) == 1
    assert len(broker.events) == 1
    assert prod_backend.received == [query.strip()]


def test_api_rejects_invalid_request_without_calling_backend(prod_backend):
    client = TestClient(app)
    for payload in (
        {"query": "  "},
        {"query": "x" * 1001},
        {"query": "Сервер", "mode": "test"},
    ):
        assert client.post("/v1/search", json=payload).status_code == 422
    assert prod_backend.received == []


def test_api_reports_backend_unavailable_without_using_demo(prod_backend):
    prod_backend.available = False
    response = TestClient(app).post("/v1/search", json={"query": "Что понадобится?"})

    assert response.status_code == 503
    assert response.json()["status"] == "unavailable"
    assert response.json()["products"] == []
    assert "search_unavailable" in response.json()["warnings"]


def test_broker_misconfiguration_keeps_search_result_without_local_fallback(prod_backend, caplog):
    response = TestClient(app).post("/v1/search", json={"query": "Монитор"})

    assert response.status_code == 200
    assert response.json()["products"][0]["product_id"] == "catalog-monitor"
    assert "broker_not_configured" in caplog.text
    assert not local_event_store().path.exists()


def test_api_requires_prod_mode_and_configured_backend(monkeypatch):
    client = TestClient(app)
    monkeypatch.setenv("SMART_SEARCH_MODE", "test")
    assert (
        client.post("/v1/search", json={"query": "Сервер"}).json()["code"] == "prod_mode_required"
    )

    monkeypatch.setenv("SMART_SEARCH_MODE", "prod")
    monkeypatch.delenv("SMART_SEARCH_BACKEND_MODULE", raising=False)
    response = client.get("/health/ready")
    assert response.status_code == 503
    assert response.json()["code"] == "backend_not_configured"
    response = client.post("/v1/search", json={"query": "Артикул: 458218"})
    assert response.status_code == 503
    assert response.json()["code"] == "backend_not_configured"


def test_readiness_checks_backend_contract_without_running_search(prod_backend):
    client = TestClient(app)
    assert client.get("/health/ready").json() == {"status": "ready", "mode": "prod"}
    assert prod_backend.received == []


def test_readiness_rejects_backend_file_with_missing_tools(tmp_path, monkeypatch):
    module_name = "incomplete_search_backend"
    (tmp_path / f"{module_name}.py").write_text(
        "from smart_search.modes.demo import search_demo as search\n", encoding="utf-8"
    )
    monkeypatch.syspath_prepend(str(tmp_path))
    monkeypatch.setenv("SMART_SEARCH_MODE", "prod")
    monkeypatch.setenv("SMART_SEARCH_BACKEND_MODULE", module_name)

    response = TestClient(app).get("/health/ready")

    assert response.status_code == 503
    assert response.json()["code"] == "backend_load_failed"
