"""Conexão com o banco + migrations simples.

Estratégia (migrations simples, sem Alembic): o schema base é criado por
`create_all` e carimbado como versão 1; deltas futuros ficam em arquivos
`migrations/NNN_*.sql` aplicados em ordem e rastreados em `schema_migrations`.
"""

from __future__ import annotations

import re
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from sqlalchemy import create_engine, event, select, text
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
        def _fk_on(dbapi_conn, _rec):  # habilita FKs no SQLite
            dbapi_conn.execute("PRAGMA foreign_keys=ON")

    @classmethod
    def from_settings(cls, settings: Settings | None = None) -> "Database":
        settings = settings or get_settings()
        settings.db_path.parent.mkdir(parents=True, exist_ok=True)
        return cls(f"sqlite:///{settings.db_path}")

    def init(self) -> None:
        """Cria o schema base e aplica migrations pendentes (idempotente)."""
        Base.metadata.create_all(self.engine)
        with self.session() as s:
            current = s.execute(select(SchemaMigration.version).order_by(
                SchemaMigration.version.desc())).scalars().first()
            if current is None:
                s.add(SchemaMigration(version=_BASE_VERSION))
                current = _BASE_VERSION
            for version, sql_path in _pending_sql(current):
                logger.info("Aplicando migration %03d (%s)", version, sql_path.name)
                for stmt in _split_sql(sql_path.read_text(encoding="utf-8")):
                    s.execute(text(stmt))
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
    """Database singleton (para app/worker). Testes criam instâncias próprias."""
    global _db
    if _db is None:
        _db = Database.from_settings(settings)
        _db.init()
    return _db
