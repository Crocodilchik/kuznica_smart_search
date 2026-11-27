# Контракт интерфейса приёма запросов v1

## Режимы

| Режим | Как включить | Приём запросов и данные |
|---|---|---|
| `test` | `SMART_SEARCH_MODE=test`, значение по умолчанию | Streamlit предлагает примеры из `ui_config.json`, принимает произвольный текст на демонстрационном срезе и может прогнать все примеры. В таблице прогона видны статус, число карточек и причины. Массовый прогон выполняется без записи в журнал. |
| `prod` | `SMART_SEARCH_MODE=prod` и `SMART_SEARCH_BACKEND_MODULE=<имя_модуля>` | Streamlit показывает одно поле с отправкой по Enter или кнопке. Программные клиенты используют HTTP. В обоих случаях произвольный запрос передаётся подключённому поисковому модулю. |

Модуль в `SMART_SEARCH_BACKEND_MODULE` экспортирует пять функций из [контракта поискового модуля](contract_tools_interface.md). В режиме `test` используются локальные адаптеры. HTTP-интерфейс поиска доступен при `SMART_SEARCH_MODE=prod`. Режим назначается процессу через окружение.

## POST /v1/search

Метод принимает `application/json` и возвращает JSON. Поиск выполняется синхронно в рамках одного HTTP-запроса. Для вызова достаточно поля `query`.

### Вход

| Поле | Тип | Правило |
|---|---|---|
| `query` | Строка, обязательное поле | От 1 до 1000 символов после удаления пробелов по краям. Свободный текст, название или артикул. |
| `request_id` | Непустая строка, необязательное поле | Используется для сопоставления запроса, ответа и события аналитики. При отсутствии сервер создаёт UUID. |
| `session_id` | Строка или `null` | Идентификатор сессии клиента. |
| `previous_context` | Строка или `null` | Предыдущая формулировка, если текущий запрос отвечает на уточняющий вопрос. |

Неизвестные поля приводят к `422`. Цена, количество устройств, ОС и другие ограничения передаются в тексте `query` и извлекаются модулем понимания запроса.

```json
{
  "request_id": "store-42",
  "session_id": "visitor-8",
  "query": "Сервер до 300 тысяч",
  "previous_context": null
}
```

Для простого вызова из PowerShell достаточно одного поля.

```powershell
$body = @{ query = "Монитор 27 дюймов для переговорной" } | ConvertTo-Json
Invoke-RestMethod -Method Post -Uri "http://127.0.0.1:8000/v1/search" `
    -ContentType "application/json; charset=utf-8" -Body $body
```

### Выход SearchResponse

| Поле | Содержимое |
|---|---|
| `request_id` | Переданный или созданный идентификатор запроса. |
| `status` | `ok`, `partial`, `empty`, `clarification_needed` или `unavailable`. |
| `products` | Массив карточек. Обязательные поля каждой карточки — `product_id`, `title`, `category`. Дополнительные поля — `sku`, `brand`, `price_kopecks`, `license_units`, `in_stock`, `url` и `attributes`. Цена передаётся целым числом в копейках. |
| `recommendations` | Массив рекомендаций с `anchor_id`, `product`, `type`, `claim` и `evidence`. Тип связи — `substitute`, `cross_sell` или `upsell`. |
| `clarifying_question` | Текст уточняющего вопроса или `null`. |
| `intent` | Распознанное намерение или `null`. |
| `reason_codes`, `warnings`, `steps` | Массивы кодов причин, предупреждений и пройденных шагов. |
| `retry_count` | Число повторов поиска. Возможные значения — 0 и 1. |

Пример результата, когда требуется уточнение.

```json
{
  "request_id": "store-42",
  "status": "clarification_needed",
  "products": [],
  "recommendations": [],
  "warnings": [],
  "reason_codes": [],
  "clarifying_question": "Сколько устройств нужно защитить?",
  "intent": "product_search",
  "retry_count": 0,
  "steps": ["understand"]
}
```

### HTTP-статусы

| HTTP | Тело | Когда возвращается |
|---|---|---|
| `200` | `SearchResponse` | `ok` при завершённом подборе, `partial` при недостатке подходящих позиций, `empty` при успешном поиске без совпадений, `clarification_needed` при необходимости уточнить запрос. |
| `422` | Ответ FastAPI с массивом `detail` | Некорректный JSON либо нарушение схемы входа. |
| `503` | `SearchResponse` со статусом `unavailable` | Недоступна нужная для запроса зависимость. Если граф успел подтвердить часть карточек, они могут остаться в ответе. |
| `503` | `{"code": "...", "message": "..."}` | Ошибка конфигурации. Коды — `invalid_mode`, `prod_mode_required`, `backend_not_configured`, `backend_load_failed`. |
| `500` | Стандартный ответ HTTP-сервера | Необработанная ошибка приложения. |

Повторная отправка того же `request_id` заново запускает поиск. В `test` SQLite сохраняет первое событие с этим ID. В `prod` повторные события имеют одинаковый `event_id`, который потребитель может использовать для дедупликации. Для независимых поисков используйте разные ID или передавайте запрос без идентификатора. [Контракт публикации](contract_events_interface.md)

## GET /health/ready

Возвращает `200` с `{"status":"ready","mode":"prod"}`, когда выбран `prod`, модуль импортируется, обязательные функции доступны и его проверка готовности завершилась успешно. Для встроенного `smart_search.backend` проверяются каталог SQLite, активный индекс Qdrant и соответствие снимка. Код `catalog_unavailable` с HTTP 503 означает, что для общего поиска данных сейчас недостаточно. SKU может продолжать работать при доступном SQLite. Код `invalid_retrieval_mode` означает, что в конфигурации поиска указано неподдерживаемое значение.

## Запуск и проверка

```powershell
uv sync --locked
$env:SMART_SEARCH_MODE = "prod"
$env:SMART_SEARCH_BACKEND_MODULE = "smart_search.backend"
$env:SMART_SEARCH_KAFKA_BOOTSTRAP_SERVERS = "127.0.0.1:19092"
uv run --locked python -m uvicorn smart_search.interfaces.api:app --host 127.0.0.1 --port 8000
```

OpenAPI доступен по `/openapi.json`, интерактивная документация — по `/docs`. Поисковую форму запускает команда `uv run --locked python -m streamlit run smart_search/interfaces/web.py`. При совместном запуске задайте для формы `SMART_SEARCH_API_URL=http://127.0.0.1:8000`, чтобы запросы обрабатывал один процесс с локальным индексом.

Вручную введённые тестовые запросы пишутся в `.local/events.sqlite3` или в файл `SMART_SEARCH_EVENT_DB`. В `prod` события направляются в брокер. Сбой доставки события журналируется, а поисковый ответ возвращается клиенту. [Контракт публикации](contract_events_interface.md)

Схемы `SearchRequest` и `SearchResponse` реализованы в [contracts.py](../../smart_search/contracts.py), HTTP-обработчики — в [api.py](../../smart_search/interfaces/api.py), сценарии проверки — в [интеграционных тестах](../../tests/integration/test_request_api.py).
