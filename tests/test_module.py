import io
import sys
from datetime import date, datetime
from decimal import Decimal

import pytest
from fake_mysql import FakeDriver

from db_sync import (
    ConnectionFailed,
    DbSync,
    MySQLAdapter,
    SafetyError,
    SQLiteAdapter,
    ValidationError,
    connect,
)
from db_sync.adapters import check_identifier
from db_sync.cli import _hide_password, main
from db_sync.dbtypes import (
    Column,
    family_from_mysql,
    family_from_sqlite,
    mysql_type,
    norm,
    to_db,
)

SHOP = """
CREATE TABLE categories (id INTEGER PRIMARY KEY, title TEXT NOT NULL);
CREATE TABLE products (
    id INTEGER PRIMARY KEY, category_id INTEGER, name TEXT NOT NULL,
    price DECIMAL(10,2), stock INTEGER, updated DATETIME
);
"""


def make_db(sql=SHOP):
    adapter = SQLiteAdapter(":memory:")
    adapter._conn.executescript(sql)
    return adapter


def fill(adapter):
    adapter.executemany("INSERT INTO categories VALUES (?,?)", [(1, "Хлеб"), (2, "Молочные")])
    adapter.executemany(
        "INSERT INTO products VALUES (?,?,?,?,?,?)",
        [
            (1, 1, "Батон", 45.5, 10, "2026-11-01 10:00:00"),
            (2, 2, "Молоко", 80, 5, "2026-11-01 11:00:00"),
            (3, 2, "Кефир", 70, 0, None),
        ],
    )


@pytest.fixture
def pair():
    local, remote = make_db(), SQLiteAdapter(":memory:")
    fill(local)
    return local, remote


# ---- типы ----
@pytest.mark.parametrize(
    "raw,family",
    [
        ("INTEGER", "integer"),
        ("BIGINT", "integer"),
        ("VARCHAR(50)", "text"),
        ("TEXT", "text"),
        ("REAL", "real"),
        ("DOUBLE", "real"),
        ("DECIMAL(10,2)", "decimal"),
        ("DATETIME", "datetime"),
        ("DATE", "date"),
        ("BLOB", "blob"),
        ("BOOLEAN", "boolean"),
        ("", "text"),
    ],
)
def test_family_from_sqlite(raw, family):
    assert family_from_sqlite(raw) == family


@pytest.mark.parametrize(
    "data_type,column_type,family",
    [
        ("bigint", "bigint(20)", "integer"),
        ("tinyint", "tinyint(1)", "boolean"),
        ("tinyint", "tinyint(4)", "integer"),
        ("varchar", "varchar(255)", "text"),
        ("longtext", "longtext", "text"),
        ("double", "double", "real"),
        ("decimal", "decimal(10,2)", "decimal"),
        ("datetime", "datetime", "datetime"),
        ("timestamp", "timestamp", "datetime"),
        ("date", "date", "date"),
        ("longblob", "longblob", "blob"),
        ("json", "json", "text"),
    ],
)
def test_family_from_mysql(data_type, column_type, family):
    assert family_from_mysql(data_type, column_type) == family


def test_mysql_types_for_columns():
    assert mysql_type(Column("a", "integer")) == "BIGINT"
    assert mysql_type(Column("a", "decimal", raw_type="DECIMAL(10,2)")) == "DECIMAL(10,2)"
    assert mysql_type(Column("a", "decimal")) == "DECIMAL(20,6)"
    assert mysql_type(Column("a", "text", length=50)) == "VARCHAR(50)"
    assert mysql_type(Column("a", "text", pk=1)) == "VARCHAR(191)"  # TEXT нельзя в ключе
    assert mysql_type(Column("a", "text")) == "TEXT"


def test_norm_makes_values_comparable_between_dbms():
    assert norm(Decimal("45.50"), "decimal") == norm(45.5, "decimal")
    assert norm(datetime(2026, 11, 1, 10, 0), "datetime") == norm("2026-11-01 10:00:00", "datetime")
    assert norm("2026-11-01T10:00:00.000000", "datetime") == "2026-11-01 10:00:00"
    assert norm(date(2026, 11, 1), "date") == norm("2026-11-01", "date")
    assert norm(True, "boolean") == 1 and norm("7", "integer") == 7 and norm(None, "text") is None


def test_to_db_converts_python_objects():
    assert to_db(Decimal("1.50")) == "1.50"
    assert to_db(datetime(2026, 1, 2, 3, 4, 5)) == "2026-01-02 03:04:05"
    assert to_db(date(2026, 1, 2)) == "2026-01-02" and to_db(True) == 1
    assert to_db(bytearray(b"ab")) == b"ab"


# ---- адаптеры и адреса ----
def test_sqlite_create_table_sql():
    sql = make_db().create_table_sql("categories", make_db().columns("categories"))
    assert (
        sql == 'CREATE TABLE "categories" ("id" INTEGER NOT NULL, "title" TEXT NOT NULL, '
        'PRIMARY KEY ("id"))'
    )


def test_mysql_create_table_sql_uses_mysql_syntax():
    adapter = MySQLAdapter("h", "u", "p", "db", driver=FakeDriver())
    sql = adapter.create_table_sql("products", make_db().columns("products"))
    assert sql.startswith("CREATE TABLE `products` (`id` BIGINT NOT NULL AUTO_INCREMENT,")
    assert "`price` DECIMAL(10,2)" in sql and "PRIMARY KEY (`id`)" in sql
    assert sql.endswith("ENGINE=InnoDB DEFAULT CHARSET=utf8mb4")


def test_composite_key_has_no_auto_increment():
    db = make_db("CREATE TABLE links (a INTEGER, b INTEGER, note TEXT, PRIMARY KEY (a, b));")
    sql = MySQLAdapter("h", "u", "p", "db", driver=FakeDriver()).create_table_sql(
        "links", db.columns("links")
    )
    assert "AUTO_INCREMENT" not in sql and "PRIMARY KEY (`a`, `b`)" in sql


def test_added_columns_are_always_nullable():
    column = Column("note", "text", nullable=False)
    assert (
        SQLiteAdapter(":memory:").add_column_sql("t", column)
        == 'ALTER TABLE "t" ADD COLUMN "note" TEXT'
    )


@pytest.mark.parametrize("name", ["a;DROP TABLE x", "1abc", "", "a b", None, "x" * 65, "таблица"])
def test_dangerous_identifiers_are_rejected(name):
    with pytest.raises(ValidationError):
        check_identifier(name)


def test_connect_parses_urls():
    assert isinstance(connect("sqlite:///:memory:"), SQLiteAdapter)
    driver = FakeDriver()
    adapter = connect("mysql://dev:p%40ss@db.example.com:3307/shop", driver=driver)
    assert adapter.describe() == "MySQL: dev@db.example.com:3307/shop"
    assert (
        driver.connection.kwargs["password"] == "p@ss" and driver.connection.kwargs["port"] == 3307
    )


@pytest.mark.parametrize(
    "url", ["", "shop.db", "postgres://u@h/db", "mysql://u:p@/db", "mysql://u:p@h/", "sqlite://"]
)
def test_bad_urls_are_rejected(url):
    with pytest.raises(ValidationError):
        connect(url, driver=FakeDriver())


def test_missing_mysql_driver_and_connection_errors(monkeypatch):
    monkeypatch.setitem(sys.modules, "pymysql", None)
    with pytest.raises(ConnectionFailed, match="pymysql"):
        MySQLAdapter("h", "u", "p", "db")

    class Broken:
        @staticmethod
        def connect(**kw):
            raise OSError("refused")

    with pytest.raises(ConnectionFailed, match="refused"):
        MySQLAdapter("h", "u", "p", "db", driver=Broken)


# ---- планирование и синхронизация (SQLite -> SQLite) ----
def test_dry_run_changes_nothing(pair):
    local, remote = pair
    plan = DbSync(local, remote).push(dry_run=True)
    assert plan.n_inserts == 5 and len(plan.schema_sql) == 2
    assert remote.tables() == []


def test_push_creates_tables_and_copies_rows(pair):
    local, remote = pair
    result = DbSync(local, remote).push(confirm=True)
    assert result.ok and result.inserted == 5 and len(result.schema_sql) == 2
    assert remote.fetchall("SELECT id, name FROM products ORDER BY id") == [
        (1, "Батон"),
        (2, "Молоко"),
        (3, "Кефир"),
    ]


def test_second_push_has_nothing_to_do(pair):
    local, remote = pair
    sync = DbSync(local, remote)
    sync.push(confirm=True)
    plan = sync.push(dry_run=True)
    assert plan.is_empty and sum(t.unchanged for t in plan.tables) == 5


def test_changed_and_new_rows_are_updated_and_inserted(pair):
    local, remote = pair
    sync = DbSync(local, remote)
    sync.push(confirm=True)
    local.execute("UPDATE products SET price = 99, stock = 1 WHERE id = 1")
    local.execute("INSERT INTO products VALUES (4, 1, 'Булка', 20, 7, NULL)")
    result = sync.push(confirm=True)
    assert (result.inserted, result.updated, result.deleted) == (1, 1, 0)
    assert remote.fetchall("SELECT price, stock FROM products WHERE id = 1") == [(99, 1)]


def test_rows_missing_in_source_are_kept_unless_delete_requested(pair):
    local, remote = pair
    sync = DbSync(local, remote)
    sync.push(confirm=True)
    local.execute("DELETE FROM products WHERE id = 3")
    assert sync.push(confirm=True).deleted == 0
    assert remote.row_count("products") == 3
    plan = sync.push(delete_missing=True, dry_run=True)
    assert plan.n_deletes == 1 and plan.has_deletes
    with pytest.raises(SafetyError):
        sync.apply(plan, confirm=False)
    assert sync.apply(plan, confirm=True).deleted == 1 and remote.row_count("products") == 2


def test_push_to_server_needs_confirmation_but_pull_does_not(pair):
    local, remote = pair
    sync = DbSync(local, remote)
    with pytest.raises(SafetyError, match="сервер"):
        sync.push()
    sync.push(confirm=True)
    remote.execute("UPDATE products SET stock = 100 WHERE id = 1")
    assert sync.pull().updated == 1 and local.fetchall(
        "SELECT stock FROM products WHERE id = 1"
    ) == [(100,)]


def test_protection_can_be_switched_off(pair):
    local, remote = pair
    assert DbSync(local, remote, protect_remote=False).push().ok


def test_new_column_is_added_and_filled(pair):
    local, remote = pair
    sync = DbSync(local, remote)
    sync.push(confirm=True)
    local.execute("ALTER TABLE categories ADD COLUMN slug TEXT")
    local.execute("UPDATE categories SET slug = 'bread' WHERE id = 1")
    result = sync.push(confirm=True)
    assert 'ALTER TABLE "categories" ADD COLUMN "slug" TEXT' in result.schema_sql
    assert remote.fetchall("SELECT slug FROM categories ORDER BY id") == [("bread",), (None,)]


def test_extra_target_column_is_kept_with_warning(pair):
    local, remote = pair
    sync = DbSync(local, remote)
    sync.push(confirm=True)
    remote.execute("ALTER TABLE categories ADD COLUMN legacy TEXT")
    plan = sync.push(dry_run=True)
    table = next(t for t in plan.tables if t.name == "categories")
    assert any("legacy" in w for w in table.warnings) and plan.is_empty


def test_table_without_primary_key_is_skipped():
    local, remote = make_db("CREATE TABLE log (msg TEXT);"), SQLiteAdapter(":memory:")
    local.execute("INSERT INTO log VALUES ('x')")
    plan = DbSync(local, remote).push(dry_run=True)
    assert "первичного ключа" in plan.tables[0].skipped
    assert DbSync(local, remote).push(confirm=True).inserted == 0


def test_different_primary_keys_are_not_synced():
    local = make_db("CREATE TABLE t (id INTEGER PRIMARY KEY, v TEXT);")
    remote = make_db("CREATE TABLE t (id INTEGER, v TEXT PRIMARY KEY);")
    assert "ключи" in DbSync(local, remote).push(dry_run=True).tables[0].skipped


def test_tables_filter_exclude_and_unknown_table(pair):
    local, remote = pair
    sync = DbSync(local, remote)
    assert [t.name for t in sync.push(tables=["products"], dry_run=True).tables] == ["products"]
    assert [t.name for t in sync.push(exclude=["products"], dry_run=True).tables] == ["categories"]
    with pytest.raises(ValidationError, match="nope"):
        sync.push(tables=["nope"], dry_run=True)
    with pytest.raises(ValidationError):
        sync.plan("sideways")


def test_schema_switch_off_skips_missing_tables(pair):
    local, remote = pair
    plan = DbSync(local, remote).push(schema=False, dry_run=True)
    assert all(t.skipped for t in plan.tables) and plan.is_empty


def test_error_in_one_table_rolls_back_everything():
    local = make_db(
        "CREATE TABLE a_first (id INTEGER PRIMARY KEY, v TEXT); "
        "CREATE TABLE b_second (id INTEGER PRIMARY KEY, v TEXT);"
    )
    remote = make_db(
        "CREATE TABLE a_first (id INTEGER PRIMARY KEY, v TEXT); "
        "CREATE TABLE b_second (id INTEGER PRIMARY KEY, v TEXT NOT NULL);"
    )
    local.execute("INSERT INTO a_first VALUES (1, 'ok')")
    local.execute("INSERT INTO b_second VALUES (1, NULL)")  # на сервере v обязателен
    result = DbSync(local, remote).push(confirm=True)
    assert not result.ok and "NOT NULL" in result.error
    assert remote.row_count("a_first") == 0 and remote.row_count("b_second") == 0


def test_large_tables_are_written_in_chunks():
    local = make_db("CREATE TABLE big (id INTEGER PRIMARY KEY, v INTEGER);")
    remote = SQLiteAdapter(":memory:")
    local.executemany("INSERT INTO big VALUES (?,?)", [(i, i) for i in range(1, 1301)])
    sync = DbSync(local, remote)
    assert sync.push(confirm=True).inserted == 1300
    local.execute("UPDATE big SET v = v + 1")
    assert sync.push(confirm=True).updated == 1300


def test_plan_signature_changes_when_data_changes(pair):
    local, remote = pair
    sync = DbSync(local, remote)
    first = sync.push(dry_run=True).signature
    assert sync.push(dry_run=True).signature == first
    local.execute("INSERT INTO categories VALUES (3, 'Новая')")
    assert sync.push(dry_run=True).signature != first


def test_status_counts_per_table(pair):
    local, remote = pair
    sync = DbSync(local, remote)
    sync.push(confirm=True)
    local.execute("INSERT INTO categories VALUES (3, 'Только у меня')")
    remote.execute("INSERT INTO categories VALUES (9, 'Только на сервере')")
    remote.execute("UPDATE products SET stock = 77 WHERE id = 2")
    status = {row["name"]: row for row in sync.status()}
    assert (status["categories"]["only_local"], status["categories"]["only_remote"]) == (1, 1)
    assert status["products"]["different"] == 1 and status["products"]["same"] == 2
    assert status["products"]["local_rows"] == 3


def test_history_is_kept_in_memory_and_in_file(pair, tmp_path):
    local, remote = pair
    path = str(tmp_path / "history.jsonl")
    sync = DbSync(local, remote, history_path=path)
    sync.push(confirm=True)
    remote.execute("DELETE FROM categories")
    sync.push(confirm=True)
    assert [h["ok"] for h in sync.history()] == [True, True] and sync.history()[1]["inserted"] == 5
    assert (
        len(DbSync.read_history_file(path)) == 2
        and DbSync.read_history_file(str(tmp_path / "none")) == []
    )


# ---- MySQL через двойник драйвера ----
def mysql_side():
    driver = FakeDriver()
    return MySQLAdapter("db.local", "dev", "secret", "shop", driver=driver), driver.connection


def test_push_to_mysql_uses_mysql_dialect(pair):
    local, _ = pair
    remote, conn = mysql_side()
    result = DbSync(local, remote).push(confirm=True)
    assert result.ok and result.inserted == 5
    assert any(
        s.startswith("CREATE TABLE `categories`") and "ENGINE=InnoDB" in s for s in conn.statements
    )
    assert any(s.startswith("INSERT INTO `products`") and "%s" in s for s in conn.statements)
    assert (
        "SET FOREIGN_KEY_CHECKS = 0" in conn.statements
        and "SET FOREIGN_KEY_CHECKS = 1" in conn.statements
    )
    assert conn.db.execute("SELECT name FROM products WHERE id = 3").fetchall() == [("Кефир",)]


def test_mysql_roundtrip_has_no_phantom_differences(pair):
    local, _ = pair
    remote, _ = mysql_side()
    sync = DbSync(local, remote)
    sync.push(confirm=True)
    assert sync.push(dry_run=True).is_empty and sync.pull(dry_run=True).is_empty


def test_pull_from_mysql_creates_local_tables():
    remote, conn = mysql_side()
    conn.db.executescript(
        "CREATE TABLE notes (id BIGINT PRIMARY KEY, title VARCHAR(50), created DATETIME);"
    )
    conn.db.execute("INSERT INTO notes VALUES (1, 'с сервера', '2026-11-01 10:00:00')")
    local = SQLiteAdapter(":memory:")
    result = DbSync(local, remote).pull()
    assert result.ok and local.fetchall("SELECT id, title FROM notes") == [(1, "с сервера")]
    assert 'CREATE TABLE "notes"' in result.schema_sql[0] and "VARCHAR" not in result.schema_sql[0]


def test_mysql_error_is_rolled_back():
    local = make_db("CREATE TABLE categories (id INTEGER PRIMARY KEY, title TEXT);")
    local.execute("INSERT INTO categories VALUES (1, NULL)")  # на сервере title обязателен
    remote, conn = mysql_side()
    conn.db.executescript(
        "CREATE TABLE categories (id BIGINT PRIMARY KEY, title VARCHAR(50) NOT NULL);"
    )
    result = DbSync(local, remote).push(confirm=True)
    assert not result.ok and conn.db.execute("SELECT COUNT(*) FROM categories").fetchone()[0] == 0


# ---- командная строка ----
def run_cli(*args):
    out = io.StringIO()
    code = main(list(args), out=out)
    return code, out.getvalue()


def cli_paths(tmp_path):
    local, remote = str(tmp_path / "local.db"), str(tmp_path / "remote.db")
    db = SQLiteAdapter(local)
    db._conn.executescript(SHOP)
    fill(db)
    db.close()
    return f"sqlite:///{local}", f"sqlite:///{remote}", remote


def test_cli_without_yes_only_shows_plan(tmp_path):
    local, remote, remote_path = cli_paths(tmp_path)
    code, text = run_cli("push", "--local", local, "--remote", remote)
    assert code == 0 and "добавить 3" in text and "--yes" in text
    assert SQLiteAdapter(remote_path).tables() == []


def test_cli_push_with_yes_then_status(tmp_path):
    local, remote, remote_path = cli_paths(tmp_path)
    code, text = run_cli("push", "--local", local, "--remote", remote, "--yes")
    assert code == 0 and "добавлено 5" in text
    code, text = run_cli("status", "--local", local, "--remote", remote)
    assert code == 0 and "products: локально 3, на сервере 3, только локально 0" in text


def test_cli_pull_and_tables_option(tmp_path):
    local, remote, _ = cli_paths(tmp_path)
    run_cli("push", "--local", local, "--remote", remote, "--yes")
    code, text = run_cli(
        "pull", "--local", local, "--remote", remote, "--tables", "products", "--yes"
    )
    assert code == 0 and "categories" not in text.split("Готово")[0].split("Сервер:")[1]


def test_cli_errors_have_exit_codes(tmp_path):
    assert run_cli("push")[0] == 2
    local, remote, _ = cli_paths(tmp_path)
    code, text = run_cli("push", "--local", local, "--remote", remote, "--tables", "nope")
    assert code == 1 and "nope" in text


def test_password_is_hidden_in_cli_output():
    assert _hide_password("mysql://dev:topsecret@host/db") == "mysql://dev:***@host/db"
    assert _hide_password("sqlite:///x.db") == "sqlite:///x.db"
