"""Deterministic checks on catalog offers and the research attached to each claim."""

import re
from collections import defaultdict
from urllib.parse import urlsplit

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
from softline_search.licensing import units_to_buy
from softline_search.text import canonical_os, sku_key

RELATIONS = {"alternatives": "substitute", "complements": "cross_sell"}


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
    return total, None


def _identity(product: Product, spec: QuerySpec, category_ids: set[str]) -> str | None:
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


def validate(
    spec: QuerySpec,
    result: SearchResult,
    recs: RecommendationResult,
    evidence: list[EvidenceResult],
) -> ValidationResult:
    category_ids = set(resolve_category(spec.category) or []) if spec.category else set()
    relation = RELATIONS.get(spec.intent)
    products, anchors, costs = {}, {}, {}
    reasons = []
    for hit in result.hits:
        product = hit.product
        failure = _identity(product, spec, category_ids)
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
            filters = filters.model_copy(update={"max_total_price_kopecks": None})
        cost, failure = _purchase(product, filters, extra_cost=extra_cost)
        if failure:
            reasons.append(failure)
            continue
        accepted[product.product_id] = Recommendation(**draft.model_dump(), evidence=sources)
        if bundle and cost is not None and extra_cost is not None:
            costs[draft.anchor_id] = extra_cost + cost

    for product_id in accepted:
        products.pop(product_id, None)
    count = len(accepted) if relation else len(products)
    return ValidationResult(
        products=list(products.values()),
        recommendations=list(accepted.values()),
        reason_codes=list(dict.fromkeys(reasons)),
        needs_retry=result.available and spec.intent != "exact_product" and count < 4,
    )
