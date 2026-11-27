# Умный поиск Softline

Прототип принимает запрос на естественном языке, ищет товары, проверяет ограничения и показывает связанные предложения с основанием. Страница аналитики показывает число поисков, статусы, причины недобора и p95.

## Единый пайплайн

Ветка локальной интеграции объединяет `module1` (LangGraph, API, UI и события), `module2` (клиент DeepSeek/Ollama), `module-3` (каталог и Qdrant) и `module4` (планирование рекомендаций и проверка источников). Рабочие реализации связаны в [smart_search/backend.py](smart_search/backend.py). Команды ниже выполняются из корня этого репозитория.

1. Установите окружение командой `uv sync --locked --python 3.12`. Для нового клона скопируйте шаблон командой `if (!(Test-Path .env)) { Copy-Item .env.example .env }`. В подготовленной локальной сборке файл уже создан.
2. В корневом `.env` заполните `DEEPSEEK_API_KEY` и при необходимости `DEEPSEEK_MODEL`. Для Ollama выберите `LLM_PROVIDER=ollama` и укажите доступную модель в `OLLAMA_MODEL`. Переменные процесса имеют приоритет. После изменения настроек перезапустите приложение. [Шаблон параметров](.env.example)
3. Подготовьте каталог и индекс, если переносите приложение на новую машину. В рабочей директории `kuznica_merge` они уже собраны из выгрузки Softline. В новом клоне укажите фактический путь к XML.

   ```powershell
   uv run --locked python -X utf8 -m softline_search prepare "../Умный поиск на сайте/Mindbox.yml.gz.xml" --output data/catalog --reports .local/catalog-reports
   uv run --locked --env-file .env python -X utf8 -m softline_search index --catalog data/catalog --output data/qdrant
   ```

4. Для работы через браузер запустите `uv run --locked python -m streamlit run smart_search/interfaces/web.py`. Форма вызывает весь граф в этом процессе. Для HTTP API используйте `uv run --locked python -m uvicorn smart_search.interfaces.api:app --host 127.0.0.1 --port 8000`. Интерактивная документация доступна по адресу `http://127.0.0.1:8000/docs`. Если запускаете оба процесса одновременно, укажите для Streamlit `SMART_SEARCH_API_URL=http://127.0.0.1:8000`. Тогда локальным Qdrant владеет только процесс API. Для нескольких процессов поиска нужен сервер Qdrant.
5. Для аналитики укажите адрес брокера в `SMART_SEARCH_KAFKA_BOOTSTRAP_SERVERS` и создайте топик `smart-search.search-events.v1`. При пустом адресе приложение сообщает об ошибке доставки в журнале и возвращает пользователю найденные товары. [Контракт событий](docs/contracts/contract_events_interface.md)

Пример проверки готовности и поиска артикула через API из PowerShell:

```powershell
Invoke-RestMethod http://127.0.0.1:8000/health/ready
$body = @{ query = "Артикул: AL16-03SWS701-0100" } | ConvertTo-Json
Invoke-RestMethod -Method Post -Uri http://127.0.0.1:8000/v1/search -ContentType "application/json; charset=utf-8" -Body ([Text.Encoding]::UTF8.GetBytes($body))
```

### Поиск и модели

Для точного SKU используется SQLite, для остальных запросов в готовой локальной конфигурации — BM25 в Qdrant. Для семантического поиска задайте `SOFTLINE_DENSE_MODEL` именем поддерживаемой FastEmbed модели, укажите это же имя при индексации через `--dense-model` и установите `SMART_SEARCH_RETRIEVAL_MODE=hybrid`. Это отдельные веса для эмбеддингов. Ключ DeepSeek используется для разбора сложных запросов, планирования рекомендаций и проверки цитат.

При таймауте модели разбор сохраняет извлечённые правилами ограничения. Для рекомендаций резервом служат направления из [графа категорий](data/recommendation_graph.json) и совпадение именованного семейства для вариантов товара. Для явного выбора локального режима рекомендаций установите `SMART_SEARCH_RECOMMENDATION_PLANNER=graph`. В рабочей сборке источниками доказательств служат поля карточек. Отдельный корпус документов вендоров в исходных ветках отсутствует. При неизвестной совместимости валидатор исключает рекомендацию.

Граф передаёт одинаковые карточки в планирование и пакетную проверку. Проверка сопоставляет исходный товар, предложенный товар, утверждение и фрагмент источника. У подбора есть один повтор с увеличенным числом кандидатов. Сквозные тесты проходят этот путь от HTTP-запроса до записи события с настоящим временным индексом. [Сквозные проверки](tests/integration/test_pipeline.py)

## Структура проекта

| Каталог | Содержимое |
|---|---|
| [smart_search](smart_search) | Модели, контракт инструментов, граф Supervisor, выбор режима и аналитика. |
| [smart_search/modes](smart_search/modes) | Демонстрационные адаптеры `test` и загрузка рабочего модуля для `prod`. |
| [smart_search/interfaces](smart_search/interfaces) | Streamlit, HTTP API и JSON-настройки интерфейса. |
| [smart_search/events](smart_search/events) | Выбор получателя событий и Kafka-продьюсер для аналитики. |
| [scripts](scripts) | Команды оценки качества. |
| [tests](tests) | Модульные проверки в `unit/`, интеграционные проверки в `integration/` и данные в `fixtures/`. |
| [docs/contracts](docs/contracts) | [HTTP-контракт](docs/contracts/contract_request_interface.md), [контракт поискового модуля](docs/contracts/contract_tools_interface.md) и [контракт событий](docs/contracts/contract_events_interface.md). |

## Запуск в режиме test

Python 3.12 и `uv`:

```powershell
uv sync --locked
$env:SMART_SEARCH_MODE = "test"
uv run --locked python -m streamlit run smart_search/interfaces/web.py
```

Откройте адрес из вывода Streamlit. Попробуйте «Сервер», «Сервер до 300 тысяч», «Аналоги AnyDesk» и «Что докупить к Astra Linux SE?». В последнем сценарии в срезе есть предложение техподдержки Astra Linux SE и отдельная серверная лицензия КриптоПро CSP. Для точного маршрута используйте «Артикул: 458218».

Демонстрационные адаптеры используют небольшой срез предложений из предоставленной выгрузки Softline. Для проверки переходов в нём заданы тестовые связи и примеры оснований. Журнал демо хранится в `.local/events.sqlite3`. Можно задать другой путь через `SMART_SEARCH_EVENT_DB`.

Готовые запросы, подписи рекомендаций и тексты статусов настраиваются в [ui_config.json](smart_search/interfaces/ui_config.json). Кнопка «Прогнать примеры» на вкладке «Проверка примеров» запускает весь список и выводит таблицу результатов. Для автоматических проверок используйте `uv run --locked python -m pytest -q`.

## Запуск в режиме prod

Общие модели и владельцы перечислены в [AGENTS.md](AGENTS.md). Укажите `prod` и имя модуля с реальными поисковыми адаптерами. В форме Streamlit доступны произвольный текст и отправка по Enter.

```powershell
$env:SMART_SEARCH_MODE = "prod"
$env:SMART_SEARCH_BACKEND_MODULE = "smart_search.backend"
$env:SMART_SEARCH_KAFKA_BOOTSTRAP_SERVERS = "127.0.0.1:19092"
uv run --locked python -m streamlit run smart_search/interfaces/web.py
```

Этот модуль экспортирует `understand`, `search`, `recommend`, `research` и `validate`. Для вызовов из Python используйте `smart_search.runtime.run_search(SearchRequest(query="Сервер"))`. Для тестов доступна функция `smart_search.supervisor.build_search` с явной подстановкой `Services`.

Для программного приёма запросов запустите в том же окружении `uv run --locked python -m uvicorn smart_search.interfaces.api:app --host 127.0.0.1 --port 8000` и отправляйте JSON на `POST /v1/search`. Формат запроса, ответа и ошибок описан в [контракте интерфейса](docs/contracts/contract_request_interface.md).

Граф сначала определяет маршрут. Явный SKU идёт прямо в поиск. При других запросах узел понимания извлекает ограничения или возвращает вопрос. После поиска валидатор проверяет предложения. Для дополнений и замен граф передаёт исходную позицию в рекомендации и собирает доказательства. При недоборе он один раз увеличивает лимит кандидатов, сохраняя обязательные ограничения. Для явного запроса альтернатив количество считается по проверенным заменам, для дополнений — по проверенным сопутствующим предложениям.

После формирования ответа Supervisor создаёт событие результата. В `prod` он отправляет его в Kafka-совместимый топик `smart-search.search-events.v1`. Подготовьте топик на брокере и укажите адрес приложения через `SMART_SEARCH_KAFKA_BOOTSTRAP_SERVERS`. [Формат сообщения и настройка Redpanda](docs/contracts/contract_events_interface.md)

## Отчёт качества

```powershell
uv run --locked python -m scripts.evaluate --output .local/demo-evaluation.json
uv run --locked python -m scripts.evaluate --backend-module smart_search.backend --judgments path/to/judgments.json --output .local/integration-evaluation.json
```

Разметка задаёт `query` и оценки `grades` от 0 до 3 по стабильным ID товаров. Precision@4 делится на четыре, включая незаполненные позиции. NDCG@10 использует прирост `2^grade - 1`. При отсутствии релевантных позиций NDCG в отчёте равен нулю. Неуказанные в разметке ID получают оценку ноль, поэтому для справедливого сравнения заранее разметьте объединение кандидатов. Доля пустых выдач считается среди завершённых поисков `ok / partial / empty`. Ошибки зависимости и уточнения показываются отдельными статусами. p95 считается по ближайшему рангу.

Набор `tests/fixtures/demo_judgments.json` предназначен для проверки контура. В этом режиме BM25, dense и hybrid используют одинаковую заглушку поиска. Метрики рабочего Qdrant и DeepSeek вычисляются при подключении реальных адаптеров.
