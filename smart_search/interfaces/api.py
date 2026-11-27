"""Production HTTP request interface for Softline search."""

from typing import Literal

from fastapi import FastAPI, Request, Response
from fastapi.responses import JSONResponse

from smart_search.contracts import Contract, SearchRequest, SearchResponse
from smart_search.runtime import get_search_runner
from smart_search.runtime_config import RuntimeConfigurationError


class InterfaceError(Contract):
    code: str
    message: str


class ReadyResponse(Contract):
    status: Literal["ready"] = "ready"
    mode: Literal["prod"] = "prod"


app = FastAPI(title="Softline Smart Search", version="1.0.0")


@app.exception_handler(RuntimeConfigurationError)
async def configuration_error(_request: Request, error: RuntimeConfigurationError) -> JSONResponse:
    body = InterfaceError(code=error.code, message=str(error))
    return JSONResponse(status_code=503, content=body.model_dump())


@app.get(
    "/health/ready",
    responses={503: {"model": InterfaceError, "description": "Backend configuration unavailable"}},
)
def readiness() -> ReadyResponse:
    get_search_runner(require_prod=True, check_dependencies=True)
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
def search(request: SearchRequest, response: Response) -> SearchResponse:
    result = get_search_runner(require_prod=True)(request)
    if result.status == "unavailable":
        response.status_code = 503
    return result
