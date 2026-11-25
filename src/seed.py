"""Создаёт демонстрационные базы: локальную (новая версия) и «серверную» (старая версия).

Запуск: python src/seed.py
"""

import os
import sqlite3

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

LOCAL_SQL = """
CREATE TABLE categories (id INTEGER PRIMARY KEY, title TEXT NOT NULL, slug TEXT);
CREATE TABLE products (
    id INTEGER PRIMARY KEY, category_id INTEGER, name TEXT NOT NULL,
    price DECIMAL(10,2), stock INTEGER, updated DATETIME
);
CREATE TABLE orders (id INTEGER PRIMARY KEY, product_id INTEGER, qty INTEGER, created DATETIME);
INSERT INTO categories VALUES (1, 'Хлеб и выпечка', 'bread'), (2, 'Молочные продукты', 'dairy'),
    (3, 'Напитки', 'drinks'), (4, 'Сладости', 'sweets');
INSERT INTO products VALUES
    (1, 1, 'Батон нарезной', 45.50, 24, '2026-11-02 09:00:00'),
    (2, 1, 'Хлеб бородинский', 62.00, 12, '2026-11-02 09:00:00'),
    (3, 2, 'Молоко 3,2%', 84.90, 40, '2026-11-03 10:30:00'),
    (4, 2, 'Кефир 1%', 72.00, 18, '2026-11-03 10:30:00'),
    (5, 3, 'Сок яблочный', 119.00, 30, '2026-11-03 11:00:00'),
    (6, 3, 'Вода минеральная', 39.90, 100, '2026-11-03 11:00:00'),
    (7, 4, 'Печенье овсяное', 98.00, 15, '2026-11-04 08:15:00'),
    (8, 4, 'Шоколад молочный', 105.00, 22, '2026-11-04 08:15:00');
INSERT INTO orders VALUES (1, 1, 2, '2026-11-03 12:00:00'), (2, 3, 1, '2026-11-03 12:05:00'),
    (3, 5, 3, '2026-11-04 09:40:00'), (4, 8, 1, '2026-11-04 09:45:00');
"""

REMOTE_SQL = """
CREATE TABLE categories (id INTEGER PRIMARY KEY, title TEXT NOT NULL);
CREATE TABLE products (
    id INTEGER PRIMARY KEY, category_id INTEGER, name TEXT NOT NULL,
    price DECIMAL(10,2), stock INTEGER, updated DATETIME
);
INSERT INTO categories VALUES (1, 'Хлеб'), (2, 'Молочные продукты'), (9, 'Устаревшая категория');
INSERT INTO products VALUES
    (1, 1, 'Батон нарезной', 42.00, 24, '2026-10-25 09:00:00'),
    (2, 1, 'Хлеб бородинский', 62.00, 12, '2026-11-02 09:00:00'),
    (3, 2, 'Молоко 3,2%', 79.90, 40, '2026-10-25 10:30:00'),
    (4, 2, 'Кефир 1%', 72.00, 18, '2026-11-03 10:30:00'),
    (5, 3, 'Сок яблочный', 119.00, 30, '2026-11-03 11:00:00');
"""


def seed(local_path=None, remote_path=None):
    """Пересоздаёт обе базы. Возвращает пути к файлам."""
    local_path = local_path or os.path.join(BASE_DIR, "local.db")
    remote_path = remote_path or os.path.join(BASE_DIR, "remote.db")
    for path, sql in ((local_path, LOCAL_SQL), (remote_path, REMOTE_SQL)):
        if os.path.exists(path):
            os.remove(path)
        db = sqlite3.connect(path)
        db.executescript(sql)
        db.commit()
        db.close()
    return local_path, remote_path


if __name__ == "__main__":
    local, remote = seed()
    print("Созданы базы:", local, remote)
