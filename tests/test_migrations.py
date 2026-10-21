"""Unit tests for migration discovery (no database needed)."""

import pytest

from src.db import MigrationError, discover_migrations, get_engine


def test_project_migrations_are_valid_and_ordered():
    migrations = discover_migrations()
    versions = [m.version for m in migrations]
    assert versions, "expected at least one migration in sql/migrations"
    assert versions == sorted(versions)
    assert versions[0] == "0001"


def test_discovery_sorts_and_hashes(tmp_path):
    (tmp_path / "0002_second.sql").write_text("SELECT 2;")
    (tmp_path / "0001_first.sql").write_text("SELECT 1;")
    migrations = discover_migrations(tmp_path)
    assert [m.version for m in migrations] == ["0001", "0002"]
    assert all(len(m.sha256) == 64 for m in migrations)


def test_duplicate_versions_are_rejected(tmp_path):
    (tmp_path / "0001_a.sql").write_text("SELECT 1;")
    (tmp_path / "0001_b.sql").write_text("SELECT 1;")
    with pytest.raises(MigrationError, match="duplicate"):
        discover_migrations(tmp_path)


@pytest.mark.parametrize("name", ["1_short.sql", "0001-Dash.sql", "0001_Upper.sql"])
def test_bad_filenames_are_rejected(tmp_path, name):
    (tmp_path / name).write_text("SELECT 1;")
    with pytest.raises(MigrationError, match="bad migration filename"):
        discover_migrations(tmp_path)


def test_unsafe_search_path_is_rejected():
    with pytest.raises(ValueError, match="unsafe"):
        get_engine("postgresql+psycopg2://u:p@127.0.0.1/d", search_path="x; DROP TABLE reports")
