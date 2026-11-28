"""Deterministic checks on catalog offers and the research attached to each claim."""

import json
import logging
import os
import re
from collections import defaultdict
from urllib.parse import urlsplit

from pydantic import BaseModel, Field, ValidationError

from smart_search.catalog_backend import resolve_category
from smart_search.contracts import (
    Evidence,
    EvidenceResult,
    HardFilters,
    Product,
    QuerySpec,
    Recommendation,
    RecommendationResult,
    SearchResult,
    ValidationResult,
)
from smart_search.llm.exceptions import LLMError
from smart_search.model_client import complete_json
from smart_search.understanding import KEYBOARD, MOUSE, RGB
from softline_search.licensing import units_to_buy
from softline_search.text import canonical_os, sku_key

LOGGER = logging.getLogger(__name__)
RELATIONS = {"alternatives": "substitute", "complements": "cross_sell"}
MAX_RERANK_CANDIDATES = 100
MAX_RERANK_RESULTS = 12
_SKIP_ATTR_KEYS = frozenset(
    {
        "description_fields",
        "price_status",
        "price_source",
        "license_units_source",
        "Тип продукта",
        "Тип поставки",
        "Товар для геймеров",
    }
)
_PRIORITY_ATTR_KEYS = (
    "Сертификаты",
    "Особенности",
    "Подсветка",
    "Подсветка клавиш",
    "Диагональ",
    "Диагональ экрана",
    "Разрешение экрана",
)


class RerankedSelection(BaseModel):
    """Structured selection returned by the relevance reranker."""

    relevant_ids: list[str] = Field(default_factory=list)


_RERANK_PROMPT = """Ты — финальный смысловой проверяющий поисковой выдачи ИТ-магазина Store.softline.ru.
Пользователь ввёл запрос (`query`, `normalized_query`, и опционально `product_type_hint` и `required_terms`).
Тебе дан список кандидатов (`id`, `t` — название, `c` — категория, `s` — краткие характеристики/описание), которые уже прошли числовые фильтры цены, ОС, наличия и лицензий.

Задача:
1. Отбери ТОЛЬКО те `id`, которые по смыслу являются именно искомым товаром и удовлетворяют явным требованиям запроса.
2. Отсортируй их от наиболее подходящих к менее подходящим (не более 12 штук).
3. Смысловые требования и синонимы проверяй по `t`, `c` И `s`: например, «27 дюймов» соответствует `27"` или `27.0-inch`, «платиновый» — `80 PLUS Platinum`, «RGB» — `RGB` / `подсветка` / `Lightsync` / `Chroma`, если свойство не отрицается явно («без подсветки»).
4. Мягкие формулировки сценария («для работы с текстом», «для офиса», «для учёбы», «для дома», «надёжный») НЕ требуют дословного упоминания в описании карточки: оставляй стандартные подходящие модели этого типа товара и ставь выше те, чьи параметры (например, IPS/FullHD/большая диагональ для текста) лучше подходят под задачу.
5. Если ищут сам прибор или устройство (или задан `product_type_hint`, например `ноутбук`, `монитор`, `сервер`, `ИБП`, `сканер`, `мышь`, `клавиатура`, `принтер`, `роутер`, `БП` и т.д.), СТРОГО ИСКЛЮЧИ аксессуары, запчасти, расходники и смежные товары «для него» (столы, подставки, сумки, чехлы, рюкзаки, кабели, переходники, док-станции, кронштейны, сменные батареи, картриджи, ОЗУ/SSD для ноутбука и т.п. — ими при `solution`/`complements` занимается отдельный шаг подбора дополнений), если только `product_type_hint` сам не относится к аксессуару или готовому набору аксессуаров.
6. Исключай товары другого класса (например мини-ПК или моноблок при поиске ноутбука, рабочую станцию при поиске сервера).
7. Используй только `id` из входного списка `candidates`. Если подходящих товаров нет совсем — верни `{"relevant_ids": []}`.
"""

# Mapping of canonical product_type_hint -> (positive regex, conflicting regex unless kit requested)
PRODUCT_TYPE_RULES: dict[str, tuple[re.Pattern[str], re.Pattern[str]]] = {
    "мышь": (
        re.compile(r"\b(?:мыш[ьиеа]|мышк[ауи]|mouse)\b", re.IGNORECASE),
        re.compile(r"\b(?:клавиатур\w*|коврик\w*|keyboard|mousepad|подставк\w*)\b", re.IGNORECASE),
    ),
    "клавиатура": (
        re.compile(r"\b(?:клавиатур\w*|keyboard)\b", re.IGNORECASE),
        re.compile(
            r"\b(?:мыш[ьиеа]|мышк[ауи]|mouse|коврик\w*|mousepad|наклейк\w*)\b", re.IGNORECASE
        ),
    ),
    "монитор": (
        re.compile(r"\b(?:монитор\w*|monitor|диспле\w*)\b", re.IGNORECASE),
        re.compile(
            r"\b(?:кронштейн\w*|подставк\w*|кабел\w*|пленк\w*|чех[ое]л\w*)\b", re.IGNORECASE
        ),
    ),
    "ноутбук": (
        re.compile(r"\b(?:ноутбук\w*|laptop|ultrabook|ультрабук\w*)\b", re.IGNORECASE),
        re.compile(
            r"\b(?:сумк\w*|рюкзак\w*|чех[ое]л\w*|зарядн\w*|блок\s+питания|аккумулятор\w*|стол(?:ик)?\w*|подставк\w*|док-?станц\w*|мыш[ьиеа]\w*|клавиатур\w*|числов\w+\s+блок\w*|оперативн\w+\s+памят\w*)\b|(?:для|под|к)\s+ноутбук\w*",
            re.IGNORECASE,
        ),
    ),
    "ибп": (
        re.compile(r"\b(?:ибп|ups|бесперебойн\w*)\b", re.IGNORECASE),
        re.compile(r"\b(?:сменн\w+\s+батаре\w*|аккумуляторн\w+\s+батаре\w*)\b", re.IGNORECASE),
    ),
}
BUNDLE_WORDS = re.compile(r"\b(?:комплект\w*|набор\w*|combo|kit)\b", re.IGNORECASE)


def _purchase(
    product: Product, filters: HardFilters, *, extra_cost: int | None = 0
) -> tuple[int | None, str | None]:
    if filters.os and canonical_os(filters.os) not in {canonical_os(name) for name in product.os}:
        return None, "os_unconfirmed"
    if filters.country and product.country != filters.country:
        return None, "country_unconfirmed"
    if filters.in_stock is not None and product.in_stock != filters.in_stock:
        return None, "inventory_unconfirmed"
    units, failure = units_to_buy(product, filters.devices)
    if failure:
        return None, failure
    total = units * product.price_kopecks if product.price_kopecks else None
    if filters.max_total_price_kopecks is not None:
        if total is None or extra_cost is None:
            return None, "price_unknown"
        if total + extra_cost > filters.max_total_price_kopecks:
            return None, "budget_exceeded"
    if filters.min_total_price_kopecks is not None:
        if total is None or extra_cost is None:
            return None, "price_unknown"
        if total + extra_cost < filters.min_total_price_kopecks:
            return None, "price_below_minimum"
    return total, None


def _matches_required_term(product: Product, term: str) -> bool:
    escaped = re.escape(term.strip())
    if not escaped:
        return True
    positive = re.compile(rf"(?<!\w){escaped}(?!\w)", re.IGNORECASE)
    negated = re.compile(rf"\b(?:без|no|without)\s+{escaped}(?!\w)", re.IGNORECASE)
    sources = [product.title, product.description, *map(str, product.attributes.values())]
    return any(positive.search(value) and not negated.search(value) for value in sources)


def _compact_specs(product: Product, *, max_chars: int = 160) -> str:
    parts: list[str] = []
    seen_keys: set[str] = set()

    def add_attr(key: str, raw: object) -> None:
        if isinstance(raw, list):
            val = ", ".join(str(v) for v in raw if v)
        else:
            val = str(raw or "").strip()
        if val:
            parts.append(f"{key}: {val}")

    for key in _PRIORITY_ATTR_KEYS:
        if key in product.attributes:
            seen_keys.add(key)
            add_attr(key, product.attributes[key])
    if product.description.strip():
        parts.append(" ".join(product.description.split()))
    for key, raw in product.attributes.items():
        if key in seen_keys or key in _SKIP_ATTR_KEYS:
            continue
        add_attr(key, raw)
    return "; ".join(parts)[:max_chars]


def _text_rules_failure(product: Product, spec: QuerySpec) -> str | None:
    """Fallback regex validation for product types and literal feature terms when LLM is absent."""
    type_hint = (spec.product_type_hint or "").strip().casefold()
    if not type_hint and spec.intent == "product_search":
        if MOUSE.search(spec.normalized_query):
            type_hint = "мышь"
        elif KEYBOARD.search(spec.normalized_query):
            type_hint = "клавиатура"
    if type_hint and spec.intent in {"product_search", "solution"}:
        if type_hint in {"сервер", "server_hardware"} and (
            product.product_type == "software"
            or re.search(r"рабочая станция", product.title, re.IGNORECASE)
        ):
            return "product_type_mismatch"
        rule = PRODUCT_TYPE_RULES.get(type_hint)
        if rule is not None:
            pos_re, neg_re = rule
            wants_bundle = bool(BUNDLE_WORDS.search(spec.raw_query))
            if not pos_re.search(product.title):
                return "product_type_mismatch"
            if not wants_bundle and neg_re.search(product.title):
                return "product_type_mismatch"

    required = list(spec.required_terms)
    if (
        spec.intent == "product_search"
        and MOUSE.search(spec.normalized_query)
        and RGB.search(spec.raw_query)
        and not any(t.casefold() == "rgb" for t in required)
    ):
        required.append("RGB")
    for term in required:
        if not _matches_required_term(product, term):
            return "rgb_unconfirmed" if term.casefold() == "rgb" else "feature_unconfirmed"
    return None


def _identity(
    product: Product,
    spec: QuerySpec,
    category_ids: set[str],
    *,
    apply_text_rules: bool = True,
) -> str | None:
    if spec.sku and sku_key(spec.sku) != sku_key(product.sku):
        return "sku_mismatch"
    if spec.category:
        ids = {product.category_id, product.family_id, *product.category_ids}
        names = {name.casefold() for name in [product.category, *product.category_path]}
        if not category_ids.intersection(ids) and spec.category.casefold() not in names:
            return "category_mismatch"
        if spec.category == "server_hardware" and (
            product.product_type == "software"
            or re.search(r"рабочая станция", product.title, re.IGNORECASE)
        ):
            return "product_type_mismatch"

    if apply_text_rules:
        text_failure = _text_rules_failure(product, spec)
        if text_failure:
            return text_failure

    # Restrict dotted product versions only when attached to a name, not prices or SKU parts.
    versions = re.findall(
        r"(?<=[^\W\d_])\s+(?:v(?:ersion)?\s*)?(\d+(?:\.\d+)+)\b",
        spec.normalized_query,
        re.IGNORECASE,
    )
    description = " ".join(
        [product.title, product.family_name or "", str(product.attributes.get("version") or "")]
    )
    for version in versions:
        if not re.search(rf"(?<![\w.])v?{re.escape(version)}(?![\w.])", description, re.IGNORECASE):
            return "version_unconfirmed"
    return None


def _traceable(item: Evidence) -> bool:
    if item.source_type == "catalog":
        return bool(item.catalog_field and item.catalog_field.strip())
    try:
        url = urlsplit(item.source_url or "")
        return url.scheme in {"http", "https"} and bool(url.hostname)
    except ValueError:
        return False


def _llm_rerank_active(spec: QuerySpec) -> bool:
    return (
        spec.intent in {"product_search", "solution"}
        and not spec.sku
        and os.getenv("LLM_PROVIDER", "deepseek").strip().lower() != "mock"
    )


def _rerank_with_llm(
    spec: QuerySpec, candidates: list[Product]
) -> tuple[list[Product] | None, bool]:
    """Filter out semantic mismatches/accessories and order top relevant hits via DeepSeek."""
    if not candidates:
        return None, False
    by_id = {item.product_id: item for item in candidates}
    payload = json.dumps(
        {
            "query": spec.raw_query,
            "normalized_query": spec.normalized_query,
            "product_type_hint": spec.product_type_hint,
            "required_terms": spec.required_terms,
            "candidates": [
                {
                    "id": item.product_id,
                    "t": item.title[:140],
                    "c": item.category,
                    "s": _compact_specs(item),
                }
                for item in candidates[:MAX_RERANK_CANDIDATES]
            ],
        },
        ensure_ascii=False,
    )
    try:
        response = complete_json(_RERANK_PROMPT, payload, RerankedSelection)
    except (LLMError, TimeoutError, ConnectionError, ValidationError, ValueError) as exc:
        LOGGER.warning("Validation LLM rerank failed (%s)", type(exc).__name__)
        return None, True

    selected: list[Product] = []
    seen: set[str] = set()
    for pid in response.relevant_ids:
        if pid in by_id and pid not in seen:
            seen.add(pid)
            selected.append(by_id[pid])
            if len(selected) >= MAX_RERANK_RESULTS:
                break
    return selected, False


def validate(
    spec: QuerySpec,
    result: SearchResult,
    recs: RecommendationResult,
    evidence: list[EvidenceResult],
) -> ValidationResult:
    category_ids = set(resolve_category(spec.category) or []) if spec.category else set()
    relation = RELATIONS.get(spec.intent)
    use_llm_rerank = _llm_rerank_active(spec)
    products, anchors, costs = {}, {}, {}
    reasons = []
    warnings = []
    for hit in result.hits:
        product = hit.product
        failure = _identity(product, spec, category_ids, apply_text_rules=not use_llm_rerank)
        if not failure:
            if relation:
                # A reference product can cost more than the requested substitute.
                anchors[product.product_id] = product
            cost, failure = _purchase(product, spec.hard_filters)
            if not failure:
                products.setdefault(product.product_id, product)
                anchors[product.product_id] = product
                costs[product.product_id] = cost
        if failure:
            reasons.append(failure)

    if products and use_llm_rerank:
        reranked, fallback_used = _rerank_with_llm(spec, list(products.values()))
        if fallback_used:
            warnings.append("validation_rerank_fallback")
            filtered: dict[str, Product] = {}
            for pid, item in products.items():
                text_failure = _text_rules_failure(item, spec)
                if text_failure:
                    reasons.append(text_failure)
                    continue
                filtered[pid] = item
                if len(filtered) >= MAX_RERANK_RESULTS:
                    break
            products = filtered
            anchors = dict(products)
        elif reranked is not None:
            if len(reranked) < len(products):
                reasons.append("llm_relevance_mismatch")
            products = {item.product_id: item for item in reranked}
            anchors = dict(products)

    proofs = defaultdict(list)
    for item in evidence:
        proofs[(item.anchor_id, item.product_id, item.claim)].append(item)

    accepted = {}
    for draft in recs.recommendations:
        product = draft.product
        anchor = anchors.get(draft.anchor_id)
        if not anchor:
            reasons.append("anchor_unconfirmed")
            continue
        if product.product_id == draft.anchor_id or product.product_id in accepted:
            continue
        if relation and relation != draft.type:
            continue
        results = proofs[(draft.anchor_id, product.product_id, draft.claim)]
        sources = [
            entry
            for item in results
            if item.status == "confirmed"
            for entry in item.evidence
            if _traceable(entry)
        ]
        if any(item.status == "contradicted" for item in results):
            reasons.append("evidence_contradicted")
            continue
        if not sources:
            reasons.append("evidence_unconfirmed")
            continue

        filters = spec.hard_filters
        extra_cost = 0
        bundle = draft.type == "cross_sell" and filters.budget_scope == "bundle"
        if bundle:
            # Each accepted add-on contributes to the running cost of this particular bundle.
            extra_cost = costs.get(draft.anchor_id)
        elif draft.type == "cross_sell":
            filters = filters.model_copy(
                update={"max_total_price_kopecks": None, "min_total_price_kopecks": None}
            )
        cost, failure = _purchase(product, filters, extra_cost=extra_cost)
        if failure:
            reasons.append(failure)
            continue
        accepted[product.product_id] = Recommendation(**draft.model_dump(), evidence=sources)
        if bundle and cost is not None and extra_cost is not None:
            costs[draft.anchor_id] = extra_cost + cost

    for product_id in accepted:
        products.pop(product_id, None)
    if relation and (accepted or evidence):
        # For complements/alternatives, SearchResponse.products holds the anchor lookup;
        # once recommendations are researched, returning anchors/similars duplicates or pollutes results.
        products.clear()
    count = len(accepted) if relation else len(products)
    return ValidationResult(
        products=list(products.values()),
        recommendations=list(accepted.values()),
        warnings=list(dict.fromkeys(warnings)),
        reason_codes=list(dict.fromkeys(reasons)),
        needs_retry=result.available and spec.intent != "exact_product" and count < 4,
    )
