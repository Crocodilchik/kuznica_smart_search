import gzip

import pytest
from defusedxml.common import EntitiesForbidden

from softline_search.catalog import Catalog, build_catalog
from softline_search.feed import category_chain, iter_records, license_capacity, parse_price
from softline_search.text import clean_text, lexical_text


def test_real_yml_semantics_are_preserved(catalog):
    product = catalog.get_product("1")
    assert product.price_kopecks == 99999
    assert product.category_ids == ["1", "2"]
    assert product.family_id == "4"
    assert product.os == ["Windows", "Linux"]
    assert product.attributes["Язык интерфейса"] == ["Русская версия", "English"]
    assert "Версия" not in product.attributes
    assert product.license_units == 50 and product.license_unit_type == "device"
    unknown = catalog.get_product("2")
    assert unknown.price_kopecks is None and unknown.sku is None
    assert unknown.license_units is None  # "users up to" is not package capacity.
    assert unknown.country is None
    assert unknown.in_stock is False
    assert catalog.by_sku(" sec-50/a ")[0].product_id == "1"
    assert catalog.by_sku("1") == []  # offer id must not be silently used as SKU.
    assert catalog.get_product("absent") is None


@pytest.mark.parametrize(
    "value, expected",
    [
        ("1", 100),
        ("0.00", None),
        ("0.01", 1),
        ("NaN", None),
        ("Infinity", None),
        ("-1", None),
        ("1.001", None),
        ("1 234,56", 123456),
    ],
)
def test_money_uses_exact_kopecks(value, expected):
    assert parse_price(value, "RUR") == expected
    assert parse_price(value, "USD") is None


@pytest.mark.parametrize(
    "title, expected",
    [
        ("ПО на 50 устройств", (50, "device")),
        ("Антивирус лицензия на 3 года на 4 ПК", (4, "device")),
        ("Антивирус на 50ПК", (50, "device")),
        ("ПО на 10 рабочих станций", (10, "device")),
        ("ПО для 10-50 ПК", (None, None)),
        ("Модель SEC50PC", (None, None)),
        ("Пакет 25 лицензий", (25, "license")),
        ("Защита 50 пользователей", (50, "user")),
        ("ПО от 10 до 50 устройств", (None, None)),
        ("ПО 10–50 устройств", (None, None)),
        ("ПО до 50 устройств", (None, None)),
        ("ПО на 25 устройств и 50 пользователей", (None, None)),
    ],
)
def test_license_capacity_does_not_invent_pack_sizes(title, expected):
    assert license_capacity(title) == expected


def test_plain_xml_and_gzip_are_detected_by_magic(tmp_path, feed_text, monkeypatch):
    monkeypatch.chdir(tmp_path)
    path = tmp_path / "misleading.xml"
    path.write_bytes(gzip.compress(feed_text.encode()))
    manifest = build_catalog(path, tmp_path / "out", tmp_path / "reports")
    assert manifest["products"] == 6
    # Renaming on Windows proves every SQLite reader has released its handle.
    catalog = Catalog(tmp_path / "out")
    catalog.get_product("1")
    catalog.by_sku("SEC-50/A")
    list(catalog.iter_products())
    catalog.db_path.rename(tmp_path / "renamed.sqlite")


def test_duplicate_ids_are_reported_instead_of_overwriting(tmp_path, feed_text, monkeypatch):
    monkeypatch.chdir(tmp_path)
    path = tmp_path / "feed.xml"
    path.write_text(feed_text.replace('id="6" available', 'id="1" available'), encoding="utf-8")
    manifest = build_catalog(path, tmp_path / "out", tmp_path / "reports")
    assert manifest["products"] == 5 and manifest["rejected"] == 1
    assert (tmp_path / "reports/rejected_offers.jsonl").read_text(encoding="utf-8")


def test_external_entities_are_not_resolved(tmp_path):
    path = tmp_path / "bad.xml"
    path.write_text(
        '<!DOCTYPE yml_catalog [<!ENTITY secret SYSTEM "file:///C:/Windows/win.ini">]>'
        '<yml_catalog><offer id="x"><name>&secret;</name></offer></yml_catalog>',
        encoding="utf-8",
    )
    with pytest.raises(EntitiesForbidden):
        list(iter_records(path))


def test_cleanup_and_morphology():
    assert clean_text("<p>Текст&nbsp;тут</p><script>ignore()</script><p>ещё</p>") == "Текст тут ещё"
    assert lexical_text("защита компьютеров") == lexical_text("защиты компьютера")
    assert "2022" in lexical_text("Windows Server 2022 Standard 16 Core")
    with pytest.raises(ValueError, match="cycle"):
        category_chain("1", {"1": {"parent_id": "2"}, "2": {"parent_id": "1"}})
