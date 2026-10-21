"""Create or replace the analysis views in sql/views/ (idempotent, applied in file-name order).

Views are not migrations: they hold no data and are expected to evolve with the analyses, so they are
re-created on every analysis run instead of being versioned in sql/migrations/ (DESIGN.md D-028).
"""

from __future__ import annotations

from pathlib import Path

from sqlalchemy import Engine

from src.config import PROJECT_ROOT

VIEWS_DIR = PROJECT_ROOT / "sql" / "views"


def apply_views(engine: Engine, directory: Path = VIEWS_DIR) -> list[str]:
    applied = []
    with engine.begin() as conn:
        for path in sorted(directory.glob("*.sql")):
            conn.connection.cursor().execute(path.read_text(encoding="utf-8"))
            applied.append(path.name)
    return applied
