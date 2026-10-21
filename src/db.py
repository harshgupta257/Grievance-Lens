"""Database engine creation and a minimal, checksum-verified SQL migration runner.

Migrations are plain SQL files named `NNNN_description.sql` in `sql/migrations/`. Each one runs in its own
transaction and is recorded in `schema_migrations` with its SHA-256. Re-running applies only new files.
If an already-applied file has been edited, the runner refuses to continue, because silently diverging
schemas are worse than a loud error.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import Engine, create_engine, text

from src.config import MIGRATIONS_DIR

_MIGRATION_NAME = re.compile(r"^(\d{4})_[a-z0-9_]+\.sql$")


class MigrationError(RuntimeError):
    """Raised for invalid migration files or a mismatch with what the database has recorded."""


@dataclass(frozen=True)
class Migration:
    version: str
    path: Path
    sha256: str

    @property
    def sql(self) -> str:
        return self.path.read_text(encoding="utf-8")


def get_engine(database_url: str, search_path: str | None = None) -> Engine:
    """Create an engine. `search_path` scopes all unqualified names to a schema (used by the tests)."""
    connect_args = {"connect_timeout": 10}
    if search_path:
        if not re.fullmatch(r"[a-z_][a-z0-9_]*", search_path):
            raise ValueError(f"unsafe schema name: {search_path!r}")
        connect_args["options"] = f"-csearch_path={search_path}"
    return create_engine(database_url, pool_pre_ping=True, connect_args=connect_args)


def discover_migrations(directory: Path = MIGRATIONS_DIR) -> list[Migration]:
    """Return the migration files in version order, validating their names and uniqueness."""
    migrations: list[Migration] = []
    seen: dict[str, str] = {}
    for path in sorted(directory.glob("*.sql")):
        match = _MIGRATION_NAME.match(path.name)
        if not match:
            raise MigrationError(f"bad migration filename {path.name!r}: expected NNNN_lowercase_name.sql")
        version = match.group(1)
        if version in seen:
            raise MigrationError(f"duplicate migration version {version}: {seen[version]} and {path.name}")
        seen[version] = path.name
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        migrations.append(Migration(version=version, path=path, sha256=digest))
    return migrations


_CREATE_LEDGER = """
CREATE TABLE IF NOT EXISTS schema_migrations (
    version     text PRIMARY KEY,
    filename    text NOT NULL,
    sha256      char(64) NOT NULL,
    applied_at  timestamptz NOT NULL DEFAULT now()
)
"""


def applied_migrations(engine: Engine) -> dict[str, str]:
    """Return {version: sha256} for the migrations recorded in the database (empty if none)."""
    with engine.begin() as conn:
        conn.execute(text(_CREATE_LEDGER))
        rows = conn.execute(text("SELECT version, sha256 FROM schema_migrations")).all()
    return {version: digest.strip() for version, digest in rows}


def pending_migrations(engine: Engine, directory: Path = MIGRATIONS_DIR) -> list[Migration]:
    """Return the migrations not yet applied. Raise if an applied file changed or is missing."""
    available = discover_migrations(directory)
    applied = applied_migrations(engine)
    by_version = {m.version: m for m in available}
    for version, digest in applied.items():
        if version not in by_version:
            raise MigrationError(f"migration {version} is recorded in the database but its file is missing")
        if by_version[version].sha256 != digest:
            raise MigrationError(
                f"migration {by_version[version].path.name} was edited after being applied. "
                "Add a new migration instead of changing an applied one."
            )
    return [m for m in available if m.version not in applied]


def migrate(engine: Engine, directory: Path = MIGRATIONS_DIR) -> list[Migration]:
    """Apply all pending migrations, each in its own transaction. Return the ones applied."""
    todo = pending_migrations(engine, directory)
    for migration in todo:
        with engine.begin() as conn:
            # Raw DBAPI cursor: runs the multi-statement file as-is, with no parameter interpolation
            # (so '%' in SQL literals is safe).
            conn.connection.cursor().execute(migration.sql)
            conn.execute(
                text("INSERT INTO schema_migrations (version, filename, sha256) VALUES (:v, :f, :s)"),
                {"v": migration.version, "f": migration.path.name, "s": migration.sha256},
            )
    return todo
