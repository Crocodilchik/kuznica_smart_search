# Умный поиск Softline

Прототип принимает запрос на естественном языке, ищет товары, проверяет ограничения и показывает связанные предложения с основанием. В Streamlit остаётся аналитика по поискам, статусам, причинам недобора и p95.

## Единый пайплайн

Ветка локальной интеграции объединяет `module1` (LangGraph, API, UI и события), `module2` (клиент DeepSeek/Ollama), `module-3` (каталог и Qdrant) и `module4` (планирование рекомендаций и проверка источников). Рабочие реализации связаны в [smart_search/backend.py](smart_search/backend.py). Команды ниже выполняются из корня этого репозитория.

1. Установите окружение командой `uv sync --locked --python 3.12`. Для нового клона скопируйте шаблон командой `if (!(Test-Path .env)) { Copy-Item .env.example .env }`. В подготовленной локальной сборке файл уже создан.
2. В корневом `.env` заполните `DEEPSEEK_API_KEY` и при необходимости `DEEPSEEK_MODEL`. Для Ollama выберите `LLM_PROVIDER=ollama` и укажите доступную модель в `OLLAMA_MODEL`. Переменные процесса имеют приоритет. После изменения настроек перезапустите приложение. [Шаблон параметров](.env.example)
3. Подготовьте каталог и индекс, если переносите приложение на новую машину. В рабочей директории `kuznica_merge` они уже собраны из выгрузки Softline. В новом клоне укажите фактический путь к XML.

   ```powershell
   uv run --locked python -X utf8 -m softline_search prepare "data/raw/Mindbox.yml.gz.xml" --output data/catalog --reports .local/catalog-reports
   uv run --locked --env-file .env python -X utf8 -m softline_search index --catalog data/catalog --output data/qdrant
   ```

4. Для работы через браузер запустите Node.js-фронтенд из `frontend/` (`npm ci`, затем `npm run dev`). Нужен Node.js 24 LTS. В `.env` задайте `SMART_SEARCH_API_URL=http://127.0.0.1:8000`; серверный маршрут Next.js передаст запрос в FastAPI. Для HTTP API используйте `uv run --locked python -m uvicorn smart_search.interfaces.api:app --host 127.0.0.1 --port 8000`. Интерактивная документация доступна по адресу `http://127.0.0.1:8000/docs`. Streamlit остаётся отдельным интерфейсом для демонстрационного режима, аналитики и массовой проверки примеров; для этих экранов в API пока нет контрактов.
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

Граф передаёт одинаковые карточки в планирование и пакетную проверку. Проверка сопоставляет исходный товар, предложенный товар, утверждение и фрагмент источника. При недоборе граф может повторить поиск один раз. Для исчерпанного списка повтор сохраняется только при расширении предварительной области категорий. Явная категория и условия покупки при расширении сохраняются. [Сквозные проверки](tests/integration/test_pipeline.py)

## Структура проекта

| Каталог | Содержимое |
|---|---|
| [smart_search](smart_search) | Модели, контракт инструментов, граф Supervisor, выбор режима и аналитика. |
| [smart_search/modes](smart_search/modes) | Демонстрационные адаптеры `test` и загрузка рабочего модуля для `prod`. |
| [smart_search/interfaces](smart_search/interfaces) | Streamlit, HTTP API и JSON-настройки интерфейса. |
| [frontend](frontend) | Node.js, Next.js, React и TypeScript; серверный прокси к FastAPI и поисковая страница. |
| [smart_search/events](smart_search/events) | Выбор получателя событий и Kafka-продьюсер для аналитики. |
| [scripts](scripts) | Команды оценки качества. |
| [tests](tests) | Модульные проверки в `unit/`, интеграционные проверки в `integration/` и данные в `fixtures/`. |
| [docs/contracts](docs/contracts) | [HTTP-контракт](docs/contracts/contract_request_interface.md), [контракт поискового модуля](docs/contracts/contract_tools_interface.md) и [контракт событий](docs/contracts/contract_events_interface.md). |

## Запуск через Docker

На Windows и macOS нужен Docker Desktop в режиме Linux-контейнеров. На Linux подойдут Docker Engine и Compose 2.24 или новее. Python на компьютере устанавливать отдельно не нужно. [Требование к версии Compose](https://docs.docker.com/reference/compose-file/services/#env_file)

Compose поднимает FastAPI, Next.js и Qdrant. Qdrant доступен внутри сети Compose по адресу `http://qdrant:6333`. Файлы SQLite и манифест серверной коллекции лежат в томе `search-data`, данные Qdrant — в `qdrant-data`. Перезапуск API использует уже созданную коллекцию. Открыть интерфейс можно на [127.0.0.1:3000](http://127.0.0.1:3000). Порты интерфейса и API задаются через `SMART_SEARCH_FRONTEND_PORT` и `SMART_SEARCH_API_PORT`.

При первой сборке положите XML в [`data/raw/`](data/raw/README.md) или задайте его путь через `SOFTLINE_FEED_PATH`. Для обновления снимка остановите API на время замены каталога.

```powershell
docker compose stop api
docker compose run --rm --build catalog
if ($LASTEXITCODE -ne 0) { throw 'Не удалось собрать каталог' }
docker compose up -d --build --wait
```

Если каталог уже импортирован, `docker compose run --rm index` построит серверную коллекцию из существующего SQLite. Это также способ миграции со встроенного Qdrant. После успешной индексации выполните `docker compose up -d --build --wait`. Для подключения внешнего Qdrant заполните `SMART_SEARCH_COMPOSE_QDRANT_URL` и при необходимости `SMART_SEARCH_COMPOSE_QDRANT_API_KEY` в [`.env`](.env.example) до индексации. Для остановки используйте `docker compose down`. Том с индексом сохраняется при остановке.

Node.js-фронтенд обращается только к серверному маршруту `/api/search`. Для локального запуска задайте `SMART_SEARCH_API_URL` в `.env`; внутренняя переменная не имеет префикса `NEXT_PUBLIC_`. Compose задаёт её как `http://api:8000`. Готовность проверяется через `/api/health/ready`. Синхронный запрос к API может длиться до 120 секунд. Streamlit не удалён: его демонстрационные экраны аналитики и массовой проверки примеров запускаются отдельно и остаются вне Node UI, поскольку HTTP API для них отсутствует.

Если Docker Engine запущен внутри WSL и контейнер останавливается после выхода из сеанса WSL, запускайте `docker compose up --no-build` без `-d` и оставляйте терминал открытым.

### Замер BM25

Для замера первого обращения после перезапуска Qdrant запускайте команды последовательно на готовом индексе, при отсутствии параллельных поисковых запросов. Скрипт измеряет первый вызов с созданием поискового клиента и последующие вызовы того же клиента. Модель, рекомендации и Kafka в измерение не входят.

```powershell
$OutputEncoding = [Text.UTF8Encoding]::new($false)
docker compose restart qdrant
Get-Content -Raw -Encoding utf8 scripts/benchmark_bm25.py |
    docker compose run --rm -T index python -X utf8 -
```

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

### Генерация запросов по мониторам

```powershell
$env:DEEPSEEK_API_KEY = [Environment]::GetEnvironmentVariable('DEEPSEEK_API_KEY', 'User')
$env:DEEPSEEK_MODEL = 'deepseek-flash'
$env:LLM_PROVIDER = 'deepseek'
$env:LLM_TIMEOUT_SECONDS = '30'
uv run --locked python -m scripts.generate_judgments --source data/raw/Mindbox.yml.gz.xml --count 12
```

Укажите фактический путь к XML, если файл лежит в другой папке. DeepSeek формулирует запросы по выбранным карточкам. Скрипт проверяет разрешение экрана, диагональ и бюджет у всех мониторов в выгрузке. Оценка `3` означает совпадение, `0` — нарушение хотя бы одного явного условия. Карточки с недостаточными данными остаются без оценки.

`.local/monitor-judgments.json` подходит для флага `--judgments` оценщика. В `.local/monitor-judgments.audit.json` сохранены условия и число подходящих, неподходящих и неразмеченных карточек для каждого запроса. Проверьте формулировки в этом файле перед сравнением поисковых режимов.
