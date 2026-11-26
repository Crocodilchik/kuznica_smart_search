"""Reproducible catalog exports, SQLite lookup and data-quality report."""

from __future__ import annotations

import hashlib
import json
import random
import re
import sqlite3
import time
from collections import Counter, defaultdict
from collections.abc import Iterator
from contextlib import closing
from pathlib import Path

from .contracts import Product, QuerySpec, SearchParams, SearchResult
from .feed import category_chain, iter_records, normalize_offer
from .text import clean_text, meaningful, sku_key

ALIAS_PATTERNS = {
    "endpoint_security": r"^Антивирусы для |^Российские антивирусы|защита.*(?:рабоч|конеч)",
    "security": r"^Антивирусы и безопасность$",
    "server_hardware": r"^(?:(?:Rack|Tower|Blade)[ -]?серверы|Микросерверы|Серверы)$",
    "server_os": r"серверные операционные|операционные системы.*сервер|серверные ОС",
    "operating_systems": r"^Операционные системы$",
    "backup": r"резервн.*копир|^Резервное копирование$",
    "virtualization": r"виртуализац",
    "ups": r"источники бесперебойного питания|^ИБП$",
}


def build_aliases(categories: dict[str, dict]) -> dict[str, list[str]]:
    aliases = {
        alias: [
            key for key, category in categories.items() if re.search(pattern, category["name"], re.IGNORECASE)
        ]
        for alias, pattern in ALIAS_PATTERNS.items()
    }
    if not aliases["server_os"]:
        aliases["server_os"] = [
            key
            for key, category in categories.items()
            if "2052" in category.get("path_ids", [])
            and re.search(r"сервер|server", category["name"], re.IGNORECASE)
        ]
    return aliases


def dump_json(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)


def build_catalog(source: Path, output: Path, reports: Path, sample_size: int = 300) -> dict:
    started = time.perf_counter()
    output.mkdir(parents=True, exist_ok=True)
    reports.mkdir(parents=True, exist_ok=True)
    db_path = output / "catalog.sqlite"
    temporary_db = output / "catalog.sqlite.tmp"
    if temporary_db.exists():
        temporary_db.unlink()  # Only our own incomplete build in the selected output folder.
    categories: dict[str, dict] = {}
    counts: dict[str, Counter] = defaultdict(Counter)
    attributes: dict[str, Counter] = defaultdict(Counter)
    sample_pools: dict[str, list[dict]] = defaultdict(list)
    seen_per_root: Counter = Counter()
    rng = random.Random(42)
    total = rejected = 0
    content_hash = hashlib.sha256()
    source_date = None
    with (
        closing(sqlite3.connect(temporary_db)) as connection,
        (output / "products.jsonl.tmp").open("w", encoding="utf-8") as products_file,
        (reports / "rejected_offers.jsonl").open("w", encoding="utf-8") as rejected_file,
    ):
        connection.execute(
            "CREATE TABLE products(product_id TEXT PRIMARY KEY, sku_key TEXT, payload TEXT NOT NULL)"
        )
        for kind, element in iter_records(source):
            if kind == "metadata":
                source_date = element.get("date")
            elif kind == "category":
                category_id = element.get("id", "")
                if category_id in categories:
                    raise ValueError(f"Duplicate category id {category_id}")
                categories[category_id] = {
                    "id": category_id,
                    "name": clean_text(element.text),
                    "parent_id": element.get("parentId"),
                }
            elif kind == "offer":
                total += 1
                for param in element.findall("param"):
                    if not meaningful(param.text):
                        counts["placeholder_params"][clean_text(param.get("name"))] += 1
                try:
                    product = normalize_offer(element, categories, source_date)
                    payload = product.model_dump_json()
                    connection.execute(
                        "INSERT INTO products VALUES (?, ?, ?)",
                        (product.product_id, sku_key(product.sku), payload),
                    )
                except (ValueError, sqlite3.IntegrityError) as error:
                    rejected += 1
                    rejected_file.write(
                        json.dumps({"offer_id": element.get("id"), "reason": str(error)}, ensure_ascii=False)
                        + "\n"
                    )
                    continue
                products_file.write(payload + "\n")
                content_hash.update((payload + "\n").encode("utf-8"))
                counts["availability"][str(product.in_stock)] += 1
                counts["product_type"][product.product_type] += 1
                counts["price_status"]["known" if product.price_kopecks is not None else "unknown"] += 1
                counts["brand"][product.brand or "<unknown>"] += 1
                counts["category_id"][product.category_id or "<unknown>"] += 1
                counts["license_type"][product.license_type or "<unknown>"] += 1
                for warning in product.data_warnings:
                    counts["data_warnings"][warning] += 1
                for name in ("sku", "price_kopecks", "license_units", "country", "os", "min_purchase"):
                    counts["known_fields"][name] += int(bool(getattr(product, name)))
                for name, value in product.attributes.items():
                    if isinstance(value, list):
                        attributes[name].update(value)
                root = product.category_ids[0] if product.category_ids else "<unknown>"
                counts["root_category"][root] += 1
                seen_per_root[root] += 1
                pool = sample_pools[root]
                if len(pool) < 12:
                    pool.append(product.model_dump(mode="json"))
                else:
                    position = rng.randrange(seen_per_root[root])
                    if position < len(pool):
                        pool[position] = product.model_dump(mode="json")
                if total % 25000 == 0:
                    connection.commit()
                    print(f"Normalized {total:,} offers", flush=True)
        connection.execute("CREATE INDEX products_sku ON products(sku_key)")
        connection.commit()
    for category_id, category in categories.items():
        ids = category_chain(category_id, categories)
        category["path_ids"] = ids
        category["path"] = [categories.get(key, {}).get("name", key) for key in ids]
        category["offer_count"] = counts["category_id"][category_id]
    aliases = build_aliases(categories)
    samples = [pool[0] for pool in sample_pools.values() if pool]
    rest = [product for pool in sample_pools.values() for product in pool[1:]]
    rng.shuffle(rest)
    samples = (samples + rest)[:sample_size]
    with (output / "sample_products.jsonl").open("w", encoding="utf-8") as file:
        for product in samples:
            file.write(json.dumps(product, ensure_ascii=False) + "\n")
    dump_json(output / "categories.json", list(categories.values()))
    dump_json(output / "category_aliases.json", aliases)
    dump_json(
        output / "attributes.json",
        {
            name: {
                "occurrences": sum(values.values()),
                "unique_values": len(values),
                "top_values": dict(values.most_common(20)),
            }
            for name, values in attributes.items()
        },
    )
    for model in (Product, QuerySpec, SearchParams, SearchResult):
        dump_json(Path("schemas") / f"{model.__name__}.json", model.model_json_schema())
    with source.open("rb") as file:
        source_hash = hashlib.file_digest(file, "sha256").hexdigest()
    manifest = {
        "schema_version": 1,
        "source_name": source.name,
        "source_sha256": source_hash,
        "content_sha256": content_hash.hexdigest(),
        "source_date": source_date,
        "source_bytes": source.stat().st_size,
        "offers_seen": total,
        "products": total - rejected,
        "rejected": rejected,
        "categories": len(categories),
        "sample_products": len(samples),
        "statistics": {name: dict(counter.most_common()) for name, counter in counts.items()},
        "elapsed_seconds": round(time.perf_counter() - started, 3),
    }
    temporary_db.replace(db_path)
    (output / "products.jsonl.tmp").replace(output / "products.jsonl")
    dump_json(output / "manifest.json", manifest)
    dump_json(reports / "catalog_profile.json", manifest)
    report = [
        "# Анализ товарного фида Softline",
        "",
        f"Снимок: {source_date}; SHA-256: `{source_hash}`.",
        "",
        f"Предложений: **{total:,}**; принято: **{total - rejected:,}**; отклонено: **{rejected}**.",
        f"Категорий: **{len(categories):,}**; тестовый срез: **{len(samples)}**.",
        "",
        "## Заполненность нормализованных полей",
        "",
        "| Поле | Известно | Доля |",
        "| --- | ---: | ---: |",
    ]
    for name, count in counts["known_fields"].items():
        report.append(f"| {name} | {count:,} | {count / max(1, total - rejected):.1%} |")
    report += [
        "",
        "## Выводы для реализации",
        "",
        "- Нулевая цена преобразуется в null: бесплатность по этому фиду не подтверждена.",
        "- Пустой vendorCode не заменяется offer id или фрагментом URL. По product_id есть отдельный lookup.",
        "- Цена хранится целым числом копеек; валюта RUR нормализуется без конвертации.",
        "- Повторяющиеся param сохраняются списком; значения-заглушки исключаются из поиска.",
        "- Платформа извлекается из структурированной части description, а не из упоминаний в названии.",
        "- Страна не определяется по бренду, языку интерфейса или записи в реестре ПО.",
        "- К-во пользователей до, ценовые диапазоны и минимальная покупка не равны ёмкости лицензии.",
        "- Ёмкость берётся только из однозначного указания в названии с сохранением типа единицы.",
        "- Типы лицензий (продление, техподдержка, обновление) сохранены отдельно.",
        "- available и цена относятся к дате снимка; текущая доступность не проверялась.",
        "- Категории программ и оборудования сохранены с предками, чтобы их можно было явно разделять.",
        "",
        "## Типы предложений",
        "",
    ]
    report += [f"- {name}: {count:,}" for name, count in counts["product_type"].items()]
    report += ["", "## Качество", ""]
    report += [f"- {name}: {count:,}" for name, count in counts["data_warnings"].items()]
    (reports / "eda.md").write_text("\n".join(report) + "\n", encoding="utf-8")
    return manifest


class Catalog:
    def __init__(self, directory: Path | str):
        self.directory = Path(directory).resolve()
        self.db_path = self.directory / "catalog.sqlite"
        if not self.db_path.is_file():
            raise FileNotFoundError(f"Catalog is not built: {self.db_path}")
        self.categories = json.loads((self.directory / "categories.json").read_text(encoding="utf-8"))
        self.aliases = json.loads((self.directory / "category_aliases.json").read_text(encoding="utf-8"))
        self.manifest = json.loads((self.directory / "manifest.json").read_text(encoding="utf-8"))

    def connect(self):
        return sqlite3.connect(self.db_path.as_uri() + "?mode=ro", uri=True)

    def iter_products(self, limit: int | None = None) -> Iterator[Product]:
        with closing(self.connect()) as connection:
            cursor = connection.execute(
                "SELECT payload FROM products ORDER BY rowid LIMIT ?", (-1 if limit is None else limit,)
            )
            for (payload,) in cursor:
                yield Product.model_validate_json(payload)

    def by_sku(self, sku: str) -> list[Product]:
        key = sku_key(sku)
        if not key:
            return []
        with closing(self.connect()) as connection:
            return [
                Product.model_validate_json(row[0])
                for row in connection.execute(
                    "SELECT payload FROM products WHERE sku_key=? ORDER BY product_id", (key,)
                )
            ]

    def get_product(self, product_id: str) -> Product | None:
        with closing(self.connect()) as connection:
            row = connection.execute(
                "SELECT payload FROM products WHERE product_id=?", (product_id,)
            ).fetchone()
        return Product.model_validate_json(row[0]) if row else None

    def resolve_category(self, category: str | None) -> list[str] | None:
        if not category:
            return None
        if category in self.aliases:
            return self.aliases[category]
        key = category.strip().casefold()
        return [
            item["id"]
            for item in self.categories
            if item["id"] == category
            or item["name"].casefold() == key
            or " > ".join(item["path"]).casefold() == key
        ]
