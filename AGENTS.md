# Умный поиск Softline

## Границы модулей

| Владелец | Зона | Входы для интеграции |
|---|---|---|
| Разработчик 1 | `smart_search/`, `scripts/evaluate.py`, `tests/` | `run_search`, схема поискового события и отчёт оценки |
| Разработчик 2 | Понимание запроса, валидация, клиент DeepSeek | `understand(SearchRequest) -> QuerySpec`, `validate(QuerySpec, SearchResult, RecommendationResult, list[EvidenceResult]) -> ValidationResult` |
| Разработчик 3 | Каталог и Qdrant | `search(SearchParams) -> SearchResult` с режимами `sku`, `bm25`, `dense`, `hybrid` |
| Разработчик 4 | Рекомендации и официальный корпус документов | `recommend(QuerySpec, list[Product]) -> RecommendationResult`, `research(RecommendationDraft) -> EvidenceResult` |

Для стенда единый Python-модуль реэкспортирует пять функций из правого столбца ролей 2–4. Настройте `SMART_SEARCH_MODE=prod` и имя модуля в `SMART_SEARCH_BACKEND_MODULE`. Режим по умолчанию `test` использует демонстрационные адаптеры. Для тестов передавайте `Services` в `build_search`. [Контракт модуля](docs/contracts/contract_tools_interface.md) · [HTTP-контракт](docs/contracts/contract_request_interface.md)

## Контракты и проверки

- Общие модели живут в `smart_search/contracts.py`, типы функций — в `smart_search/tools.py`. Согласуйте смысл новых полей с потребителем модуля. Добавляйте проверяемый пример для новых ошибок и обязательных полей.
- Сохраняйте обязательные ограничения в `QuerySpec.hard_filters`. Денежные суммы целые и выражены в копейках. Неизвестные цены или условия лицензии обозначайте `None`. Пустой результат поиска успешен. Недоступность обозначайте `SearchResult(available=False, warnings=[...])`.
- Для фразы с явным артикулом Supervisor выбирает путь SKU. При запросе замены или дополнения поиск исходного продукта служит для определения ссылки на товар. Итоговые карточки проходят валидацию с исходными ограничениями покупателя. Повторные результаты объединяются по `product_id`.
- Адаптеры отвечают за тайм-ауты своего клиента. DeepSeek при сбое должен вернуть безопасный результат правил с предупреждением или сообщить о недоступности. Supervisor обрабатывает стандартные `TimeoutError` и `ConnectionError`. Невалидированный ответ исключается.
- Для подтверждённой связи указывайте исходный товар, найденный товар, утверждение и применимый источник. Для официального документа необходим URL, для каталога — поле, подтверждающее утверждение.
- Supervisor создаёт событие после сборки ответа. В `test` оно сохраняется в SQLite, в `prod` публикуется в Kafka-совместимый брокер. При изменении схемы сверяйте продьюсер и тест потребителя с [контрактом событий](docs/contracts/contract_events_interface.md).
- События, выходные отчёты, выгрузки, токены и окружение храните вне отслеживаемых исходников. Работа ведётся локально до разрешения пользователя на отправку изменений.

## Команды из корня

```powershell
uv sync --locked
uv run --locked python -m pytest -q
uv run --locked ruff check .
uv run --locked ruff format --check .
uv run --locked python -m streamlit run smart_search/interfaces/web.py
uv run --locked python -m scripts.evaluate --output .local/demo-evaluation.json
```

В демонстрационном отчёте поисковые заглушки используют одну карту товаров. Для сравнения BM25, dense и hybrid нужен реальный адаптер и зафиксированная размеченная выборка.
