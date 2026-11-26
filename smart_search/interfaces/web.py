"""Streamlit entry point for an offline demo or an integrated backend."""

import re
import sqlite3
from urllib.parse import urlsplit
from uuid import uuid4

import streamlit as st
from pydantic import ValidationError

from smart_search.analytics import local_event_store, summarize
from smart_search.contracts import (
    MAX_QUERY_LENGTH,
    Product,
    Recommendation,
    SearchRequest,
    SearchResponse,
)
from smart_search.interfaces.ui_config import UIConfig, load_ui_config
from smart_search.runtime import load_services, run_search
from smart_search.runtime_config import AppMode, RuntimeConfigurationError, get_mode
from smart_search.supervisor import build_search


def _escape(value: str) -> str:
    return re.sub(r"([\\\x60*_{}\[\]<>()#!|])", r"\\\1", value)


def _https(url: str | None) -> str | None:
    try:
        parsed = urlsplit(url or "")
        return url if parsed.scheme == "https" and parsed.hostname and not parsed.username else None
    except ValueError:
        return None


def _price(kopecks: int | None) -> str:
    if not kopecks:
        return "Цена по запросу"
    rubles, fraction = divmod(kopecks, 100)
    whole = f"{rubles:,}".replace(",", "\u00a0")
    return f"{whole},{fraction:02d} ₽" if fraction else f"{whole} ₽"


def _card(item: Product, config: UIConfig, recommendation: Recommendation | None = None) -> None:
    with st.container(border=True):
        label = config.relation_labels[recommendation.type] if recommendation else item.category
        st.caption(_escape(label))
        st.markdown(f"**{_escape(item.title)}**")
        st.markdown(_price(item.price_kopecks))
        if item.in_stock is not None:
            st.caption("Доступно по данным каталога" if item.in_stock else "Недоступно")
        if recommendation is not None:
            st.write(_escape(recommendation.claim))
            for item_evidence in recommendation.evidence:
                st.caption(_escape(item_evidence.excerpt))
                if source_url := _https(item_evidence.source_url):
                    st.link_button("Открыть источник", source_url)
        if url := _https(item.url):
            st.link_button("Открыть карточку Softline", url)


def _render_results(response: SearchResponse, config: UIConfig, *, show_trace: bool) -> None:
    st.subheader(_escape(config.status_labels[response.status]))
    if response.clarifying_question:
        st.info(_escape(response.clarifying_question))
    if response.warnings and response.status != "unavailable":
        st.warning("Часть данных могла быть недоступна при поиске.")
    if response.products:
        st.markdown("#### Товары")
        for product in response.products:
            _card(product, config)
    if response.recommendations:
        st.markdown("#### Рекомендации")
        for recommendation in response.recommendations:
            _card(recommendation.product, config, recommendation)
    if show_trace:
        with st.expander("Ход запроса"):
            st.text(response.request_id)
            if response.steps:
                st.text(" → ".join(response.steps))
            if response.reason_codes:
                st.text(", ".join(response.reason_codes))


def _render_analytics() -> None:
    try:
        report = summarize(local_event_store().read())
    except (OSError, sqlite3.Error, ValidationError):
        st.warning("Журнал поиска временно недоступен.")
        return
    first, second, third = st.columns(3)
    first.metric("Поиски", report.total_searches)
    has_results = any(report.status_counts.get(value) for value in ("ok", "partial", "empty"))
    second.metric("Пустые выдачи", f"{report.empty_rate:.0%}" if has_results else "—")
    third.metric(
        "Задержка p95",
        f"{report.p95_latency_ms:.0f} мс" if report.p95_latency_ms is not None else "—",
    )
    if report.status_counts:
        st.table(
            {"Статус": list(report.status_counts), "Запросы": list(report.status_counts.values())}
        )
    if report.problem_categories:
        st.table(
            {
                "Категория неполной выдачи": list(report.problem_categories),
                "Запросы": list(report.problem_categories.values()),
            }
        )
    if report.reason_counts:
        st.table(
            {"Причина": list(report.reason_counts), "Запросы": list(report.reason_counts.values())}
        )


def _render_search(config: UIConfig, mode: AppMode) -> None:
    if mode == "test":
        example = st.selectbox(
            "Готовый запрос", config.examples, index=None, placeholder="Выберите пример"
        )
        if st.button("Подставить пример", disabled=example is None):
            st.session_state.query = example
    with st.form("search", enter_to_submit=True):
        query = st.text_input(
            "Что вы ищете?",
            key="query",
            max_chars=MAX_QUERY_LENGTH,
            placeholder="Название товара, артикул или описание задачи",
        )
        follow_up = st.session_state.get("follow_up")
        use_context = (
            st.checkbox("Добавить ответ к предыдущему запросу", value=True) if follow_up else False
        )
        submitted = st.form_submit_button("Найти товары", key="submit_search", type="primary")
    if submitted:
        st.session_state.response = None
        if not query.strip():
            st.warning("Введите название товара или опишите задачу.")
        else:
            request = SearchRequest(
                query=query,
                session_id=st.session_state.session_id,
                previous_context=follow_up if use_context else None,
            )
            try:
                with st.spinner("Подбираем товары"):
                    response = run_search(request)
                st.session_state.response = response
                st.session_state.follow_up = query if response.clarifying_question else None
            except (ImportError, ValueError):
                st.error("Поисковый сервис временно недоступен.")
    if response := st.session_state.get("response"):
        _render_results(response, config, show_trace=mode == "test")


def _render_example_checks(config: UIConfig) -> None:
    if st.button("Прогнать примеры", key="run_examples", disabled=not config.examples):
        run = build_search(load_services())
        rows = []
        with st.spinner("Выполняем тестовые запросы"):
            for query in config.examples:
                result = run(SearchRequest(query=query))
                rows.append(
                    {
                        "Запрос": query,
                        "Статус": result.status,
                        "Товары": len(result.products),
                        "Рекомендации": len(result.recommendations),
                        "Причины": ", ".join(
                            dict.fromkeys([*result.reason_codes, *result.warnings])
                        ),
                    }
                )
        st.session_state.example_results = rows
    if rows := st.session_state.get("example_results"):
        st.table(rows)


def main() -> None:
    st.set_page_config(page_title="Умный поиск Softline", page_icon="🔎", layout="wide")
    try:
        mode = get_mode()
    except RuntimeConfigurationError as error:
        st.error(str(error))
        return
    try:
        config = load_ui_config()
    except (OSError, ValueError):
        st.error("Не удалось загрузить настройки интерфейса. Проверьте файл ui_config.json.")
        return

    st.session_state.setdefault("session_id", str(uuid4()))
    st.title("Умный поиск Softline")
    if mode == "prod":
        _render_search(config, mode)
        return

    st.info("Тестовый режим. Цены и наличие взяты из демонстрационного среза каталога.")
    search_tab, checks_tab, analytics_tab = st.tabs(["Поиск", "Проверка примеров", "Аналитика"])
    with search_tab:
        _render_search(config, mode)
    with checks_tab:
        _render_example_checks(config)
    with analytics_tab:
        _render_analytics()


if __name__ == "__main__":
    main()
