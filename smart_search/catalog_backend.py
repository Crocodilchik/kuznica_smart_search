"""Production adapter for the normalized Softline catalog and its Qdrant index."""

import json
import os
import sqlite3
from contextlib import closing, contextmanager
from functools import cache
from threading import RLock

from qdrant_client import QdrantClient
from qdrant_client.http.exceptions import ResponseHandlingException, UnexpectedResponse

from smart_search.contracts import SearchHit, SearchParams, SearchResult
from softline_search.contracts import SearchParams as CatalogParams
from softline_search.contracts import SearchResult as CatalogResult
from softline_search.retrieval import FastEmbedEncoder
from softline_search.search import SearchEngine

_LOCK = RLock()


class _QueryEncoder:
    """Let SKU and BM25 run independently of optional neural model weights."""

    def __init__(self, model_name: str):
        self.model_name = model_name
        self._init_lock = RLock()
        self._model = None

    @property
    def model(self):
        if self._model is not None:
            return self._model
        with self._init_lock:
            if self._model is not None:
                return self._model
            try:
                self._model = FastEmbedEncoder(self.model_name)
            except Exception as exc:
                # FastEmbed and ONNX providers use different exception classes.
                # SearchEngine handles a ValueError as a failure of this branch only.
                raise ValueError(f"dense_encoder_unavailable: {type(exc).__name__}") from exc
            return self._model

    @property
    def dimension(self) -> int:
        return self.model.dimension

    def query(self, text: str) -> list[float]:
        model = self.model
        try:
            return model.query(text)
        except Exception as exc:
            raise ValueError(f"dense_query_failed: {type(exc).__name__}") from exc

    def documents(self, texts: list[str]) -> list[list[float]]:
        return self.model.documents(texts)


@cache
def _engine() -> SearchEngine:
    model = os.getenv("SOFTLINE_DENSE_MODEL")
    return SearchEngine(
        os.getenv("SOFTLINE_CATALOG_DIR", "data/catalog"),
        os.getenv("SOFTLINE_INDEX_DIR", "data/qdrant"),
        url=os.getenv("QDRANT_URL"),
        api_key=os.getenv("QDRANT_API_KEY"),
        encoder=_QueryEncoder(model) if model else None,
    )


@contextmanager
def _access():
    # Local single-process Qdrant storage requires exclusive serialization via _LOCK.
    # Remote server Qdrant (QDRANT_URL) supports full concurrent requests without holding _LOCK.
    with _LOCK:
        try:
            engine = _engine()
        except (
            OSError,
            sqlite3.Error,
            ValueError,
            KeyError,
            RuntimeError,
            UnexpectedResponse,
            ResponseHandlingException,
        ) as exc:
            raise ConnectionError(f"catalog_unavailable: {type(exc).__name__}") from exc
        is_remote = bool(engine.url)
        if is_remote and engine.client is None:
            try:
                engine._open_index()
            except (
                OSError,
                sqlite3.Error,
                ValueError,
                KeyError,
                RuntimeError,
                UnexpectedResponse,
                ResponseHandlingException,
            ) as exc:
                raise ConnectionError(f"catalog_unavailable: {type(exc).__name__}") from exc

    if is_remote:
        try:
            yield engine
        except (
            OSError,
            sqlite3.Error,
            ValueError,
            KeyError,
            RuntimeError,
            UnexpectedResponse,
            ResponseHandlingException,
        ) as exc:
            raise ConnectionError(f"catalog_unavailable: {type(exc).__name__}") from exc
        return

    with _LOCK:
        try:
            yield engine
        except (
            OSError,
            sqlite3.Error,
            ValueError,
            KeyError,
            RuntimeError,
            UnexpectedResponse,
            ResponseHandlingException,
        ) as exc:
            raise ConnectionError(f"catalog_unavailable: {type(exc).__name__}") from exc


def close() -> None:
    """Release local Qdrant and reload environment settings on the next request."""
    with _LOCK:
        if _engine.cache_info().currsize:
            _engine().close()
        _engine.cache_clear()


def search_rich(params: CatalogParams | dict) -> CatalogResult:
    """Shared retrieval entry point for recommendation and primary search."""
    params = CatalogParams.model_validate(params)
    try:
        with _access() as engine:
            return engine.search(params)
    except ConnectionError as exc:
        return CatalogResult(
            status="unavailable", warnings=[str(exc)], diagnostics={"mode": params.mode}
        )


def search(params: SearchParams) -> SearchResult:
    native = search_rich(params.model_dump(exclude={"query_spec": {"warnings"}}))
    return SearchResult(
        hits=[SearchHit.model_validate(hit.model_dump()) for hit in native.hits],
        warnings=native.warnings,
        available=native.status != "unavailable",
        diagnostics={**native.diagnostics, "status": native.status},
    )


def category_names() -> dict[str, str]:
    """Offer-bearing categories available to the recommendation planner."""
    with _access() as engine:
        return {
            category["id"]: category["name"]
            for category in engine.catalog.categories
            if category["offer_count"] > 0
        }


def resolve_category(category: str | None) -> list[str] | None:
    """Resolve a query filter to catalog IDs, including ancestors and aliases."""
    if not category:
        return None
    try:
        with _access() as engine:
            return engine.catalog.resolve_category(category)
    except ConnectionError:
        return []


def check_ready() -> None:
    """Verify the published catalog and index without loading models or issuing a search."""
    with _access() as engine:
        with closing(engine.catalog.connect()) as db:
            db.execute("SELECT 1 FROM products LIMIT 1").fetchone()
        manifest = json.loads((engine.index_dir / "index.json").read_text(encoding="utf-8"))
        if (
            manifest["catalog_source_sha256"] != engine.catalog.manifest["source_sha256"]
            or manifest.get("catalog_content_sha256")
            != engine.catalog.manifest.get("content_sha256")
            or bool(manifest.get("remote", False)) != bool(engine.url)
        ):
            raise ValueError("catalog_index_mismatch")
        collection = manifest["collection"]
        if engine.url:
            with closing(QdrantClient(url=engine.url, api_key=engine.api_key, timeout=5)) as client:
                if not client.collection_exists(collection):
                    raise ValueError("collection_missing")
        else:
            # Opening a local QdrantClient would create missing storage and acquire
            # an exclusive lock. Inspect the published SDK files in read-only mode.
            storage = engine.index_dir / manifest.get("storage_path", "storage")
            meta = json.loads((storage / "meta.json").read_text(encoding="utf-8"))
            if collection not in meta["collections"]:
                raise ValueError("collection_missing")
            points = storage / "collection" / collection / "storage.sqlite"
            with closing(sqlite3.connect(points.as_uri() + "?mode=ro&immutable=1", uri=True)) as db:
                db.execute("SELECT 1 FROM points LIMIT 1").fetchone()
