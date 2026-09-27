"""Настройка структурного логирования.

Секреты маскируются фильтром процессора, а содержимое ответов модели и
инструментов пишется только на уровне `DEBUG`. Логгеры сторонних библиотек
ограничены уровнем `WARNING`: иначе подробный режим тонет в их шуме, а драйвер
базы может записать в лог текст выполняемого запроса.
"""

import logging
import sys
from typing import Any

import structlog

SENSITIVE_KEYS = frozenset({"credentials", "password", "dsn", "token", "secret"})
REDACTED = "<redacted>"
THIRD_PARTY_LEVEL = "WARNING"
"""Уровень для логгеров сторонних библиотек."""

THIRD_PARTY_LOGGERS = ("httpx", "httpcore", "urllib3", "psycopg", "langchain", "langchain_gigachat")
"""Логгеры, чей подробный вывод не нужен при разборе прогонов.

Список нужен для библиотек, которые не пробрасывают записи в корневой логгер.
Остальные закрывает сам корневой уровень: наши логи идут мимо него, поэтому
`DEBUG` приложения и `WARNING` для чужих библиотек не конфликтуют.
"""


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


def silence_third_party() -> None:
    """Ограничить подробный вывод сторонних библиотек."""

    level = getattr(logging, THIRD_PARTY_LEVEL)
    for name in THIRD_PARTY_LOGGERS:
        logging.getLogger(name).setLevel(level)


def configure_logging(*, level: str = "INFO", json_logs: bool = False) -> None:
    """Настроить structlog и стандартный logging.

    Уровень приложения задаётся structlog, а корневой логгер держится на
    `WARNING`: записи чужих библиотек пишутся через стандартный `logging`, и
    иначе подробный режим тонул бы в их отладке. Логгеры с собственными
    обработчиками (uvicorn) настройку не теряют.

    Уровень логгеров не кэшируется: повторный вызов с другим уровнем должен
    действовать, иначе переключение `INFO` → `DEBUG` не давало бы эффекта.
    """

    app_level = getattr(logging, level.upper(), logging.INFO)
    logging.basicConfig(
        format="%(message)s",
        stream=sys.stdout,
        level=getattr(logging, THIRD_PARTY_LEVEL),
        force=True,
    )
    silence_third_party()

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
        wrapper_class=structlog.make_filtering_bound_logger(app_level),
        logger_factory=structlog.PrintLoggerFactory(),
        cache_logger_on_first_use=False,
    )


def get_logger(name: str | None = None) -> Any:
    """Вернуть структурный логгер."""

    return structlog.get_logger(name)
