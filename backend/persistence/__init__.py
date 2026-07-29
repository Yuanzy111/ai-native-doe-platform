"""Persistence layer: SQLite repository and transaction boundaries (architecture v0.2, §7)."""

from backend.persistence.repository import PersistenceError, SqliteRepository

__all__ = ["PersistenceError", "SqliteRepository"]
