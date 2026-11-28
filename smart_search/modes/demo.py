"""Offline adapters for exercising the shared contracts on a small XML-derived fixture."""

import re
from dataclasses import replace
from datetime import UTC, datetime
from difflib import get_close_matches
from typing import Any

from smart_search.contracts import (
    Evidence,
    EvidenceResult,
    HardFilters,
    Product,
    ProductDetailsResult,
    ProductDetailsTarget,
    QuerySpec,
    Recommendation,
    RecommendationDraft,
    RecommendationResult,
    SearchHit,
    SearchParams,
    SearchRequest,
    SearchResult,
    ValidationResult,
)
from smart_search.tools import Services

SERVERS = "Серверное оборудование"
REMOTE = "Удалённый доступ"
ANTIVIRUS = "Антивирусное ПО"
OS = "Операционные системы"
ALIASES = {
    "астра линукс": "astra linux",
    "винда": "windows",
    "винды": "windows",
    "антивирь": "антивирус",
    "касперский": "kaspersky",
}
WORD = re.compile(r"[a-zа-яё\d]+")
PRICES = re.compile(r"\bдо\s+([0-9][0-9 ]*?)\s*(тысяч\w*|к\b|k\b|₽|руб\w*|$)")
DEVICES = re.compile(r"\bна\s+([1-9]\d*)\s+(?:комп\w*|пк|устройств\w*|пользовател\w*)")
SOURCE_DATE = datetime(2026, 11, 26, tzinfo=UTC)


def _product(
    offer_id: str, title: str, category: str, slug: str, rubles: float, **fields: Any
) -> Product:
    return Product(
        product_id=offer_id,
        title=title,
        category=category,
        in_stock=True,
        url=f"https://store.softline.ru/{slug}/",
        price_kopecks=round(rubles * 100),
        **fields,
    )


CATALOG = (
    _product(
        "482091",
        "Техподдержка Astra Linux Special Edition x86-64",
        OS,
        "astralinux/tc1000x8600digsktws00-pr12-482091",
        12600,
        sku="TC1000X8600DIGSKTWS00-PR12",
        brand="Группа Астра",
        attributes={"os": "Astra Linux SE", "country": "RU", "offer_kind": "support"},
    ),
    _product(
        "458218",
        "КриптоПро CSP 5.0 КС3 для Astra Linux SE, серверная лицензия",
        "Криптография",
        "kripto-pro/-458218",
        130000,
        brand="КриптоПро",
        attributes={"os": "Astra Linux SE", "country": "RU"},
    ),
    _product(
        "495105",
        "Альт Сервер 11, бессрочная лицензия на флеш-носителе",
        OS,
        "basealt/alt11-0100s-f-495105",
        16680,
        sku="ALT11-0100S-F",
        brand="BaseALT",
        attributes={"country": "RU"},
    ),
    _product(
        "402265",
        "AnyDesk Solo, лицензия на 1 год",
        REMOTE,
        "anydesk/-402265",
        37358,
        brand="AnyDesk",
        attributes={"purpose": "Удалённый доступ"},
    ),
    _product(
        "535437",
        "САФИБ АССИСТЕНТ, удалённый мониторинг и управление",
        REMOTE,
        "safib/-535437",
        36000,
        brand="Ассистент",
        attributes={"purpose": "Удалённый доступ", "country": "RU"},
    ),
    _product(
        "248970",
        "Kaspersky Small Office Security, новая лицензия на 5 устройств",
        ANTIVIRUS,
        "kaspersky/kl4141rcefs-248970",
        6739.20,
        sku="KL4141RCEFS",
        brand="Лаборатория Касперского",
        license_units=5,
        attributes={"os": "Windows", "country": "RU"},
    ),
    _product(
        "4845",
        "Kaspersky Endpoint Security, лицензия Base на 2 года",
        ANTIVIRUS,
        "kaspersky/kl4867lakds-4845",
        7703.10,
        brand="Лаборатория Касперского",
        attributes={"os": "Windows", "country": "RU"},
    ),
    _product(
        "531625",
        "Микросервер DEPO Stream 1040C2X",
        SERVERS,
        "depo/1040c2x-op-n3060-4gb3sl1-1dt240-cc-4glan-bkt-car1s-531625",
        6268,
        brand="DEPO",
    ),
    _product(
        "438239",
        "Rack-сервер DEPO Storm 3470S2A",
        SERVERS,
        "depo/3470s2a-1-438239",
        1698756,
        brand="DEPO",
    ),
    _product(
        "467607",
        "Rack-сервер DEPO Storm 3470E2A",
        SERVERS,
        "depo/3470e2a-6-467607",
        1143183,
        brand="DEPO",
    ),
    _product(
        "542026",
        "Сервер INFERIT RS212",
        SERVERS,
        "inferit/ifs-00909-542026",
        4460000,
        brand="Инферит",
    ),
    _product(
        "541589",
        "МТС Линк Стандарт, лицензия на 1 год",
        "Видеоконференции",
        "webinar/-541589",
        3600,
        brand="МТС Линк",
    ),
)
OFFERS = {product.product_id: product for product in CATALOG}
VOCABULARY = {"anydesk", "altdesk", "astra", "kaspersky", "csp", "сервер", "антивирус"}


def understand_demo(request: SearchRequest) -> QuerySpec:
    normalized = request.query.casefold().replace("ё", "е")
    for source, target in ALIASES.items():
        normalized = re.sub(rf"(?<!\w){re.escape(source)}(?!\w)", target, normalized)
    for word in WORD.findall(normalized):
        close = get_close_matches(word, VOCABULARY, n=1, cutoff=0.81) if len(word) > 4 else []
        if word not in VOCABULARY and close:
            normalized = re.sub(rf"\b{word}\b", close[0], normalized)
    intent = "product_search"
    if re.search(r"аналог|замен|альтернатив", normalized):
        intent = "alternatives"
    elif re.search(r"докуп|совместим|дополн", normalized):
        intent = "complements"
    elif "комплект" in normalized:
        intent = "solution"
    category = None
    if "антивирус" in normalized:
        category = ANTIVIRUS
    elif "сервер" in normalized and not any(term in normalized for term in ("linux", "windows")):
        category = SERVERS
    elif "astra linux" in normalized and "криптопро" not in normalized and "csp" not in normalized:
        category = OS
    price = PRICES.search(normalized)
    devices = DEVICES.search(normalized)
    budget = None
    if price:
        scale = 1_000 if price.group(2).startswith(("тысяч", "к", "k")) else 1
        budget = int(price.group(1).replace(" ", "")) * scale * 100
    sku = next((p.sku for p in CATALOG if p.sku and normalized == p.sku.casefold()), None)
    question = None
    if "windows server" in normalized and intent == "alternatives" and not request.previous_context:
        question = "Какая функция Windows Server нужна: служба каталогов, файлы или виртуализация?"
    return QuerySpec(
        raw_query=request.query,
        normalized_query=normalized,
        sku=sku,
        intent="exact_product" if sku else intent,
        category=category,
        clarifying_question=question,
        hard_filters=HardFilters(
            os="Windows" if "windows" in normalized and not question else None,
            country="RU" if "российск" in normalized else None,
            devices=int(devices.group(1)) if devices else None,
            max_total_price_kopecks=budget,
            budget_scope="bundle" if "комплект" in normalized and budget is not None else None,
        ),
    )


def search_demo(params: SearchParams) -> SearchResult:
    spec = params.query_spec
    if params.mode == "sku":
        hits = [
            p
            for p in CATALOG
            if spec.sku
            and spec.sku.casefold() in {p.product_id.casefold(), (p.sku or "").casefold()}
        ]
        return SearchResult(hits=[SearchHit(product=p, rank=i) for i, p in enumerate(hits, 1)])
    tokens = set(WORD.findall(spec.normalized_query))
    terms = tokens - {"до", "к", "на", "для", "в", "с", "что", "нужен", "тысяч"}
    terms = {term for term in terms if not term.isdecimal()}
    scored = []
    for item in CATALOG:
        if spec.category and item.category != spec.category:
            continue
        title_tokens = set(WORD.findall(item.title.casefold()))
        category_tokens = set(WORD.findall(item.category.casefold()))
        score = 3 * len(terms & title_tokens) + len(terms & category_tokens)
        if spec.category == item.category:
            score += 2
        if score:
            scored.append((score, item))
    scored.sort(key=lambda entry: -entry[0])
    return SearchResult(
        hits=[
            SearchHit(product=item, rank=rank, score=score)
            for rank, (score, item) in enumerate(scored[: params.top_k], 1)
        ]
    )


def recommend_demo(spec: QuerySpec, anchors: list[Product]) -> RecommendationResult:
    drafts = []
    for anchor in anchors:
        if anchor.product_id == "482091" and spec.intent in {"complements", "solution"}:
            drafts.append(
                RecommendationDraft(
                    anchor_id=anchor.product_id,
                    product=OFFERS["458218"],
                    type="cross_sell",
                    claim="Серверная лицензия КриптоПро CSP 5.0 КС3 для Astra Linux Special Edition.",
                )
            )
        if anchor.product_id == "402265" and spec.intent == "alternatives":
            drafts.append(
                RecommendationDraft(
                    anchor_id=anchor.product_id,
                    product=OFFERS["535437"],
                    type="substitute",
                    claim="Кандидат для удалённого управления, с отдельными условиями лицензирования.",
                )
            )
    return RecommendationResult(recommendations=drafts)


def research_demo(draft: RecommendationDraft, *, confirmed: bool = True) -> EvidenceResult:
    evidence = []
    if confirmed and (draft.anchor_id, draft.product.product_id) == ("482091", "458218"):
        evidence = [
            Evidence(
                source_type="vendor",
                source_url="https://cryptopro.ru/products/csp",
                excerpt="КриптоПро CSP 5.0 КС3 для Astra Linux Special Edition.",
                checked_at=SOURCE_DATE,
            )
        ]
    if confirmed and (draft.anchor_id, draft.product.product_id) == ("402265", "535437"):
        evidence = [
            Evidence(
                source_type="catalog",
                source_url=draft.product.url,
                catalog_field="name",
                excerpt="Система удаленного мониторинга и управления версия 7",
                checked_at=SOURCE_DATE,
            )
        ]
    return EvidenceResult(
        anchor_id=draft.anchor_id,
        product_id=draft.product.product_id,
        claim=draft.claim,
        status="confirmed" if evidence else "unknown",
        evidence=evidence,
    )


def _constraint_error(filters: HardFilters, product: Product, *, extra_cost: int | None = 0):
    if filters.in_stock is not None and product.in_stock != filters.in_stock:
        return "inventory_unconfirmed"
    if filters.os and product.attributes.get("os") != filters.os:
        return "os_unconfirmed"
    if filters.country and product.attributes.get("country") != filters.country:
        return "country_unconfirmed"
    if filters.devices is not None:
        if product.license_units is None:
            return "license_capacity_unknown"
        if product.license_units < filters.devices:
            return "license_capacity_insufficient"
    if filters.max_total_price_kopecks is not None:
        if not product.price_kopecks or extra_cost is None:
            return "price_unknown"
        if product.price_kopecks + extra_cost > filters.max_total_price_kopecks:
            return "budget_exceeded"
    return None


def validate_demo(
    spec: QuerySpec,
    search: SearchResult,
    recommendations: RecommendationResult,
    evidence: list[EvidenceResult],
) -> ValidationResult:
    products = {}
    reasons = []
    for hit in search.hits:
        error = _constraint_error(spec.hard_filters, hit.product)
        if error:
            reasons.append(error)
        else:
            products.setdefault(hit.product.product_id, hit.product)
    accepted = {}
    for draft in recommendations.recommendations:
        product_id = draft.product.product_id
        verified = next(
            (
                item.evidence
                for item in evidence
                if (item.anchor_id, item.product_id, item.claim)
                == (draft.anchor_id, product_id, draft.claim)
                and item.status == "confirmed"
            ),
            None,
        )
        if not verified:
            reasons.append("evidence_unconfirmed")
            continue
        filters = spec.hard_filters
        extra_cost = 0
        if draft.type == "cross_sell":
            if filters.budget_scope != "bundle":
                filters = filters.model_copy(update={"max_total_price_kopecks": None})
            else:
                anchor = products.get(draft.anchor_id)
                extra_cost = (anchor.price_kopecks or None) if anchor else None
        error = _constraint_error(filters, draft.product, extra_cost=extra_cost)
        if error:
            reasons.append(error)
            continue
        accepted.setdefault(product_id, Recommendation(**draft.model_dump(), evidence=verified))
    if spec.intent in {"alternatives", "complements"}:
        for candidate_id in accepted:
            products.pop(candidate_id, None)
    return ValidationResult(
        products=list(products.values()),
        recommendations=list(accepted.values()),
        reason_codes=list(dict.fromkeys(reasons)),
    )


def get_product_details_demo(target: ProductDetailsTarget) -> ProductDetailsResult:
    from smart_search.product_details import matches_name
    from softline_search.text import sku_key

    if target.kind == "id":
        product = OFFERS.get(target.value)
        return ProductDetailsResult(candidates=[product] if product else [])
    candidates = [card for card in CATALOG if sku_key(card.sku) == sku_key(target.value)]
    if target.kind == "name" and not candidates:
        candidates = [card for card in CATALOG if matches_name(target.value, card)]
    return ProductDetailsResult(candidates=candidates[:3])


def demo_services(**overrides: Any) -> Services:
    return replace(
        Services(
            understand_demo,
            search_demo,
            recommend_demo,
            research_demo,
            validate_demo,
            get_product_details=get_product_details_demo,
        ),
        **overrides,
    )
