"""Application-facing search(SearchParams) -> SearchResult; no LLM or external API required."""

from __future__ import annotations

import json
import os
import time
from functools import lru_cache
from pathlib import Path

from qdrant_client import QdrantClient
from qdrant_client import models as qm
from qdrant_client.http.exceptions import ResponseHandlingException, UnexpectedResponse

from .catalog import Catalog
from .contracts import Product, QuerySpec, SearchHit, SearchParams, SearchResult
from .licensing import units_to_buy
from .retrieval import DenseEncoder, SparseBM25, rrf
from .text import canonical_os, lexical_text


def payload_filter(spec: QuerySpec, category_ids: list[str] | None) -> qm.Filter | None:
    filters = spec.hard_filters
    must = []
    if category_ids is not None:
        must.append(qm.FieldCondition(key="category_ids", match=qm.MatchAny(any=category_ids)))
    for name, value in (
        ("os", canonical_os(filters.os) if filters.os else None),
        ("country", filters.country),
        ("in_stock", filters.in_stock),
    ):
        if value is not None:
            must.append(qm.FieldCondition(key=name, match=qm.MatchValue(value=value)))
    if filters.max_total_price_kopecks is not None:
        must.append(
            qm.FieldCondition(
                key="price_kopecks", range=qm.Range(gte=1, lte=filters.max_total_price_kopecks)
            )
        )
    elif filters.min_total_price_kopecks is not None:
        must.append(qm.FieldCondition(key="price_kopecks", range=qm.Range(gte=1)))
    if (
        filters.min_total_price_kopecks is not None
        and filters.min_total_price_kopecks > 1
        and filters.devices is None
    ):
        must.append(
            qm.Filter(
                should=[
                    qm.FieldCondition(
                        key="price_kopecks",
                        range=qm.Range(gte=filters.min_total_price_kopecks),
                    ),
                    qm.FieldCondition(key="product.min_purchase", range=qm.Range(gt=1)),
                ]
            )
        )
    if filters.devices is not None:
        must += [
            qm.Filter(
                should=[
                    qm.FieldCondition(key="license_units", range=qm.Range(gte=filters.devices)),
                    qm.FieldCondition(
                        key="product.attributes.license_stackable", match=qm.MatchValue(value=True)
                    ),
                ]
            ),
            qm.FieldCondition(key="license_unit_type", match=qm.MatchValue(value="device")),
        ]
    return qm.Filter(must=must) if must else None


def matches_filters(product: Product, spec: QuerySpec, category_ids: list[str] | None) -> bool:
    filters = spec.hard_filters
    if category_ids is not None and not set(category_ids).intersection(
        product.category_ids + ([product.family_id] if product.family_id else [])
    ):
        return False
    if filters.os is not None and canonical_os(filters.os) not in product.os:
        return False
    if filters.country is not None and filters.country != product.country:
        return False
    if filters.in_stock is not None and filters.in_stock != product.in_stock:
        return False
    units, failure = units_to_buy(product, filters.devices)
    if failure:
        return False
    if filters.max_total_price_kopecks is not None:
        if product.price_kopecks is None:
            return False
        if product.price_kopecks * units > filters.max_total_price_kopecks:
            return False
    if filters.min_total_price_kopecks is not None:
        if product.price_kopecks is None:
            return False
        if product.price_kopecks * units < filters.min_total_price_kopecks:
            return False
    return True


class SearchEngine:
    def __init__(
        self,
        catalog_dir: Path | str = "data/catalog",
        index_dir: Path | str = "data/qdrant",
        *,
        encoder: DenseEncoder | None = None,
        url: str | None = None,
        api_key: str | None = None,
    ):
        self.catalog = Catalog(Path(catalog_dir).resolve())
        self.index_dir = Path(index_dir).resolve()
        self.encoder = encoder
        self.url, self.api_key = url, api_key
        self.client: QdrantClient | None = None
        self.index: dict | None = None
        self.sparse: SparseBM25 | None = None

    def close(self):
        if self.client is not None:
            self.client.close()
            self.client = None

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()

    def _open_index(self):
        if self.client is not None:
            return
        manifest_path = self.index_dir / "index.json"
        if not manifest_path.is_file():
            raise FileNotFoundError("index_not_built")
        self.index = json.loads(manifest_path.read_text(encoding="utf-8"))
        if self.index["catalog_source_sha256"] != self.catalog.manifest["source_sha256"]:
            raise ValueError("catalog_index_source_mismatch")
        if self.index.get("catalog_content_sha256") != self.catalog.manifest.get("content_sha256"):
            raise ValueError("catalog_index_content_mismatch: rebuild after changing the parser")
        if self.index.get("remote", False) != bool(self.url):
            raise ValueError(
                "index_location_mismatch: supply the same Qdrant URL used for indexing"
            )
        client = (
            QdrantClient(url=self.url, api_key=self.api_key, timeout=120)
            if self.url
            else QdrantClient(path=str(self.index_dir / self.index.get("storage_path", "storage")))
        )
        try:
            if self.index["backend"] == "fulltext":
                # Local text-index metadata isn't persistent in every SDK version.
                if not self.url:
                    client.create_payload_index(
                        self.index["collection"],
                        "lexical_text",
                        qm.TextIndexParams(type=qm.TextIndexType.TEXT, scoring=True),
                    )
            else:
                self.sparse = SparseBM25(self.index_dir / "models", self.index["sparse_avg_len"])
            self.client = client
        except Exception:
            client.close()
            raise

    def _branch(
        self, spec: QuerySpec, mode: str, categories: list[str] | None, top_k: int
    ) -> tuple[list[tuple[Product, float]], bool]:
        self._open_index()
        assert self.client is not None and self.index is not None
        if mode == "bm25":
            text = lexical_text(spec.normalized_query)
            query = (
                qm.TextQuery(text=text)
                if self.index["backend"] == "fulltext"
                else self.sparse.query(text)
            )
            using = "lexical_text" if self.index["backend"] == "fulltext" else "bm25"
        else:
            if not self.index["dense_model"] or self.encoder is None:
                raise ValueError("dense_not_configured")
            if (
                self.encoder.model_name != self.index["dense_model"]
                or self.encoder.dimension != self.index["dense_dimension"]
            ):
                raise ValueError("dense_model_mismatch")
            query, using = self.encoder.query(spec.normalized_query), "dense"
        # Refill after checking minimum-purchase totals; never silently relax hard constraints.
        eligible = []
        offset = 0
        batch_size = max(32, top_k + 1)
        # Look one eligible hit past the limit to prove whether expanding can help.
        while len(eligible) <= top_k:
            points = self.client.query_points(
                self.index["collection"],
                query=query,
                using=using,
                query_filter=payload_filter(spec, categories),
                score_threshold=spec.score_threshold if mode == "dense" else None,
                limit=batch_size,
                offset=offset,
                with_payload=True,
                with_vectors=False,
            ).points
            for point in points:
                product = Product.model_validate(point.payload["product"])
                if matches_filters(product, spec, categories):
                    eligible.append((product, point.score))
            if len(points) < batch_size:
                break
            offset += len(points)
        return eligible[:top_k], len(eligible) <= top_k

    def search(self, params: SearchParams | dict) -> SearchResult:
        if not isinstance(params, SearchParams):
            params = SearchParams.model_validate(params)
        started = time.perf_counter()
        spec = params.query_spec
        warnings = []
        diagnostics = {
            "mode": params.mode,
            "source_date": self.catalog.manifest["source_date"],
            "category_hints_applied": False,
        }
        categories = self.catalog.resolve_category(spec.category)
        if categories == []:
            diagnostics["latency_ms"] = round((time.perf_counter() - started) * 1000, 3)
            diagnostics["exhausted"] = True
            return SearchResult(
                status="empty", warnings=["category_unresolved"], diagnostics=diagnostics
            )
        if (
            spec.hard_filters.country is not None
            and self.catalog.manifest.get("statistics", {}).get("known_fields", {}).get("country")
            == 0
        ):
            diagnostics.update(
                backend="catalog_metadata",
                latency_ms=round((time.perf_counter() - started) * 1000, 3),
                exhausted=True,
            )
            return SearchResult(
                status="empty", warnings=["country_unknown_in_catalog"], diagnostics=diagnostics
            )
        if categories is None and params.mode != "sku" and spec.category_hints:
            categories = (
                list(
                    dict.fromkeys(
                        item
                        for hint in spec.category_hints
                        for item in self.catalog.resolve_category(hint) or []
                    )
                )
                or None
            )
            diagnostics["category_hints_applied"] = categories is not None
        if spec.hard_filters.budget_scope == "bundle":
            warnings.append("bundle_total_requires_validation")
        if spec.hard_filters.devices is not None:
            warnings.append(
                "devices_filter_requires_explicit_device_pack_capacity; no assumed license multiplication"
            )
        ranking = []
        branch_scores: dict[str, dict[str, float]] = {}
        status = "ok"
        exhausted = False
        try:
            if params.mode == "sku":
                sku = spec.sku or spec.normalized_query.strip()
                ranking = [
                    (product, 1.0)
                    for product in self.catalog.by_sku(sku)
                    if matches_filters(product, spec, categories)
                ]
                exhausted = len(ranking) <= params.top_k
                ranking = ranking[: params.top_k]
                diagnostics["backend"] = "sqlite_exact_sku"
            elif not lexical_text(spec.normalized_query):
                warnings.append("query_has_no_search_terms")
                exhausted = True
            elif params.mode == "hybrid":
                rankings = []
                exhausted_branches = []
                for mode in ("bm25", "dense"):
                    try:
                        branch, exhausted_branch = self._branch(
                            spec, mode, categories, max(100, params.top_k)
                        )
                        rankings.append(branch)
                        exhausted_branches.append(exhausted_branch)
                        for product, score in branch:
                            branch_scores.setdefault(product.product_id, {})[mode] = score
                    except (
                        ValueError,
                        OSError,
                        UnexpectedResponse,
                        ResponseHandlingException,
                    ) as error:
                        warnings.append(f"{mode}_unavailable: {error}")
                if not rankings:
                    status = "unavailable"
                elif len(rankings) < 2:
                    status = "partial"
                ranking = rrf(rankings)
                exhausted = (
                    status == "ok" and all(exhausted_branches) and len(ranking) <= params.top_k
                )
                ranking = ranking[: params.top_k]
            else:
                ranking, exhausted = self._branch(spec, params.mode, categories, params.top_k)
        except (ValueError, OSError, UnexpectedResponse, ResponseHandlingException) as error:
            warnings.append(f"search_unavailable: {error}")
            status = "unavailable"
        if self.index is not None and params.mode != "sku":
            diagnostics.update(
                backend=self.index["backend"],
                indexed_products=self.index["products"],
                dense_model=self.index["dense_model"],
                is_subset=self.index["is_subset"],
            )
            if self.index["is_subset"]:
                warnings.append("index_contains_catalog_subset")
        if not ranking and status == "ok":
            status = "empty"
        diagnostics["exhausted"] = exhausted
        diagnostics["latency_ms"] = round((time.perf_counter() - started) * 1000, 3)
        return SearchResult(
            hits=[
                SearchHit(
                    product=product,
                    rank=rank,
                    score=float(score),
                    branch_scores=branch_scores.get(product.product_id, {}),
                )
                for rank, (product, score) in enumerate(ranking, 1)
            ],
            warnings=warnings,
            status=status,
            diagnostics=diagnostics,
        )


@lru_cache(maxsize=1)
def _default_engine():
    return SearchEngine(
        os.environ.get("SOFTLINE_CATALOG_DIR", "data/catalog"),
        os.environ.get("SOFTLINE_INDEX_DIR", "data/qdrant"),
        url=os.environ.get("QDRANT_URL"),
        api_key=os.environ.get("QDRANT_API_KEY"),
    )


def search(params: SearchParams | dict) -> SearchResult:
    try:
        return _default_engine().search(params)
    except OSError as error:
        return SearchResult(status="unavailable", warnings=[f"catalog_unavailable: {error}"])


def close_default_engine():
    if _default_engine.cache_info().currsize:
        _default_engine().close()
    _default_engine.cache_clear()
