"""Доменные модели: имена, кандидаты, замеры, решения пользователя.

Модели описаны так, чтобы аргументы инструментов оставались простыми:
без `Union` и `anyOf`, которые не поддерживает GigaChat.
"""

from enum import StrEnum

from pydantic import BaseModel, Field


class ColumnNames(BaseModel):
    """Колонки одной таблицы в том виде, как они записаны в запросе."""

    table: str = Field(description="Имя таблицы, как оно написано в запросе")
    columns: list[str] = Field(
        default_factory=list,
        description="Имена колонок этой таблицы, как они написаны в запросе",
    )


class SchemaEntities(BaseModel):
    """Аргумент инструмента проверки имён."""

    entities: list[ColumnNames] = Field(
        default_factory=list,
        description="Таблицы и колонки, извлечённые из запроса без исправления опечаток",
    )


class NameCandidate(BaseModel):
    """Похожее существующее имя."""

    name: str
    score: float


class UnknownNameKind(StrEnum):
    """Что именно не найдено."""

    TABLE = "table"
    COLUMN = "column"


class UnknownName(BaseModel):
    """Ненайденное имя и предложенные замены."""

    kind: UnknownNameKind
    table: str
    name: str
    candidates: list[NameCandidate] = Field(default_factory=list)


class SchemaCheckResult(BaseModel):
    """Результат работы инструмента проверки имён."""

    ok: bool
    checked_tables: int = 0
    checked_columns: int = 0
    unknown: list[UnknownName] = Field(default_factory=list)
    unknown_tables: list[str] = Field(default_factory=list)


class TimingStats(BaseModel):
    """Статистика замеров одного прогона."""

    samples: list[float] = Field(default_factory=list)
    minimum_ms: float = 0.0
    median_ms: float = 0.0
    maximum_ms: float = 0.0

    @property
    def runs(self) -> int:
        """Сколько замеров учтено."""

        return len(self.samples)
