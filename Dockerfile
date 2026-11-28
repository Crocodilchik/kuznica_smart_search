# syntax=docker/dockerfile:1
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    UV_PYTHON_DOWNLOADS=never \
    UV_LINK_MODE=copy \
    PATH="/app/.venv/bin:$PATH" \
    SOFTLINE_CATALOG_DIR=/var/lib/smart-search/catalog \
    SOFTLINE_INDEX_DIR=/var/lib/smart-search/qdrant \
    SMART_SEARCH_EVENT_DB=/var/lib/smart-search/events.sqlite3 \
    STREAMLIT_BROWSER_GATHER_USAGE_STATS=false

WORKDIR /app
RUN apt-get update \
    && apt-get install -y --no-install-recommends libgomp1 \
    && rm -rf /var/lib/apt/lists/* \
    && pip install --no-cache-dir uv==0.13.10
COPY pyproject.toml uv.lock ./
RUN uv sync --locked --no-dev --python /usr/local/bin/python

RUN groupadd --gid 10001 app \
    && useradd --uid 10001 --gid app --create-home app \
    && install -d -o app -g app /var/lib/smart-search \
    && touch /var/lib/smart-search/.keep

COPY .streamlit/ .streamlit/
COPY smart_search/ smart_search/
COPY softline_search/ softline_search/
COPY src/ src/
COPY data/recommendation_graph.json data/recommendation_graph.json
COPY contracts.py contracts.py

USER app
EXPOSE 8501
HEALTHCHECK --interval=20s --timeout=5s --start-period=20s \
    CMD ["python", "-c", "from urllib.request import urlopen; urlopen('http://127.0.0.1:8501/_stcore/health', timeout=3).close()"]
CMD ["python", "-m", "streamlit", "run", "smart_search/interfaces/web.py", "--server.address", "0.0.0.0", "--server.port", "8501", "--server.headless", "true"]
