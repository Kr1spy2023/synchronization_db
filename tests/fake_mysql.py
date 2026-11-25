"""Двойник драйвера MySQL (интерфейс PyMySQL) на основе SQLite.

Нужен, чтобы проверять код MySQLAdapter без настоящего сервера: двойник принимает SQL в диалекте
MySQL (обратные кавычки, %s, ENGINE=..., AUTO_INCREMENT), записывает все запросы и отвечает на
запросы к information_schema. Семантику настоящего MySQL он не воспроизводит, её проверяет
интеграционный тест.
"""

import re
import sqlite3


class FakeCursor:
    def __init__(self, conn):
        self._conn = conn
        self._rows = []

    def execute(self, sql, params=()):
        self._conn.statements.append(sql)
        text = sql.strip()
        upper = text.upper()
        if upper.startswith("SET FOREIGN_KEY_CHECKS"):
            self._rows = []
            return
        if "INFORMATION_SCHEMA" in upper:
            self._rows = self._conn.information_schema(upper, params)
            return
        text = text.replace("`", '"').replace("%s", "?")
        text = re.sub(r"\s+AUTO_INCREMENT", "", text)
        text = re.sub(r"\s+ENGINE=\w+\s+DEFAULT CHARSET=\w+", "", text)
        cur = self._conn.db.execute(text, tuple(params))
        self._rows = cur.fetchall() if cur.description else []

    def executemany(self, sql, seq):
        self._conn.statements.append(sql)
        text = sql.replace("`", '"').replace("%s", "?")
        self._conn.db.executemany(text, seq)

    def fetchall(self):
        return list(self._rows)

    def close(self):
        pass


class FakeConnection:
    def __init__(self, **kwargs):
        self.kwargs = kwargs
        self.statements = []
        self.db = sqlite3.connect(":memory:", isolation_level=None)

    def cursor(self):
        return FakeCursor(self)

    def begin(self):
        self.db.execute("BEGIN")

    def commit(self):
        self.db.execute("COMMIT")

    def rollback(self):
        self.db.execute("ROLLBACK")

    def close(self):
        pass

    def information_schema(self, upper_sql, params):
        if "INFORMATION_SCHEMA.TABLES" in upper_sql:
            rows = self.db.execute(
                "SELECT name FROM sqlite_master "
                "WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
            ).fetchall()
            return [(r[0],) for r in rows]
        table = params[1]
        info = self.db.execute(f'PRAGMA table_info("{table}")').fetchall()
        if "KEY_COLUMN_USAGE" in upper_sql:
            return [(r[1],) for r in sorted((r for r in info if r[5]), key=lambda r: r[5])]
        result = []
        for _cid, name, decl, notnull, _default, pk in info:
            decl = (decl or "").lower()
            data_type = re.sub(r"\(.*\)", "", decl).strip() or "text"
            if "int" in data_type:
                data_type = "bigint"
            length = re.search(r"\((\d+)\)", decl)
            result.append(
                (
                    name,
                    data_type,
                    decl or data_type,
                    "NO" if notnull or pk else "YES",
                    int(length.group(1)) if length and data_type == "varchar" else None,
                )
            )
        return result


class FakeDriver:
    """Подставляется вместо модуля pymysql: driver.connect(**kwargs) возвращает FakeConnection."""

    def __init__(self):
        self.connection = None

    def connect(self, **kwargs):
        self.connection = FakeConnection(**kwargs)
        return self.connection
