# Пакет разработчика 3 для команды

Полная спецификация: [docs/CONTRACTS.md](docs/CONTRACTS.md).
Все JSON-примеры проверены действующими Pydantic-моделями. Карточки взяты из реального фида.

| Кому | Что открыть |
| --- | --- |
| Разработчик 2 | [QuerySpec](examples/dev2_query_spec_inventory.json), [Schema](schemas/QuerySpec.json), справочники в data/catalog |
| Разработчик 4 | [Карточка ПО](examples/product_software.json), [карточка сервера](examples/product_server.json), [Product Schema](schemas/Product.json), [вход рекомендаций](examples/recommendation_input_server.json) |
| Разработчик 1 | [SearchParams](examples/search_params_bm25.json), [результат BM25](examples/search_result_bm25.json), [результат SKU](examples/search_result_sku.json), исходный код и зависимости |

`country=RU` в текущем фиде даёт предупреждение об отсутствии метаданных страны; отдельный пример это показывает.
`search_result_unavailable_simulated.json` получен контролируемым обращением к отсутствующему индексу.
Пакет содержит 300 тестовых карточек и полные справочники. Большой SQLite-каталог, Qdrant-индекс, XML и .venv не включены.
Для запуска полного поиска скопируйте соответствующие данные той же сборки либо подготовьте их из XML.
Не заменяйте общие модели разработчика 1 без объединения контрактов.
