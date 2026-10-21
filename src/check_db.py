"""Check database connectivity and migration status, and optionally apply migrations.

Usage (PowerShell, from the project root, with the venv active):
    python -m src.check_db            # connect, report server version and migration status
    python -m src.check_db --migrate  # also apply pending migrations

Exit codes: 0 = OK and up to date, 1 = configuration or connection error, 2 = migrations pending.
"""

from __future__ import annotations

import argparse
import sys

from sqlalchemy import text
from sqlalchemy.exc import OperationalError

from src.config import ConfigError, load_settings
from src.db import MigrationError, get_engine, migrate, pending_migrations


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--migrate", action="store_true", help="apply pending migrations")
    args = parser.parse_args(argv)

    try:
        settings = load_settings()
    except ConfigError as exc:
        print(f"[config] {exc}", file=sys.stderr)
        return 1

    print(f"Database: {settings.redacted_url()}")
    engine = get_engine(settings.database_url)
    try:
        with engine.connect() as conn:
            version = conn.execute(text("SHOW server_version")).scalar_one()
        print(f"Connected: PostgreSQL {version}")
        if args.migrate:
            for m in migrate(engine):
                print(f"Applied migration {m.path.name}")
        pending = pending_migrations(engine)
    except OperationalError as exc:
        print(f"[connection] Could not connect: {exc.orig}".rstrip(), file=sys.stderr)
        print("Is the database running? Try: docker compose ps", file=sys.stderr)
        return 1
    except MigrationError as exc:
        print(f"[migrations] {exc}", file=sys.stderr)
        return 1
    finally:
        engine.dispose()

    if pending:
        print("Pending migrations: " + ", ".join(m.path.name for m in pending))
        print("Run: python -m src.check_db --migrate")
        return 2
    print("Schema is up to date.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
