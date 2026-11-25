"""Типы столбцов: приведение к общим «семействам» и перевод между SQLite и MySQL."""

import re
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal

FAMILIES = ("integer", "boolean", "real", "decimal", "text", "blob", "datetime", "date")
_LEN_RE = re.compile(r"\(\s*(\d+)")
_DEC_RE = re.compile(r"\(\s*(\d+)\s*,\s*(\d+)\s*\)")
DEFAULT_VARCHAR_FOR_KEY = 191  # безопасная длина ключа в MySQL (utf8mb4)


@dataclass
class Column:
    """Описание столбца в общем виде (одинаково для SQLite и MySQL)."""

    name: str
    family: str
    raw_type: str = ""
    nullable: bool = True
    pk: int = 0  # позиция в первичном ключе (1, 2, ...), 0 — не входит в ключ
    length: int = None


def parse_length(raw):
    match = _LEN_RE.search(raw or "")
    return int(match.group(1)) if match else None


def family_from_sqlite(raw):
    """Семейство по объявленному типу SQLite."""
    t = (raw or "").upper()
    if "BOOL" in t:
        return "boolean"
    if "INT" in t:
        return "integer"
    if any(k in t for k in ("CHAR", "CLOB", "TEXT")):
        return "text"
    if "BLOB" in t:
        return "blob"
    if any(k in t for k in ("REAL", "FLOA", "DOUB")):
        return "real"
    if "DATETIME" in t or "TIMESTAMP" in t:
        return "datetime"
    if "DATE" in t:
        return "date"
    if "DEC" in t or "NUM" in t:
        return "decimal"
    return "text"


def family_from_mysql(data_type, column_type=""):
    """Семейство по типу MySQL (data_type и column_type из information_schema)."""
    dt, ct = (data_type or "").lower(), (column_type or "").lower()
    if dt == "tinyint" and ct.startswith("tinyint(1)"):
        return "boolean"
    if dt in ("tinyint", "smallint", "mediumint", "int", "integer", "bigint"):
        return "integer"
    if dt in ("float", "double", "real"):
        return "real"
    if dt in ("decimal", "numeric"):
        return "decimal"
    if dt in ("datetime", "timestamp"):
        return "datetime"
    if dt == "date":
        return "date"
    if dt in ("tinyblob", "blob", "mediumblob", "longblob", "binary", "varbinary"):
        return "blob"
    return "text"


def sqlite_type(column):
    """Тип столбца для SQLite."""
    return {
        "integer": "INTEGER",
        "boolean": "INTEGER",
        "real": "REAL",
        "decimal": "NUMERIC",
        "text": "TEXT",
        "blob": "BLOB",
        "datetime": "DATETIME",
        "date": "DATE",
    }[column.family]


def mysql_type(column):
    """Тип столбца для MySQL."""
    family = column.family
    if family == "integer":
        return "BIGINT"
    if family == "boolean":
        return "TINYINT(1)"
    if family == "real":
        return "DOUBLE"
    if family == "decimal":
        match = _DEC_RE.search(column.raw_type or "")
        return f"DECIMAL({match.group(1)},{match.group(2)})" if match else "DECIMAL(20,6)"
    if family == "datetime":
        return "DATETIME"
    if family == "date":
        return "DATE"
    if family == "blob":
        return "BLOB"
    if column.length:
        return f"VARCHAR({column.length})"
    if column.pk:
        return f"VARCHAR({DEFAULT_VARCHAR_FOR_KEY})"  # TEXT нельзя использовать в ключе
    return "TEXT"


def norm(value, family):
    """Приводит значение к виду, в котором его можно сравнивать между СУБД."""
    if value is None:
        return None
    if isinstance(value, (bytes, bytearray, memoryview)):
        return bytes(value)
    if isinstance(value, bool):
        return int(value)
    try:
        if family in ("integer", "boolean"):
            return int(value)
        if family in ("real", "decimal"):
            return round(float(value), 6)
    except (TypeError, ValueError):
        return str(value)
    if family == "datetime":
        text = value.isoformat(sep=" ") if isinstance(value, datetime) else str(value)
        text = text.replace("T", " ")
        return text.rstrip("0").rstrip(".") if "." in text else text
    if family == "date":
        return str(value)[:10]
    return str(value)


def to_db(value):
    """Приводит значение к виду, который принимают оба драйвера при записи."""
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, datetime):
        return value.isoformat(sep=" ")
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, (bytearray, memoryview)):
        return bytes(value)
    return value
