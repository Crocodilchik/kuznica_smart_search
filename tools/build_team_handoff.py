"""Export validated team contracts, real catalog records and a compact transfer archive."""

from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

from softline_search.catalog import Catalog, dump_json
from softline_search.contracts import HardFilters, Product, QuerySpec, SearchParams, SearchResult
from softline_search.search import SearchEngine, matches_filters

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "handoff" / "dev3-contracts-v1"


def export_model(relative_path: str, model) -> None:
    data = model.model_dump(mode="json")
    type(model).model_validate(data)
    dump_json(OUTPUT / relative_path, data)


def copy(relative_source: str, relative_target: str | None = None) -> None:
    target = OUTPUT / (relative_target or relative_source)
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(ROOT / relative_source, target)


def main() -> None:
    OUTPUT.mkdir(parents=True, exist_ok=True)
    catalog = Catalog(ROOT / "data/catalog")
    spec = QuerySpec(
        raw_query="Нужно ПО для инвентаризации 50 ПК под Windows в наличии до 100 тысяч рублей",
        normalized_query="инвентаризация компьютеров",
        intent="product_search",
        category="23336",
        hard_filters=HardFilters(
            os="Windows",
            devices=50,
            max_total_price_kopecks=10_000_000,
            budget_scope="primary",
            in_stock=True,
        ),
    )
    params = SearchParams(query_spec=spec, mode="bm25", top_k=10)
    exact_spec = QuerySpec(
        raw_query="AL16-03SWS701-0100",
        normalized_query="AL16-03SWS701-0100",
        intent="exact_product",
        sku="AL16-03SWS701-0100",
    )
    exact_params = SearchParams(query_spec=exact_spec, mode="sku", top_k=4)
    russian = QuerySpec(
        raw_query="Российский антивирус для Windows на 50 ПК до 100 тысяч рублей",
        normalized_query="корпоративное антивирусное ПО",
        category="endpoint_security",
        hard_filters=HardFilters(
            os="Windows", country="RU", devices=50, max_total_price_kopecks=10_000_000, budget_scope="primary"
        ),
    )
    server_spec = QuerySpec(
        raw_query="Подбери сервер и сопутствующие решения",
        normalized_query="сервер",
        intent="solution",
        category="server_hardware",
    )
    complements = SearchParams(
        query_spec=QuerySpec(
            raw_query="Найти кандидатов в источники бесперебойного питания для сервера",
            normalized_query="источник бесперебойного питания",
            intent="complements",
            category="ups",
        ),
        mode="bm25",
        top_k=10,
    )
    models = {
        "dev2_query_spec_inventory.json": spec,
        "dev2_query_spec_exact_sku.json": exact_spec,
        "dev2_query_spec_russian_strict.json": russian,
        "search_params_bm25.json": params,
        "search_params_sku.json": exact_params,
        "search_params_russian_strict.json": SearchParams(query_spec=russian, mode="bm25", top_k=10),
        "search_params_complements_ups.json": complements,
    }
    for name, model in models.items():
        export_model("examples/" + name, model)
    cards = {}
    for name, product_id in {
        "software": "49033",
        "antivirus": "533196",
        "server": "542029",
        "unknown": "504",
    }.items():
        product = catalog.get_product(product_id)
        if product is None:
            raise ValueError(f"Missing catalog example {product_id}")
        cards[name] = product
        export_model(f"examples/product_{name}.json", product)
    assert matches_filters(cards["software"], spec, catalog.resolve_category(spec.category))
    for name, query, anchors in (
        ("inventory", spec, [cards["software"]]),
        ("server", server_spec, [cards["server"]]),
    ):
        payload = {
            "query_spec": query.model_dump(mode="json"),
            "anchors": [product.model_dump(mode="json") for product in anchors],
        }
        QuerySpec.model_validate(payload["query_spec"])
        for product in payload["anchors"]:
            Product.model_validate(product)
        dump_json(OUTPUT / f"examples/recommendation_input_{name}.json", payload)
    with SearchEngine(catalog.directory, ROOT / "data/qdrant") as engine:
        result = engine.search(params)
        assert result.status == "ok" and result.hits
        assert all(
            matches_filters(hit.product, spec, catalog.resolve_category(spec.category)) for hit in result.hits
        )
        export_model("examples/search_result_bm25.json", result)
        export_model("examples/search_result_sku.json", engine.search(exact_params))
        empty_result = engine.search(SearchParams(query_spec=russian))
        assert empty_result.status == "empty" and "country_unknown_in_catalog" in empty_result.warnings
        export_model("examples/search_result_country_unknown.json", empty_result)
    missing_index = OUTPUT / "_not_an_index"
    assert not (missing_index / "index.json").exists()
    with SearchEngine(catalog.directory, missing_index) as engine:
        failure = engine.search(params)
        assert failure.status == "unavailable"
        export_model("examples/search_result_unavailable_simulated.json", failure)
    for model in (QuerySpec, SearchParams, Product, SearchResult):
        dump_json(OUTPUT / "schemas" / f"{model.__name__}.json", model.model_json_schema())
    for name in (
        "categories.json",
        "category_aliases.json",
        "attributes.json",
        "sample_products.jsonl",
        "manifest.json",
    ):
        copy(f"data/catalog/{name}")
    for file in (ROOT / "softline_search").glob("*.py"):
        copy(file.relative_to(ROOT).as_posix())
    for name in (
        "contracts.py",
        "pyproject.toml",
        "requirements.lock.txt",
        "docs/INTEGRATION.md",
        "project-agent-roles.md",
        "tools/build_team_handoff.py",
        "reports/eda.md",
        "reports/catalog_profile.json",
        "reports/smoke_results.json",
    ):
        copy(name)
    copy("README.md", "PROJECT_README.md")
    copy("docs/TEAM_HANDOFF.md", "docs/CONTRACTS.md")
    copy("reports/eda.md", "docs/EDA.md")
    dump_json(
        OUTPUT / "capabilities.json",
        {
            "contract_revision": "dev3-contracts-v1",
            "transport": "python_functions_and_json",
            "catalog_products": catalog.manifest["products"],
            "catalog_categories": catalog.manifest["categories"],
            "catalog_source_date": catalog.manifest["source_date"],
            "catalog_source_sha256": catalog.manifest["source_sha256"],
            "active_modes": ["sku", "bm25"],
            "dense_vectors_present": False,
            "country_known_products": catalog.manifest["statistics"]["known_fields"]["country"],
            "sample_products": catalog.manifest["sample_products"],
            "full_catalog_sqlite_included": False,
            "qdrant_storage_included": False,
            "raw_feed_included": False,
        },
    )
    readme = """# Пакет разработчика 3 для команды

Полная спецификация: [docs/CONTRACTS.md](docs/CONTRACTS.md).
Все JSON-примеры проверены действующими Pydantic-моделями. Карточки взяты из реального фида.

| Кому | Что открыть |
| --- | --- |
| Разработчик 2 | [QuerySpec](examples/dev2_query_spec_inventory.json), [Schema](schemas/QuerySpec.json), справочники в data/catalog |
| Разработчик 4 | [Карточка ПО](examples/product_software.json), [карточка сервера](examples/product_server.json), [Product Schema](schemas/Product.json), [вход рекомендаций](examples/recommendation_input_server.json) |
| Разработчик 1 | [SearchParams](examples/search_params_bm25.json), [результат BM25](examples/search_result_bm25.json), [результат SKU](examples/search_result_sku.json), исходный код и зависимости |

`country=RU` в текущем фиде даёт предупреждение об отсутствии метаданных страны; отдельный пример это показывает.
`search_result_unavailable_simulated.json` получен контролируемым обращением к отсутствующему индексу.
Пакет содержит 300 тестовых карточек и полные справочники. Большой SQLite-каталог, Qdrant-индекс, XML и .venv не включены.
Для запуска полного поиска скопируйте соответствующие данные той же сборки либо подготовьте их из XML.
Не заменяйте общие модели разработчика 1 без объединения контрактов.
"""
    (OUTPUT / "README.md").write_text(readme, encoding="utf-8")
    # Verify every data file before creating the deliverable archive.
    for file in OUTPUT.rglob("*.json"):
        json.loads(file.read_text(encoding="utf-8"))
    count = 0
    for line in (OUTPUT / "data/catalog/sample_products.jsonl").read_text(encoding="utf-8").splitlines():
        Product.model_validate_json(line)
        count += 1
    assert count == catalog.manifest["sample_products"]
    checksum_manifest = {}
    for file in OUTPUT.rglob("*"):
        if file.is_file() and file.name != "checksums.json":
            checksum_manifest[file.relative_to(OUTPUT).as_posix()] = hashlib.sha256(
                file.read_bytes()
            ).hexdigest()
    dump_json(OUTPUT / "checksums.json", checksum_manifest)
    archive = OUTPUT.with_suffix(".zip")
    with ZipFile(archive, "w", compression=ZIP_DEFLATED, compresslevel=6) as file:
        for source in sorted(OUTPUT.rglob("*")):
            if source.is_file():
                file.write(source, (Path(OUTPUT.name) / source.relative_to(OUTPUT)).as_posix())
    with ZipFile(archive) as file:
        assert file.testzip() is None
    print(
        json.dumps(
            {
                "folder": str(OUTPUT),
                "archive": str(archive),
                "archive_bytes": archive.stat().st_size,
                "files": len(checksum_manifest) + 1,
                "sample_products_checked": count,
                "bm25_hits": len(result.hits),
                "status": "validated",
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
