"""Select adapters and expose the request handler shared by the interfaces."""

import logging
from functools import lru_cache
from os import getenv
from typing import Literal, cast

from smart_search.contracts import SearchRequest, SearchResponse
from smart_search.events.publisher import record_search_event
from smart_search.modes.production import load_backend
from smart_search.runtime_config import RuntimeConfigurationError, get_mode
from smart_search.supervisor import build_search
from smart_search.tools import SearchRunner, Services

LOGGER = logging.getLogger(__name__)


def load_services(module_name: str | None = None) -> Services:
    if not module_name:
        from smart_search.modes.demo import demo_services

        return demo_services()
    return load_backend(module_name)


@lru_cache(maxsize=2)
def _configured_runner(module_name: str | None) -> SearchRunner:
    retrieval = getenv("SMART_SEARCH_RETRIEVAL_MODE", "hybrid") if module_name else "hybrid"
    if retrieval not in {"bm25", "dense", "hybrid"}:
        raise RuntimeConfigurationError("invalid_retrieval_mode", "Select bm25, dense or hybrid.")
    return build_search(
        load_services(module_name),
        record_event=record_search_event,
        search_mode=cast(Literal["bm25", "dense", "hybrid"], retrieval),
    )


def get_search_runner(
    *, require_prod: bool = False, check_dependencies: bool = False
) -> SearchRunner:
    mode = get_mode()
    if mode == "test":
        if require_prod:
            raise RuntimeConfigurationError(
                "prod_mode_required", "The HTTP search interface requires SMART_SEARCH_MODE=prod."
            )
        return _configured_runner(None)

    module_name = getenv("SMART_SEARCH_BACKEND_MODULE", "").strip()
    if not module_name:
        raise RuntimeConfigurationError(
            "backend_not_configured", "Set SMART_SEARCH_BACKEND_MODULE for prod mode."
        )
    try:
        runner = _configured_runner(module_name)
        if check_dependencies:
            probe = load_services(module_name).check_ready
            if probe is not None:
                try:
                    probe()
                except ConnectionError as error:
                    raise RuntimeConfigurationError(
                        "catalog_unavailable", "The catalog or search index is unavailable."
                    ) from error
        return runner
    except RuntimeConfigurationError:
        raise
    except (ImportError, OSError, ValueError) as error:
        LOGGER.error("Unable to load search backend (%s)", type(error).__name__)
        raise RuntimeConfigurationError(
            "backend_load_failed", "The configured search backend could not be loaded."
        ) from error


def run_search(request: SearchRequest) -> SearchResponse:
    return get_search_runner()(request)
