"""Resolve references to shown products and prepare factual catalog details."""

import re
import unicodedata
from collections.abc import Sequence
from urllib.parse import urlsplit

from smart_search.contracts import (
    Product,
    ProductDetails,
    ProductDetailsTarget,
    ProductFact,
    SearchResponse,
    ShownProduct,
)
from softline_search.text import clean_text, sku_key

_ORDINALS = {"перв": 1, "втор": 2, "трет": 3, "четверт": 4, "пят": 5}
_POSITION = re.compile(
    r"(?:№\s*(\d+)|(?:вариант|номер)\s+(\d+)|"
    r"(\d+)(?:-?(?:й|ый|ой|ий|я|ую))?(?:\s+вариант\w*)?|"
    r"(перв|втор|трет|четверт|пят)(?:ый|ой|ий|ья|ий|ая|ую|ью|ого|ьего|ому|ьему|ом|ьем)"
    r"(?:\s+(?:вариант|товар|модел|позиц)\w*)?)"
)
_DETAILS = re.compile(
    r"^(?:а\s+)?(?:расскажи(?:те)?(?:\s+пожалуйста)?(?:\s+поподробнее|\s+подробнее)?"
    r"|(?:покажи(?:те)?|можно)\s+(?:подробности|подробнее)"
    r"|поподробнее|подробнее|что\s+за)"
    r"(?:\s+(?:про|о(?:бо?)?|насч[её]т))?(?:\s+|$)",
    re.IGNORECASE,
)
_SKU = re.compile(r"(?:артикул\w*|sku)\s*[:№#]?\s*([\w./-]+)", re.IGNORECASE)
_FACTS = (
    "Интерфейс",
    "Тип подключения",
    "Тип датчика",
    "Разрешение сенсора",
    "Количество клавиш мыши",
    "Цвет корпуса",
    "Операционная система",
    "Тип лицензии",
    "Минимальная покупка",
)


def chat_products(response: SearchResponse) -> list[ShownProduct]:
    if response.status not in {"ok", "partial"}:
        return []
    ordered = {}
    primary = [] if response.intent in {"alternatives", "complements"} else response.products
    for product in [*(item.product for item in response.recommendations), *primary]:
        ordered.setdefault(
            product.product_id,
            ShownProduct(
                product_id=product.product_id,
                title=product.title,
                sku=product.sku,
                brand=product.brand,
            ),
        )
        if len(ordered) == 3:
            break
    return list(ordered.values())


def _words(value: str) -> str:
    text = unicodedata.normalize("NFKC", value).casefold().replace("ё", "е")
    return " ".join(re.findall(r"[^\W_]+", text))


def matches_name(query: str, card: Product | ShownProduct) -> bool:
    name = _words(query)
    title = _words(card.title)
    if not name:
        return False
    if name == title:
        return True
    brand = _words(card.brand or "")
    includes_model = any(
        any(char.isalpha() for char in word) and any(char.isdigit() for char in word)
        for word in name.split()
    ) or (brand and name.startswith(f"{brand} "))
    return bool(includes_model) and f" {name} " in f" {title} "


def parse_reference(
    query: str, shown: Sequence[ShownProduct], *, allow_short: bool
) -> int | ProductDetailsTarget | list[ShownProduct] | None:
    text = query.strip().strip("?!.,").strip()
    prefix = _DETAILS.match(text)
    if prefix is None and not allow_short:
        return None
    target = text[prefix.end() :].strip(" «»\"'") if prefix else text
    position = _POSITION.fullmatch(target.casefold().replace("ё", "е"))
    if position and (prefix or not target.isdecimal()):
        number = next((part for part in position.groups()[:3] if part), None)
        return int(number) if number is not None else _ORDINALS[position[4]]
    if prefix and (sku := _SKU.fullmatch(target)):
        return ProductDetailsTarget(kind="sku", value=sku[1])
    matches = [
        card
        for card in shown
        if (card.sku and sku_key(target) == sku_key(card.sku)) or matches_name(target, card)
    ]
    if len(matches) == 1:
        return ProductDetailsTarget(kind="id", value=matches[0].product_id)
    if matches or (prefix and not target):
        return matches
    if prefix:
        return ProductDetailsTarget(kind="name", value=target)
    return None


def make_details(product: Product) -> ProductDetails:
    summary = clean_text(product.description)
    if len(summary) > 350:
        summary = summary[:349].rsplit(" ", 1)[0] + "…"
    facts = []
    nested = product.attributes.get("description_fields")
    attributes = (
        {**nested, **product.attributes} if isinstance(nested, dict) else product.attributes
    )
    for label in _FACTS:
        value = attributes.get(label)
        if isinstance(value, list):
            value = ", ".join(entry for entry in value if isinstance(entry, str))
        if isinstance(value, str) and (value := clean_text(value)):
            facts.append(ProductFact(label=label, value=value[:160]))
        if len(facts) == 3:
            break
    link = None
    if product.url and not any(char.isspace() or char == "\\" for char in product.url):
        try:
            parsed = urlsplit(product.url)
            if parsed.scheme == "https" and parsed.hostname and parsed.username is None:
                link = product.url
        except ValueError:
            pass
    return ProductDetails(
        product_id=product.product_id,
        title=product.title,
        brand=product.brand,
        sku=product.sku,
        summary=summary or None,
        facts=facts,
        url=link,
        price_kopecks=product.price_kopecks,
        in_stock=product.in_stock,
    )
