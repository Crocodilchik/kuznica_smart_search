import json
from pathlib import Path

import httpx
import pytest
from pydantic import ValidationError
from streamlit.testing.v1 import AppTest

from smart_search.interfaces import ui_config

WEB_ENTRY = Path(__file__).resolve().parents[2] / "smart_search" / "interfaces" / "web.py"


def test_demo_search_and_event_dashboard(tmp_path, monkeypatch):
    monkeypatch.setenv("SMART_SEARCH_MODE", "test")
    monkeypatch.delenv("SMART_SEARCH_BACKEND_MODULE", raising=False)
    monkeypatch.setenv("SMART_SEARCH_EVENT_DB", str(tmp_path / "events.sqlite3"))
    app = AppTest.from_file(WEB_ENTRY, default_timeout=15).run()

    assert not app.exception
    app.text_input(key="query").set_value("Что докупить к Astra Linux SE?")
    app.button(key="submit_search").click().run()
    assert not app.exception
    assert any("КриптоПро" in element.value for element in app.markdown)
    assert any(metric.label == "Поиски" and metric.value == "1" for metric in app.metric)

    app.run()
    assert any(metric.label == "Поиски" and metric.value == "1" for metric in app.metric)


def test_ui_reads_examples_and_labels_from_json(tmp_path, monkeypatch):
    path = tmp_path / "ui_config.json"
    config = json.loads(ui_config.UI_CONFIG_PATH.read_text(encoding="utf-8"))
    config["examples"] = ["Что докупить к Astra Linux SE?"]
    config["relation_labels"]["cross_sell"] = "Проверенное дополнение"
    config["status_labels"]["partial"] = "Подбор по тестовой конфигурации"
    path.write_text(json.dumps(config, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr(ui_config, "UI_CONFIG_PATH", path)
    monkeypatch.setenv("SMART_SEARCH_MODE", "test")
    monkeypatch.delenv("SMART_SEARCH_BACKEND_MODULE", raising=False)
    monkeypatch.setenv("SMART_SEARCH_EVENT_DB", str(tmp_path / "events.sqlite3"))

    app = AppTest.from_file(WEB_ENTRY, default_timeout=15).run()
    assert app.selectbox[0].options == ["Что докупить к Astra Linux SE?"]

    app.text_input(key="query").set_value("Что докупить к Astra Linux SE?")
    app.button(key="submit_search").click().run()

    assert not app.exception
    assert any(item.value == "Подбор по тестовой конфигурации" for item in app.subheader)
    assert any(item.value == "Проверенное дополнение" for item in app.caption)

    app.button(key="run_examples").click().run()
    assert not app.exception
    rows = app.table[0].value.to_dict("records")
    assert [(row["Запрос"], row["Статус"]) for row in rows] == [
        ("Что докупить к Astra Linux SE?", "partial"),
    ]
    assert any(metric.label == "Поиски" and metric.value == "1" for metric in app.metric)


def test_prod_form_accepts_free_text_and_hides_test_controls(prod_backend):
    app = AppTest.from_file(WEB_ENTRY, default_timeout=15).run()

    assert not app.exception
    assert len(app.selectbox) == 0
    assert [tab.label for tab in app.tabs] == []
    app.text_input(key="query").set_value("Монитор 27 дюймов для переговорной")
    app.button(key="submit_search").click().run()

    assert not app.exception
    assert any("Монитор для переговорной" in item.value for item in app.markdown)
    assert prod_backend.received == ["Монитор 27 дюймов для переговорной"]


def test_example_configuration_respects_search_request_length():
    config = ui_config.load_ui_config().model_dump()
    config["examples"] = ["x" * 1001]

    with pytest.raises(ValidationError, match="examples.0"):
        ui_config.UIConfig.model_validate(config)


def test_prod_form_can_share_the_running_api_process(prod_backend, monkeypatch):
    monkeypatch.setenv("SMART_SEARCH_API_URL", "http://search.test:8000")
    from fastapi.testclient import TestClient

    from smart_search.interfaces.api import app as api

    with TestClient(api) as client:

        def post(url, *, json, timeout):
            assert url == "http://search.test:8000/v1/search" and timeout > 0
            return client.post("/v1/search", json=json)

        monkeypatch.setattr(httpx, "post", post)
        form = AppTest.from_file(WEB_ENTRY, default_timeout=15).run()
        form.text_input(key="query").set_value("Монитор")
        form.button(key="submit_search").click().run()
    assert not form.exception
    assert any("Монитор для переговорной" in item.value for item in form.markdown)
    assert prod_backend.received == ["Монитор"]
