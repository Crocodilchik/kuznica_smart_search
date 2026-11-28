"""Streaming, dependency-free audit of the supplied YML before normalization."""

from __future__ import annotations

import argparse
import gzip
import json
import time
import xml.etree.ElementTree as ET
from collections import Counter, defaultdict
from decimal import Decimal, InvalidOperation
from pathlib import Path


def inspect(source: Path) -> dict:
    started = time.perf_counter()
    counters = defaultdict(Counter)
    values = defaultdict(Counter)
    ids: set[str] = set()
    categories: dict[str, dict] = {}
    samples: list[dict] = []
    prices: list[Decimal] = []
    offers = 0
    with source.open("rb") as raw:
        magic = raw.read(2)
        raw.seek(0)
        stream = gzip.GzipFile(fileobj=raw) if magic == b"\x1f\x8b" else raw
        stack = []
        for event, element in ET.iterparse(stream, events=("start", "end")):
            if event == "start":
                stack.append(element)
                continue
            tag = element.tag.rsplit("}", 1)[-1]
            if tag == "category":
                categories[element.get("id", "")] = {
                    "name": element.text,
                    "parent_id": element.get("parentId"),
                }
            if tag == "offer":
                offers += 1
                offer_id = element.get("id", "")
                counters["quality"]["duplicate_id"] += int(offer_id in ids)
                ids.add(offer_id)
                data = {
                    child.tag: (child.text or "").strip()
                    for child in element
                    if child.tag != "param"
                }
                for key, value in data.items():
                    counters["fields_present"][key] += 1
                    if not value:
                        counters["fields_empty"][key] += 1
                params = defaultdict(list)
                for child in element.findall("param"):
                    key = child.get("name", "")
                    value = (child.text or "").strip()
                    params[key].append(value)
                    values[key][value] += 1
                for key, entries in params.items():
                    counters["parameter_coverage"][key] += 1
                    counters["repeated_parameters"][key] += int(len(entries) > 1)
                for key in ("vendor", "currencyId", "categoryId", "group_id"):
                    counters[key][data.get(key, "<missing>")] += 1
                counters["availability"][element.get("available", "<missing>")] += 1
                for key in ("vendorCode", "name", "description", "url", "price"):
                    counters["missing_or_empty"][key] += int(not data.get(key))
                try:
                    price = Decimal(data.get("price", ""))
                    if price.is_finite() and price > 0:
                        prices.append(price)
                        counters["price_status"]["positive"] += 1
                    else:
                        counters["price_status"]["zero_or_negative"] += 1
                except InvalidOperation:
                    counters["price_status"]["invalid_or_missing"] += 1
                if len(samples) < 5 or (
                    len(samples) < 20 and data.get("vendorCode") and data.get("price") != "0.00"
                ):
                    samples.append(
                        {
                            "id": offer_id,
                            "available": element.get("available"),
                            **data,
                            "params": dict(params),
                        }
                    )
                if offers % 25000 == 0:
                    print(f"Inspected {offers:,} offers", flush=True)
            if tag in {"offer", "category"}:
                element.clear()
                if len(stack) > 1:
                    stack[-2].remove(element)
            stack.pop()
    prices.sort()
    price_summary = {}
    if prices:
        for name, fraction in (("min", 0), ("p50", 0.5), ("p90", 0.9), ("max", 1)):
            price_summary[name] = str(prices[int((len(prices) - 1) * fraction)])
    return {
        "source": str(source),
        "bytes": source.stat().st_size,
        "offers": offers,
        "unique_ids": len(ids),
        "categories": len(categories),
        "counters": {key: dict(value.most_common()) for key, value in counters.items()},
        "parameter_values": {key: dict(value.most_common(15)) for key, value in values.items()},
        "positive_price_rub": price_summary,
        "samples": samples,
        "root_categories": {
            key: value for key, value in categories.items() if not value["parent_id"]
        },
        "elapsed_seconds": round(time.perf_counter() - started, 3),
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("source", type=Path)
    parser.add_argument("--output", type=Path, default=Path("reports/raw_profile.json"))
    args = parser.parse_args()
    profile = inspect(args.source)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(profile, ensure_ascii=False, indent=2), encoding="utf-8")
    print(
        json.dumps(
            {
                key: profile[key]
                for key in ("offers", "categories", "positive_price_rub", "elapsed_seconds")
            },
            ensure_ascii=False,
        )
    )
