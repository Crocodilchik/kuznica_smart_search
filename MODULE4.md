# Модуль 4. Рекомендации и проверка совместимости

`recommend` получает разобранный запрос и карточки основных товаров. В режиме DeepSeek модель планирует до двух поисковых запросов. Поиск возвращает карточки каталога, а модуль отбирает кандидатов в аналоги или дополнения.

`research_batch` проверяет пары сопутствующих товаров одним запросом к DeepSeek. Для вывода нужны цитаты из обеих карточек, которые сверяются с каталогом. При недостатке сведений возвращается `unknown`.

## Локальный запуск

```powershell
python -m pip install -r requirements-module4.txt
```

Для демо укажите пути к готовой базе и векторному индексу:

```powershell
$env:SOFTLINE_DB_PATH = 'D:/smar_search/data/catalog.db'
$env:SOFTLINE_INDEX_PATH = 'D:/smar_search/data/search_index.npz'
python -m src.recommendation --planner graph --offer-id 436655 --query "Что докупить к ноутбуку?"
```

Чтобы использовать DeepSeek, задайте `DEEPSEEK_API_KEY` в окружении процесса и уберите `--planner graph`. Для подбора аналогов в режиме DeepSeek добавьте `--relation substitute`.

В `data/catalog_card_examples.json` есть три полные карточки для ознакомления с данными. Для запуска CLI нужны база и индекс, указанные выше, либо тот же YML для локальной сборки:

```powershell
$env:SOFTLINE_XML_PATH = 'D:/smar_search/Mindbox.yml.gz.xml'
python -c "from src.catalog import build_catalog; from src.config import XML_FEED_PATH, DB_PATH; print(build_catalog(XML_FEED_PATH, DB_PATH))"
python -c "from src.config import DB_PATH, INDEX_PATH; from src.retrieval import CharacterEncoder, build_index, compact_representation; print(build_index(DB_PATH, INDEX_PATH, CharacterEncoder(), 1024, compact_representation))"
```

## Подключение к поиску команды

Передайте в `recommend(query_spec, anchors, search=search, llm=llm, categories=categories)` функцию разработчика 3 `softline_search.search.search` или метод долгоживущего `SearchEngine`. Функция должна принимать JSON-совместимый `SearchParams` и возвращать `SearchResult`. Справочник `categories` принимается как словарь `{category_id: название}`.

После подбора вызовите `research_batch(result.recommendations, anchors=anchors, query=query_spec.raw_query, llm=llm)`. Контракты `Product`, `QuerySpec`, `RecommendationResult` и `EvidenceResult` находятся в `src/contracts.py`.

В режиме графа направления берутся из `data/recommendation_graph.json`. Этот режим выполняется локально и использует проверку явных полей совместимости.
