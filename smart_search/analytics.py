"""Durable, idempotent storage and a summary for the demo."""

import sqlite3
from collections import Counter
from collections.abc import Iterable
from contextlib import closing
from math import ceil
from os import getenv
from pathlib import Path

from smart_search.contracts import AnalyticsReport, SearchEvent

DEMO_EVENTS = Path(__file__).resolve().parents[1] / ".local" / "events.sqlite3"


class EventStore:
    def __init__(self, path: str | Path):
        self.path = Path(path)

    def record(self, event: SearchEvent) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with closing(sqlite3.connect(self.path)) as connection, connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS search_events (
                    request_id TEXT PRIMARY KEY,
                    payload TEXT NOT NULL
                )
                """
            )
            connection.execute(
                "INSERT INTO search_events VALUES (?, ?) ON CONFLICT(request_id) DO NOTHING",
                (event.request_id, event.model_dump_json()),
            )

    def read(self) -> list[SearchEvent]:
        if not self.path.is_file():
            return []
        with closing(sqlite3.connect(self.path)) as connection:
            table = connection.execute(
                "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'search_events'"
            ).fetchone()
            if table is None:
                return []
            rows = connection.execute("SELECT payload FROM search_events ORDER BY rowid")
            return [SearchEvent.model_validate_json(payload) for (payload,) in rows]


def local_event_store() -> EventStore:
    return EventStore(getenv("SMART_SEARCH_EVENT_DB", str(DEMO_EVENTS)))


def percentile_95(values: Iterable[float]) -> float | None:
    ordered = sorted(values)
    if not ordered:
        return None
    return ordered[ceil(0.95 * len(ordered)) - 1]


def summarize(events: Iterable[SearchEvent]) -> AnalyticsReport:
    events = list(events)
    statuses = Counter(event.status for event in events)
    completed_searches = statuses["ok"] + statuses["partial"] + statuses["empty"]
    return AnalyticsReport(
        total_searches=len(events),
        empty_rate=statuses["empty"] / completed_searches if completed_searches else 0.0,
        p95_latency_ms=percentile_95(event.latency_ms for event in events),
        status_counts=dict(statuses),
        problem_categories=dict(
            Counter(
                event.category or "Без категории"
                for event in events
                if event.status in {"empty", "partial"}
            ).most_common()
        ),
        reason_counts=dict(
            Counter(
                reason for event in events for reason in dict.fromkeys(event.reason_codes)
            ).most_common()
        ),
    )
