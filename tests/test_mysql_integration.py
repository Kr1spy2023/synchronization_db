"""Проверка на настоящем MySQL. Запускается только если задана переменная MYSQL_TEST_URL.

Пример: MYSQL_TEST_URL=mysql://root:pass@127.0.0.1:3306/test pytest tests/test_mysql_integration.py
"""

import os

import pytest

from db_sync import DbSync, SQLiteAdapter, connect

URL = os.environ.get("MYSQL_TEST_URL")
pytestmark = pytest.mark.skipif(not URL, reason="нужна переменная MYSQL_TEST_URL")

TABLES = ("dbsync_it_products", "dbsync_it_categories")


@pytest.fixture
def sides():
    local = SQLiteAdapter(":memory:")
    local._conn.executescript("""
        CREATE TABLE dbsync_it_categories (id INTEGER PRIMARY KEY, title TEXT NOT NULL);
        CREATE TABLE dbsync_it_products (
            id INTEGER PRIMARY KEY, category_id INTEGER, name TEXT NOT NULL,
            price DECIMAL(10,2), updated DATETIME);
        INSERT INTO dbsync_it_categories VALUES (1, 'Хлеб'), (2, 'Молочные');
        INSERT INTO dbsync_it_products VALUES (1, 1, 'Батон', 45.5, '2026-11-01 10:00:00');
        INSERT INTO dbsync_it_products VALUES (2, 2, 'Молоко', 80, '2026-11-01 11:00:00');
        """)
    remote = connect(URL)
    for name in TABLES:
        remote.execute(f"DROP TABLE IF EXISTS {name}")
    remote.commit()
    yield local, remote
    for name in TABLES:
        remote.execute(f"DROP TABLE IF EXISTS {name}")
    remote.commit()
    remote.close()


def test_push_pull_roundtrip_on_real_mysql(sides):
    local, remote = sides
    sync = DbSync(local, remote)
    result = sync.push(confirm=True)
    assert result.ok, result.error
    # после отправки различий быть не должно, несмотря на Decimal и datetime из драйвера
    assert sync.push(dry_run=True).is_empty and sync.pull(dry_run=True).is_empty
    remote.begin()
    remote.execute("UPDATE dbsync_it_products SET price = 99.90 WHERE id = 1")
    remote.commit()
    assert sync.pull().updated == 1
    value = local.fetchall("SELECT price FROM dbsync_it_products WHERE id = 1")[0][0]
    assert float(value) == 99.9
