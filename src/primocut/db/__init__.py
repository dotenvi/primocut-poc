"""Persistence layer (SQLite via SQLAlchemy 2.0)."""

from .session import Database, get_database

__all__ = ["Database", "get_database"]
