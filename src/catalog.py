"""Stream the YML feed into a shared offer-level catalog."""

import gzip
import json
import sqlite3
from collections import defaultdict
from pathlib import Path
from tempfile import TemporaryDirectory
from uuid import uuid4
from xml.etree.ElementTree import iterparse


def offers(source):
    categories = {}
    stack = []
    opener = gzip.open if source.suffix == ".gz" else Path.open
    with opener(source, "rb") as stream:
        for event, node in iterparse(stream, events=("start", "end")):
            if event == "start":
                stack.append(node)
                continue
            stack.pop()
            if node.tag == "category":
                categories[node.attrib["id"]] = (node.text or "", node.get("parentId"))
            elif node.tag == "offer":
                yield normalize(node, categories)
            if node.tag in {"category", "offer"}:
                stack[-1].remove(node)


def normalize(node, categories):
    def value(tag):
        return node.findtext(tag, "").strip()

    def price(tag):
        amount = float(value(tag) or 0)
        return amount if amount > 0 else None

    category = value("categoryId")
    path = []
    seen = set()
    current = category
    while current in categories and current not in seen:
        seen.add(current)
        label, current = categories[current]
        path.append(label)
    params = defaultdict(list)
    for param in node.findall("param"):
        name = param.get("name", "").strip()
        content = (param.text or "").strip()
        if (
            name
            and content
            and content.casefold() not in {"другое или отсутствует", "не определено"}
        ):
            params[name].append(content)
    return {
        "offer_id": node.attrib["id"],
        "group_id": node.get("group_id") or value("group_id") or None,
        "name": value("name"),
        "vendor": value("vendor"),
        "vendor_url": value("vendorUrl"),
        "vendor_code": value("vendorCode"),
        "category_id": category,
        "category_path": list(reversed(path)),
        "description": value("description"),
        "params": dict(params),
        "price": price("price"),
        "old_price": price("oldprice"),
        "currency": value("currencyId"),
        "available": node.get("available", "").lower() == "true",
        "url": value("url"),
        "picture": value("picture"),
    }


def build_catalog(source, destination):
    destination.parent.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(dir=destination.parent) as temporary:
        path = Path(temporary) / destination.name
        db = sqlite3.connect(path)
        try:
            with db:
                db.execute("CREATE TABLE metadata (generation TEXT NOT NULL)")
                db.execute("INSERT INTO metadata VALUES (?)", (uuid4().hex,))
                db.execute("CREATE TABLE offers (id TEXT PRIMARY KEY, product TEXT NOT NULL)")
                db.executemany(
                    "INSERT INTO offers VALUES (?, ?)",
                    (
                        (offer["offer_id"], json.dumps(offer, ensure_ascii=False))
                        for offer in offers(source)
                    ),
                )
                count = db.execute("SELECT count(*) FROM offers").fetchone()[0]
                if not count:
                    raise ValueError("Catalog contains no offers")
        finally:
            db.close()
        path.replace(destination)
    return count


def open_catalog(path):
    return sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)
