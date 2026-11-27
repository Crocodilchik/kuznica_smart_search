"""Upsert precomputed GPU dense embeddings + native BM25 into Qdrant."""

from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np
from qdrant_client import QdrantClient

from softline_search.catalog import Catalog
from softline_search.retrieval import build_index


class PrecomputedEncoder:
    def __init__(self, model_name: str, embeddings_path: Path):
        self.model_name = model_name
        self.arr = np.load(embeddings_path, mmap_mode="r")
        self.dimension = int(self.arr.shape[1])
        self.offset = 0

    def documents(self, texts: list[str]) -> list[list[float]]:
        end = self.offset + len(texts)
        chunk = self.arr[self.offset : end].tolist()
        self.offset = end
        return chunk

    def query(self, text: str) -> list[float]:
        raise NotImplementedError("Query encoding is only used at search time.")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--catalog", type=Path, default=Path("data/catalog"))
    parser.add_argument("--output", type=Path, default=Path("data/qdrant-server"))
    parser.add_argument("--embeddings", type=Path, default=Path(".local/dense_embeddings.f32.npy"))
    parser.add_argument(
        "--model", default="sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
    )
    parser.add_argument("--url", default="http://127.0.0.1:6333")
    parser.add_argument("--cleanup-old", action="store_true")
    args = parser.parse_args()

    catalog = Catalog(args.catalog)
    encoder = PrecomputedEncoder(args.model, args.embeddings)
    if len(encoder.arr) != catalog.manifest["products"]:
        raise ValueError(
            f"Embedding count ({len(encoder.arr)}) != catalog products ({catalog.manifest['products']})"
        )

    manifest = build_index(
        catalog,
        args.output,
        backend="fulltext",
        encoder=encoder,
        batch_size=256,
        url=args.url,
    )
    print(
        f"[GPU-UPSERT] Built collection {manifest['collection']} in {manifest['elapsed_seconds']}s"
    )

    if args.cleanup_old:
        client = QdrantClient(url=args.url, timeout=60)
        for col in client.get_collections().collections:
            if col.name != manifest["collection"]:
                client.delete_collection(col.name)
                print(f"[GPU-UPSERT] Deleted old collection: {col.name}")
        client.close()


if __name__ == "__main__":
    main()
