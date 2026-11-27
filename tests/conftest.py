import sys
from types import ModuleType

import pytest

from smart_search.contracts import Product, QuerySpec, SearchHit, SearchResult
from smart_search.events.kafka import broker_event_publisher
from smart_search.modes.demo import demo_services
from smart_search.runtime import _configured_runner


@pytest.fixture
def prod_backend(tmp_path, monkeypatch):
    backend = ModuleType("test_catalog_backend")
    backend.received = []
    backend.available = True

    def understand(request):
        backend.received.append(request.query)
        return QuerySpec(raw_query=request.query, normalized_query=request.query)

    def search(_params):
        hits = [
            SearchHit(
                product=Product(
                    product_id="catalog-monitor",
                    title="Монитор для переговорной",
                    category="мониторы",
                ),
                rank=1,
            )
        ]
        if backend.available:
            return SearchResult(hits=hits)
        return SearchResult(available=False, warnings=["search_unavailable"])

    services = demo_services(understand=understand, search=search)
    for name in ("understand", "search", "recommend", "research", "validate"):
        setattr(backend, name, getattr(services, name))

    _configured_runner.cache_clear()
    broker_event_publisher.cache_clear()
    monkeypatch.setitem(sys.modules, backend.__name__, backend)
    monkeypatch.setenv("SMART_SEARCH_MODE", "prod")
    monkeypatch.setenv("SMART_SEARCH_BACKEND_MODULE", backend.__name__)
    monkeypatch.delenv("SMART_SEARCH_API_URL", raising=False)
    monkeypatch.setenv("SMART_SEARCH_EVENT_DB", str(tmp_path / "test-events.sqlite3"))
    monkeypatch.delenv("SMART_SEARCH_KAFKA_BOOTSTRAP_SERVERS", raising=False)
    yield backend
    _configured_runner.cache_clear()
    broker_event_publisher.cache_clear()
