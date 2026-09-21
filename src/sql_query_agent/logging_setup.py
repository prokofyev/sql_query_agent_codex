"""Настройка структурного логирования.

Секреты и тексты запросов в логи не попадают: `credentials` маскируется
явным фильтром процессора.
"""

import logging
import sys
from typing import Any

import structlog

SENSITIVE_KEYS = frozenset({"credentials", "password", "dsn", "token", "secret"})
REDACTED = "<redacted>"


def redact_sensitive(
    _logger: Any,
    _method_name: str,
    event_dict: dict[str, Any],
) -> dict[str, Any]:
    """Заменить значения чувствительных ключей на заглушку."""

    for key in list(event_dict):
        if key.lower() in SENSITIVE_KEYS:
            event_dict[key] = REDACTED
    return event_dict


def configure_logging(*, level: str = "INFO", json_logs: bool = False) -> None:
    """Настроить structlog и стандартный logging."""

    logging.basicConfig(
        format="%(message)s",
        stream=sys.stdout,
        level=getattr(logging, level.upper(), logging.INFO),
        force=True,
    )

    renderer: Any = (
        structlog.processors.JSONRenderer(ensure_ascii=False)
        if json_logs
        else structlog.dev.ConsoleRenderer()
    )

    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
            redact_sensitive,
            renderer,
        ],
        wrapper_class=structlog.make_filtering_bound_logger(
            getattr(logging, level.upper(), logging.INFO)
        ),
        logger_factory=structlog.PrintLoggerFactory(),
        cache_logger_on_first_use=True,
    )


def get_logger(name: str | None = None) -> Any:
    """Вернуть структурный логгер."""

    return structlog.get_logger(name)
