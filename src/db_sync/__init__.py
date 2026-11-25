"""db_sync - модуль синхронизации локальной БД (SQLite) с удалённым сервером (MySQL)."""

from .adapters import Adapter, MySQLAdapter, SQLiteAdapter, connect
from .errors import ConnectionFailed, SafetyError, SyncError, ValidationError
from .planner import SyncPlan, TablePlan
from .sync import DbSync, SyncResult, TableResult

__version__ = "0.1.0"

__all__ = [
    "DbSync",
    "SyncPlan",
    "TablePlan",
    "SyncResult",
    "TableResult",
    "Adapter",
    "SQLiteAdapter",
    "MySQLAdapter",
    "connect",
    "SyncError",
    "ValidationError",
    "ConnectionFailed",
    "SafetyError",
]
