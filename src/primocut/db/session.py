"""Database connection + simple migrations.

Strategy (simple migrations, no Alembic): the base schema is created by
`create_all` and stamped as version 1; future deltas live in
`migrations/NNN_*.sql` files applied in order and tracked in `schema_migrations`.
"""

from __future__ import annotations

import re
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from sqlalchemy import create_engine, event, select, text
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session, sessionmaker

from ..config import Settings, get_settings
from ..logging_conf import get_logger
from .models import Base, SchemaMigration

logger = get_logger(__name__)

_MIGRATIONS_DIR = Path(__file__).parent / "migrations"
_BASE_VERSION = 1


class Database:
    def __init__(self, url: str) -> None:
        self.engine = create_engine(url, future=True)
        self._Session = sessionmaker(self.engine, expire_on_commit=False, future=True)

        @event.listens_for(self.engine, "connect")
        def _fk_on(dbapi_conn, _rec):  # enable FKs on SQLite
            dbapi_conn.execute("PRAGMA foreign_keys=ON")

    @classmethod
    def from_settings(cls, settings: Settings | None = None) -> "Database":
        settings = settings or get_settings()
        settings.db_path.parent.mkdir(parents=True, exist_ok=True)
        return cls(f"sqlite:///{settings.db_path}")

    def init(self) -> None:
        """Create the base schema and apply pending migrations (idempotent)."""
        Base.metadata.create_all(self.engine)
        with self.session() as s:
            current = s.execute(select(SchemaMigration.version).order_by(
                SchemaMigration.version.desc())).scalars().first()
            if current is None:
                s.add(SchemaMigration(version=_BASE_VERSION))
                current = _BASE_VERSION
            for version, sql_path in _pending_sql(current):
                logger.info("Applying migration %03d (%s)", version, sql_path.name)
                for stmt in _split_sql(sql_path.read_text(encoding="utf-8")):
                    try:
                        s.execute(text(stmt))
                    except OperationalError as exc:
                        # Fresh databases already match the current ORM schema from
                        # create_all(), so column-shape migrations are no-ops there:
                        # a duplicate-column ADD COLUMN, or a DROP COLUMN for a
                        # column create_all() never made.
                        msg = str(exc).lower()
                        if "duplicate column name" not in msg and "no such column" not in msg:
                            raise
                s.add(SchemaMigration(version=version))

    @contextmanager
    def session(self) -> Iterator[Session]:
        s = self._Session()
        try:
            yield s
            s.commit()
        except Exception:
            s.rollback()
            raise
        finally:
            s.close()


def _pending_sql(current: int) -> list[tuple[int, Path]]:
    if not _MIGRATIONS_DIR.exists():
        return []
    out = []
    for p in sorted(_MIGRATIONS_DIR.glob("*.sql")):
        m = re.match(r"^(\d+)_", p.name)
        if m and int(m.group(1)) > current:
            out.append((int(m.group(1)), p))
    return out


def _split_sql(sql: str) -> list[str]:
    return [s.strip() for s in sql.split(";") if s.strip()]


_db: Database | None = None


def get_database(settings: Settings | None = None) -> Database:
    """Database singleton (for app/worker). Tests create their own instances."""
    global _db
    if _db is None:
        _db = Database.from_settings(settings)
        _db.init()
    return _db
