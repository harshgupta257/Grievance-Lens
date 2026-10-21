"""Application configuration loaded from environment variables (and `.env`), validated at startup."""

from __future__ import annotations

import os
from collections.abc import Mapping
from pathlib import Path

from dotenv import load_dotenv
from pydantic import BaseModel, ValidationError, field_validator
from sqlalchemy.engine import make_url

PROJECT_ROOT = Path(__file__).resolve().parent.parent
MIGRATIONS_DIR = PROJECT_ROOT / "sql" / "migrations"

_URL_PARTS = ("POSTGRES_USER", "POSTGRES_PASSWORD", "POSTGRES_DB")


class ConfigError(RuntimeError):
    """Raised when required configuration is missing or invalid."""


class Settings(BaseModel):
    """Validated settings. Only database settings exist so far. LLM settings arrive in later phases and
    will stay optional, so deterministic analytics never need an API key."""

    database_url: str

    @field_validator("database_url")
    @classmethod
    def _must_be_postgres(cls, value: str) -> str:
        try:
            url = make_url(value)
        except Exception as exc:  # SQLAlchemy raises ArgumentError for malformed URLs
            raise ValueError(f"not a valid database URL: {exc}") from exc
        if url.get_backend_name() != "postgresql":
            raise ValueError(f"expected a postgresql URL, got backend '{url.get_backend_name()}'")
        return value

    def redacted_url(self) -> str:
        """The database URL with the password hidden: safe for logs and console output."""
        return redact_url(self.database_url)


def redact_url(url: str) -> str:
    return make_url(url).render_as_string(hide_password=True)


def build_database_url(env: Mapping[str, str]) -> str:
    """Return DATABASE_URL if set, otherwise build it from the POSTGRES_* variables used by Docker Compose."""
    if env.get("DATABASE_URL"):
        return env["DATABASE_URL"]
    missing = [name for name in _URL_PARTS if not env.get(name)]
    if missing:
        raise ConfigError(
            "Database is not configured. Set DATABASE_URL, or all of: "
            + ", ".join(_URL_PARTS)
            + f" (missing: {', '.join(missing)}). Copy .env.example to .env to start."
        )
    host = env.get("POSTGRES_HOST", "127.0.0.1")
    port = env.get("POSTGRES_HOST_PORT", "5432")
    return (
        make_url("postgresql+psycopg2://")
        .set(
            username=env["POSTGRES_USER"],
            password=env["POSTGRES_PASSWORD"],
            host=host,
            port=int(port),
            database=env["POSTGRES_DB"],
        )
        .render_as_string(hide_password=False)
    )


def load_settings(env: Mapping[str, str] | None = None, env_file: Path | None = None) -> Settings:
    """Load settings from `env` (defaults to os.environ, after reading `.env`) and validate them.

    Variables already set in the real environment take precedence over values in `.env`.
    """
    if env is None:
        load_dotenv(env_file or PROJECT_ROOT / ".env", override=False)
        env = os.environ
    try:
        return Settings(database_url=build_database_url(env))
    except ValidationError as exc:
        raise ConfigError(f"Invalid configuration: {exc.errors()[0]['msg']}") from exc
