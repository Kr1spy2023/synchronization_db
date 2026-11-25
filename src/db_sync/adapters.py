"""Адаптеры баз данных: SQLite (локальная) и MySQL (сервер продукта)."""

import re
import sqlite3
import urllib.parse

from .dbtypes import (
    Column,
    family_from_mysql,
    family_from_sqlite,
    mysql_type,
    parse_length,
    sqlite_type,
)
from .errors import ConnectionFailed, ValidationError

_IDENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,63}$")


def is_valid_identifier(name):
    return isinstance(name, str) and bool(_IDENT_RE.match(name))


def check_identifier(name):
    """Имена таблиц и столбцов: латиница, цифры, подчёркивание, до 64 символов."""
    if not is_valid_identifier(name):
        raise ValidationError(f"Недопустимое имя: {name!r}")
    return name


class Adapter:
    """Общая часть: построение SQL. Подключение и чтение схемы — в наследниках."""

    dialect = "generic"
    placeholder = "?"

    def __init__(self):
        self._conn = None

    # ---------- имена и SQL ----------
    def quote(self, name):
        check_identifier(name)
        return self._quote(name)

    def _quote(self, name):
        return f'"{name}"'

    auto_increment_sql = ""

    def column_sql(self, column, with_not_null=True, auto=False):
        parts = [self.quote(column.name), self.type_sql(column)]
        if with_not_null and (not column.nullable or column.pk):
            parts.append("NOT NULL")
        if auto and self.auto_increment_sql:
            parts.append(self.auto_increment_sql)
        return " ".join(parts)

    def type_sql(self, column):
        raise NotImplementedError

    def create_table_sql(self, table, columns):
        keys = [c.name for c in sorted((c for c in columns if c.pk), key=lambda c: c.pk)]
        single_key = len(keys) == 1
        lines = [
            self.column_sql(c, auto=single_key and c.pk == 1 and c.family == "integer")
            for c in columns
        ]
        if keys:
            lines.append("PRIMARY KEY (" + ", ".join(self.quote(k) for k in keys) + ")")
        return f"CREATE TABLE {self.quote(table)} (" + ", ".join(lines) + ")" + self.table_options()

    def table_options(self):
        return ""

    def add_column_sql(self, table, column):
        # Добавляемый столбец всегда необязательный: в таблице уже могут быть строки.
        definition = self.column_sql(column, with_not_null=False)
        return f"ALTER TABLE {self.quote(table)} ADD COLUMN {definition}"

    def insert_sql(self, table, columns):
        names = ", ".join(self.quote(c) for c in columns)
        marks = ", ".join(self.placeholder for _ in columns)
        return f"INSERT INTO {self.quote(table)} ({names}) VALUES ({marks})"

    def update_sql(self, table, set_columns, key_columns):
        sets = ", ".join(f"{self.quote(c)} = {self.placeholder}" for c in set_columns)
        where = " AND ".join(f"{self.quote(c)} = {self.placeholder}" for c in key_columns)
        return f"UPDATE {self.quote(table)} SET {sets} WHERE {where}"

    def delete_sql(self, table, key_columns):
        where = " AND ".join(f"{self.quote(c)} = {self.placeholder}" for c in key_columns)
        return f"DELETE FROM {self.quote(table)} WHERE {where}"

    def select_sql(self, table, columns, order_by):
        names = ", ".join(self.quote(c) for c in columns)
        order = ", ".join(self.quote(c) for c in order_by)
        return f"SELECT {names} FROM {self.quote(table)} ORDER BY {order}"

    # ---------- выполнение ----------
    def fetchall(self, sql, params=()):
        cursor = self._conn.cursor()
        try:
            cursor.execute(sql, params)
            return [tuple(row) for row in cursor.fetchall()]
        finally:
            cursor.close()

    def execute(self, sql, params=()):
        cursor = self._conn.cursor()
        try:
            cursor.execute(sql, params)
        finally:
            cursor.close()

    def executemany(self, sql, rows):
        if not rows:
            return
        cursor = self._conn.cursor()
        try:
            cursor.executemany(sql, rows)
        finally:
            cursor.close()

    def rows(self, table, columns, order_by):
        return self.fetchall(self.select_sql(table, columns, order_by))

    def row_count(self, table):
        return self.fetchall(f"SELECT COUNT(*) FROM {self.quote(table)}")[0][0]

    def begin(self):
        raise NotImplementedError

    def commit(self):
        self._conn.commit()

    def rollback(self):
        self._conn.rollback()

    def close(self):
        if self._conn is not None:
            self._conn.close()
            self._conn = None

    # ---------- схема ----------
    def tables(self):
        raise NotImplementedError

    def columns(self, table):
        raise NotImplementedError

    def describe(self):
        return self.dialect


class SQLiteAdapter(Adapter):
    """Локальная база SQLite. Транзакции управляются вручную."""

    dialect = "sqlite"

    def __init__(self, path):
        super().__init__()
        self.path = path
        try:
            self._conn = sqlite3.connect(path, isolation_level=None, check_same_thread=False)
        except sqlite3.Error as err:
            raise ConnectionFailed(f"Не удалось открыть SQLite-файл {path}: {err}")

    def type_sql(self, column):
        return sqlite_type(column)

    def begin(self):
        self.execute("BEGIN")

    def commit(self):
        self.execute("COMMIT")

    def rollback(self):
        self.execute("ROLLBACK")

    def tables(self):
        rows = self.fetchall(
            "SELECT name FROM sqlite_master "
            "WHERE type = 'table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
        )
        return [r[0] for r in rows]

    def columns(self, table):
        rows = self.fetchall(f"PRAGMA table_info({self.quote(table)})")
        return [
            Column(
                name=r[1],
                family=family_from_sqlite(r[2]),
                raw_type=r[2] or "",
                nullable=not r[3] and not r[5],
                pk=r[5],
                length=parse_length(r[2]),
            )
            for r in rows
        ]

    def describe(self):
        return f"SQLite: {self.path}"


class MySQLAdapter(Adapter):
    """Сервер MySQL (нужен пакет PyMySQL: pip install pymysql).

    :param driver: модуль драйвера (по умолчанию pymysql); подмена нужна для тестов.
    """

    dialect = "mysql"
    placeholder = "%s"
    auto_increment_sql = "AUTO_INCREMENT"

    def __init__(self, host, user, password, database, port=3306, driver=None):
        super().__init__()
        self.host, self.database, self.port, self.user = host, database, port, user
        if driver is None:
            try:
                import pymysql as driver
            except ImportError:
                raise ConnectionFailed("Для работы с MySQL установите драйвер: pip install pymysql")
        try:
            self._conn = driver.connect(
                host=host,
                user=user,
                password=password,
                database=database,
                port=port,
                charset="utf8mb4",
                autocommit=False,
            )
        except Exception as err:  # у разных драйверов свои классы ошибок
            raise ConnectionFailed(
                f"Не удалось подключиться к MySQL {host}:{port}/{database}: {err}"
            )

    def _quote(self, name):
        return f"`{name}`"

    def type_sql(self, column):
        return mysql_type(column)

    def table_options(self):
        return " ENGINE=InnoDB DEFAULT CHARSET=utf8mb4"

    def begin(self):
        self.execute("SET FOREIGN_KEY_CHECKS = 0")  # порядок таблиц не должен ломать связи
        self._conn.begin()

    def commit(self):
        self._conn.commit()
        self.execute("SET FOREIGN_KEY_CHECKS = 1")

    def rollback(self):
        self._conn.rollback()
        self.execute("SET FOREIGN_KEY_CHECKS = 1")

    def tables(self):
        rows = self.fetchall(
            "SELECT table_name FROM information_schema.tables "
            "WHERE table_schema = %s AND table_type = 'BASE TABLE' ORDER BY table_name",
            (self.database,),
        )
        return [r[0] for r in rows]

    def columns(self, table):
        info = self.fetchall(
            "SELECT column_name, data_type, column_type, is_nullable, character_maximum_length "
            "FROM information_schema.columns WHERE table_schema = %s AND table_name = %s "
            "ORDER BY ordinal_position",
            (self.database, table),
        )
        keys = self.fetchall(
            "SELECT column_name FROM information_schema.key_column_usage "
            "WHERE table_schema = %s AND table_name = %s AND constraint_name = 'PRIMARY' "
            "ORDER BY ordinal_position",
            (self.database, table),
        )
        key_pos = {k[0]: i + 1 for i, k in enumerate(keys)}
        return [
            Column(
                name=r[0],
                family=family_from_mysql(r[1], r[2]),
                raw_type=r[2] or "",
                nullable=(r[3] == "YES"),
                pk=key_pos.get(r[0], 0),
                length=(
                    int(r[4])
                    if r[4] is not None and family_from_mysql(r[1], r[2]) == "text"
                    else None
                ),
            )
            for r in info
        ]

    def describe(self):
        return f"MySQL: {self.user}@{self.host}:{self.port}/{self.database}"


def connect(url, driver=None):
    """Создаёт адаптер по адресу: sqlite:///файл.db или mysql://user:password@host:3306/база."""
    if not isinstance(url, str) or "://" not in url:
        raise ValidationError(
            "Адрес БД должен быть вида sqlite:///file.db или mysql://user:pass@host/db"
        )
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme == "sqlite":
        path = url[len("sqlite:///") :] if url.startswith("sqlite:///") else parsed.path
        if not path:
            raise ValidationError("В адресе SQLite не указан файл")
        return SQLiteAdapter(path)
    if parsed.scheme == "mysql":
        database = parsed.path.lstrip("/")
        if not parsed.hostname or not database:
            raise ValidationError("В адресе MySQL нужны хост и имя базы")
        return MySQLAdapter(
            parsed.hostname,
            urllib.parse.unquote(parsed.username or ""),
            urllib.parse.unquote(parsed.password or ""),
            database,
            parsed.port or 3306,
            driver=driver,
        )
    raise ValidationError(f"Неизвестный тип БД: {parsed.scheme}")
