import pytest

from smart_search.analytics import EventStore, summarize
from smart_search.contracts import SearchEvent


def event(request_id, status, *, latency=10, reasons=None):
    return SearchEvent(
        request_id=request_id,
        normalized_query="защита сервера",
        intent="product_search",
        status=status,
        result_count=0 if status == "empty" else 1,
        latency_ms=latency,
        reason_codes=reasons or [],
        category="безопасность",
    )


def test_store_keeps_first_delivery_across_instances(tmp_path):
    path = tmp_path / "events.sqlite3"
    first = event("first", "empty", reasons=["offer_not_found"])
    EventStore(path).record(first)
    EventStore(path).record(event("first", "ok"))

    assert EventStore(path).read() == [first]


def test_summary_separates_empty_search_from_technical_failure():
    report = summarize(
        [
            event("1", "empty", latency=10, reasons=["offer_not_found"]),
            event("2", "partial", latency=20, reasons=["offer_not_found"]),
            event("3", "unavailable", latency=30, reasons=["search_unavailable"]),
            event("4", "ok", latency=100),
        ]
    )

    assert report.status_counts == {"empty": 1, "ok": 1, "partial": 1, "unavailable": 1}
    assert report.empty_rate == pytest.approx(1 / 3)
    assert report.p95_latency_ms == 100
    assert report.problem_categories == {"безопасность": 2}
    assert report.reason_counts["offer_not_found"] == 2
