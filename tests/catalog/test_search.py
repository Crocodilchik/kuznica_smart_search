import pytest
from pydantic import ValidationError

from softline_search.contracts import HardFilters, QuerySpec, SearchParams
from softline_search.retrieval import build_index, rrf
from softline_search.search import SearchEngine


def params(query="защита", **kwargs):
    return SearchParams(
        query_spec=QuerySpec(raw_query=query, normalized_query=query, hard_filters=HardFilters(**kwargs)),
        top_k=4,
    )


@pytest.fixture
def index(catalog, tmp_path):
    directory = tmp_path / "qdrant"
    manifest = build_index(catalog, directory)
    assert manifest["backend"] == "fulltext"
    return directory


def test_native_bm25_filters_and_persistent_reopen(catalog, index):
    for _ in range(2):
        with SearchEngine(catalog.directory, index) as engine:
            result = engine.search(params(os="windows", in_stock=True, max_total_price_kopecks=100000))
            assert result.status == "ok"
            assert [hit.product.product_id for hit in result.hits] == ["1"]
            assert result.hits[0].score > 0
            assert engine.search(params("несуществующийтермин")).status == "empty"
            assert engine.search(params("антииииии", country="RU")).hits == []
            assert engine.search(params("защита", devices=50)).hits
            # Missing currency/price and unsupported operating systems do not satisfy hard filters.
            assert all(hit.product.price_kopecks is not None for hit in result.hits)


def test_sku_and_category_use_the_same_constraints(catalog, index):
    with SearchEngine(catalog.directory, index) as engine:
        request = params("SEC-50/A", os="Linux")
        request.mode = "sku"
        assert engine.search(request).hits[0].product.product_id == "1"
        request.query_spec.hard_filters.in_stock = False
        assert engine.search(request).hits == []
        request = params("защита")
        request.query_spec.category = "server_hardware"
        assert engine.search(request).hits == []
        request.query_spec.category = "misspelled_unknown_category"
        assert "category_unresolved" in engine.search(request).warnings


def test_sku_diagnostics_remain_correct_after_bm25(catalog, index):
    with SearchEngine(catalog.directory, index) as engine:
        assert engine.search(params()).hits
        request = params("SEC-50/A")
        request.mode = "sku"
        assert engine.search(request).diagnostics["backend"] == "sqlite_exact_sku"


def test_missing_index_differs_from_empty_and_sku_still_works(catalog, tmp_path):
    with SearchEngine(catalog.directory, tmp_path / "absent") as engine:
        assert engine.search(params()).status == "unavailable"
        request = params("SEC-50/A")
        request.mode = "sku"
        assert engine.search(request).status == "ok"


def test_dense_is_not_faked(catalog, index):
    with SearchEngine(catalog.directory, index) as engine:
        request = params()
        request.mode = "dense"
        assert engine.search(request).status == "unavailable"
        request.mode = "hybrid"
        result = engine.search(request)
        assert result.status == "partial" and result.hits
        assert any("dense_not_configured" in warning for warning in result.warnings)


def test_native_sparse_fallback_has_real_scores(catalog, tmp_path):
    directory = tmp_path / "sparse"
    manifest = build_index(catalog, directory, backend="sparse")
    assert manifest["backend"] == "sparse"
    with SearchEngine(catalog.directory, directory) as engine:
        result = engine.search(params(os="Windows", max_total_price_kopecks=100000))
        assert result.status == "ok" and result.hits[0].product.product_id == "1"
        assert result.hits[0].score > 0


class ToyEncoder:
    """Deterministic integration fixture, not a production semantic model."""

    model_name = "test-only"
    dimension = 2

    def documents(self, texts):
        return [[1.0, 0.1] if "устройств" in text else [0.1, 1.0] for text in texts]

    def query(self, text):
        return [1.0, 0.1]


def test_hybrid_and_dense_apply_identical_hard_filters(catalog, tmp_path):
    directory = tmp_path / "hybrid"
    build_index(catalog, directory, encoder=ToyEncoder())
    with SearchEngine(catalog.directory, directory, encoder=ToyEncoder()) as engine:
        for mode in ("dense", "hybrid"):
            request = params(os="Windows", max_total_price_kopecks=100000)
            request.mode = mode
            result = engine.search(request)
            assert result.status == "ok"
            assert [hit.product.product_id for hit in result.hits] == ["1"]


def test_rrf_preserves_unique_ids(catalog):
    first, second = catalog.get_product("1"), catalog.get_product("2")
    result = rrf([[(first, 9.0), (second, 8.0)], [(second, 0.9), (first, 0.8)]])
    assert len(result) == 2
    assert result[0][1] == pytest.approx(1 / 61 + 1 / 62)


def test_invalid_money_and_devices_are_rejected():
    with pytest.raises(ValidationError):
        HardFilters(max_total_price_kopecks=123.45)
    with pytest.raises(ValidationError):
        HardFilters(devices=0)
    with pytest.raises(ValidationError):
        HardFilters(devices=True)


def test_country_and_device_capacity_are_not_guessed(catalog, index):
    with SearchEngine(catalog.directory, index) as engine:
        result = engine.search(params("антивирус", country="RU"))
        assert {hit.product.product_id for hit in result.hits} <= {"1"}
        result = engine.search(params("антивирус", devices=1000))
        assert not result.hits and result.status == "empty"


def test_empty_query_does_not_return_random_products(catalog, index):
    with SearchEngine(catalog.directory, index) as engine:
        result = engine.search(params("для и в"))
        assert result.status == "empty" and not result.hits
        assert "query_has_no_search_terms" in result.warnings


def test_auto_falls_back_only_when_text_scoring_is_unavailable(catalog, tmp_path, monkeypatch):
    def unsupported(*args):
        raise NotImplementedError("text scoring unsupported")

    monkeypatch.setattr("softline_search.retrieval.ensure_fulltext", unsupported)
    directory = tmp_path / "fallback"
    manifest = build_index(catalog, directory)
    assert manifest["backend"] == "sparse" and manifest["warnings"]
    with SearchEngine(catalog.directory, directory) as engine:
        assert engine.search(params()).hits


def test_index_for_a_different_source_is_rejected(catalog, index):
    import json

    path = index / "index.json"
    metadata = json.loads(path.read_text(encoding="utf-8"))
    metadata["catalog_source_sha256"] = "wrong-source"
    path.write_text(json.dumps(metadata), encoding="utf-8")
    with SearchEngine(catalog.directory, index) as engine:
        result = engine.search(params())
        assert result.status == "unavailable"
        assert any("catalog_index_source_mismatch" in warning for warning in result.warnings)


def test_index_for_previous_normalization_is_rejected(catalog, index):
    import json

    path = index / "index.json"
    metadata = json.loads(path.read_text(encoding="utf-8"))
    metadata["catalog_content_sha256"] = "previous-normalization"
    path.write_text(json.dumps(metadata), encoding="utf-8")
    with SearchEngine(catalog.directory, index) as engine:
        assert engine.search(params()).status == "unavailable"
