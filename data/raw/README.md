# Готовая векторизованная БД и исходная выгрузка Softline

## Быстрый старт с готовой базой (рекомендуется для команды)

1. Распакуйте переданные два каталога прямо в `data/raw/`, чтобы получилось:
   - `data/raw/qdrant-data/` — база Qdrant (136 040 товаров с 384d-векторами и BM25)
   - `data/raw/search-data/` — `catalog/`, `models/` и `qdrant-server/index.json`
2. Скопируйте `.env.example` в `.env` и заполните `DEEPSEEK_API_KEY`.
3. Запустите стек из корня репозитория:
   ```powershell
   docker compose up -d --build --wait
   ```
4. Откройте сайт: `http://127.0.0.1:3000` (API Swagger: `http://127.0.0.1:8000/docs`).

## Если нужно пересобрать базу с нуля из XML

1. Положите исходный XML в эту папку под именем `data/raw/Mindbox.yml.gz.xml`.
2. Выполните:
   ```powershell
   docker compose run --rm --build catalog
   docker compose up -d --build --wait
   ```

Git игнорирует содержимое `data/raw/*` (кроме этого файла).
