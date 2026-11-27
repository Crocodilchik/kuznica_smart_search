# Smart Search: универсальный LLM Client

Пакет предоставляет общий асинхронный интерфейс для DeepSeek API и локального Ollama. Провайдер выбирается через `LLM_PROVIDER`; при ошибке выбранного провайдера автоматического переключения нет. Query Understanding и Research должны получать `LLMClient` через внедрение зависимости. Контракт `QuerySpec` остаётся общим контрактом проекта и не определяется этим пакетом.

## Установка

Требуется Python 3.11 или новее.

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install -e ".[dev]"
Copy-Item .env.example .env
```

Отредактируйте `.env` локально. Не добавляйте его в Git и не вставляйте реальный API-ключ в команды, журналы или примеры.

## Выбор DeepSeek

В `.env` укажите:

```ini
LLM_PROVIDER=deepseek
DEEPSEEK_API_KEY=ваш_ключ
DEEPSEEK_BASE_URL=https://api.deepseek.com
DEEPSEEK_MODEL=deepseek-chat
```

Проверьте, что ключ и выбранная модель доступны вашему аккаунту, затем выполните текстовую и структурированную smoke-проверку:

```powershell
python scripts/check_llm.py
```

Клиент отправит запросы в DeepSeek API. Вызовы могут расходовать баланс аккаунта.

## Выбор Ollama

Установите и запустите Ollama. Проверьте локально доступные модели:

```powershell
ollama list
ollama show qwen3:8b
```

В проверенном окружении предустановлена `qwen3:8b` (5.2 GB); `qwen3:4b` не установлен. Если список моделей на вашей машине отличается, задайте доступное имя в `.env` или загрузите модель отдельно. Например:

```powershell
ollama pull qwen3:4b
```

Для smoke-проверки крупной локальной модели увеличьте `LLM_TIMEOUT_SECONDS` в `.env` (в проверенном окружении потребовалось `180`), затем подберите значение по фактическим замерам. Здесь `qwen3:8b` прошла текстовую и JSON-проверки с этим таймаутом; попытка с `60` секундами закончилась таймаутом. Значение по умолчанию `5` секунд из примера для этой модели слишком мало.

В `.env` выберите Ollama и модель, которая действительно доступна в этом окружении:

```ini
LLM_PROVIDER=ollama
OLLAMA_BASE_URL=http://localhost:11434
OLLAMA_MODEL=qwen3:8b
```

Запустите ту же проверку:

```powershell
python scripts/check_llm.py
```

Smoke-скрипт выполняет один текстовый и один структурированный запрос к выбранному провайдеру. Ollama должен быть запущен; модель должна быть загружена локально.

## Использование в модулях

```python
from pydantic import BaseModel

from smart_search.llm import LLMClient


class QueryInterpretation(BaseModel):
    normalized_query: str


async def interpret(query: str, llm: LLMClient) -> QueryInterpretation:
    return await llm.complete_json(
        system_prompt="Interpret the search query.",
        user_prompt=query,
        response_model=QueryInterpretation,
    )
```

Оркестратор создаёт один клиент через `LLMFactory.create()` при старте и закрывает его при остановке через `await client.aclose()`. В Query Understanding технические ошибки `LLMError` должны запускать детерминированный fallback и сохранять диагностику; Research также работает через `LLMClient`. Валидация модели и конфигурация не зависят от провайдера.

## Конфигурация

| Переменная | Назначение | Значение по умолчанию |
| --- | --- | --- |
| `LLM_PROVIDER` | `deepseek`, `ollama` или тестовый `mock` | `deepseek` |
| `LLM_TIMEOUT_SECONDS` | Таймаут одного HTTP-запроса | `5` |
| `LLM_TEMPERATURE` | Температура генерации | `0` |
| `DEEPSEEK_API_KEY` | Ключ DeepSeek; нужен только для DeepSeek | пусто |
| `DEEPSEEK_BASE_URL` | Базовый URL DeepSeek API | `https://api.deepseek.com` |
| `DEEPSEEK_MODEL` | Имя модели DeepSeek | `deepseek-chat` |
| `OLLAMA_BASE_URL` | Базовый URL сервера Ollama | `http://localhost:11434` |
| `OLLAMA_MODEL` | Имя локальной модели Ollama | `qwen3:4b` |

`MockLLMProvider` предназначен для тестов без сети. Ошибки моделей не преобразуются в пустые результаты и не переключают провайдера.

## Unit-тесты

```powershell
python -m pytest
```

Тесты провайдеров используют HTTPX mock transport и не требуют API-ключа, запущенного Ollama или сетевого доступа.
