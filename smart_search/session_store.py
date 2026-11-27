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

from smart_search.contracts import Contract, Intent, SearchResponse, SearchStatus

LOGGER = logging.getLogger(__name__)
_REDIS_ERRORS = (RedisError, OSError, TimeoutError)

DEFAULT_SESSION_TTL_SECONDS = 86_400  # 24 hours
DEFAULT_MAX_HISTORY_ITEMS = 200
DEFAULT_CONTEXT_WINDOW_ITEMS = 1
DEFAULT_CONTEXT_MAX_CHARS = 500

_FOLLOWUP_START_RE = re.compile(
    r"^(?:а|и|но|тогда|теперь|давай(?:те)?|лучше|желательно|можно|ещ[её]|или|либо|только|до|от|не\s+дороже|не\s+дешевле|в\s+пределах|бюджет\w*|подешевле|дешевле|подороже|дороже|под|для|на|с|без|в\s+наличии)\b|^нет(?=\s*(?:,|давай|лучше|покаж|нуж))",
    re.IGNORECASE,
)
_FOLLOWUP_ANAPHORA_RE = re.compile(
    r"\b(?:к\s+нему|к\s+ней|для\s+него|для\s+не[её]|вместо\s+него|вместо\s+не[её]|к\s+этом\w*|от\s+не[её]|этого|такой\s+же|такую\s+же|такие\s+же)\b",
    re.IGNORECASE,
)
_FOLLOWUP_SINGLE_ADJ_RE = re.compile(
    r"^(?:беспроводн\w+|проводн\w+|игров\w+|офисн\w+|красн\w+|ч[её]рн\w+|бел\w+|сер\w+|российск\w+|отечественн\w+|платинов\w+|золот\w+|бронзов\w+|титанов\w+|rgb|\d+\s*(?:к|k|тыс\w*|руб\w*|₽)?)$",
    re.IGNORECASE,
)
_FOLLOWUP_MEASURE_RE = re.compile(
    r"^\d+(?:[.,]\d+)?\s*(?:дюйм\w*|\"|''|вт|w|ватт\w*|гб|тб|мб|гц|порт\w*|шт\.?|штук\w*|к|k|тыс\w*|млн\.?|руб\w*|₽)(?:\s+(?:и\s+выше|и\s+больше|и\s+меньше|минимум|максимум|в\s+наличии))?$",
    re.IGNORECASE,
)


def is_followup_query(query: str, last_turn: SessionTurn | None) -> bool:
    """Return True only when the new query refines or answers the previous turn."""
    if last_turn is None:
        return False
    if last_turn.status == "clarification_needed" or last_turn.clarifying_question:
        return True
    cleaned = query.strip()
    if not cleaned:
        return False
    if _FOLLOWUP_START_RE.search(cleaned) or _FOLLOWUP_ANAPHORA_RE.search(cleaned):
        return True
    return bool(
        _FOLLOWUP_SINGLE_ADJ_RE.fullmatch(cleaned) or _FOLLOWUP_MEASURE_RE.fullmatch(cleaned)
    )


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
                turns.append(SessionTurn.model_validate_json(raw))
            except (ValidationError, ValueError):
                LOGGER.debug("Skipping malformed session turn for session_id=%s", session_id)
        return turns

    async def get_context(self, session_id: str, *, query: str | None = None) -> str | None:
        turns = await self.get_history(session_id)
        if not turns:
            return None
        if query is not None and not is_followup_query(query, turns[-1]):
            return None
        return format_context(turns)

    async def append_turn(
        self,
        session_id: str,
        query: str,
        response: SearchResponse,
    ) -> None:
        client = self._get_client()
        if client is None or not session_id.strip():
            return
        turn = SessionTurn(
            timestamp=datetime.now(UTC),
            query=query,
            intent=response.intent,
            status=response.status,
            product_count=len(response.products),
            top_titles=[p.title for p in response.products[:3]],
            clarifying_question=response.clarifying_question,
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
