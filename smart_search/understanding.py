"""Translate Russian catalog requests into the supervisor's synchronous QuerySpec."""

import json
import re
from decimal import Decimal

from pydantic import ValidationError

from smart_search.catalog_backend import resolve_category, resolve_type_category_hints
from smart_search.contracts import HardFilters, Preferences, QuerySpec, SearchRequest
from smart_search.llm.exceptions import LLMError
from smart_search.model_client import complete_json

SLANG = (
    (r"\b(?:моник|манитор|мониторчик|мониторик)\b", "монитор"),
    (r"\bргб\b", "RGB"),
    (r"\b(?:антивир[ья]|антивриус|антиврус|антивирсу)\b", "антивирус"),
    (r"\b(?:винд(?:а|ы|у|е|ой|овс)|виндоус|виндовс)\b", "Windows"),
    (r"\b(?:комп(?:ы|ов|ах|ам|а|е|ами)?)\b", "компьютеры"),
    (r"\b(?:бэкап|бек[аэ]п)\w*\b", "резервное копирование"),
)
MOUSE = re.compile(r"\b(?:мышь|мышк[ауи])\b", re.IGNORECASE)
KEYBOARD = re.compile(r"\b(?:клавиатур[ауыеой]*|клав[ауы])\b", re.IGNORECASE)
RGB = re.compile(r"\b(?:rgb|ргб)\b", re.IGNORECASE)
OS_NAME = re.compile(r"\b(windows|linux|mac\s?os|android|ios)\b", re.IGNORECASE)
OS_LABELS = {
    "windows": "Windows",
    "linux": "Linux",
    "macos": "macOS",
    "android": "Android",
    "ios": "iOS",
}
DEVICE_NOUN = r"(?:устройств\w*|компьютер\w*|пк|ноутбук\w*|рабоч\w*\s+(?:станци|мест)\w*)"
DEVICES = re.compile(rf"\b(\d+)\s*{DEVICE_NOUN}\b", re.IGNORECASE)
NUMBER = r"(?:\d{1,3}(?: \d{3})+|\d+)(?:[,.]\d{1,2})?"
MONEY = (
    rf"(?P<amount>{NUMBER})"
    rf"(?:\s*(?P<scale>тыс(?:яч\w*)?\.?|тыщ\w*|[кk]|млн\.?|миллион\w*))?"
    rf"(?:\s*(?:руб\w*\.?|₽|р\.))?"
)
CEILING = re.compile(
    rf"\b(?:до|не\s+(?:более|дороже|выше)|максимум|бюджет(?:ом)?(?:\s+до)?)\s*[:—–-]?\s*{MONEY}(?![\w\d])",
    re.IGNORECASE,
)
FLOOR = re.compile(
    rf"\b(?:от|дороже|не\s+дешевле|минимум)\s*[:—–-]?\s*{MONEY}(?![\w\d])",
    re.IGNORECASE,
)
TARGET = re.compile(rf"\b(?:около|примерно|ориентир(?:овочно)?)\s*{MONEY}(?![\w\d])", re.IGNORECASE)
IN_STOCK = re.compile(r"\b(?:в\s+наличии|со\s+склада|на\s+складе)\b", re.IGNORECASE)
COUNTRY_RU = re.compile(r"\b(?:российск\w*|отечественн\w*|из\s+(?:россии|рф))\b", re.IGNORECASE)
BUNDLE = re.compile(r"\b(?:комплект\w*|набор\w*)\b", re.IGNORECASE)
SKU_MARKER = re.compile(r"\b(?:sku|артикул[ауеом]*)\s*[:№#]?\s+([\w./-]+)", re.IGNORECASE)
SKU_TOKEN = re.compile(r"[A-Za-z0-9]+(?:[-_./][A-Za-z0-9]+)+")
ALTERNATIVE = re.compile(
    r"\b(?:аналог\w*|замен[ауые]|заменить|вместо|альтернатив\w*)\s*(?:(?:для|к|у)\s+)?",
    re.IGNORECASE,
)
COMPLEMENT = re.compile(
    r"\b(?:докуп\w*|дополнени\w*|совместим\w*|подойд[её]т)\s*(?:(?:для|с|к)\s+)?",
    re.IGNORECASE,
)
PRODUCT_TYPE_PATTERNS = (
    (MOUSE.pattern, "мышь"),
    (KEYBOARD.pattern, "клавиатура"),
    (r"\bмонитор\w*\b", "монитор"),
    (r"(?:^|\b(?:нужен|подберите|найти|купить)\s+)сервер(?:ы|а)?\b", "сервер"),
    (r"\bибп\b|бесперебойн\w+\s+питани", "ибп"),
    (r"\bноутбук\w*\b", "ноутбук"),
    (r"\b(?:блок\w*\s+питания|бп)\b", "блок питания"),
    (r"\bпринтер\w*\b|\bмфу\b", "принтер"),
    (r"\b(?:коммутатор\w*|свитч\w*|свич\w*)\b", "коммутатор"),
    (r"\bроутер\w*\b|\bмаршрутизатор\w*\b", "роутер"),
    (r"\bвеб-?камер\w*\b", "веб-камера"),
    (r"\bssd\b|твердотельн\w+\s+накопител", "ssd"),
)

PROMPT = """Ты — главный ИИ-маршрутизатор и интерпретатор запросов интернет-магазина ИТ-продуктов Store.softline.ru.
Верни ТОЛЬКО один JSON-объект, строго соответствующий схеме QuerySpec.

Твоя задача:
1. Понять смысл запроса пользователя (включая опечатки, жаргон, разговорный стиль, неверные предлоги вроде «да 10 тыщ»).
2. Сохранить исходный текст в `raw_query`.
3. Сформировать чистый, грамотный `normalized_query` для поиска:
   - исправь опечатки, транслит и сленг («хачу мышку да 10 тыщ рублей» -> «компьютерная мышь», «моник» -> «монитор», «антивирь под винду» -> «антивирус Windows», «noutbuk dlya raboty do 80k» -> «ноутбук для работы»);
   - убери слова вроде «хочу», «купить», «подскажите», повторы и фразы о цене/бюджете;
   - обязательно сохраняй точные названия моделей, бренды, версии с точками (5.0, 1.8, 2024) и важные характеристики (27 дюймов, 1920×1080, RGB, 750W).
4. Самостоятельно выбери поисковый инструмент в `search_mode`:
   - "sku" — только если пользователь указал конкретный артикул (тогда скопируй его в `sku` дословно из текста).
   - "bm25" — если пользователь ввёл точное название продукта, бренд и модель, или конкретную версию ПО («КриптоПро CSP 5.0», «Astra Linux Special Edition», «AnyDesk»). Для bm25 ставь `score_threshold: null`.
   - "dense" — если запрос разговорный, с опечатками, жаргоном или описывает задачу своими словами («хачу мышку да 10 тыщ рублей», «моник для работы с текстом», «чем защитить корпоративную почту»). Для dense задавай `score_threshold` (обычно 0.30–0.42, например 0.35), чтобы отсечь далёкие по смыслу товары.
   - "hybrid" — если запрос сочетает точные характеристики/версии/числа и свободное описание («монитор 27 дюймов 2560×1440 для текста», «клавиатура от 10 тыс с ргб», «российский антивирус под Windows на 50 компов»). Для hybrid можешь задать `score_threshold` (например 0.30) или `null`.
5. Определи `intent`:
   - "product_search" — обычный подбор товара;
   - "alternatives" — запрос аналогов/замены («вместо AnyDesk», «аналог Windows Server»);
   - "complements" — запрос дополнений («что докупить к ...»);
   - "solution" — запрос комплекта или комплексного ИТ-решения («комплект для офиса», «чем защитить сеть предприятия»);
   - "exact_product" — поиск конкретного артикула (`sku`).
   Для "alternatives" и "complements" в `normalized_query` укажи исходный товар, относительно которого ищутся аналоги/дополнения.
6. Не угадывай названия разделов каталога: оставляй `category: null` и `category_hints: []`, чтобы не отрезать подходящие товары при несовпадении названия папки магазина.
   Вместо угадывания категории используй:
   - `product_type_hint`: канонический тип товара («мышь», «клавиатура», «монитор», «сервер», «ноутбук», «ИБП», «антивирус», «операционная система»), если пользователь явно просит конкретный вид товара (иначе `null`);
   - `required_terms`: список обязательных характеристик, которые должны быть подтверждены в карточке (например `["RGB"]` при запросе «с ргб»).
7. Извлеки ограничения в `hard_filters` (все денежные суммы ЦЕЛЫМИ ЧИСЛАМИ В КОПЕЙКАХ, т.е. рубли * 100):
   - `min_total_price_kopecks`: минимальная цена («от 10 тыс» -> 1000000);
   - `max_total_price_kopecks`: максимальный бюджет («да 10 тыщ» / «до 30к» -> 1000000 / 3000000);
   - КРИТИЧНО: НИКОГДА не принимай технические характеристики с предлогами «от»/«до» за цену! Например: «от 750 ватт» (мощность БП), «от 27 дюймов» (диагональ), «на 8 портов», «от 144 Гц», «на 1 ТБ», «от 2 штук» — это НЕ деньги, для них `min_total_price_kopecks: null` и `max_total_price_kopecks: null`;
   - `os`: "Windows" | "Linux" | "macOS" | "Android" | "iOS" (НЕ ставь os, если ищут замену самой ОС, например «аналог Windows Server»);
   - `country`: "RU", если требуется российское/отечественное ПО или оборудование;
   - `devices`: целое положительное число устройств/ПК;
   - `in_stock`: true, если явно запрошено «в наличии»;
   - `budget_scope`: "bundle" для комплекта, иначе "primary", если задан бюджет.
8. Не придумывай артикулы, бренды, цены или ограничения. Если запрос принципиально неоднозначен или содержит отрицание без выбора («антивирус не для Windows»), задай короткий `clarifying_question`.
9. Как использовать `previous_context`:
   - Опирайся на `previous_context` ТОЛЬКО если текущий `request` является прямым продолжением или уточнением предыдущего запроса (например: «подешевле», «до 5 тысяч», «беспроводную», «под Linux», «что к нему докупить?», ответ на `clarifying_question`).
   - Если `request` называет НОВЫЙ самостоятельный товар или категорию (например, после «антивирус под Windows на 50 компов» пользователь ввёл «мышь», «красная клавиатура» или «сервер»), полностью ИГНОРИРУЙ старые `hard_filters` (`os`, `devices`, `country`, бюджет) и старый товар из `previous_context`.

Примеры:
Запрос: «хачу мышку да 10 тыщ рублей»
Ответ: {"raw_query":"хачу мышку да 10 тыщ рублей","normalized_query":"компьютерная мышь","intent":"product_search","search_mode":"dense","score_threshold":0.35,"product_type_hint":"мышь","hard_filters":{"max_total_price_kopecks":1000000,"budget_scope":"primary"}}

Запрос: «хочу клавиатуру от 10 тыс с ргб»
Ответ: {"raw_query":"хочу клавиатуру от 10 тыс с ргб","normalized_query":"клавиатура RGB","intent":"product_search","search_mode":"hybrid","score_threshold":0.3,"product_type_hint":"клавиатура","required_terms":["RGB"],"hard_filters":{"min_total_price_kopecks":1000000,"budget_scope":"primary"}}

Запрос: «КриптоПро CSP 5.0»
Ответ: {"raw_query":"КриптоПро CSP 5.0","normalized_query":"КриптоПро CSP 5.0","intent":"product_search","search_mode":"bm25","score_threshold":null}

Запрос: «моник для работы с текстом до 25к»
Ответ: {"raw_query":"моник для работы с текстом до 25к","normalized_query":"монитор для работы с текстом","intent":"product_search","search_mode":"dense","score_threshold":0.35,"product_type_hint":"монитор","hard_filters":{"max_total_price_kopecks":2500000,"budget_scope":"primary"}}

Запрос: «нужен российский антивирь под винду на 50 компов до 100к»
Ответ: {"raw_query":"нужен российский антивирь под винду на 50 компов до 100к","normalized_query":"российский антивирус Windows","intent":"product_search","search_mode":"hybrid","product_type_hint":"антивирус","hard_filters":{"os":"Windows","country":"RU","devices":50,"max_total_price_kopecks":10000000,"budget_scope":"primary"}}

Запрос: «что взять вместо AnyDesk до 5к?»
Ответ: {"raw_query":"что взять вместо AnyDesk до 5к?","normalized_query":"AnyDesk","intent":"alternatives","search_mode":"hybrid","hard_filters":{"max_total_price_kopecks":500000,"budget_scope":"primary"}}

Запрос: «что докупить к Astra Linux Special Edition 1.8?»
Ответ: {"raw_query":"что докупить к Astra Linux Special Edition 1.8?","normalized_query":"Astra Linux Special Edition 1.8","intent":"complements","search_mode":"bm25"}

Запрос: «noutbuk dlya raboty do 80k»
Ответ: {"raw_query":"noutbuk dlya raboty do 80k","normalized_query":"ноутбук для работы","intent":"product_search","search_mode":"dense","score_threshold":0.35,"product_type_hint":"ноутбук","hard_filters":{"max_total_price_kopecks":8000000,"budget_scope":"primary"}}

Запрос: «хочу золотой блок питания от 750 ватт»
Ответ: {"raw_query":"хочу золотой блок питания от 750 ватт","normalized_query":"блок питания 80 PLUS Gold 750W","intent":"product_search","search_mode":"hybrid","score_threshold":0.3,"product_type_hint":"блок питания","required_terms":["80 PLUS Gold","750W"],"hard_filters":{}}

Вход с контекстом (уточнение предыдущего товара): {"request": "а беспроводную до 5к?", "previous_context": "Предыдущий запрос: мышь"}
Ответ: {"raw_query":"а беспроводную до 5к?","normalized_query":"беспроводная компьютерная мышь","intent":"product_search","search_mode":"dense","score_threshold":0.35,"product_type_hint":"мышь","required_terms":["беспроводная"],"hard_filters":{"max_total_price_kopecks":500000,"budget_scope":"primary"}}

Вход с контекстом (смена товара — старые фильтры НЕ переносятся): {"request": "мышь", "previous_context": "Предыдущий запрос: Антивирь для винды на 50 компов до 100к"}
Ответ: {"raw_query":"мышь","normalized_query":"компьютерная мышь","intent":"product_search","search_mode":"dense","score_threshold":0.35,"product_type_hint":"мышь","hard_filters":{}}
"""


def _normalize(text: str) -> str:
    words = text.split()
    midpoint = len(words) // 2
    if midpoint > 1 and words[:midpoint] == words[midpoint:]:
        words = words[:midpoint]
    text = " ".join(words)
    for pattern, replacement in SLANG:
        text = re.sub(pattern, replacement, text, flags=re.IGNORECASE)
    return text


def _sku(entity: str) -> str | None:
    marked = SKU_MARKER.search(entity)
    if marked:
        return marked[1].rstrip(".")
    token = entity.strip(" !?.,")
    if (
        SKU_TOKEN.fullmatch(token)
        and re.search(r"[a-z]", token, re.IGNORECASE)
        and re.search(r"\d", token)
    ):
        return token
    # Bare catalog codes can contain digits only, or a contiguous alphanumeric model.
    if re.fullmatch(r"\d{5,}", token) or (
        re.fullmatch(r"[A-Z0-9]{5,}", token)
        and re.search(r"[A-Z]", token)
        and re.search(r"\d{2,}", token)
    ):
        return token
    return None


def _money(match: re.Match) -> int:
    value = Decimal(match["amount"].replace(" ", "").replace(",", "."))
    scale = (match["scale"] or "").casefold()
    multiplier = 1_000_000 if scale.startswith("м") else 1_000 if scale else 1
    return int(value * multiplier * 100)


def _prices(pattern: re.Pattern, text: str) -> list[re.Match]:
    out = []
    for match in pattern.finditer(text):
        tail = text[match.end() :]
        if re.match(
            rf"\s*(?:{DEVICE_NOUN}|шт\.?|год\w*|лет|мес\w*|дюйм\w*|ватт\w*|вт\b|w\b|порт\w*|гц\b|мгц\b|гб\b|тб\b|мм\b|см\b|дб\b|кг\b)",
            tail,
            re.IGNORECASE,
        ):
            continue
        # Require either explicit scale/currency or a plausible ruble amount (>= 100)
        has_unit = bool(match["scale"]) or bool(
            re.search(r"(?:руб|₽|\bр\.)", match.group(0), re.IGNORECASE)
        )
        if not has_unit and _money(match) < 10_000:
            continue
        out.append(match)
    return out


def _detect_product_type(text: str) -> str | None:
    for pattern, label in PRODUCT_TYPE_PATTERNS:
        if re.search(pattern, text, re.IGNORECASE):
            return label
    return None


def _rules(request: SearchRequest) -> QuerySpec:
    """Deterministic fallback parser used when DeepSeek is unavailable or on pure SKU."""
    text = _normalize(" ".join(filter(None, [request.previous_context, request.query])))
    ceilings = _prices(CEILING, text)
    floors = _prices(FLOOR, text)
    targets = _prices(TARGET, text)
    device_counts = list(DEVICES.finditer(text))
    relation = ALTERNATIVE.search(text) or COMPLEMENT.search(text)
    intent = "solution" if BUNDLE.search(text) else "product_search"
    entity = text
    if relation:
        intent = "alternatives" if relation.re is ALTERNATIVE else "complements"
        entity = text[relation.end() :].strip(" !?.,")
        entity = re.split(
            r"\s+(?=до\s+\d|от\s+\d|не дороже|в наличии|на \d+|под Windows|для \d+)",
            entity,
            maxsplit=1,
            flags=re.IGNORECASE,
        )[0]
    sku = _sku(entity)
    if sku:
        entity = sku
        if not relation:
            intent = "exact_product"
    product_type = None if sku else _detect_product_type(entity)
    required_terms = ["RGB"] if (not sku and RGB.search(text)) else []
    os_matches = list(OS_NAME.finditer(text))
    if (
        relation or re.search(r"windows\s+server|серверн\w+\s+ос", text, re.IGNORECASE)
    ) and os_matches:
        os_matches = [
            m
            for m in os_matches
            if re.search(r"(?:под|для|на|с поддержкой)\s+$", text[: m.start()], re.IGNORECASE)
        ]
    systems = {OS_LABELS[m[0].replace(" ", "").casefold()] for m in os_matches}
    question = None
    if re.search(
        r"\b(?:не|кроме|без)\s+(?:(?:под|для|на)\s+)?(?:windows|linux|mac\s?os|android|ios|российск\w*|отечественн\w*)\b",
        text,
        re.IGNORECASE,
    ):
        question = "Какие платформу или страну производителя нужно включить в подбор?"
        systems.clear()
    elif len(systems) > 1 or len({m[1] for m in device_counts}) > 1:
        question = "Уточните обязательную платформу и количество устройств."
    elif re.search(r"\b(?:доллар\w*|евро|usd|eur)\b|[$€]", text, re.IGNORECASE) and (
        ceilings or floors
    ):
        question = "Какой максимальный бюджет указать в рублях?"
        ceilings = []
        floors = []
    elif relation and (
        not entity
        or re.fullmatch(r"(?:нему|ней|ему|этому|этого|товару|продукту)", entity, re.IGNORECASE)
    ):
        question = "Для какого товара нужен подбор?"
    elif (
        intent == "alternatives"
        and entity.casefold() == "windows server"
        and not request.previous_context
    ):
        question = "Какую роль Windows Server должна выполнять замена?"

    max_budget = min((_money(m) for m in ceilings), default=None)
    min_budget = max((_money(m) for m in floors), default=None)
    has_budget = max_budget is not None or min_budget is not None

    if not relation and not sku:
        for match in sorted(
            [*ceilings, *floors, *targets], key=lambda item: item.start(), reverse=True
        ):
            entity = entity[: match.start()] + " " + entity[match.end() :]
        entity = re.sub(
            r"^\s*(?:(?:я\s+)?хочу\s+|купит[ь]?\s+|подберите\s+|нужен\s+|нужна\s+)+|\s+купит[ь]?(?=\s|$)",
            " ",
            entity,
            flags=re.IGNORECASE,
        )
        entity = " ".join(entity.split()).strip(" ,")
        if intent == "product_search" and MOUSE.search(entity) and RGB.search(entity):
            entity = re.sub(
                r"\b(?:я|хочу|купит[ь]?|настольн(?:ую|ая)|с|подсветк(?:ой|а))\b",
                " ",
                MOUSE.sub("мышь", entity),
                flags=re.IGNORECASE,
            )
            entity = " ".join(entity.split())

    preferred = re.search(
        r"\b(?:предпочитаю|предпочтителен бренд|желательно бренд)\s+([\w.-]+)", text, re.IGNORECASE
    )
    fallback_mode = "sku" if sku else "hybrid"
    return QuerySpec(
        raw_query=request.query,
        normalized_query=entity or text,
        intent=intent,
        sku=sku,
        category=None,
        category_hints=[],
        search_mode=fallback_mode,
        score_threshold=None,
        product_type_hint=product_type,
        required_terms=required_terms,
        clarifying_question=question,
        hard_filters=HardFilters(
            os=next(iter(systems)) if len(systems) == 1 else None,
            country="RU" if COUNTRY_RU.search(text) and not question else None,
            devices=int(device_counts[-1][1]) or None if device_counts else None,
            min_total_price_kopecks=min_budget,
            max_total_price_kopecks=max_budget,
            budget_scope=("bundle" if BUNDLE.search(text) else "primary") if has_budget else None,
            in_stock=True if IN_STOCK.search(text) else None,
        ),
        preferences=Preferences(
            preferred_brand=preferred[1] if preferred else None,
            target_price_kopecks=_money(targets[0]) if targets else None,
        ),
    )


def understand(request: SearchRequest) -> QuerySpec:
    fallback = _rules(request)
    # Fast-path explicit SKU (direct lookup, replacement, or complement) without burning an LLM call.
    if fallback.sku:
        return fallback

    prompt = json.dumps(
        {
            "request": request.query,
            "previous_context": request.previous_context,
        },
        ensure_ascii=False,
    )
    try:
        suggestion = complete_json(PROMPT, prompt, QuerySpec)
    except (LLMError, ValidationError, TimeoutError, ConnectionError):
        if fallback.intent == "product_search" and not fallback.sku and not fallback.category_hints:
            hints = resolve_type_category_hints(fallback.product_type_hint)
            if hints:
                fallback = fallback.model_copy(update={"category_hints": hints[:4]})
        return fallback.model_copy(update={"warnings": ["understanding_llm_fallback"]})

    # DeepSeek is in charge: only backfill None fields from fallback rules and guard against hallucinated SKUs.
    source_text = " ".join(filter(None, [request.previous_context, request.query])).casefold()
    sku = suggestion.sku if suggestion.sku and suggestion.sku.casefold() in source_text else None
    intent = suggestion.intent
    if intent == "exact_product" and not sku:
        intent = "product_search"

    # Never let an unverified category block search; keep category=None unless explicitly verified and requested.
    verified_category = None
    if suggestion.category and not sku:
        ids = resolve_category(suggestion.category)
        if ids and len(ids) == 1:
            verified_category = ids[0]

    hints = []
    if not (verified_category or sku):
        for hint in suggestion.category_hints:
            ids = resolve_category(hint)
            if ids:
                resolved = ids[0] if len(ids) == 1 else hint
                if resolved not in hints:
                    hints.append(resolved)
        if not hints and intent == "product_search":
            hints = resolve_type_category_hints(
                suggestion.product_type_hint or fallback.product_type_hint
            )

    hard = fallback.hard_filters.model_dump(exclude_none=True) | suggestion.hard_filters.model_dump(
        exclude_none=True
    )
    preferences = fallback.preferences.model_dump(
        exclude_none=True
    ) | suggestion.preferences.model_dump(exclude_none=True)

    entity = suggestion.normalized_query
    versions = re.findall(r"(?<![\w.,])\d+(?:\.\d+)+(?![\w.,])", request.query)
    if any(version not in entity for version in versions):
        entity = fallback.normalized_query

    search_mode = "sku" if sku else (suggestion.search_mode or "hybrid")
    score_threshold = suggestion.score_threshold if search_mode in {"dense", "hybrid"} else None

    return QuerySpec(
        raw_query=request.query,
        normalized_query=entity,
        intent=intent,
        sku=sku,
        category=verified_category,
        category_hints=hints[:4],
        search_mode=search_mode,
        score_threshold=score_threshold,
        product_type_hint=suggestion.product_type_hint,
        required_terms=suggestion.required_terms,
        hard_filters=HardFilters.model_validate(hard),
        preferences=Preferences.model_validate(preferences),
        clarifying_question=suggestion.clarifying_question,
    )
