"""Measure a new search client and subsequent BM25 calls without invoking the graph."""

import argparse
import json
from os import getenv
from statistics import median
from time import perf_counter

from softline_search.contracts import HardFilters, QuerySpec, SearchParams
from softline_search.search import SearchEngine


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--query", default="сервер", help="Normalized text passed to BM25")
    parser.add_argument("--category", default="server_hardware")
    parser.add_argument("--max-price-kopecks", type=int, default=30_000_000)
    parser.add_argument("--warm-runs", type=int, default=8)
    args = parser.parse_args()
    if args.warm_runs < 1:
        parser.error("--warm-runs must be positive")
    params = SearchParams(
        query_spec=QuerySpec(
            raw_query=args.query,
            normalized_query=args.query,
            category=args.category or None,
            hard_filters=HardFilters(max_total_price_kopecks=args.max_price_kopecks),
        ),
        mode="bm25",
        top_k=12,
    )
    url = getenv("QDRANT_URL") or None
    started = perf_counter()
    with SearchEngine(
        getenv("SOFTLINE_CATALOG_DIR", "data/catalog"),
        getenv("SOFTLINE_INDEX_DIR", "data/qdrant"),
        url=url,
        api_key=getenv("QDRANT_API_KEY") or None,
    ) as engine:
        first = engine.search(params)
        cold_ms = (perf_counter() - started) * 1000
        if first.status == "unavailable":
            raise SystemExit(f"BM25 unavailable: {first.warnings}")
        warm = []
        for _ in range(args.warm_runs):
            result = engine.search(params)
            if result.status == "unavailable":
                raise SystemExit(f"BM25 unavailable: {result.warnings}")
            warm.append(result.diagnostics["latency_ms"])
    print(
        json.dumps(
            {
                "transport": "server" if url else "embedded",
                "query": args.query,
                "category": args.category,
                "max_price_kopecks": args.max_price_kopecks,
                "diagnostics": first.diagnostics,
                "first_hits": [hit.product.product_id for hit in first.hits],
                "cold_client_ms": round(cold_ms, 3),
                "cold_query_ms": first.diagnostics["latency_ms"],
                "warm_samples_ms": warm,
                "warm_median_ms": median(warm),
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
