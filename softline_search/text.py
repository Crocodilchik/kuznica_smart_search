"""Deterministic text cleanup; no correction of SKU, versions or numeric constraints."""

import re
import unicodedata
from functools import lru_cache
from html import unescape
from html.parser import HTMLParser

from py_rust_stemmers import SnowballStemmer

UNKNOWN_VALUES = frozenset(
    {
        "",
        "другое или отсутствует",
        "не определено",
        "не определен",
        "не определена",
        "нет данных",
        "не указано",
        "n/a",
        "null",
        "-",
    }
)
STOPWORDS = frozenset(
    [
        "и",
        "в",
        "во",
        "на",
        "для",
        "с",
        "со",
        "к",
        "ко",
        "от",
        "до",
        "по",
        "из",
        "за",
        "у",
        "о",
        "об",
        "а",
        "но",
        "или",
        "это",
        "the",
        "of",
        "for",
        "and",
        "a",
        "an",
        "to",
        "in",
    ]
)
_STEMMER = SnowballStemmer("russian")
_TOKEN = re.compile(r"[^\W_]+", re.UNICODE)


class _PlainText(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.hidden = 0

    def handle_starttag(self, tag, attrs):
        if tag in {"script", "style"}:
            self.hidden += 1
        elif tag in {"p", "br", "div", "li", "tr"}:
            self.parts.append(" ")

    def handle_endtag(self, tag):
        if tag in {"script", "style"}:
            self.hidden = max(0, self.hidden - 1)
        elif tag in {"p", "div", "li", "tr"}:
            self.parts.append(" ")

    def handle_data(self, data):
        if not self.hidden:
            self.parts.append(data)


def clean_text(value: str | None) -> str:
    value = unicodedata.normalize("NFKC", unescape(value or ""))
    value = re.sub(r"[\u200b-\u200f\ufeff]", "", value)
    if re.search(r"</?[a-zA-Z][^>]*>", value):
        parser = _PlainText()
        parser.feed(value)
        value = "".join(parser.parts)
    return " ".join(value.split())


def meaningful(value: str | None) -> str | None:
    value = clean_text(value)
    return value if value.casefold().replace("ё", "е") not in UNKNOWN_VALUES else None


def sku_key(value: str | None) -> str:
    return unicodedata.normalize("NFKC", value or "").strip().casefold()


@lru_cache(maxsize=100000)
def _stem(token: str) -> str:
    return _STEMMER.stem_word(token)


def lexical_text(value: str) -> str:
    """Apply the same Russian stemming before indexing and querying native BM25."""
    tokens = _TOKEN.findall(unicodedata.normalize("NFKC", value).casefold().replace("ё", "е"))
    return " ".join(_stem(token) for token in tokens if token not in STOPWORDS)


def canonical_os(value: str) -> str:
    key = value.casefold().strip()
    return {
        "windows": "Windows",
        "винда": "Windows",
        "linux": "Linux",
        "macos": "macOS",
        "mac os": "macOS",
        "mac osx": "macOS",
        "android": "Android",
        "ios": "iOS",
    }.get(key, value)
