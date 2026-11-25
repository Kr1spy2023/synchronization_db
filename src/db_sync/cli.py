"""Командная строка: python -m db_sync status|push|pull --local ... --remote ..."""

import argparse
import os
import sys
import urllib.parse

from .errors import SafetyError, SyncError
from .sync import DbSync


def _hide_password(url):
    """Скрывает пароль в адресе, чтобы он не попадал в вывод и логи."""
    parsed = urllib.parse.urlparse(url)
    if parsed.password:
        netloc = parsed.netloc.replace(":" + parsed.password, ":***")
        return parsed._replace(netloc=netloc).geturl()
    return url


def build_parser():
    parser = argparse.ArgumentParser(
        prog="db_sync", description="Синхронизация локальной БД и сервера"
    )
    parser.add_argument("command", choices=["status", "push", "pull"])
    parser.add_argument(
        "--local", default=os.environ.get("DBSYNC_LOCAL"), help="адрес локальной БД"
    )
    parser.add_argument("--remote", default=os.environ.get("DBSYNC_REMOTE"), help="адрес сервера")
    parser.add_argument("--tables", help="таблицы через запятую (по умолчанию все)")
    parser.add_argument("--exclude", default="", help="таблицы, которые не трогать")
    parser.add_argument(
        "--delete-missing", action="store_true", help="удалять строки, которых нет в источнике"
    )
    parser.add_argument("--no-schema", action="store_true", help="не создавать таблицы и столбцы")
    parser.add_argument(
        "--yes", action="store_true", help="выполнить изменения (без флага только показать план)"
    )
    parser.add_argument("--history", help="файл журнала синхронизаций")
    return parser


def _csv(value):
    return [x.strip() for x in value.split(",") if x.strip()] if value else []


def main(argv=None, out=None):
    out = out or sys.stdout
    args = build_parser().parse_args(argv)
    if not args.local or not args.remote:
        print(
            "Нужно указать --local и --remote (или переменные DBSYNC_LOCAL и DBSYNC_REMOTE)",
            file=out,
        )
        return 2
    try:
        sync = DbSync.from_urls(args.local, args.remote, history_path=args.history)
        print(f"Локальная БД: {_hide_password(args.local)}", file=out)
        print(f"Сервер:       {_hide_password(args.remote)}", file=out)
        if args.command == "status":
            for row in sync.status(_csv(args.tables), _csv(args.exclude)):
                print(
                    f"{row['name']}: локально {row['local_rows']}, "
                    f"на сервере {row['remote_rows']}, "
                    f"только локально {row['only_local']}, только на сервере {row['only_remote']}, "
                    f"отличаются {row['different']}",
                    file=out,
                )
            return 0
        plan = sync.plan(
            args.command,
            _csv(args.tables) or None,
            _csv(args.exclude),
            args.delete_missing,
            not args.no_schema,
        )
        for line in plan.summary_lines() + plan.warnings:
            print(line, file=out)
        for sql in plan.schema_sql:
            print("  SQL:", sql, file=out)
        if not args.yes:
            print("Это только план. Чтобы выполнить, добавьте --yes", file=out)
            return 0
        result = sync.apply(plan, confirm=True)
        if not result.ok:
            print(f"Ошибка, изменения отменены: {result.error}", file=out)
            return 1
        print(
            f"Готово: добавлено {result.inserted}, изменено {result.updated}, "
            f"удалено {result.deleted}",
            file=out,
        )
        return 0
    except SafetyError as err:
        print(f"Защита: {err}", file=out)
        return 3
    except SyncError as err:
        print(f"Ошибка: {err}", file=out)
        return 1
