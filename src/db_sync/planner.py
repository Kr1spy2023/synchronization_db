"""План синхронизации: что нужно сделать в целевой БД, чтобы она совпала с источником."""

import hashlib
import json
from dataclasses import dataclass, field

from .adapters import is_valid_identifier
from .dbtypes import norm

PREVIEW_ROWS = 5


@dataclass
class TablePlan:
    """Изменения для одной таблицы."""

    name: str
    columns: list = field(default_factory=list)  # какие столбцы переносятся
    key_columns: list = field(default_factory=list)
    schema_sql: list = field(default_factory=list)  # CREATE TABLE / ALTER TABLE
    inserts: list = field(default_factory=list)  # строки, которых нет в целевой БД
    updates: list = field(default_factory=list)  # строки, которые отличаются
    deletes: list = field(default_factory=list)  # ключи строк, которых нет в источнике
    unchanged: int = 0
    warnings: list = field(default_factory=list)
    skipped: str = None  # причина, по которой строки таблицы не переносятся

    @property
    def n_changes(self):
        return len(self.schema_sql) + len(self.inserts) + len(self.updates) + len(self.deletes)

    def preview(self, limit=PREVIEW_ROWS):
        """Первые строки каждого вида изменений в виде словарей (для показа человеку)."""
        as_dict = lambda row: dict(zip(self.columns, row))  # noqa: E731
        return {
            "inserts": [as_dict(r) for r in self.inserts[:limit]],
            "updates": [as_dict(r) for r in self.updates[:limit]],
            "deletes": [dict(zip(self.key_columns, k)) for k in self.deletes[:limit]],
        }


@dataclass
class SyncPlan:
    """Что будет сделано при выполнении синхронизации."""

    direction: str  # "push" (локальная -> сервер) или "pull" (сервер -> локальная)
    source: str
    target: str
    delete_missing: bool = False
    tables: list = field(default_factory=list)
    warnings: list = field(default_factory=list)

    @property
    def n_inserts(self):
        return sum(len(t.inserts) for t in self.tables)

    @property
    def n_updates(self):
        return sum(len(t.updates) for t in self.tables)

    @property
    def n_deletes(self):
        return sum(len(t.deletes) for t in self.tables)

    @property
    def schema_sql(self):
        return [sql for t in self.tables for sql in t.schema_sql]

    @property
    def has_deletes(self):
        return self.n_deletes > 0

    @property
    def is_empty(self):
        return all(t.n_changes == 0 for t in self.tables)

    @property
    def signature(self):
        """Короткий отпечаток плана: если данные изменились, он изменится."""
        data = [self.direction, self.delete_missing] + [
            [t.name, t.schema_sql, len(t.inserts), len(t.updates), len(t.deletes), t.skipped]
            for t in self.tables
        ]
        return hashlib.sha256(json.dumps(data, ensure_ascii=False).encode("utf-8")).hexdigest()[:16]

    def summary_lines(self):
        lines = []
        for t in self.tables:
            if t.skipped:
                lines.append(f"{t.name}: пропущена ({t.skipped})")
            else:
                lines.append(
                    f"{t.name}: добавить {len(t.inserts)}, изменить {len(t.updates)}, "
                    f"удалить {len(t.deletes)}, без изменений {t.unchanged}"
                    + (f", схема: {len(t.schema_sql)} изм." if t.schema_sql else "")
                )
        return lines


def build_table_plan(source, target, table, delete_missing=False, schema=True):
    """Сравнивает таблицу в источнике и в целевой БД и строит план для неё."""
    plan = TablePlan(name=table)
    src_cols = source.columns(table)
    if not src_cols:
        plan.skipped = "таблицы нет в источнике"
        return plan
    if not all(is_valid_identifier(c.name) for c in src_cols):
        plan.skipped = "в таблице есть столбцы с недопустимыми именами"
        return plan

    target_exists = table in target.tables()
    tgt_cols = target.columns(table) if target_exists else []
    tgt_by_name = {c.name: c for c in tgt_cols}
    src_names = {c.name for c in src_cols}

    if not target_exists:
        if not schema:
            plan.skipped = "таблицы нет в целевой БД, а создание схемы отключено"
            return plan
        plan.schema_sql.append(target.create_table_sql(table, src_cols))
        carried = list(src_cols)
    else:
        carried = []
        for col in src_cols:
            if col.name in tgt_by_name:
                carried.append(col)
                if tgt_by_name[col.name].family != col.family:
                    plan.warnings.append(
                        f"тип столбца {col.name} отличается: "
                        f"{col.family} -> {tgt_by_name[col.name].family}"
                    )
            elif schema:
                plan.schema_sql.append(target.add_column_sql(table, col))
                carried.append(col)
            else:
                plan.warnings.append(f"столбца {col.name} нет в целевой БД, он не переносится")
        for col in tgt_cols:
            if col.name not in src_names:
                plan.warnings.append(f"в целевой БД есть лишний столбец {col.name} (не удаляется)")

    keys = sorted((c for c in src_cols if c.pk), key=lambda c: c.pk)
    plan.columns = [c.name for c in carried]
    plan.key_columns = [c.name for c in keys]
    if not keys:
        plan.skipped = "нет первичного ключа, строки не синхронизируются"
        return plan
    if target_exists and {c.name for c in tgt_cols if c.pk} != {c.name for c in keys}:
        plan.skipped = "первичные ключи в БД различаются"
        return plan
    if not set(plan.key_columns) <= set(plan.columns):
        plan.skipped = "столбцы ключа отсутствуют в целевой БД"
        return plan

    families = {c.name: c.family for c in src_cols}
    key_idx = [plan.columns.index(k) for k in plan.key_columns]

    def key_of(values, index_by_name=None):
        return tuple(norm(values[i], families[plan.key_columns[n]]) for n, i in enumerate(key_idx))

    src_rows = source.rows(table, plan.columns, plan.key_columns)
    existing = [c for c in plan.columns if c in tgt_by_name]
    tgt_rows = target.rows(table, existing, plan.key_columns) if target_exists else []
    tgt_map = {}
    for row in tgt_rows:
        values = dict(zip(existing, row))
        key = tuple(norm(values[k], families[k]) for k in plan.key_columns)
        tgt_map[key] = values

    seen = set()
    for row in src_rows:
        key = key_of(row)
        seen.add(key)
        current = tgt_map.get(key)
        if current is None:
            plan.inserts.append(tuple(row))
            continue
        same = all(
            norm(row[i], families[name]) == norm(current.get(name), families[name])
            for i, name in enumerate(plan.columns)
        )
        if same:
            plan.unchanged += 1
        else:
            plan.updates.append(tuple(row))
    if delete_missing:
        for key, values in tgt_map.items():
            if key not in seen:
                plan.deletes.append(tuple(values[k] for k in plan.key_columns))
    return plan
