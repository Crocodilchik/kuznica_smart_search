from __future__ import annotations

from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient

from smart_search.contracts import Product, Recommendation, SearchResponse
from smart_search.interfaces import api as api_module
from smart_search.session_store import (
    RedisSessionStore,
    SessionTurn,
    format_context,
    is_followup_query,
)


class FakePipeline:
    def __init__(self, backend: FakeAsyncRedis):
        self.backend = backend
        self.ops: list[tuple] = []

    def rpush(self, key: str, value: str):
        self.ops.append(("rpush", key, value))
        return self

    def ltrim(self, key: str, start: int, end: int):
        self.ops.append(("ltrim", key, start, end))
        return self

    def expire(self, key: str, ttl: int):
        self.ops.append(("expire", key, ttl))
        return self

    async def execute(self):
        for op in self.ops:
            kind = op[0]
            if kind == "rpush":
                self.backend.lists.setdefault(op[1], []).append(op[2])
            elif kind == "ltrim":
                items = self.backend.lists.get(op[1], [])
                self.backend.lists[op[1]] = items[op[2] :]
            elif kind == "expire":
                self.backend.ttls[op[1]] = op[2]
        return [True] * len(self.ops)


class FakeAsyncRedis:
    def __init__(self):
        self.lists: dict[str, list[str]] = {}
        self.ttls: dict[str, int] = {}

    def pipeline(self, transaction: bool = True):
        return FakePipeline(self)

    async def lrange(self, key: str, start: int, end: int):
        items = self.lists.get(key, [])
        return items[start:] if end == -1 else items[start : end + 1]

    async def delete(self, key: str):
        existed = key in self.lists
        self.lists.pop(key, None)
        self.ttls.pop(key, None)
        return 1 if existed else 0

    async def aclose(self):
        pass


@pytest.mark.asyncio
async def test_redis_session_store_persists_full_24h_history_and_clears():
    fake = FakeAsyncRedis()
    store = RedisSessionStore("redis://fake:6379/0", ttl_seconds=86_400)
    store._client = fake
    store._client_url = "redis://fake:6379/0"

    resp1 = SearchResponse(
        request_id="r1",
        status="ok",
        intent="product_search",
        products=[Product(product_id="p1", title="Мышь игровая RGB", category="Мыши")],
    )
    resp2 = SearchResponse(
        request_id="r2",
        status="ok",
        intent="product_search",
        products=[Product(product_id="p2", title="Мышь беспроводная", category="Мыши")],
    )

    await store.append_turn("sess-1", "хочу мышку до 10 тыс", resp1)
    await store.append_turn("sess-1", "а покажи беспроводную", resp2)

    history = await store.get_history("sess-1")
    assert len(history) == 2
    assert [t.query for t in history] == ["хочу мышку до 10 тыс", "а покажи беспроводную"]
    assert fake.ttls["smart_search:session:sess-1"] == 86_400

    ctx = await store.get_context("sess-1")
    assert ctx is not None and "а покажи беспроводную" in ctx and "Мышь беспроводная" in ctx
    assert await store.get_context("sess-1", query="мышь") is None
    assert await store.get_context("sess-1", query="а до 5к?") is not None

    assert await store.clear("sess-1") is True
    assert await store.get_history("sess-1") == []


def test_is_followup_query_distinguishes_refinements_from_new_products():
    prev_ok = SessionTurn(
        timestamp=datetime.now(UTC),
        query="Антивирь для винды на 50 компов до 100к",
        status="ok",
        product_count=12,
    )
    prev_clarify = SessionTurn(
        timestamp=datetime.now(UTC),
        query="вава",
        status="clarification_needed",
        product_count=0,
        clarifying_question="Уточните, что ищете?",
    )
    assert is_followup_query("а под Linux?", prev_ok) is True
    assert is_followup_query("подешевле", prev_ok) is True
    assert is_followup_query("беспроводную", prev_ok) is True
    assert is_followup_query("к нему", prev_ok) is True
    assert is_followup_query("27 дюймов", prev_ok) is True
    assert is_followup_query("750 ватт", prev_ok) is True
    assert is_followup_query("нет, давай до 95 тысяч", prev_ok) is True
    assert is_followup_query("ладно, до 50 тысяч", prev_ok) is True
    assert is_followup_query("ок, покажи в наличии", prev_ok) is True
    assert is_followup_query("хорошо, а подешевле", prev_ok) is True
    assert is_followup_query("не принтер, а сканер до 30 тысяч", prev_ok) is True
    assert is_followup_query("за 50 тысяч", prev_ok) is True
    assert is_followup_query("мышь", prev_ok) is False
    assert is_followup_query("красная клавиатура", prev_ok) is False
    assert is_followup_query("нетбук", prev_ok) is False
    assert is_followup_query("мышка", prev_clarify) is True


@pytest.mark.asyncio
async def test_session_store_is_noop_without_redis_url(monkeypatch):
    monkeypatch.delenv("REDIS_URL", raising=False)
    store = RedisSessionStore()
    assert await store.get_history("sess-x") == []
    assert await store.get_context("sess-x") is None
    assert await store.clear("sess-x") is False


def test_format_context_caps_window_for_long_sessions():
    turns = [
        SessionTurn(
            timestamp=datetime.now(UTC),
            query=f"запрос номер {i}",
            status="ok",
            product_count=4,
            top_titles=[f"Товар {i}"],
        )
        for i in range(25)
    ]
    ctx = format_context(turns, max_items=8, max_chars=1200)
    assert ctx is not None
    assert "запрос номер 24" in ctx
    assert "запрос номер 0" not in ctx
    assert len(ctx) <= 1200


def test_api_hydrates_previous_context_and_exposes_session_endpoints(prod_backend, monkeypatch):
    fake = FakeAsyncRedis()
    store = RedisSessionStore("redis://fake:6379/0", ttl_seconds=86_400)
    store._client = fake
    store._client_url = "redis://fake:6379/0"
    monkeypatch.setattr(api_module, "session_store", store)

    seen_contexts: list[str | None] = []
    original_understand = prod_backend.understand

    def spy_understand(req):
        seen_contexts.append(req.previous_context)
        return original_understand(req)

    monkeypatch.setattr(prod_backend, "understand", spy_understand)

    with TestClient(api_module.app) as client:
        r1 = client.post(
            "/v1/search",
            json={"query": "Монитор 27 дюймов", "session_id": "anon-42"},
        )
        assert r1.status_code == 200
        assert seen_contexts[-1] is None

        r2 = client.post(
            "/v1/search",
            json={"query": "А теперь дешевле", "session_id": "anon-42"},
        )
        assert r2.status_code == 200
        assert seen_contexts[-1] is not None and "Монитор 27 дюймов" in seen_contexts[-1]

        r3 = client.post(
            "/v1/search",
            json={"query": "мышь", "session_id": "anon-42"},
        )
        assert r3.status_code == 200
        assert seen_contexts[-1] is None

        hist = client.get("/v1/sessions/anon-42")
        assert hist.status_code == 200
        body = hist.json()
        assert body["ttl_seconds"] == 86_400
        assert len(body["turns"]) == 3

        cleared = client.delete("/v1/sessions/anon-42")
        assert cleared.status_code == 200 and cleared.json()["cleared"] is True
        assert client.get("/v1/sessions/anon-42").json()["turns"] == []


def test_chat_details_follow_the_referenced_list_without_crossing_sessions(
    prod_backend, monkeypatch
):
    from smart_search.contracts import ProductDetailsResult

    fake = FakeAsyncRedis()
    store = RedisSessionStore("redis://fake:6379/0")
    store._client, store._client_url = fake, "redis://fake:6379/0"
    monkeypatch.setattr(api_module, "session_store", store)
    calls = []

    def get_details(target):
        calls.append(target.value)
        return ProductDetailsResult(
            candidates=[
                Product(
                    product_id=target.value,
                    title="Монитор для переговорной",
                    category="мониторы",
                    description="Монитор для рабочего пространства.",
                    url="https://store.softline.ru/monitor/",
                )
            ]
        )

    monkeypatch.setattr(prod_backend, "get_product_details", get_details, raising=False)
    with TestClient(api_module.app) as client:
        context = {"channel": "chat", "session_id": "a"}
        listing = client.post("/v1/search", json={**context, "query": "Нужен монитор"}).json()
        assert listing["chat_product_ids"] == ["catalog-monitor"]
        query = {
            **context,
            "query": "Расскажи подробнее про первый вариант",
            "reference_request_id": listing["request_id"],
            "previous_context": "текст из браузера",
        }
        details = client.post("/v1/search", json=query).json()
        assert details["status"] == "ok"
        assert details["product_details"]["product_id"] == "catalog-monitor"
        other = client.post("/v1/search", json={**query, "session_id": "b"}).json()
        assert other["reason_codes"] == ["detail_reference_expired"]
        assert calls == ["catalog-monitor"]
        assert len(client.get("/v1/sessions/a").json()["turns"]) == 1


@pytest.mark.asyncio
async def test_session_store_preserves_complement_anchor_when_products_empty():
    fake = FakeAsyncRedis()
    store = RedisSessionStore("redis://fake:6379/0", ttl_seconds=86_400)
    store._client = fake
    store._client_url = "redis://fake:6379/0"

    comp_resp = SearchResponse(
        request_id="r-comp",
        status="partial",
        intent="complements",
        products=[],
        recommendations=[
            Recommendation(
                anchor_id="504964",
                product=Product(product_id="80456", title="Buro BHP HDMI 2", category="Кабели"),
                type="cross_sell",
                claim="Совместим с товаром «Монитор AOC 24B30HM2 23.8-inch черный».",
            )
        ],
    )
    await store.append_turn("sess-anc", "Монитор AOC 24B30HM2 что посоветуешь", comp_resp)
    ctx = await store.get_context("sess-anc", query="а аналоги ему до 10к?")
    assert ctx is not None
    assert "Монитор AOC 24B30HM2 23.8-inch черный" in ctx
