"""Run all candidate configurations against the same graded query set."""

import argparse
import json
from math import log2
from pathlib import Path
from statistics import mean
from time import perf_counter
from typing import Annotated, Literal
from uuid import uuid4

from pydantic import Field, TypeAdapter

from smart_search.analytics import percentile_95, summarize
from smart_search.contracts import (
    Contract,
    NonEmptyText,
    QuerySpec,
    RecommendationResult,
    SearchEvent,
    SearchParams,
    SearchRequest,
    SearchStatus,
)
from smart_search.runtime import load_services
from smart_search.supervisor import DEPENDENCY_FAILURES, build_search
from smart_search.tools import Services

Grade = Annotated[int, Field(strict=True, ge=0, le=3)]
EvaluationMode = Literal["bm25", "dense", "hybrid", "agentic_hybrid"]
MODES: tuple[EvaluationMode, ...] = ("bm25", "dense", "hybrid", "agentic_hybrid")


class JudgedQuery(Contract):
    query: NonEmptyText
    grades: dict[str, Grade]


class RankingMetrics(Contract):
    precision_at_4: float
    ndcg_at_10: float


class QueryMeasurement(RankingMetrics):
    query: str
    product_ids: list[str]
    status: SearchStatus
    latency_ms: float


class EvaluationReport(RankingMetrics):
    mode: EvaluationMode
    query_count: int
    empty_rate: float
    p95_latency_ms: float
    status_counts: dict[str, int]
    queries: list[QueryMeasurement]


def ranking_metrics(product_ids: list[str], grades: dict[str, int]) -> RankingMetrics:
    unique_ids = list(dict.fromkeys(product_ids))
    ranked = [grades.get(identifier, 0) for identifier in unique_ids]
    ideal = sorted(grades.values(), reverse=True)

    def dcg(values: list[int]) -> float:
        return sum(
            (2**grade - 1) / log2(position + 1) for position, grade in enumerate(values[:10], 1)
        )

    ideal_dcg = dcg(ideal)
    return RankingMetrics(
        precision_at_4=sum(grade > 0 for grade in ranked[:4]) / 4,
        ndcg_at_10=dcg(ranked) / ideal_dcg if ideal_dcg else 0.0,
    )


def evaluate(queries: list[JudgedQuery], services: Services) -> list[EvaluationReport]:
    if not queries:
        raise ValueError("The evaluation set must contain at least one query.")
    agent = build_search(services)
    reports = []
    for mode in MODES:
        measurements = []
        events = []
        for item in queries:
            request = SearchRequest(query=item.query, request_id=str(uuid4()))
            started = perf_counter()
            if mode == "agentic_hybrid":
                output = agent(request)
                ids = [product.product_id for product in output.products]
                status = output.status
            else:
                spec = QuerySpec(raw_query=item.query, normalized_query=item.query.casefold())
                ids, status = [], "unavailable"
                try:
                    found = services.search(SearchParams(query_spec=spec, mode=mode))
                    if found.available:
                        checked = services.validate(spec, found, RecommendationResult(), [])
                        ids = [product.product_id for product in checked.products]
                        status = "empty" if not ids else "partial" if len(set(ids)) < 4 else "ok"
                except DEPENDENCY_FAILURES:
                    pass
            elapsed_ms = (perf_counter() - started) * 1000
            stats = ranking_metrics(ids, item.grades)
            measurements.append(
                QueryMeasurement(
                    **stats.model_dump(),
                    query=item.query,
                    product_ids=ids,
                    status=status,
                    latency_ms=elapsed_ms,
                )
            )
            events.append(
                SearchEvent(
                    request_id=request.request_id,
                    normalized_query=item.query,
                    status=status,
                    result_count=len(set(ids)),
                    latency_ms=elapsed_ms,
                )
            )
        summary = summarize(events)
        reports.append(
            EvaluationReport(
                mode=mode,
                query_count=len(measurements),
                empty_rate=summary.empty_rate,
                precision_at_4=mean(item.precision_at_4 for item in measurements),
                ndcg_at_10=mean(item.ndcg_at_10 for item in measurements),
                p95_latency_ms=percentile_95(item.latency_ms for item in measurements) or 0,
                status_counts=summary.status_counts,
                queries=measurements,
            )
        )
    return reports


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--judgments",
        type=Path,
        default=Path(__file__).resolve().parents[1] / "tests" / "fixtures" / "demo_judgments.json",
    )
    parser.add_argument(
        "--backend-module", help="Python module exporting the five service functions"
    )
    parser.add_argument("--output", type=Path, help="Write the JSON report to this path")
    args = parser.parse_args()
    queries = TypeAdapter(list[JudgedQuery]).validate_json(
        args.judgments.read_text(encoding="utf-8")
    )
    reports = evaluate(queries, load_services(args.backend_module))
    report = json.dumps(
        {
            "backend": args.backend_module or "fixture",
            "judgments": str(args.judgments),
            "reports": [item.model_dump() for item in reports],
        },
        ensure_ascii=False,
        indent=2,
    )
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(report + "\n", encoding="utf-8")
    else:
        print(report)


if __name__ == "__main__":
    main()
