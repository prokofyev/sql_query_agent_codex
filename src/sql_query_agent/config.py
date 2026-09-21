"""Конфигурация приложения на pydantic-settings.

Настройки читаются из переменных окружения с префиксом `SQA_` и из файла
`.env`. Вложенность задаётся двойным подчёркиванием: `SQA_DATABASE__DSN`,
`SQA_GIGACHAT__CREDENTIALS`.
"""

from functools import lru_cache

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

DEFAULT_DSN = "postgresql://prokofyev@localhost:5432/products"


class DatabaseSettings(BaseSettings):
    """Параметры подключения к целевой базе с демо-данными."""

    dsn: str = DEFAULT_DSN
    schema_name: str = "public"
    """Схема с демо-данными: таблицы `brand`, `product`, `sku` и другие."""

    agent_schema: str = "agent"
    """Схема для таблиц checkpoint'ов LangGraph."""

    metrics_schema: str = "metrics"
    """Схема для журнала прогонов."""

    statement_timeout_ms: int = Field(default=5_000, gt=0)
    lock_timeout_ms: int = Field(default=1_000, gt=0)


class GigaChatSettings(BaseSettings):
    """Параметры доступа к GigaChat."""

    credentials: SecretStr = SecretStr("")
    scope: str = "GIGACHAT_API_PERS"
    model: str = "GigaChat"
    verify_ssl: bool = True
    timeout_seconds: float = Field(default=60.0, gt=0)
    max_retries: int = Field(default=3, ge=0)


class ValidationSettings(BaseSettings):
    """Параметры проверки имён."""

    fuzzy_threshold: float = Field(default=65.0, ge=0.0, le=100.0)
    """Порог схожести rapidfuzz: ниже него кандидат не предлагается."""

    suggestion_limit: int = Field(default=3, ge=1, le=10)


class MeasurementSettings(BaseSettings):
    """Параметры замера времени выполнения."""

    warmup_runs: int = Field(default=1, ge=0)
    """Прогревочные прогоны: их результат не попадает в статистику."""

    repeat_runs: int = Field(default=3, ge=1)
    """Число замеряемых прогонов."""

    significance_ratio: float = Field(default=1.10, ge=1.0)
    """Во сколько раз запрос должен ускориться, чтобы ускорение считалось значимым."""


class ObservabilitySettings(BaseSettings):
    """Параметры логирования."""

    log_level: str = "INFO"
    json_logs: bool = False


class Settings(BaseSettings):
    """Корневые настройки приложения."""

    model_config = SettingsConfigDict(
        env_prefix="SQA_",
        env_nested_delimiter="__",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    database: DatabaseSettings = Field(default_factory=DatabaseSettings)
    gigachat: GigaChatSettings = Field(default_factory=GigaChatSettings)
    validation: ValidationSettings = Field(default_factory=ValidationSettings)
    measurement: MeasurementSettings = Field(default_factory=MeasurementSettings)
    observability: ObservabilitySettings = Field(default_factory=ObservabilitySettings)


@lru_cache
def get_settings() -> Settings:
    """Вернуть настройки приложения (кэшируются на процесс)."""

    return Settings()


def reset_settings_cache() -> None:
    """Сбросить кэш настроек — нужно тестам."""

    get_settings.cache_clear()
