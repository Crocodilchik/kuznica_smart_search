import os
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT_DIR / "data"
STATIC_DIR = ROOT_DIR / "static"

XML_FEED_PATH = Path(os.environ.get("SOFTLINE_XML_PATH", ROOT_DIR / "Mindbox.yml.gz.xml"))
DB_PATH = Path(os.getenv("SOFTLINE_DB_PATH", DATA_DIR / "catalog.db"))
INDEX_PATH = Path(os.getenv("SOFTLINE_INDEX_PATH", DATA_DIR / "search_index.npz"))
BENCHMARK_PATH = DATA_DIR / "benchmark_queries.csv"

# Default weights for multi-criteria ranking S(q, d)
DEFAULT_WEIGHTS = {
    "lex": 0.34,
    "sem": 0.24,
    "intent": 0.16,
    "attr": 0.12,
    "price": 0.06,
    "business": 0.08,
}

# Known Russian domestic IT vendors in Softline catalog (used alongside Registry param)
RUSSIAN_VENDORS = {
    "группа астра",
    "астра линукс",
    "«лаборатория касперского»",
    "лаборатория касперского",
    "доктор веб",
    "криптопро",
    "киберпротект",
    "positive technologies",
    "нпо «эшелон»",
    "нпо эшелон",
    "ред софт",
    "базальт спо",
    "мойофис",
    "р7-офис",
    "р7",
    "content ai",
    "контент ии",
    "мтс линк",
    "вебинар технологии",
    "webinar",
    "код безопасности",
    "инфотекс",
    "usergate",
    "ideco",
    "searchinform",
    "сёрчинформ",
    "инфовотч",
    "infowatch",
    "1с",
    "1с-битрикс",
    "аскон",
    "нанософт",
    "коммунигейт",
    "communigate",
    "яндeкс",
    "яндекс",
    "sber",
    "сбер",
    "вк",
    "vk",
    "роса",
    "нтц ит роса",
    "рупост",
    "rupost",
    "алладин р.д.",
    "рутокен",
    "актив-софт",
    "флант",
    "аврора",
    "контур",
    "битрикс24",
    "ammyy",
    "ассистент",
    "рудесктоп",
    "rudesktop",
    "pro32",
    "арос",
    "информатик",
    "promt",
    "промт",
    "сетере",
    "setere",
}
