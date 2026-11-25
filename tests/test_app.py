import re
import sqlite3

import pytest

from app import create_app
from seed import seed


@pytest.fixture
def paths(tmp_path):
    local, remote = seed(str(tmp_path / "local.db"), str(tmp_path / "remote.db"))
    return local, remote, str(tmp_path / "history.jsonl")


@pytest.fixture
def app(paths):
    local, remote, history = paths
    return create_app(
        f"sqlite:///{local}", f"sqlite:///{remote}", secret_key="test", history_path=history
    )


class Browser:
    """Обёртка над test client: сама подставляет CSRF-токен в POST."""

    def __init__(self, client):
        self.c = client

    def get(self, url, **kw):
        return self.c.get(url, **kw)

    def post(self, url, data=None, **kw):
        self.c.get("/")  # создаёт csrf в сессии
        with self.c.session_transaction() as s:
            token = s["csrf"]
        return self.c.post(url, data={**(data or {}), "csrf": token}, **kw)


@pytest.fixture
def web(app):
    return Browser(app.test_client())


def rows(path, sql):
    db = sqlite3.connect(path)
    try:
        return db.execute(sql).fetchall()
    finally:
        db.close()


def signature(page):
    return re.search(r'name="signature" value="([0-9a-f]+)"', page).group(1)


def build_plan(web, direction="push", **extra):
    return web.post("/plan", {"direction": direction, **extra}).get_data(as_text=True)


def test_index_shows_comparison(web):
    page = web.get("/").get_data(as_text=True)
    assert "categories" in page and "products" in page and "orders" in page
    assert "нет таблицы" in page  # orders есть только локально


def test_plan_page_shows_counts_and_sql(web):
    page = build_plan(web)
    assert "ALTER TABLE" in page and "CREATE TABLE" in page and "orders" in page
    assert "Применить план" in page and "записаны на сервер продукта" in page


def test_plan_does_not_write_anything(web, paths):
    build_plan(web)
    assert rows(paths[1], "SELECT COUNT(*) FROM products") == [(5,)]
    assert "orders" not in str(rows(paths[1], "SELECT name FROM sqlite_master"))


def test_apply_without_confirmation_is_refused(web, paths):
    page = build_plan(web)
    r = web.post(
        "/apply", {"direction": "push", "signature": signature(page)}, follow_redirects=True
    )
    assert "Подтвердите запись" in r.get_data(as_text=True)
    assert rows(paths[1], "SELECT COUNT(*) FROM products") == [(5,)]


def test_apply_push_writes_to_server(web, paths):
    page = build_plan(web)
    r = web.post("/apply", {"direction": "push", "signature": signature(page), "confirm": "1"})
    text = r.get_data(as_text=True)
    assert "Готово: добавлено 9, изменено 4, удалено 0" in text
    assert rows(paths[1], "SELECT price FROM products WHERE id = 1") == [(45.5,)]
    assert rows(paths[1], "SELECT COUNT(*) FROM orders") == [(4,)]
    assert rows(paths[1], "SELECT slug FROM categories WHERE id = 4") == [("sweets",)]


def test_rows_missing_locally_are_kept_unless_delete_requested(web, paths):
    page = build_plan(web)
    web.post("/apply", {"direction": "push", "signature": signature(page), "confirm": "1"})
    assert rows(paths[1], "SELECT COUNT(*) FROM categories WHERE id = 9") == [(1,)]
    page = build_plan(web, delete_missing="1")
    assert "удаление 1 строк" in page
    web.post(
        "/apply",
        {"direction": "push", "signature": signature(page), "confirm": "1", "delete_missing": "1"},
    )
    assert rows(paths[1], "SELECT COUNT(*) FROM categories WHERE id = 9") == [(0,)]


def test_stale_plan_is_refused(web, paths):
    page = build_plan(web)
    db = sqlite3.connect(paths[0])
    db.execute("INSERT INTO categories (id, title, slug) VALUES (5, 'Новая', 'new')")
    db.commit()
    db.close()
    r = web.post(
        "/apply",
        {"direction": "push", "signature": signature(page), "confirm": "1"},
        follow_redirects=True,
    )
    assert "Постройте план заново" in r.get_data(as_text=True)
    assert rows(paths[1], "SELECT COUNT(*) FROM products") == [(5,)]


def test_tables_option_limits_the_plan(web, paths):
    page = build_plan(web, tables="products")
    web.post(
        "/apply",
        {"direction": "push", "signature": signature(page), "confirm": "1", "tables": "products"},
    )
    assert rows(paths[1], "SELECT COUNT(*) FROM products") == [(8,)]
    assert "orders" not in str(rows(paths[1], "SELECT name FROM sqlite_master"))


def test_pull_updates_local_database(web, paths):
    page = build_plan(web, direction="pull", tables="products")
    assert "Подтверждаю запись изменений в локальную базу" in page
    web.post(
        "/apply",
        {"direction": "pull", "signature": signature(page), "confirm": "1", "tables": "products"},
    )
    assert rows(paths[0], "SELECT price FROM products WHERE id = 1") == [(42,)]


def test_unknown_table_in_plan_shows_error(web):
    r = web.post("/plan", {"direction": "push", "tables": "nope"}, follow_redirects=True)
    assert "nope" in r.get_data(as_text=True)


def test_history_lists_applied_syncs(web):
    page = build_plan(web)
    web.post("/apply", {"direction": "push", "signature": signature(page), "confirm": "1"})
    text = web.get("/history").get_data(as_text=True)
    assert "на сервер" in text and "успешно" in text and "orders" in text


def test_table_view_shows_rows_and_rejects_unknown(web):
    assert "Хлеб и выпечка" in web.get("/table/local/categories").get_data(as_text=True)
    assert web.get("/table/remote/orders").status_code == 404
    assert web.get("/table/other/products").status_code == 404


def test_invalid_direction_and_csrf(web, app):
    assert web.post("/plan", {"direction": "sideways"}).status_code == 400
    assert app.test_client().post("/plan", data={"direction": "push"}).status_code == 400
