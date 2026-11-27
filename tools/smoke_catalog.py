"""Functional checks and measured latency on the supplied catalog; not a relevance benchmark."""

import argparse
import statistics
import time
from pathlib import Path

from softline_search.catalog import dump_json
from softline_search.contracts import HardFilters, QuerySpec, SearchParams
from softline_search.search import SearchEngine


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--index", type=Path, default=Path("data/qdrant"))
    parser.add_argument("--output", type=Path, default=Path("reports/smoke_results.json"))
    args = parser.parse_args()
    results = []
    cases = [
        ("exact_sku", "AL16-03SWS701-0100", "sku", None, {}),
        ("product_name", "Kaspersky Endpoint Security", "bm25", None, {}),
        ("budget_stock", "антивирус", "bm25", None, {"in_stock": True, "max_total_price_kopecks": 10000000}),
        ("server_category", "сервер", "bm25", "server_hardware", {}),
        ("backup", "резервное копирование", "bm25", None, {}),
        (
            "device_budget",
            "инвентаризация компьютеров",
            "bm25",
            None,
            {"devices": 50, "in_stock": True, "max_total_price_kopecks": 10000000},
        ),
        ("zero_results", "несуществующийтоварzxqv928573", "bm25", None, {}),
        ("unknown_country", "антивирус", "bm25", None, {"country": "RU"}),
    ]
    started = time.perf_counter()
    with SearchEngine(index_dir=args.index) as engine:
        for name, query, mode, category, filters in cases:
            params = SearchParams(
                query_spec=QuerySpec(
                    raw_query=query,
                    normalized_query=query,
                    category=category,
                    hard_filters=HardFilters(**filters),
                ),
                mode=mode,
                top_k=4,
            )
            result = engine.search(params)
            if name == "exact_sku":
                assert result.hits and result.hits[0].product.sku.casefold() == query.casefold()
            if name in {"zero_results", "unknown_country"}:
                assert result.status == "empty" and not result.hits
            if name == "budget_stock":
                assert result.hits
                assert all(
                    hit.product.in_stock is True and 0 < hit.product.price_kopecks <= 10000000
                    for hit in result.hits
                )
            if name == "device_budget":
                assert result.hits
                assert all(
                    hit.product.license_unit_type == "device"
                    and hit.product.license_units >= 50
                    and hit.product.price_kopecks <= 10000000
                    and hit.product.in_stock is True
                    for hit in result.hits
                )
            if name in {"product_name", "server_category", "backup"}:
                assert result.status == "ok" and result.hits
            row = {
                "case": name,
                "status": result.status,
                "warnings": result.warnings,
                "diagnostics": result.diagnostics,
                "hits": [
                    {
                        "product_id": hit.product.product_id,
                        "title": hit.product.title,
                        "price_kopecks": hit.product.price_kopecks,
                        "in_stock": hit.product.in_stock,
                        "category": hit.product.category,
                        "score": hit.score,
                    }
                    for hit in result.hits
                ],
            }
            results.append(row)
            print(
                f"{name}: {result.status}, hits={len(result.hits)}, {result.diagnostics.get('latency_ms')} ms",
                flush=True,
            )
    latencies = [row["diagnostics"]["latency_ms"] for row in results if row["case"] != "exact_sku"]
    dump_json(
        args.output,
        {
            "note": "Functional smoke checks; queries are not relevance labels. First BM25 includes loading.",
            "cases": results,
            "median_bm25_ms": statistics.median(latencies),
            "elapsed_seconds": round(time.perf_counter() - started, 3),
        },
    )


if __name__ == "__main__":
    main()
