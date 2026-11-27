"""Production HTTP request interface for Softline search."""

from contextlib import asynccontextmanager
from typing import Literal

from fastapi import FastAPI, Request, Response
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse

from smart_search.contracts import Contract, SearchRequest, SearchResponse
from smart_search.runtime import get_search_runner
from smart_search.runtime_config import RuntimeConfigurationError
from smart_search.session_store import SessionHistoryResponse, session_store


class InterfaceError(Contract):
    code: str
    message: str


class ReadyResponse(Contract):
    status: Literal["ready"] = "ready"
    mode: Literal["prod"] = "prod"


class SessionClearedResponse(Contract):
    session_id: str
    cleared: bool


@asynccontextmanager
async def lifespan(_app: FastAPI):
    try:
        yield
    finally:
        await session_store.aclose()


app = FastAPI(title="Softline Smart Search", version="1.0.0", lifespan=lifespan)


@app.exception_handler(RuntimeConfigurationError)
async def configuration_error(_request: Request, error: RuntimeConfigurationError) -> JSONResponse:
    body = InterfaceError(code=error.code, message=str(error))
    return JSONResponse(status_code=503, content=body.model_dump())


@app.get(
    "/health/ready",
    responses={503: {"model": InterfaceError, "description": "Backend configuration unavailable"}},
)
async def readiness() -> ReadyResponse:
    await run_in_threadpool(get_search_runner, require_prod=True, check_dependencies=True)
    return ReadyResponse()


@app.post(
    "/v1/search",
    responses={
        503: {
            "model": SearchResponse | InterfaceError,
            "description": "Search unavailable or prod backend configuration missing",
        },
    },
)
async def search(request: SearchRequest, response: Response) -> SearchResponse:
    runner = await run_in_threadpool(get_search_runner, require_prod=True)
    enriched = request
    if request.session_id and not request.previous_context:
        stored_context = await session_store.get_context(request.session_id)
        if stored_context:
            enriched = request.model_copy(update={"previous_context": stored_context})

    result = await run_in_threadpool(runner, enriched)

    if request.session_id and result.status != "unavailable":
        await session_store.append_turn(request.session_id, request.query, result)

    if result.status == "unavailable":
        response.status_code = 503
    return result


@app.get("/v1/sessions/{session_id}", response_model=SessionHistoryResponse)
async def get_session_history(session_id: str) -> SessionHistoryResponse:
    turns = await session_store.get_history(session_id)
    return SessionHistoryResponse(
        session_id=session_id,
        ttl_seconds=session_store.ttl_seconds,
        turns=turns,
    )


@app.delete("/v1/sessions/{session_id}", response_model=SessionClearedResponse)
async def clear_session_history(session_id: str) -> SessionClearedResponse:
    cleared = await session_store.clear(session_id)
    return SessionClearedResponse(session_id=session_id, cleared=cleared)
