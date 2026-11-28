import pytest

from scripts.evaluate import JudgedQuery, evaluate, ranking_metrics
from smart_search.runtime import load_services


def test_ranking_scores_use_fixed_precision_denominator_and_ideal_dcg():
    result = ranking_metrics(["outside", "good", "good"], {"good": 2, "best": 3})

    assert result.precision_at_4 == 0.25
    assert result.ndcg_at_10 == pytest.approx((3 / 1.5849625) / (7 + 3 / 1.5849625))


def test_evaluation_compares_all_modes_on_the_same_queries():
    queries = [
        JudgedQuery(query="Сервер", grades={"531625": 2, "438239": 2, "467607": 2, "542026": 2}),
        JudgedQuery(query="Заклинание заморозки", grades={}),
    ]
    reports = evaluate(queries, load_services())

    assert [report.mode for report in reports] == ["bm25", "dense", "hybrid", "agentic_hybrid"]
    assert all(report.query_count == 2 for report in reports)
    assert reports[-1].precision_at_4 == 0.5
    assert reports[-1].empty_rate == 0.5
    assert reports[-1].p95_latency_ms >= 0
