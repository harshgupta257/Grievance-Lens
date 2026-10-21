"""Shared fixtures. Integration tests are opt-in: they run only when TEST_DATABASE_URL is set."""

from __future__ import annotations

import os
import uuid
from collections.abc import Iterator

import pytest
from dotenv import load_dotenv
from sqlalchemy import Engine, text

from src.config import PROJECT_ROOT
from src.db import get_engine

load_dotenv(PROJECT_ROOT / ".env", override=False)


@pytest.fixture(scope="session")
def test_database_url() -> str:
    url = os.environ.get("TEST_DATABASE_URL")
    if not url:
        pytest.skip("integration test: set TEST_DATABASE_URL to run it (see .env.example)")
    return url


@pytest.fixture()
def isolated_engine(test_database_url: str) -> Iterator[Engine]:
    """An engine whose search_path is a fresh, empty schema. The schema is dropped afterwards, so tests
    never touch real data, even when they point at the development database."""
    schema = f"test_{uuid.uuid4().hex[:12]}"
    admin = get_engine(test_database_url)
    with admin.begin() as conn:
        conn.execute(text(f'CREATE SCHEMA "{schema}"'))
    engine = get_engine(test_database_url, search_path=schema)
    try:
        yield engine
    finally:
        engine.dispose()
        with admin.begin() as conn:
            conn.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        admin.dispose()
