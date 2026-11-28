"""Qdrant native full-text BM25, native sparse fallback and optional dense retrieval."""

from __future__ import annotations

import os
import sqlite3
import time
from collections import defaultdict
from pathlib import Path
from typing import Protocol
from uuid import NAMESPACE_URL, uuid4, uuid5

from qdrant_client import QdrantClient
from qdrant_client import models as qm
from qdrant_client.http.exceptions import UnexpectedResponse

from .catalog import Catalog, dump_json
from .contracts import Product
from .text import lexical_text, sku_key


class DenseEncoder(Protocol):
    model_name: str
    dimension: int

    def documents(self, texts: list[str]) -> list[list[float]]: ...
    def query(self, text: str) -> list[float]: ...


class FastEmbedEncoder:
    def __init__(self, model_name: str, cache_dir: str | None = None, threads: int | None = None):
        from fastembed import TextEmbedding

        resolved_cache = (
            cache_dir
            or os.getenv("FASTEMBED_CACHE_PATH")
            or str(Path(os.getenv("SOFTLINE_INDEX_DIR", "data/qdrant")).parent / "models")
        )
        resolved_threads = threads or int(
            os.getenv("FASTEMBED_THREADS") or min(8, os.cpu_count() or 4)
        )
        descriptions = {item["model"]: item for item in TextEmbedding.list_supported_models()}
        if model_name not in descriptions:
            raise ValueError(f"Unsupported FastEmbed dense model: {model_name}")
        self.model_name = model_name
        self.dimension = descriptions[model_name]["dim"]
        self.model = TextEmbedding(model_name, cache_dir=resolved_cache, threads=resolved_threads)

    def documents(self, texts):
        return [item.tolist() for item in self.model.passage_embed(texts)]

    def query(self, text):
        return next(iter(self.model.query_embed(text))).tolist()


def point_id(product_id: str) -> str:
    return str(uuid5(NAMESPACE_URL, f"https://store.softline.ru/offer/{product_id}"))


def product_payload(product: Product) -> dict:
    return {
        "product": product.model_dump(mode="json"),
        # Weight the title for lexical retrieval; dense text stays a single natural-language description.
        "lexical_text": lexical_text(product.title + "\n" + product.search_text),
        "sku_key": sku_key(product.sku),
        "category_ids": list(
            dict.fromkeys(product.category_ids + ([product.family_id] if product.family_id else []))
        ),
        "os": product.os,
        "country": product.country,
        "in_stock": product.in_stock,
        "price_kopecks": product.price_kopecks,
        "license_units": product.license_units,
        "license_unit_type": product.license_unit_type,
        "product_type": product.product_type,
    }


class SparseBM25:
    """Official FastEmbed BM25, with Qdrant calculating IDF. BM25 has no neural weights."""

    def __init__(self, cache_dir: Path, avg_len: float):
        from fastembed import SparseTextEmbedding

        # Inline stopwords and pre-tokenized input make external tokenizer assets unnecessary.
        model_dir = cache_dir / "bm25"
        model_dir.mkdir(parents=True, exist_ok=True)
        (model_dir / "config.json").write_text("{}", encoding="utf-8")
        self.model = SparseTextEmbedding(
            "Qdrant/bm25",
            specific_model_path=str(model_dir),
            disable_stemmer=True,
            stopwords=set(),
            avg_len=avg_len,
            token_max_length=1000,
        )

    def documents(self, texts: list[str]) -> list[qm.SparseVector]:
        return [
            qm.SparseVector(indices=item.indices.tolist(), values=item.values.tolist())
            for item in self.model.embed(texts)
        ]

    def query(self, text: str) -> qm.SparseVector:
        item = next(iter(self.model.query_embed(text)))
        return qm.SparseVector(indices=item.indices.tolist(), values=item.values.tolist())


def ensure_fulltext(client: QdrantClient, collection: str) -> None:
    client.create_payload_index(
        collection,
        "lexical_text",
        qm.TextIndexParams(type=qm.TextIndexType.TEXT, scoring=True),
        wait=True,
    )
    client.query_points(
        collection, query=qm.TextQuery(text="capabilityprobe"), using="lexical_text", limit=1
    )


def _new_collection_bulk_connection(
    client: QdrantClient, collection: str
) -> sqlite3.Connection | None:
    """Tune only a new, unpublished local collection; never change installed SDK code.

    Local persistence commits each point. WAL/NORMAL avoids one disk flush per point;
    FULL + checkpoint below makes the completed collection durable before publication.
    The SDK-specific access is isolated and covered by persistence/reopen tests.
    """
    collections = getattr(client._client, "collections", {})
    local_collection = collections.get(collection)
    persistence = getattr(local_collection, "storage", None)
    connection = getattr(persistence, "storage", None)
    if not isinstance(connection, sqlite3.Connection):
        return None
    connection.execute("PRAGMA journal_mode=WAL")
    connection.execute("PRAGMA synchronous=NORMAL")
    return connection


def build_index(
    catalog: Catalog,
    directory: Path,
    *,
    backend: str = "auto",
    limit: int | None = None,
    encoder: DenseEncoder | None = None,
    batch_size: int = 256,
    url: str | None = None,
    api_key: str | None = None,
) -> dict:
    if backend not in {"auto", "fulltext", "sparse"}:
        raise ValueError("backend must be auto, fulltext or sparse")
    if limit is not None and limit < 1:
        raise ValueError("limit must be positive")
    if batch_size < 1:
        raise ValueError("batch_size must be positive")
    started = time.perf_counter()
    directory.mkdir(parents=True, exist_ok=True)
    collection = "products_" + uuid4().hex[:12]
    storage_path = f"builds/{collection}"
    client = (
        QdrantClient(url=url, api_key=api_key, timeout=120)
        if url
        else QdrantClient(path=str(directory / storage_path))
    )
    warnings = []
    count = 0
    avg_len = 256.0
    try:
        client.create_collection(
            collection,
            vectors_config={
                "dense": qm.VectorParams(size=encoder.dimension, distance=qm.Distance.COSINE)
            }
            if encoder
            else {},
            sparse_vectors_config={"bm25": qm.SparseVectorParams(modifier=qm.Modifier.IDF)},
        )
        bulk_connection = _new_collection_bulk_connection(client, collection) if not url else None
        selected = backend
        if backend in {"auto", "fulltext"}:
            try:
                ensure_fulltext(client, collection)
                selected = "fulltext"
            except (UnexpectedResponse, NotImplementedError, ValueError) as error:
                if backend == "fulltext":
                    raise
                if isinstance(error, UnexpectedResponse) and error.status_code not in {
                    400,
                    404,
                    422,
                }:
                    raise
                selected = "sparse"
                warnings.append(f"fulltext_scoring_unavailable: {type(error).__name__}")
        sparse = None
        if selected == "sparse":
            token_count = document_count = 0
            for product in catalog.iter_products(limit):
                token_count += len(product_payload(product)["lexical_text"].split())
                document_count += 1
            avg_len = max(1.0, token_count / max(1, document_count))
            sparse = SparseBM25(directory / "models", avg_len)
        # Local Qdrant evaluates payload filters itself; server deployments need these indexes.
        if url:
            for name in (
                "sku_key",
                "category_ids",
                "os",
                "country",
                "product_type",
                "license_unit_type",
            ):
                client.create_payload_index(
                    collection, name, qm.PayloadSchemaType.KEYWORD, wait=True
                )
            for name in ("price_kopecks", "license_units", "product.min_purchase"):
                client.create_payload_index(
                    collection, name, qm.PayloadSchemaType.INTEGER, wait=True
                )
            client.create_payload_index(
                collection, "in_stock", qm.PayloadSchemaType.BOOL, wait=True
            )
        batch = []

        def upload(products: list[Product]):
            payloads = [product_payload(product) for product in products]
            sparse_vectors = (
                sparse.documents([item["lexical_text"] for item in payloads]) if sparse else None
            )
            dense_vectors = (
                encoder.documents([product.search_text for product in products])
                if encoder
                else None
            )
            if dense_vectors is not None and len(dense_vectors) != len(products):
                raise ValueError("Dense encoder returned a different number of vectors")
            points = []
            for position, (product, payload) in enumerate(zip(products, payloads, strict=True)):
                vectors = {}
                if sparse_vectors is not None:
                    vectors["bm25"] = sparse_vectors[position]
                if dense_vectors is not None:
                    vectors["dense"] = dense_vectors[position]
                points.append(
                    qm.PointStruct(id=point_id(product.product_id), vector=vectors, payload=payload)
                )
            client.upsert(collection, points=points, wait=True)

        for product in catalog.iter_products(limit):
            batch.append(product)
            if len(batch) >= batch_size:
                upload(batch)
                count += len(batch)
                batch.clear()
                if count % (batch_size * 100) == 0:
                    print(f"Indexed {count:,} products ({selected})", flush=True)
        if batch:
            upload(batch)
            count += len(batch)
        if bulk_connection is not None:
            bulk_connection.execute("PRAGMA synchronous=FULL")
            checkpoint = bulk_connection.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()
            if checkpoint[0] != 0:
                raise OSError("Could not checkpoint the completed local index")
        client.close()
        manifest = {
            "schema_version": 1,
            "collection": collection,
            "backend": selected,
            "products": count,
            "catalog_products": catalog.manifest["products"],
            "catalog_source_sha256": catalog.manifest["source_sha256"],
            "catalog_content_sha256": catalog.manifest.get("content_sha256"),
            "catalog_schema_version": catalog.manifest["schema_version"],
            "is_subset": count < catalog.manifest["products"],
            "remote": bool(url),
            "storage_path": None if url else storage_path,
            "local_bulk_wal": bulk_connection is not None,
            "dense_model": encoder.model_name if encoder else None,
            "dense_dimension": encoder.dimension if encoder else None,
            "sparse_avg_len": avg_len,
            "warnings": warnings,
            "elapsed_seconds": round(time.perf_counter() - started, 3),
        }
        # Only publish the new collection after every batch has completed.
        dump_json(directory / "index.json", manifest)
        return manifest
    finally:
        client.close()


def rrf(rankings: list[list[tuple[Product, float]]], k: int = 60) -> list[tuple[Product, float]]:
    if k < 0:
        raise ValueError("RRF k must be nonnegative")
    scores: dict[str, float] = defaultdict(float)
    products = {}
    for ranking in rankings:
        seen = set()
        for rank, (product, _) in enumerate(ranking, 1):
            if product.product_id in seen:
                continue
            seen.add(product.product_id)
            products[product.product_id] = product
            scores[product.product_id] += 1.0 / (k + rank)
    return [
        (products[key], scores[key]) for key in sorted(scores, key=lambda key: (-scores[key], key))
    ]
