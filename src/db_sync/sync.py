"""Синхронизация: построить план, показать разработчику, выполнить по команде."""

import json
import time
from dataclasses import dataclass, field

from .adapters import connect, is_valid_identifier
from .dbtypes import to_db
from .errors import SafetyError, ValidationError
from .planner import SyncPlan, build_table_plan

CHUNK = 500
DIRECTIONS = ("push", "pull")


@dataclass
class TableResult:
    name: str
    inserted: int = 0
    updated: int = 0
    deleted: int = 0


@dataclass
class SyncResult:
    """Итог выполнения плана."""

    direction: str
    started_at: int = 0
    finished_at: int = 0
    ok: bool = True
    error: str = None
    schema_sql: list = field(default_factory=list)
    tables: list = field(default_factory=list)

    @property
    def inserted(self):
        return sum(t.inserted for t in self.tables)

    @property
    def updated(self):
        return sum(t.updated for t in self.tables)

    @property
    def deleted(self):
        return sum(t.deleted for t in self.tables)


class DbSync:
    """Двусторонняя синхронизация локальной БД (SQLite) и сервера (MySQL или другой SQLite).

    :param local: адаптер локальной БД, с которой работает разработчик.
    :param remote: адаптер удалённой БД («сервер продукта»).
    :param protect_remote: требовать подтверждение (confirm=True) для записи на сервер.
    :param history_path: файл для журнала синхронизаций (по одной JSON-записи в строке).
    :param clock: функция текущего времени в секундах.
    """

    def __init__(self, local, remote, protect_remote=True, history_path=None, clock=time.time):
        self.local = local
        self.remote = remote
        self.protect_remote = protect_remote
        self.history_path = history_path
        self._clock = clock
        self._history = []

    @classmethod
    def from_urls(cls, local_url, remote_url, **kw):
        """Подключается по адресам sqlite:///файл.db и mysql://пользователь:пароль@хост/база."""
        return cls(connect(local_url), connect(remote_url), **kw)

    # ---------- план ----------
    def plan(self, direction, tables=None, exclude=(), delete_missing=False, schema=True):
        """Строит план без записи в базы. direction: "push" (на сервер) или "pull" (с сервера)."""
        source, target = self._pair(direction)
        plan = SyncPlan(
            direction=direction,
            source=source.describe(),
            target=target.describe(),
            delete_missing=delete_missing,
        )
        available = source.tables()
        names = self._select(available, tables, exclude, plan)
        for name in names:
            plan.tables.append(build_table_plan(source, target, name, delete_missing, schema))
        return plan

    def status(self, tables=None, exclude=()):
        """Сравнение баз по таблицам: строки только локально, только на сервере, отличающиеся."""
        names = sorted(set(self.local.tables()) | set(self.remote.tables()))
        names = [
            n
            for n in names
            if is_valid_identifier(n) and n not in exclude and (not tables or n in tables)
        ]
        result = []
        for name in names:
            in_local, in_remote = name in self.local.tables(), name in self.remote.tables()
            row = {
                "name": name,
                "in_local": in_local,
                "in_remote": in_remote,
                "local_rows": self.local.row_count(name) if in_local else None,
                "remote_rows": self.remote.row_count(name) if in_remote else None,
                "only_local": None,
                "only_remote": None,
                "different": None,
                "same": None,
                "notes": [],
            }
            if in_local and in_remote:
                table_plan = build_table_plan(self.local, self.remote, name, delete_missing=True)
                row["notes"] = table_plan.warnings + table_plan.schema_sql
                if table_plan.skipped:
                    row["notes"].append(table_plan.skipped)
                else:
                    row.update(
                        only_local=len(table_plan.inserts),
                        only_remote=len(table_plan.deletes),
                        different=len(table_plan.updates),
                        same=table_plan.unchanged,
                    )
            result.append(row)
        return result

    # ---------- выполнение ----------
    def apply(self, plan, confirm=False):
        """Выполняет план в одной транзакции. Возвращает SyncResult (ошибки записи не бросает)."""
        target = self._pair(plan.direction)[1]
        if plan.direction == "push" and self.protect_remote and not confirm and not plan.is_empty:
            raise SafetyError("Запись на сервер продукта требует подтверждения (confirm=True)")
        if plan.has_deletes and not confirm:
            raise SafetyError("Удаление строк требует подтверждения (confirm=True)")
        result = SyncResult(plan.direction, started_at=int(self._clock()))
        if not plan.is_empty:
            try:
                target.begin()
                for table_plan in plan.tables:
                    if table_plan.skipped:
                        continue
                    result.tables.append(self._apply_table(target, table_plan, result))
                target.commit()
            except Exception as err:  # у драйверов разные классы ошибок
                self._safe_rollback(target)
                result.ok = False
                result.error = str(err)
                result.tables = []
        result.finished_at = int(self._clock())
        self._log(plan, result)
        return result

    def push(
        self,
        tables=None,
        exclude=(),
        delete_missing=False,
        schema=True,
        confirm=False,
        dry_run=False,
    ):
        """Отправляет изменения из локальной БД на сервер. При dry_run только возвращает план."""
        return self._run("push", tables, exclude, delete_missing, schema, confirm, dry_run)

    def pull(
        self,
        tables=None,
        exclude=(),
        delete_missing=False,
        schema=True,
        confirm=False,
        dry_run=False,
    ):
        """Забирает изменения с сервера в локальную БД. При dry_run только возвращает план."""
        return self._run("pull", tables, exclude, delete_missing, schema, confirm, dry_run)

    def history(self, limit=20):
        """Последние синхронизации, новые первыми."""
        return list(reversed(self._history))[:limit]

    @staticmethod
    def read_history_file(path, limit=20):
        """Читает журнал синхронизаций из файла history_path, новые первыми."""
        try:
            with open(path, encoding="utf-8") as f:
                entries = [json.loads(line) for line in f if line.strip()]
        except FileNotFoundError:
            return []
        return list(reversed(entries))[:limit]

    def close(self):
        self.local.close()
        self.remote.close()

    # ---------- служебное ----------
    def _run(self, direction, tables, exclude, delete_missing, schema, confirm, dry_run):
        plan = self.plan(direction, tables, exclude, delete_missing, schema)
        return plan if dry_run else self.apply(plan, confirm=confirm)

    def _pair(self, direction):
        if direction == "push":
            return self.local, self.remote
        if direction == "pull":
            return self.remote, self.local
        raise ValidationError(
            "Направление: push (локальная -> сервер) или pull (сервер -> локальная)"
        )

    @staticmethod
    def _select(available, tables, exclude, plan):
        names = []
        for name in available:
            if not is_valid_identifier(name):
                plan.warnings.append(f"таблица {name!r} пропущена: недопустимое имя")
            else:
                names.append(name)
        if tables:
            missing = [t for t in tables if t not in names]
            if missing:
                raise ValidationError("Таблиц нет в источнике: " + ", ".join(map(str, missing)))
            names = [n for n in names if n in tables]
        return [n for n in names if n not in exclude]

    @staticmethod
    def _apply_table(target, table_plan, result):
        done = TableResult(table_plan.name)
        for sql in table_plan.schema_sql:
            target.execute(sql)
            result.schema_sql.append(sql)
        columns, keys = table_plan.columns, table_plan.key_columns
        convert = lambda row: tuple(to_db(v) for v in row)  # noqa: E731
        insert_sql = target.insert_sql(table_plan.name, columns)
        for i in range(0, len(table_plan.inserts), CHUNK):
            target.executemany(insert_sql, [convert(r) for r in table_plan.inserts[i : i + CHUNK]])
        done.inserted = len(table_plan.inserts)
        value_cols = [c for c in columns if c not in keys]
        if value_cols and table_plan.updates:
            update_sql = target.update_sql(table_plan.name, value_cols, keys)
            value_idx = [columns.index(c) for c in value_cols]
            key_idx = [columns.index(k) for k in keys]
            params = [
                convert([r[i] for i in value_idx] + [r[i] for i in key_idx])
                for r in table_plan.updates
            ]
            for i in range(0, len(params), CHUNK):
                target.executemany(update_sql, params[i : i + CHUNK])
            done.updated = len(table_plan.updates)
        if table_plan.deletes:
            delete_sql = target.delete_sql(table_plan.name, keys)
            target.executemany(delete_sql, [convert(k) for k in table_plan.deletes])
            done.deleted = len(table_plan.deletes)
        return done

    @staticmethod
    def _safe_rollback(target):
        try:
            target.rollback()
        except Exception:  # откат не удался, исходная ошибка важнее
            pass

    def _log(self, plan, result):
        entry = {
            "time": result.finished_at,
            "direction": plan.direction,
            "source": plan.source,
            "target": plan.target,
            "tables": [t.name for t in plan.tables],
            "inserted": result.inserted,
            "updated": result.updated,
            "deleted": result.deleted,
            "schema_changes": len(result.schema_sql),
            "ok": result.ok,
            "error": result.error,
        }
        self._history.append(entry)
        if self.history_path:
            with open(self.history_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(entry, ensure_ascii=False) + "\n")
