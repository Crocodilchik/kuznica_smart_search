import json

import pytest

from smart_search import catalog_backend
from smart_search.contracts import HardFilters, ProductDetailsTarget, QuerySpec, SearchParams
from softline_search.catalog import Catalog, build_catalog
from softline_search.retrieval import build_index


@pytest.fixture
def catalog_paths(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    catalog = tmp_path / "catalog"
    index = tmp_path / "qdrant"
    monkeypatch.setenv("SOFTLINE_CATALOG_DIR", str(catalog))
    monkeypatch.setenv("SOFTLINE_INDEX_DIR", str(index))
    for key in ("QDRANT_URL", "QDRANT_API_KEY", "SOFTLINE_DENSE_MODEL"):
        monkeypatch.delenv(key, raising=False)
    source = tmp_path / "feed.xml"
    source.write_text(
        """<yml_catalog date="2026-11-25"><shop><categories>
        <category id="1">Программы</category>
        <category id="2" parentId="1">Антивирусы для рабочих станций</category>
        </categories><offers><offer id="100" available="true">
        <name>Антивирус на 25 устройств</name><vendorCode>AV-25/X</vendorCode>
        <categoryId>2</categoryId><price>123.45</price><currencyId>RUR</currencyId>
        <description>Платформа: Windows • Минимальная покупка: от 2 шт.</description>
        <param name="Страна происхождения">Россия</param></offer></offers></shop></yml_catalog>""",
        encoding="utf-8",
    )
    build_catalog(source, catalog, tmp_path / "reports")
    catalog_backend.close()
    yield catalog, index
    catalog_backend.close()


def params(query, mode="hybrid", **filters):
    spec = QuerySpec(
        raw_query=query,
        normalized_query=query,
        warnings=["from_understanding"],
        hard_filters=HardFilters(**filters),
    )
    return SearchParams(query_spec=spec, mode=mode)


def test_exact_sku_preserves_catalog_fields_without_index(catalog_paths):
    result = catalog_backend.search(params("av-25/x", "sku", devices=25, country="RU"))
    assert result.available and result.diagnostics["backend"] == "sqlite_exact_sku"
    product = result.hits[0].product
    assert product.product_id == "100" and product.price_kopecks == 12345
    assert product.attributes["description_fields"]["Минимальная покупка"] == "от 2 шт."
    assert (product.os, product.country, product.min_purchase) == (["Windows"], "RU", 2)
    assert product.license_units == 25 and product.license_unit_type == "device"
    assert product.category_ids == ["1", "2"]
    assert catalog_backend.category_names() == {"2": "Антивирусы для рабочих станций"}
    assert catalog_backend.resolve_category("endpoint_security") == ["2"]
    empty = catalog_backend.search(params("AV-25/X", "sku", max_total_price_kopecks=20000))
    assert empty.available and empty.hits == [] and empty.diagnostics["status"] == "empty"


def test_missing_index_and_missing_catalog_report_unavailable(catalog_paths, monkeypatch):
    catalog, index = catalog_paths
    with pytest.raises(ConnectionError):
        catalog_backend.check_ready()
    assert not index.exists()
    result = catalog_backend.search(params("антивирус"))
    assert not result.available and result.warnings
    catalog_backend.close()
    monkeypatch.setenv("SOFTLINE_CATALOG_DIR", str(catalog.parent / "missing"))
    assert not catalog_backend.search(params("AV-25/X", "sku")).available


def test_details_by_id_and_sku_do_not_open_qdrant(catalog_paths, monkeypatch):
    def unavailable():
        raise AssertionError("Catalog details must not initialize the retrieval engine")

    with monkeypatch.context() as isolated:
        isolated.setattr(catalog_backend, "_engine", unavailable)
        by_id = catalog_backend.get_product_details(ProductDetailsTarget(kind="id", value="100"))
        by_sku = catalog_backend.get_product_details(
            ProductDetailsTarget(kind="sku", value="av-25/x")
        )
        assert by_id.candidates == by_sku.candidates
        assert by_id.candidates[0].description.startswith("Платформа: Windows")


def test_bm25_only_hybrid_is_searchable_and_reports_degradation(catalog_paths):
    catalog, index = catalog_paths
    build_index(Catalog(catalog), index)
    catalog_backend.check_ready()
    result = catalog_backend.search(params("антивирус"))
    assert result.available and result.diagnostics["status"] == "partial"
    assert result.hits[0].product.product_id == "100"
    assert set(result.hits[0].branch_scores) == {"bm25"}
    assert any("dense_not_configured" in warning for warning in result.warnings)
    assert not catalog_backend.search(params("антивирус", "dense")).available
    assert catalog_backend.search(params("несуществующийтермин", "bm25")).hits == []


def test_readiness_rejects_different_catalog_generation(catalog_paths):
    catalog, index = catalog_paths
    build_index(Catalog(catalog), index)
    path = index / "index.json"
    manifest = json.loads(path.read_text(encoding="utf-8"))
    manifest["catalog_content_sha256"] = "outdated"
    path.write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(ConnectionError):
        catalog_backend.check_ready()


def test_dense_model_only_loads_for_semantic_search(catalog_paths, monkeypatch):
    initialized = []

    class Encoder:
        model_name = "fixture"
        dimension = 2

        def __init__(self, model_name):
            initialized.append(model_name)

        def documents(self, texts):
            return [[1.0, 0.0] for _ in texts]

        def query(self, text):
            return [1.0, 0.0]

    catalog, index = catalog_paths
    build_index(Catalog(catalog), index, encoder=Encoder("fixture"))
    initialized.clear()
    monkeypatch.setenv("SOFTLINE_DENSE_MODEL", "fixture")
    monkeypatch.setattr(catalog_backend, "FastEmbedEncoder", Encoder)
    assert catalog_backend.search(params("AV-25/X", "sku")).hits
    assert catalog_backend.search(params("антивирус", "bm25")).hits
    assert not initialized
    result = catalog_backend.search(params("антивирус"))
    assert result.available and result.diagnostics["status"] == "ok"
    assert set(result.hits[0].branch_scores) == {"bm25", "dense"}
    assert initialized == ["fixture"]

    def unavailable(self, text):
        raise RuntimeError("inference failed")

    monkeypatch.setattr(Encoder, "query", unavailable)
    degraded = catalog_backend.search(params("антивирус"))
    assert degraded.available and degraded.diagnostics["status"] == "partial"
    assert set(degraded.hits[0].branch_scores) == {"bm25"}
    assert any("dense_query_failed" in warning for warning in degraded.warnings)
