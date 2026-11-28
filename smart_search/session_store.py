"""Async Redis session context storage for anonymous multi-user search sessions."""

from __future__ import annotations

import json
import logging
import os
import re
from datetime import UTC, datetime
from typing import Any

from pydantic import Field, ValidationError
from redis.exceptions import RedisError

from smart_search.contracts import Contract, Intent, SearchResponse, SearchStatus, ShownProduct
from smart_search.product_details import chat_products

LOGGER = logging.getLogger(__name__)
_REDIS_ERRORS = (RedisError, OSError, TimeoutError)

DEFAULT_SESSION_TTL_SECONDS = 86_400  # 24 hours
DEFAULT_MAX_HISTORY_ITEMS = 200
DEFAULT_CONTEXT_WINDOW_ITEMS = 1
DEFAULT_CONTEXT_MAX_CHARS = 800
_CLAIM_ANCHOR_RE = re.compile(r"«([^»]+)»")


def _turn_titles(response: SearchResponse) -> list[str]:
    if response.products:
        return [product.title for product in response.products[:3]]
    if response.intent == "complements" and response.recommendations:
        anchors: list[str] = []
        for rec in response.recommendations:
            match = _CLAIM_ANCHOR_RE.search(rec.claim)
            if match and match.group(1) not in anchors:
                anchors.append(match.group(1))
        if anchors:
            return anchors[:3]
    return [rec.product.title for rec in response.recommendations[:3]]


class SessionTurn(Contract):
    timestamp: datetime
    query: str
    intent: Intent | None = None
    status: SearchStatus
    product_count: int = Field(ge=0)
    top_titles: list[str] = Field(default_factory=list)
    clarifying_question: str | None = None


class SessionHistoryResponse(Contract):
    session_id: str
    ttl_seconds: int = Field(ge=0)
    turns: list[SessionTurn] = Field(default_factory=list)


class StoredSessionTurn(SessionTurn):
    request_id: str | None = None
    shown_products: list[ShownProduct] = Field(default_factory=list, max_length=3)


def _redis_url() -> str:
    return os.getenv("REDIS_URL", "").strip()


def _session_ttl() -> int:
    raw = os.getenv("REDIS_SESSION_TTL_SECONDS", str(DEFAULT_SESSION_TTL_SECONDS)).strip()
    try:
        value = int(raw)
        return value if value > 0 else DEFAULT_SESSION_TTL_SECONDS
    except ValueError:
        return DEFAULT_SESSION_TTL_SECONDS


def _session_key(session_id: str) -> str:
    return f"smart_search:session:{session_id}"


def format_context(
    turns: list[SessionTurn],
    *,
    max_items: int = DEFAULT_CONTEXT_WINDOW_ITEMS,
    max_chars: int = DEFAULT_CONTEXT_MAX_CHARS,
) -> str | None:
    """Build a compact dialogue summary from recent session turns for DeepSeek."""
    if not turns:
        return None
    recent = turns[-max_items:]
    lines: list[str] = []
    for turn in recent:
        parts = [f"Предыдущий запрос: {turn.query}"]
        if turn.clarifying_question:
            parts.append(f"Уточнение: {turn.clarifying_question}")
        elif turn.top_titles:
            parts.append(f"Найдено: {', '.join(turn.top_titles[:2])}")
        lines.append(" | ".join(parts))
    combined = "\n".join(lines)
    if len(combined) > max_chars:
        combined = combined[-max_chars:]
    return combined


class RedisSessionStore:
    """Graceful async Redis wrapper; no-ops cleanly when REDIS_URL is unset or unreachable."""

    def __init__(self, url: str | None = None, *, ttl_seconds: int | None = None) -> None:
        self._explicit_url = url
        self._explicit_ttl = ttl_seconds
        self._client: Any = None
        self._client_url: str = ""

    @property
    def ttl_seconds(self) -> int:
        return self._explicit_ttl if self._explicit_ttl is not None else _session_ttl()

    def _active_url(self) -> str:
        return self._explicit_url if self._explicit_url is not None else _redis_url()

    def _get_client(self):
        url = self._active_url()
        if not url:
            return None
        if self._client is not None and self._client_url == url:
            return self._client
        import redis.asyncio as aioredis

        self._client = aioredis.from_url(
            url,
            encoding="utf-8",
            decode_responses=True,
            socket_connect_timeout=2.0,
            socket_timeout=2.0,
        )
        self._client_url = url
        return self._client

    async def aclose(self) -> None:
        if self._client is not None:
            try:
                await self._client.aclose()
            except _REDIS_ERRORS as exc:
                LOGGER.debug("Session client close failed (%s)", type(exc).__name__)
            self._client = None
            self._client_url = ""

    async def get_history(self, session_id: str) -> list[SessionTurn]:
        client = self._get_client()
        if client is None or not session_id.strip():
            return []
        try:
            raw_items = await client.lrange(_session_key(session_id), 0, -1)
        except _REDIS_ERRORS as exc:
            LOGGER.warning("Session read failed (%s)", type(exc).__name__)
            return []
        turns: list[SessionTurn] = []
        for raw in raw_items:
            try:
                turn = StoredSessionTurn.model_validate_json(raw)
                turns.append(
                    SessionTurn.model_validate(
                        turn.model_dump(exclude={"request_id", "shown_products"})
                    )
                )
            except (ValidationError, ValueError):
                LOGGER.debug("Skipping malformed session turn for session_id=%s", session_id)
        return turns

    async def find_chat_snapshot(
        self, session_id: str, reference_request_id: str
    ) -> list[ShownProduct]:
        client = self._get_client()
        if client is None or not session_id.strip():
            return []
        try:
            raw_items = await client.lrange(_session_key(session_id), -20, -1)
        except _REDIS_ERRORS as exc:
            raise ConnectionError("chat_snapshot_unavailable") from exc
        matches = []
        for raw in raw_items:
            try:
                turn = StoredSessionTurn.model_validate_json(raw)
            except ValueError:
                continue
            if turn.request_id == reference_request_id and turn.shown_products:
                matches.append(turn.shown_products)
        return matches[0] if len(matches) == 1 else []

    async def get_context(self, session_id: str, *, query: str | None = None) -> str | None:
        # DeepSeek decides semantically whether the new turn continues or switches topics.
        del query
        turns = await self.get_history(session_id)
        if not turns:
            return None
        window = DEFAULT_CONTEXT_WINDOW_ITEMS
        if len(turns) >= 2 and not turns[-1].top_titles and not turns[-1].clarifying_question:
            window = max(window, 2)
        return format_context(turns, max_items=window)

    async def append_turn(
        self,
        session_id: str,
        query: str,
        response: SearchResponse,
    ) -> None:
        client = self._get_client()
        if client is None or not session_id.strip():
            return
        turn = StoredSessionTurn(
            timestamp=datetime.now(UTC),
            query=query,
            intent=response.intent,
            status=response.status,
            product_count=len(response.products),
            top_titles=_turn_titles(response),
            clarifying_question=response.clarifying_question,
            request_id=response.request_id if response.chat_product_ids else None,
            shown_products=chat_products(response) if response.chat_product_ids else [],
        )
        key = _session_key(session_id)
        payload = json.dumps(turn.model_dump(mode="json"), ensure_ascii=False)
        try:
            pipe = client.pipeline(transaction=True)
            pipe.rpush(key, payload)
            pipe.ltrim(key, -DEFAULT_MAX_HISTORY_ITEMS, -1)
            pipe.expire(key, self.ttl_seconds)
            await pipe.execute()
        except _REDIS_ERRORS as exc:
            LOGGER.warning("Session write failed (%s)", type(exc).__name__)

    async def clear(self, session_id: str) -> bool:
        client = self._get_client()
        if client is None or not session_id.strip():
            return False
        try:
            deleted = await client.delete(_session_key(session_id))
            return bool(deleted)
        except _REDIS_ERRORS as exc:
            LOGGER.warning("Session clear failed (%s)", type(exc).__name__)
            return False


session_store = RedisSessionStore()
