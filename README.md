# Синхронизация локальной БД с сервером (db_sync)

[![CI](https://github.com/Kr1spy2023/synchronization_db/actions/workflows/ci.yml/badge.svg)](https://github.com/Kr1spy2023/synchronization_db/actions/workflows/ci.yml)
[![Post-merge](https://github.com/Kr1spy2023/synchronization_db/actions/workflows/post-merge.yml/badge.svg)](https://github.com/Kr1spy2023/synchronization_db/actions/workflows/post-merge.yml)
![Python](https://img.shields.io/badge/python-3.12-blue)
![Tests](https://img.shields.io/badge/tests-86%20passed-brightgreen)
![Coverage](https://img.shields.io/badge/coverage-94%25-brightgreen)
![License](https://img.shields.io/badge/license-MIT-lightgrey)

Учебный проект по МДК 02.02, тема 13: **«Модуль синхронизации локальной БД с удалённым сервером»**.

Модуль `db_sync` нужен разработчику, который работает с локальной базой SQLite, а продукт хранит данные в MySQL.
В нужный момент разработчик командой переносит схему и данные **на сервер (push)** или **с сервера (pull)**.
Перед записью модуль строит план: что будет добавлено, изменено, удалено. Запись идёт только после подтверждения.

## Состав репозитория

| Папка / файл       | Назначение                                                           |
|--------------------|----------------------------------------------------------------------|
| `src/db_sync/`     | Сам модуль (папка копируется в любой проект, без pip)                |
| `src/app.py`       | Демо-приложение: веб-панель на Flask + Jinja2, подключает модуль     |
| `src/seed.py`      | Создаёт две демонстрационные базы: локальную и «серверную»           |
| `src/templates/`, `src/static/` | Шаблоны Jinja2 и стили панели                            |
| `tests/`           | Тесты модуля и панели (pytest)                                       |
| `docs/`            | ER-диаграмма, UML, нефункциональные требования, QA, ретроспектива, скриншоты |
| `.github/`         | CI, пост-мердж проверка, шаблоны PR и Issue, CODEOWNERS               |
| `docker-compose.yml` | Тестовый MySQL 8 для проверки на настоящем сервере                 |

## Как модуль встроен в демо-проект

1. Папка `db_sync/` скопирована целиком в `src/db_sync/` (рядом с `app.py`). Устанавливать модуль через pip не нужно.
2. В `src/app.py` модуль подключается обычным импортом: `from db_sync import DbSync, SyncError`.
3. При создании приложения собирается объект
   `DbSync.from_urls(local_url, remote_url, history_path=...)`, а маршруты панели вызывают `plan()`, `apply()`, `status()`, `history()`.
4. Адреса баз берутся из переменных окружения `LOCAL_URL` и `REMOTE_URL`. Если их нет, используются файлы `local.db` и `remote.db`.
5. Для MySQL нужен только драйвер PyMySQL (он указан в `requirements.txt` и подключается лениво: SQLite работает и без него).

## Быстрый старт

```bash
pip install -r requirements.txt
python src/seed.py
python src/app.py
```

Панель откроется на http://127.0.0.1:5000. В демо «серверная» база тоже SQLite-файл, чтобы всё работало без установки MySQL.

### Работа с настоящим MySQL

```bash
docker compose up -d
```

```bash
pip install cryptography
```

Затем в переменных окружения задайте адрес сервера и запустите панель:

```bash
export REMOTE_URL=mysql://root:test@127.0.0.1:3306/test
python src/app.py
```

В PowerShell: `$env:REMOTE_URL = "mysql://root:test@127.0.0.1:3306/test"`.

## Использование модуля из кода

```python
from db_sync import DbSync

sync = DbSync.from_urls("sqlite:///local.db", "mysql://user:pass@host:3306/shop")
plan = sync.plan("push")           # только сравнение, ничего не записывается
print("\n".join(plan.summary_lines()))
sync.apply(plan, confirm=True)     # запись на сервер, подтверждение обязательно
sync.pull(dry_run=True)            # пробный запуск в обратную сторону (вернёт план)
sync.close()
```

Командная строка:

```bash
python -m db_sync status --local sqlite:///local.db --remote mysql://user:pass@host/shop
```

Команды `push` и `pull` без флага `--yes` только показывают план. Коды выхода: 0 — успех, 1 — ошибка синхронизации,
2 — не указаны адреса баз, 3 — сработала защита (например, запись на сервер без подтверждения).
Подробности по модулю и все параметры описаны в `src/db_sync/` (docstring) и в README модуля.

## Тесты и качество кода

```bash
pytest --cov=src --cov-report=term-missing
flake8 src tests
black --check src tests
isort --check-only src tests
```

Результат на момент релиза 0.1.0: 86 тестов проходят, 1 пропущен (интеграционный тест на MySQL, запускается в CI и при
заданной переменной `MYSQL_TEST_URL`), покрытие 94 %. Работа с MySQL в обычных тестах проверяется на двойнике драйвера
(`tests/fake_mysql.py`), а на настоящем MySQL — отдельной задачей CI `mysql-integration`.

Хуки перед коммитом: `pip install pre-commit`, затем `pre-commit install`.

## Скриншоты

| Сравнение баз | План push | Результат |
|---|---|---|
| ![Главная](docs/screenshots/01_comparison.jpg) | ![План](docs/screenshots/02_plan.jpg) | ![Результат](docs/screenshots/05_result.jpg) |

## Ограничения

См. `docs/nonfunctional_requirements.md` и `CHANGELOG.md`: не переносятся индексы, внешние ключи, значения по
умолчанию; таблицы без первичного ключа пропускаются; изменения схемы в MySQL не откатываются.

## Команда

| Участник                              | Роль                          |
|---------------------------------------|-------------------------------|
| Кулаков Никита Владимирович           | Разработчик, ведущий проекта  |
| Роговский Вадим Дмитриевич            | Тестировщик, ревьюер          |

Руководитель практики: Сорокина Ж.С.
