"""Тесты настройки логирования: уровень приложения и шум сторонних библиотек.

Проверяется, что подробный режим включает содержимое ответов, но не пропускает
отладочный вывод чужих библиотек и текст SQL из драйвера базы.
"""

import logging
from typing import Any

import pytest

from sql_query_agent.logging_setup import (
    REDACTED,
    THIRD_PARTY_LEVEL,
    THIRD_PARTY_LOGGERS,
    configure_logging,
    get_logger,
    redact_sensitive,
)

SQL_MARK = "select hidden_table from t"


@pytest.fixture(autouse=True)
def _restore_logging() -> Any:
    """Вернуть настройки логирования после теста."""

    yield
    for name in THIRD_PARTY_LOGGERS:
        logging.getLogger(name).setLevel(logging.NOTSET)
    logging.basicConfig(level=logging.WARNING, force=True)


def _capture(capsys: pytest.CaptureFixture[str], **kwargs: Any) -> str:
    """Настроить логирование и вернуть всё, что попало в вывод."""

    configure_logging(**kwargs)
    get_logger("probe").debug("наш DEBUG", names=["brand_name"])
    get_logger("probe").info("наш INFO")
    logging.getLogger("httpx").debug("httpx debug")
    logging.getLogger("langchain").info("langchain info")
    logging.getLogger("psycopg").info(SQL_MARK)
    logging.getLogger("asyncio").debug("asyncio debug")
    logging.getLogger("nicegui").info("nicegui info")
    logging.getLogger("psycopg").warning("psycopg warning")
    return capsys.readouterr().out


def test_debug_level_writes_payloads(capsys: pytest.CaptureFixture[str]) -> None:
    """На уровне `DEBUG` содержимое ответов попадает в лог."""

    output = _capture(capsys, level="DEBUG")

    assert "наш DEBUG" in output
    assert "brand_name" in output


def test_info_level_skips_payloads(capsys: pytest.CaptureFixture[str]) -> None:
    """На рабочем уровне `INFO` содержимое не записывается."""

    output = _capture(capsys, level="INFO")

    assert "наш INFO" in output
    assert "brand_name" not in output


def test_third_party_noise_is_silenced(capsys: pytest.CaptureFixture[str]) -> None:
    """Отладочный вывод чужих библиотек и текст SQL драйвера не попадают в лог."""

    output = _capture(capsys, level="DEBUG")

    assert "httpx debug" not in output
    assert "langchain info" not in output
    assert "asyncio debug" not in output
    assert "nicegui info" not in output
    assert SQL_MARK not in output


def test_third_party_warnings_are_kept(capsys: pytest.CaptureFixture[str]) -> None:
    """Предупреждения сторонних библиотек остаются видимыми."""

    output = _capture(capsys, level="DEBUG")

    assert "psycopg warning" in output


def test_third_party_loggers_are_capped() -> None:
    """Уровень сторонних логгеров поднят до `WARNING`."""

    configure_logging(level="DEBUG")

    expected = getattr(logging, THIRD_PARTY_LEVEL)
    for name in THIRD_PARTY_LOGGERS:
        assert logging.getLogger(name).level == expected


def test_secrets_are_redacted() -> None:
    """Чувствительные значения маскируются."""

    event = redact_sensitive(None, "info", {"credentials": "секрет", "dsn": "строка"})

    assert event == {"credentials": REDACTED, "dsn": REDACTED}


def test_third_party_cap_does_not_hide_application_logs(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Ограничение чужих логгеров не поднимает уровень логов приложения."""

    output = _capture(capsys, level="DEBUG")

    assert "наш DEBUG" in output
    assert logging.getLogger("httpx").level > logging.DEBUG


def test_root_level_caps_unlisted_libraries(capsys: pytest.CaptureFixture[str]) -> None:
    """Корневой уровень закрывает библиотеки, которых нет в списке."""

    output = _capture(capsys, level="DEBUG")

    assert "asyncio debug" not in output
    assert "nicegui info" not in output


def test_level_switch_takes_effect(capsys: pytest.CaptureFixture[str]) -> None:
    """Переключение уровня на ходу действует: уровень логгеров не кэшируется."""

    configure_logging(level="INFO")
    get_logger("switch").debug("подробность")
    assert "подробность" not in capsys.readouterr().out

    configure_logging(level="DEBUG")
    get_logger("switch").debug("подробность")
    assert "подробность" in capsys.readouterr().out
