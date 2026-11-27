"""Streaming YML parsing with external entity resolution disabled."""

from __future__ import annotations

import gzip
import re
from collections import defaultdict
from collections.abc import Iterator
from contextlib import contextmanager
from decimal import Decimal, InvalidOperation
from pathlib import Path
from xml.etree.ElementTree import Element

from defusedxml.ElementTree import iterparse

from .contracts import Product
from .text import clean_text, meaningful


@contextmanager
def open_feed(path: Path):
    with path.open("rb") as raw:
        compressed = raw.read(2) == b"\x1f\x8b"
        raw.seek(0)
        if compressed:
            with gzip.GzipFile(fileobj=raw) as stream:
                yield stream
        else:
            yield raw


def iter_records(path: Path) -> Iterator[tuple[str, Element]]:
    """Elements remain valid until the next iteration; retain only one offer in memory."""
    with open_feed(path) as stream:
        stack: list[Element] = []
        for event, element in iterparse(
            stream, events=("start", "end"), forbid_entities=True, forbid_external=True
        ):
            if event == "start":
                stack.append(element)
                if len(stack) == 1:
                    yield "metadata", element
                continue
            tag = element.tag.rsplit("}", 1)[-1]
            if tag in {"category", "offer", "currency"}:
                yield tag, element
                element.clear()
                if len(stack) > 1:
                    stack[-2].remove(element)
            stack.pop()


def parse_price(value: str | None, currency: str | None) -> int | None:
    if currency not in {"RUR", "RUB"}:
        return None
    try:
        price = Decimal((value or "").replace("\xa0", "").replace(" ", "").replace(",", "."))
        if not price.is_finite() or price <= 0 or price * 100 != (price * 100).to_integral_value():
            return None
        return int(price * 100)
    except (InvalidOperation, ValueError):
        return None


def category_chain(category_id: str, categories: dict[str, dict]) -> list[str]:
    chain: list[str] = []
    while category_id:
        if category_id in chain:
            raise ValueError(f"Category cycle: {category_id}")
        chain.append(category_id)
        category_id = categories.get(category_id, {}).get("parent_id") or ""
    return list(reversed(chain))


def description_fields(description: str) -> dict[str, str]:
    fields = {}
    for fragment in description.split("•"):
        key, separator, value = fragment.partition(":")
        if separator and meaningful(value):
            fields[clean_text(key)] = clean_text(value)
    return fields


def license_capacity(title: str) -> tuple[int | None, str | None]:
    """Only explicit fixed quantities; upper bounds and purchase tiers aren't pack capacity."""
    pattern = (
        r"(?<![\d\-–—])\b(\d+)\s*(лицензи[яийю]+|устройств\w*|компьютер\w*|"
        r"пользовател\w*|рабочих мест|рабочих станци[ий]|ПК|PC)\b"
    )
    matches = []
    for match in re.finditer(pattern, title, re.IGNORECASE):
        before = title[max(0, match.start() - 18) : match.start()].strip().casefold()
        if re.search(r"(?:от|до|свыше|более|не менее|не более)\s*$|\d\s*[-–—]\s*$", before):
            continue
        units = int(match[1])
        if units <= 0:
            continue
        noun = match[2].casefold()
        kind = (
            "license"
            if noun.startswith("лиценз")
            else "user"
            if noun.startswith("пользовател")
            else "device"
        )
        matches.append((units, kind))
    return matches[0] if len(set(matches)) == 1 else (None, None)


def normalize_offer(
    element: Element, categories: dict[str, dict], source_date: str | None
) -> Product:
    fields = {
        child.tag.rsplit("}", 1)[-1]: clean_text("".join(child.itertext()))
        for child in element
        if child.tag.rsplit("}", 1)[-1] != "param"
    }
    product_id = meaningful(element.get("id"))
    title = meaningful(fields.get("name"))
    if not product_id or not title:
        raise ValueError("Offer must have nonempty id and name")
    params: dict[str, list[str]] = defaultdict(list)
    for child in element:
        if child.tag.rsplit("}", 1)[-1] == "param":
            key, value = clean_text(child.get("name")), meaningful("".join(child.itertext()))
            if key and value and value not in params[key]:
                params[key].append(value)
    description = fields.get("description", "")
    desc_fields = description_fields(description)
    category_id = fields.get("categoryId", "")
    ids = category_chain(category_id, categories)
    names = [categories.get(key, {}).get("name", key) for key in ids]
    family_id = meaningful(element.get("group_id")) or meaningful(fields.get("group_id"))
    family_name = categories.get(family_id or "", {}).get("name")
    price = parse_price(fields.get("price"), fields.get("currencyId"))
    warnings = []
    if price is None:
        warnings.append("price_unknown")
    sku = meaningful(fields.get("vendorCode"))
    if not sku:
        warnings.append("sku_missing")
    if category_id not in categories:
        warnings.append("category_missing")
    platforms = " ".join(
        params.get("Операционная система", [])
        + params.get("Совместимые платформы", [])
        + [desc_fields.get("Платформа", "")]
    )
    os_values = []
    for name, expression in (
        ("Windows", r"\bWindows\b"),
        ("Linux", r"\bLinux\b"),
        ("macOS", r"\bMac\s*(?:OS\s*X|OSX|OS)?\b|\bmacOS\b"),
        ("Android", r"\bAndroid\b"),
        ("iOS", r"\biOS\b"),
    ):
        if re.search(expression, platforms, re.IGNORECASE):
            os_values.append(name)
    # A vendor's name, interface language or Russian registry membership is not a country field.
    country_values = params.get("Страна происхождения", []) or params.get("Страна производства", [])
    country = None
    if len(country_values) == 1:
        raw_country = country_values[0].casefold()
        country = {"россия": "RU", "российская федерация": "RU", "ru": "RU"}.get(raw_country)
        if country is None and re.fullmatch(r"[A-Za-z]{2}", country_values[0]):
            country = country_values[0].upper()
    capacity, unit_type = license_capacity(title)
    minimum = re.fullmatch(
        r"от\s+(\d+)\s+шт\.?", desc_fields.get("Минимальная покупка", ""), re.IGNORECASE
    )
    min_purchase = int(minimum[1]) if minimum and int(minimum[1]) > 0 else None
    product_kind = (params.get("Тип продукта") or [""])[0].casefold()
    product_type = {"софт": "software", "электроника": "hardware"}.get(product_kind, "unknown")
    attributes = dict(params)
    if desc_fields:
        attributes["description_fields"] = desc_fields
    attributes["price_status"] = "known" if price is not None else "unknown"
    attributes["price_source"] = "offer.price"
    if capacity:
        attributes["license_units_source"] = "offer.name"
    search_parts = [
        title,
        fields.get("vendor", ""),
        " > ".join(names),
        family_name or "",
        description,
    ]
    search_parts += [f"{key}: {', '.join(values)}" for key, values in params.items()]
    return Product(
        product_id=product_id,
        sku=sku,
        title=title,
        category=names[-1] if names else "",
        category_id=category_id or None,
        category_ids=ids,
        category_path=names,
        family_id=family_id,
        family_name=family_name,
        brand=meaningful(fields.get("vendor")),
        price_kopecks=price,
        source_currency=fields.get("currencyId"),
        license_units=capacity,
        license_unit_type=unit_type,
        min_purchase=min_purchase,
        in_stock={"true": True, "false": False}.get(element.get("available", "").casefold()),
        url=meaningful(fields.get("url")),
        attributes=attributes,
        description=description,
        os=os_values,
        country=country,
        product_type=product_type,
        license_type=(params.get("Тип лицензии") or [desc_fields.get("Тип лицензии")])[0],
        search_text="\n".join(part for part in search_parts if part),
        source_date=source_date,
        data_warnings=warnings,
    )
