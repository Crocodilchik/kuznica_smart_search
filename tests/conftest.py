from pathlib import Path

import pytest

from softline_search.catalog import Catalog, build_catalog


@pytest.fixture
def feed_text():
    return """<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE yml_catalog SYSTEM "shops.dtd">
<yml_catalog date="2026-11-25 14:12"><shop><categories>
<category id="1">Программы</category><category id="2" parentId="1">Антивирусы для рабочих станций</category>
<category id="3">Серверы</category><category id="4" parentId="2">Защита Плюс</category>
</categories><offers>
<offer id="1" available="true" group_id="4"><name>Защита Плюс на 50 устройств</name><vendor>Вендор</vendor>
<vendorCode>SEC-50/A</vendorCode><categoryId>2</categoryId><price>999.99</price><currencyId>RUR</currencyId>
<description>Платформа: Windows/Linux • Минимальная покупка: от 1 шт.</description>
<param name="Язык интерфейса">Русская версия</param><param name="Язык интерфейса">English</param>
<param name="Тип продукта">софт</param><param name="Тип лицензии">Полная версия</param>
<param name="Страна происхождения">Россия</param><param name="Версия">другое или отсутствует</param></offer>
<offer id="2" available="false"><name>Антивирус расширенный</name><vendor>Другой Вендор</vendor>
<vendorCode/><categoryId>2</categoryId><price>0.00</price><currencyId>RUR</currencyId>
<description>Платформа: Windows</description><param name="К-во пользователей до">50</param></offer>
<offer id="3" available="true"><name>Сервер Альфа</name><vendorCode>SERVER-1</vendorCode>
<categoryId>3</categoryId><price>250000</price><currencyId>RUR</currencyId>
<description>Серверное оборудование для предприятия</description><param name="Тип продукта">электроника</param></offer>
<offer id="4" available="true"><name>Антивирус профессиональный на 100 устройств</name><vendorCode>SEC-100</vendorCode>
<categoryId>2</categoryId><price>2000</price><currencyId>RUR</currencyId>
<description>Платформа: Windows • Минимальная покупка: от 1 шт.</description></offer>
<offer id="5" available="true"><name>Антивирус для Linux</name><vendorCode>SEC-LINUX</vendorCode>
<categoryId>2</categoryId><price>100</price><currencyId>RUR</currencyId><description>Платформа: Linux</description></offer>
<offer id="6" available="true"><name>Защита Плюс на 50 устройств</name><vendorCode>SEC-MIN10</vendorCode>
<categoryId>2</categoryId><price>999.99</price><currencyId>RUR</currencyId>
<description>Платформа: Windows • Минимальная покупка: от 10 шт.</description></offer>
</offers></shop></yml_catalog>"""


@pytest.fixture
def catalog(tmp_path: Path, feed_text, monkeypatch):
    monkeypatch.chdir(tmp_path)
    source = tmp_path / "feed.xml"
    source.write_text(feed_text, encoding="utf-8")
    directory = tmp_path / "catalog"
    build_catalog(source, directory, tmp_path / "reports")
    return Catalog(directory)
