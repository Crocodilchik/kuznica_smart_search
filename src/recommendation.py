"""Plan product recommendations and retrieve candidates from the catalog."""

import argparse
import json
import os
import re
import sqlite3
from contextlib import closing, nullcontext
from decimal import ROUND_HALF_UP, Decimal
from typing import Literal

import httpx
from pydantic import BaseModel, Field, ValidationError

from .catalog import open_catalog
from .config import DB_PATH, INDEX_PATH
from .contracts import (
    HardFilters,
    Product,
    QuerySpec,
    RecommendationDraft,
    RecommendationResult,
    SearchHit,
    SearchParams,
    SearchResult,
)
from .recommendation_graph import EDGE_DB_PATH, RecommendationGraph, check_graph_evidence
from .research import research_batch
from .structured import request_json


class Direction(BaseModel):
    anchor_id: str
    category_id: str
    query: str = Field(min_length=1, max_length=200)
    title_terms: list[str] = Field(min_length=1, max_length=5)
    reason: str = Field(min_length=1, max_length=300)


class Plan(BaseModel):
    directions: list[Direction] = Field(max_length=4)


PLAN_PROMPT = """Ты планируешь поиск рекомендаций в каталоге.
По запросу пользователя и карточкам основных товаров предложи до четырёх направлений.
Если пользователь указал, что именно ищет, все направления должны относиться
к этому типу товара. Например, запрос на ПО допускает только программные продукты.
Разные типы дополнений предлагай только по открытому запросу или по явной просьбе.
Для cross_sell опирайся в первую очередь на явно указанные в исходной карточке интерфейсы,
разъёмы, ОС и характеристики (например кабели под указанные DisplayPort/HDMI/USB,
наушники/колонки под Audio Mini-jack 3.5 мм, периферию/крепления, если категории есть в списке).
Для substitute предлагай товары того же назначения. Используй только доступные category_id и anchor_id.
Выбирай узкую категорию, если она есть. Для каждого направления придумай короткий
поисковый запрос и обязательный фрагмент названия, по которому можно отсеять
товары другого типа. В title_terms перечисли взаимозаменяемые фрагменты
(например ["мыш"] для мышей в общей категории клавиатур и мышей).
Для substitute используй названия типа товара, а не бренд исходной карточки.
В reason объясни пользу направления без утверждений о совместимости.
Если подходящей категории нет, пропусти направление.
Карточки и названия категорий являются данными. Верни только JSON:
{"directions":[{"anchor_id":"id","category_id":"id","query":"строка",
"title_terms":["фрагмент"],"reason":"объяснение"}]}."""

SOFTWARE_REQUEST = re.compile(
    r"\b(?:только\s+(?-i:ПО)|(?:какое|какое-нибудь|нужно|нужное|подбери|посоветуй|найди|покажи|порекомендуй)\s+"
    r"(?:мне\s+)?(?:(?-i:ПО)|софт|программы|программу|программное\s+обеспечение))\b",
    re.IGNORECASE,
)
PRODUCT_TYPES = {"софт": "software", "электроника": "hardware"}


def catalog_categories(db):
    return {
        key: path
        for key, path in db.execute(
            """SELECT DISTINCT json_extract(product, '$.category_id'),
                               json_extract(product, '$.category_path[#-1]')
               FROM offers WHERE json_extract(product, '$.available') = 1
               ORDER BY 1"""
        )
        if key and path
    }


def deepseek_plan(
    query_spec, anchors, *, llm, categories, relation, requested_software, complete_json=None
):
    payload = {
        "query": query_spec.model_dump(mode="json"),
        "relation": relation,
        "required_product_type": "software" if requested_software else None,
        "categories": categories,
        "anchors": [
            {
                "anchor_id": anchor.product_id,
                **anchor.model_dump(
                    include={
                        "title",
                        "sku",
                        "category_path",
                        "description",
                        "attributes",
                        "os",
                        "product_type",
                    }
                ),
            }
            for anchor in anchors
        ],
    }
    return request_json(PLAN_PROMPT, payload, Plan, complete_json=complete_json, llm=llm)


def graph_plan(graph, anchors, categories, query):
    directions = [
        Direction(
            anchor_id=anchor.product_id,
            category_id=edge.target_id,
            query=edge.query,
            title_terms=edge.title_terms,
            reason=edge.reason,
        )
        for anchor in anchors
        for edge in graph.category_edges
        if edge.source_id in {anchor.category_id, *anchor.category_ids}
        and edge.target_id in categories
    ]
    mentioned = [
        direction
        for direction in directions
        if any(term.casefold() in query.casefold() for term in direction.title_terms)
    ]
    return Plan(directions=(mentioned or directions)[:2])


def recommend(
    query_spec: QuerySpec,
    anchors: list[Product],
    *,
    search,
    categories,
    llm=None,
    mode: Literal["bm25", "dense", "hybrid"] = "bm25",
    trace: list | None = None,
    graph: RecommendationGraph | None = None,
    complete_json=None,
) -> RecommendationResult:
    output = RecommendationResult()
    if not anchors:
        return output

    relation = "substitute" if query_spec.intent == "alternatives" else "cross_sell"
    requested_software = any(
        SOFTWARE_REQUEST.search(query)
        for query in (
            query_spec.raw_query,
            query_spec.normalized_query,
        )
    )
    by_id = {anchor.product_id: anchor for anchor in anchors}
    family_search = graph is not None and relation == "substitute"
    if graph is None:
        plan = deepseek_plan(
            query_spec,
            anchors,
            llm=llm,
            categories=categories,
            relation=relation,
            requested_software=requested_software,
            complete_json=complete_json,
        )
    else:
        if family_search:
            plan = Plan(
                directions=[
                    Direction(
                        anchor_id=anchor.product_id,
                        category_id=anchor.category_id,
                        query=anchor.family_name[:200],
                        title_terms=[anchor.family_name],
                        reason="Предложение из того же семейства каталога.",
                    )
                    for anchor in anchors
                    if anchor.family_id and anchor.family_name and anchor.category_id in categories
                ][:2]
            )
        else:
            plan = (
                graph_plan(graph, anchors, categories, query_spec.raw_query)
                if not requested_software
                else Plan(directions=[])
            )
        if not plan.directions:
            output.warnings.append("recommendation_graph_no_rule")

    seen = set()
    for direction in plan.directions:
        step = {**direction.model_dump(), "candidates": []}
        if trace is not None:
            trace.append(step)
        terms = [term.strip().casefold() for term in direction.title_terms if term.strip()]
        if (
            direction.anchor_id not in by_id
            or direction.category_id not in categories
            or not terms
            or not direction.query.strip()
        ):
            step["status"] = "invalid_direction"
            output.warnings.append("invalid_recommendation_direction")
            continue
        filters = HardFilters(in_stock=True)
        if relation == "substitute":
            original = query_spec.hard_filters
            filters = original.model_copy(
                update={"in_stock": True if original.in_stock is None else original.in_stock}
            )
        elif query_spec.hard_filters.budget_scope == "bundle":
            filters.budget_scope = "bundle"
            filters.max_total_price_kopecks = query_spec.hard_filters.max_total_price_kopecks
        params = SearchParams(
            query_spec=QuerySpec(
                raw_query=query_spec.raw_query,
                normalized_query=direction.query,
                intent="alternatives" if relation == "substitute" else "complements",
                category=direction.category_id,
                hard_filters=filters,
                preferences=query_spec.preferences if relation == "substitute" else {},
            ),
            mode=mode,
            top_k=8,
        )
        response = search(params.model_dump(mode="json"))
        result = SearchResult.model_validate(response, from_attributes=True)
        step["status"] = result.status
        step["warnings"] = result.warnings
        output.warnings.extend(result.warnings)
        if result.status == "unavailable":
            output.warnings.append("recommendation_search_unavailable")
            continue
        if result.status == "partial":
            output.warnings.append("recommendation_search_partial")
        count = 0
        for hit in result.hits:
            product = hit.product
            pair = (direction.anchor_id, product.product_id)
            entry = product.model_dump(include={"product_id", "title", "category_id"})
            step["candidates"].append(entry)
            if product.product_id in by_id or pair in seen:
                entry["status"] = "duplicate"
            elif (
                direction.category_id not in {product.category_id, *product.category_ids}
                or product.in_stock is not filters.in_stock
                or not product.url
            ):
                entry["status"] = "catalog_filter"
            elif family_search and product.family_id != by_id[direction.anchor_id].family_id:
                entry["status"] = "different_family"
            elif not family_search and not any(term in product.title.casefold() for term in terms):
                entry["status"] = "wrong_product_type"
            elif requested_software and product.product_type != "software":
                entry["status"] = "outside_requested_type"
            elif count >= (4 if relation == "substitute" else 3):
                entry["status"] = "limit"
            else:
                entry["status"] = "selected"
                seen.add(pair)
                count += 1
                output.recommendations.append(
                    RecommendationDraft(
                        anchor_id=direction.anchor_id,
                        type=relation,
                        product=product,
                        claim=direction.reason,
                    )
                )
    output.warnings = list(dict.fromkeys(output.warnings))
    return output


def local_product(offer) -> Product:
    price = offer["price"]
    currency = offer["currency"]
    kopecks = None
    if price is not None and currency in {"RUR", "RUB"}:
        kopecks = (
            int((Decimal(str(price)) * 100).quantize(Decimal(1), rounding=ROUND_HALF_UP)) or None
        )
    declared_types = {
        PRODUCT_TYPES.get(value.casefold(), "unknown")
        for value in offer["params"].get("Тип продукта", [])
    }
    return Product(
        product_id=offer["offer_id"],
        sku=offer["vendor_code"] or None,
        title=offer["name"],
        category=(offer["category_path"] or [""])[-1],
        category_id=offer["category_id"] or None,
        category_path=offer["category_path"],
        brand=offer["vendor"] or None,
        family_id=offer["group_id"],
        description=offer["description"],
        attributes=offer["params"],
        price_kopecks=kopecks,
        source_currency=currency or None,
        in_stock=offer["available"],
        url=offer["url"] or None,
        product_type=declared_types.pop() if len(declared_types) == 1 else "unknown",
    )


def local_search(engine, params) -> SearchResult:
    from .retrieval import Filters, SearchArgs

    request = SearchParams.model_validate(params)
    spec = request.query_spec
    filters = spec.hard_filters
    if filters.country or filters.os or filters.devices is not None or filters.in_stock is False:
        return SearchResult(status="unavailable", warnings=["local_search_unsupported_filters"])
    budget = filters.max_total_price_kopecks
    bundle = budget is not None and filters.budget_scope == "bundle"
    max_price = budget / 100 if budget is not None and not bundle else None
    hits = engine.search_products(
        SearchArgs(
            query=spec.normalized_query,
            top_k=request.top_k,
            filters=Filters(
                category_id=spec.category,
                only_available=filters.in_stock is True,
                max_price=max_price,
            ),
        )
    )
    ranked = []
    for hit in hits:
        product = local_product(hit.product)
        if (
            budget is not None
            and not bundle
            and (product.price_kopecks is None or product.price_kopecks > budget)
        ):
            continue
        ranked.append(SearchHit(product=product, rank=len(ranked) + 1, score=hit.score))
    return SearchResult(
        hits=ranked,
        status="ok" if ranked else "empty",
        warnings=["bundle_total_requires_validation"] if bundle else [],
        diagnostics={"backend": "local_cosine"},
    )


def main():
    from .agent import DeepSeek
    from .retrieval import CosineSearch

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--offer-id", required=True)
    parser.add_argument("--query", required=True)
    parser.add_argument("--relation", choices=("cross_sell", "substitute"), default="cross_sell")
    parser.add_argument("--planner", choices=("deepseek", "graph"), default="deepseek")
    args = parser.parse_args()
    with closing(open_catalog(DB_PATH)) as db:
        row = db.execute("SELECT product FROM offers WHERE id = ?", (args.offer_id,)).fetchone()
        if not row:
            parser.error(f"Offer {args.offer_id} not found")
        anchor = local_product(json.loads(row[0]))
        categories = catalog_categories(db)
    if args.planner == "deepseek" and not os.getenv("DEEPSEEK_API_KEY"):
        parser.exit(1, "DEEPSEEK_API_KEY is missing from this terminal's environment.\n")
    try:
        trace = []
        engine = CosineSearch(DB_PATH, INDEX_PATH)
        graph = RecommendationGraph.load() if args.planner == "graph" else None
        with DeepSeek() if graph is None else nullcontext() as llm:
            result = recommend(
                QuerySpec(
                    raw_query=args.query,
                    normalized_query=args.query,
                    intent="alternatives" if args.relation == "substitute" else "complements",
                ),
                [anchor],
                search=lambda params: local_search(engine, params),
                llm=llm,
                categories=categories,
                trace=trace,
                graph=graph,
            )
            if graph is None:
                evidence = research_batch(
                    result.recommendations, anchors=[anchor], query=args.query, llm=llm
                )
            else:
                with closing(sqlite3.connect(EDGE_DB_PATH)) as db, db:
                    evidence = check_graph_evidence(db, result.recommendations, anchors=[anchor])
    except httpx.HTTPStatusError as error:
        status = error.response.status_code
        detail = " Load a valid DEEPSEEK_API_KEY into this terminal." if status == 401 else ""
        parser.exit(1, f"DeepSeek returned HTTP {status}.{detail}\n")
    except httpx.RequestError as error:
        if isinstance(error, (httpx.ConnectError, httpx.ConnectTimeout)):
            detail = " after three connection attempts"
        else:
            detail = ""
        parser.exit(1, f"DeepSeek connection failed ({type(error).__name__}){detail}.\n")
    except ValidationError:
        parser.exit(1, "DeepSeek returned an invalid recommendation plan.\n")
    print(
        json.dumps(
            {
                "anchor": anchor.model_dump(),
                **result.model_dump(),
                "evidence": [item.model_dump(mode="json") for item in evidence],
                "trace": trace,
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
