"""Command-line entry points for offline preparation and the team's search boundary."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from .catalog import Catalog, build_catalog
from .contracts import HardFilters, QuerySpec, SearchParams
from .retrieval import FastEmbedEncoder, build_index
from .search import SearchEngine


def main(argv=None):
    parser = argparse.ArgumentParser(description="Softline catalog and Qdrant BM25")
    sub = parser.add_subparsers(dest="command", required=True)
    build = sub.add_parser("prepare", help="Stream the YML into a normalized catalog and EDA")
    build.add_argument("source", type=Path)
    build.add_argument("--output", type=Path, default=Path("data/catalog"))
    build.add_argument("--reports", type=Path, default=Path("reports"))
    build.add_argument("--sample-size", type=int, default=300)
    index = sub.add_parser("index", help="Build and publish a Qdrant index")
    index.add_argument("--catalog", type=Path, default=Path("data/catalog"))
    index.add_argument("--output", type=Path, default=Path("data/qdrant"))
    index.add_argument("--backend", choices=["auto", "fulltext", "sparse"], default="auto")
    index.add_argument("--limit", type=int)
    index.add_argument("--batch-size", type=int, default=256)
    index.add_argument("--dense-model", help="Optional FastEmbed dense model; downloads weights")
    query = sub.add_parser(
        "search", help="Search from the terminal, or pass a SearchParams JSON file"
    )
    query.add_argument("query", nargs="?", default="")
    query.add_argument("--params", type=Path)
    query.add_argument("--mode", choices=["sku", "bm25", "dense", "hybrid"], default="bm25")
    query.add_argument("--sku")
    query.add_argument("--category")
    query.add_argument("--os")
    query.add_argument("--country")
    query.add_argument("--devices", type=int)
    query.add_argument("--max-price-kopecks", type=int)
    query.add_argument("--in-stock", action=argparse.BooleanOptionalAction, default=None)
    query.add_argument("--top-k", type=int, default=4)
    query.add_argument("--catalog", type=Path, default=Path("data/catalog"))
    query.add_argument("--index", type=Path, default=Path("data/qdrant"))
    query.add_argument("--dense-model")
    query.add_argument("--compact", action="store_true", help="Print a short table instead of JSON")
    args = parser.parse_args(argv)
    try:
        if args.command == "prepare":
            result = build_catalog(args.source, args.output, args.reports, args.sample_size)
        elif args.command == "index":
            encoder = FastEmbedEncoder(args.dense_model) if args.dense_model else None
            result = build_index(
                Catalog(args.catalog),
                args.output,
                backend=args.backend,
                limit=args.limit,
                encoder=encoder,
                batch_size=args.batch_size,
                url=os.getenv("QDRANT_URL"),
                api_key=os.getenv("QDRANT_API_KEY"),
            )
        else:
            params = (
                SearchParams.model_validate_json(args.params.read_text(encoding="utf-8"))
                if args.params
                else SearchParams(
                    query_spec=QuerySpec(
                        raw_query=args.query,
                        normalized_query=args.query,
                        sku=args.sku,
                        intent="exact_product" if args.mode == "sku" else "product_search",
                        category=args.category,
                        hard_filters=HardFilters(
                            os=args.os,
                            country=args.country,
                            devices=args.devices,
                            max_total_price_kopecks=args.max_price_kopecks,
                            in_stock=args.in_stock,
                        ),
                    ),
                    mode=args.mode,
                    top_k=args.top_k,
                )
            )
            encoder = FastEmbedEncoder(args.dense_model) if args.dense_model else None
            with SearchEngine(
                args.catalog,
                args.index,
                encoder=encoder,
                url=os.getenv("QDRANT_URL"),
                api_key=os.getenv("QDRANT_API_KEY"),
            ) as engine:
                response = engine.search(params)
            if args.compact:
                print(f"status={response.status}; {response.diagnostics}")
                for hit in response.hits:
                    product = hit.product
                    price = (
                        f"{product.price_kopecks / 100:.2f} RUB"
                        if product.price_kopecks
                        else "price unknown"
                    )
                    print(
                        f"{hit.rank}. [{product.product_id}] {product.title} | {price} | sku={product.sku}"
                    )
                if response.warnings:
                    print("warnings:", "; ".join(response.warnings))
                return 2 if response.status == "unavailable" else 0
            result = response.model_dump(mode="json")
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 2 if result.get("status") == "unavailable" else 0
    except (OSError, ValueError) as error:
        print(f"Error: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
