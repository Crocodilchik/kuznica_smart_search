# Контракт публикации поисковых событий v1

Supervisor создаёт событие после формирования `SearchResponse`. В режиме `test` событие сохраняется в SQLite. В `prod` Supervisor выступает продьюсером и через Kafka-клиент публикует событие в Redpanda или другой Kafka-совместимый брокер. Получатель аналитики подключается к топику своим потребителем.

[Формирование события](../../smart_search/supervisor.py) · [Выбор получателя](../../smart_search/events/publisher.py) · [Продьюсер](../../smart_search/events/kafka.py) · [Модель сообщения](../../smart_search/events/contracts.py)

## Транспорт

| Параметр | Значение |
|---|---|
| Протокол | Kafka. Клиент приложения — `confluent-kafka`. |
| Топик по умолчанию | `smart-search.search-events.v1`. Для своего окружения задайте `SMART_SEARCH_KAFKA_TOPIC`. Топик создаётся на стороне брокера. |
| Ключ Kafka | `request_id` в UTF-8. События одного запроса имеют общий ключ. |
| Значение Kafka | JSON в UTF-8 с полями `event_id`, `event_type`, `schema_version`, `payload`. |
| Тип события | `search.completed`. Включает все оформленные исходы поиска, в том числе `empty`, `clarification_needed` и `unavailable`. |
| Версия схемы | Целое число `1`. Совместимое расширение добавляет необязательные поля. Для несовместимого изменения используйте новую версию схемы и топика. |
| Идентификатор события | `search.completed:<request_id>`. Потребитель может использовать его для дедупликации. |

Один проход Supervisor с готовым ответом формирует одно событие. Повтор внутри графа представлен полем `retry_count`. Валидация HTTP до запуска Supervisor и пакетный прогон примеров в Streamlit проходят без записи событий. [Входной HTTP-контракт](contract_request_interface.md)

## Сообщение

```json
{
  "event_id": "search.completed:store-42",
  "event_type": "search.completed",
  "schema_version": 1,
  "payload": {
    "request_id": "store-42",
    "normalized_query": "сервер до 300 тысяч",
    "intent": "product_search",
    "status": "partial",
    "result_count": 1,
    "latency_ms": 18.4,
    "retry_count": 1,
    "reason_codes": ["budget_exceeded", "not_enough_products"],
    "category": "Серверное оборудование",
    "recommendation_count": 0,
    "occurred_at": "2026-11-26T10:25:40Z"
  }
}
```

| Поле `payload` | Тип и смысл |
|---|---|
| `request_id` | Непустая строка из поискового запроса. Совпадает с Kafka-ключом после декодирования UTF-8. |
| `normalized_query` | Строка с формулировкой после разбора. Если этап понимания недоступен, передаётся пустая строка. |
| `intent` | `exact_product`, `product_search`, `alternatives`, `complements`, `solution` или `null`. |
| `status` | `ok`, `partial`, `empty`, `clarification_needed` или `unavailable`. Совпадает со статусом ответа. |
| `result_count` | Целое число основных карточек в ответе, от нуля. |
| `recommendation_count` | Целое число рекомендаций в отдельном блоке, от нуля. |
| `latency_ms` | Число миллисекунд от начала выполнения графа до подготовки события. Время ожидания брокера в поле не включено. |
| `retry_count` | `0` или `1`. Число повторов поиска в графе. |
| `reason_codes` | Массив причин и предупреждений без повторов. Потребитель должен переносить незнакомые коды. |
| `category` | Строка с определённой категорией или `null`. |
| `occurred_at` | Дата и время формирования события в UTC в формате ISO 8601. |

Модель полезной нагрузки определена как [SearchEvent](../../smart_search/contracts.py). В событии есть нормализованный текст запроса. `session_id` и `previous_context` в полезную нагрузку не входят.

## Подтверждение и повторы

- Продьюсер использует `acks=all` и `enable.idempotence=true`. Событие считается подтверждённым после успешного отчёта о доставке.
- Поисковый запрос ждёт подтверждения ограниченное время. При сбое настройки, сети, очереди или при истечении срока приложение журналирует ошибку и возвращает готовый поисковый ответ. В режиме `prod` SQLite для доставки не используется.
- Клиент повторяет отправку в рамках механизма Kafka и настроенного бюджета доставки. При тайм-ауте подтверждения результат доставки может быть неизвестен. Хранилище для последующей отправки отсутствует, поэтому при сбоях возможны пропуски. Повторный HTTP-запрос с тем же `request_id` может создать вторую запись с таким же `event_id`. Потребитель, которому нужен подсчёт логических событий, удаляет дубликаты по `event_id`.
- HTTP-статус описывает результат поиска. `GET /health/ready` проверяет загрузку поискового модуля. Доставку проверяют отправкой тестового поискового запроса и чтением сообщения из топика.

[Настройки клиента Kafka](https://github.com/confluentinc/librdkafka/blob/master/CONFIGURATION.md) · [Отчёты о доставке](https://docs.confluent.io/platform/current/clients/confluent-kafka-python/html/index.html)

## Настройка режима prod

| Переменная | Значение |
|---|---|
| `SMART_SEARCH_KAFKA_BOOTSTRAP_SERVERS` | Обязательный для публикации список `host:port` через запятую. |
| `SMART_SEARCH_KAFKA_TOPIC` | Имя топика. По умолчанию `smart-search.search-events.v1`. |
| `SMART_SEARCH_KAFKA_DELIVERY_TIMEOUT_MS` | Положительное число миллисекунд на доставку и ожидание отчёта. По умолчанию `1000`. |
| `SMART_SEARCH_KAFKA_SECURITY_PROTOCOL` | `PLAINTEXT` по умолчанию. Также поддерживаются `SSL`, `SASL_PLAINTEXT` и `SASL_SSL`. |
| `SMART_SEARCH_KAFKA_SASL_USERNAME`, `SMART_SEARCH_KAFKA_SASL_PASSWORD` | Обязательны при протоколе с SASL. |
| `SMART_SEARCH_KAFKA_SASL_MECHANISM` | Механизм SASL. По умолчанию `SCRAM-SHA-256`. |
| `SMART_SEARCH_KAFKA_SSL_CA_LOCATION` | Путь к доверенному CA для TLS при необходимости. |

Для локального прогона используйте [пример Redpanda с одним брокером](https://docs.redpanda.com/labs/docker-compose/single-broker/). После запуска брокера создайте топик и укажите его адрес приложению.

```powershell
docker exec redpanda-0 rpk topic create smart-search.search-events.v1
$env:SMART_SEARCH_MODE = "prod"
$env:SMART_SEARCH_BACKEND_MODULE = "team_backend"
$env:SMART_SEARCH_KAFKA_BOOTSTRAP_SERVERS = "127.0.0.1:19092"
uv run --locked python -m uvicorn smart_search.interfaces.api:app --host 127.0.0.1 --port 8000
```

Здесь `team_backend` — импортируемый модуль команды. После запроса к [POST /v1/search](contract_request_interface.md#post-v1search) прочитайте событие из топика.

```powershell
docker exec redpanda-0 rpk topic consume smart-search.search-events.v1 --offset start --num 1
```

В режиме `test` ручные запросы сохраняются в `.local/events.sqlite3` или в файле `SMART_SEARCH_EVENT_DB`. Их читает вкладка «Аналитика» в Streamlit. Сквозной тест публикации проверяет цепочку `HTTP → Supervisor → Kafka → потребитель` на временном Kafka-совместимом брокере librdkafka. [Транспортный тест](../../tests/integration/test_kafka_delivery.py)
