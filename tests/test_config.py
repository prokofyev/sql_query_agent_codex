"""Тесты конфигурации: чтение настроек из переменных окружения."""

import pytest

from sql_query_agent.config import (
    DEFAULT_DSN,
    DEFAULT_PRESETS_PATH,
    Settings,
    get_settings,
    reset_settings_cache,
)


@pytest.fixture(autouse=True)
def _clear_settings_cache():
    reset_settings_cache()
    yield
    reset_settings_cache()


def test_defaults_are_usable_without_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Без переменных окружения настройки имеют рабочие значения по умолчанию."""

    monkeypatch.delenv("SQA_DATABASE__DSN", raising=False)
    monkeypatch.delenv("SQA_PRESETS__PATH", raising=False)
    settings = Settings(_env_file=None)

    assert settings.database.dsn == DEFAULT_DSN
    assert settings.database.schema_name == "public"
    assert settings.database.agent_schema == "agent"
    assert settings.database.metrics_schema == "metrics"
    assert settings.presets.path == DEFAULT_PRESETS_PATH


def test_nested_env_variables_are_read(monkeypatch: pytest.MonkeyPatch) -> None:
    """Вложенные настройки читаются из переменных с двойным подчёркиванием."""

    monkeypatch.setenv("SQA_DATABASE__DSN", "postgresql://user@localhost:5432/other")
    monkeypatch.setenv("SQA_VALIDATION__FUZZY_THRESHOLD", "80")
    monkeypatch.setenv("SQA_MEASUREMENT__REPEAT_RUNS", "7")
    monkeypatch.setenv("SQA_GIGACHAT__SCOPE", "GIGACHAT_API_CORP")
    monkeypatch.setenv("SQA_PRESETS__PATH", "/tmp/custom-presets.yaml")

    settings = Settings(_env_file=None)

    assert settings.database.dsn == "postgresql://user@localhost:5432/other"
    assert settings.validation.fuzzy_threshold == 80
    assert settings.measurement.repeat_runs == 7
    assert settings.gigachat.scope == "GIGACHAT_API_CORP"
    assert settings.presets.path == "/tmp/custom-presets.yaml"


def test_credentials_are_not_exposed_in_repr(monkeypatch: pytest.MonkeyPatch) -> None:
    """Секрет не должен попадать в строковое представление настроек."""

    monkeypatch.setenv("SQA_GIGACHAT__CREDENTIALS", "super-secret-value")
    settings = Settings(_env_file=None)

    assert settings.gigachat.credentials.get_secret_value() == "super-secret-value"
    assert "super-secret-value" not in repr(settings)


def test_get_settings_is_cached() -> None:
    """`get_settings` возвращает один и тот же объект до сброса кэша."""

    assert get_settings() is get_settings()
