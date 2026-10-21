import pytest

from src.config import ConfigError, load_settings

PARTS = {"POSTGRES_USER": "u", "POSTGRES_PASSWORD": "s3cret", "POSTGRES_DB": "d"}


def test_builds_url_from_compose_variables_with_defaults():
    settings = load_settings(env=PARTS)
    assert settings.database_url == "postgresql+psycopg2://u:s3cret@127.0.0.1:5432/d"


def test_explicit_database_url_wins():
    env = {**PARTS, "DATABASE_URL": "postgresql+psycopg2://a:b@db.local:6543/x"}
    assert load_settings(env=env).database_url.endswith("@db.local:6543/x")


def test_custom_host_and_port():
    env = {**PARTS, "POSTGRES_HOST": "localhost", "POSTGRES_HOST_PORT": "5544"}
    assert load_settings(env=env).database_url == "postgresql+psycopg2://u:s3cret@localhost:5544/d"


def test_missing_variables_are_reported_by_name():
    with pytest.raises(ConfigError, match="POSTGRES_PASSWORD"):
        load_settings(env={"POSTGRES_USER": "u", "POSTGRES_DB": "d"})


def test_non_postgres_url_is_rejected():
    with pytest.raises(ConfigError, match="postgresql"):
        load_settings(env={"DATABASE_URL": "sqlite:///local.db"})


def test_redacted_url_hides_password():
    settings = load_settings(env=PARTS)
    assert "s3cret" not in settings.redacted_url()
    assert "***" in settings.redacted_url()


def test_password_with_special_characters_is_escaped():
    env = {**PARTS, "POSTGRES_PASSWORD": "p@ss:w/rd"}
    url = load_settings(env=env).database_url
    assert "p%40ss%3Aw%2Frd" in url
