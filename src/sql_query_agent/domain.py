"""Доменные модели: имена, кандидаты, замеры, решения пользователя.

Модели описаны так, чтобы аргументы инструментов оставались простыми:
без `Union` и `anyOf`, которые не поддерживает GigaChat.
"""

from enum import StrEnum

from pydantic import BaseModel, Field, field_validator


class SchemaEntities(BaseModel):
    """Аргумент инструмента проверки имён.

    Модель перечисляет то, что видит в запросе, и не решает, какой таблице
    принадлежит колонка: принадлежность определяет инструмент по каталогу
    схемы. Поэтому колонки передаются отдельным списком, ровно как записаны,
    а не сгруппированы по таблицам.
    """

    tables: list[str] = Field(
        default_factory=list,
        description="Настоящие имена таблиц запроса, без алиасов",
    )
    aliases: list[str] = Field(
        default_factory=list,
        description="Алиасы запроса парами «алиас=настоящая таблица», например b=brand",
    )
    columns: list[str] = Field(
        default_factory=list,
        description="Колонки запроса как написаны, с квалификатором, если он указан",
    )

    @field_validator("tables", "aliases", "columns", mode="before")
    @classmethod
    def _none_is_empty(cls, value: object) -> object:
        """Модель иногда присылает `null` вместо пустого списка."""

        return [] if value is None else value


class NameCandidate(BaseModel):
    """Похожее существующее имя."""

    name: str
    score: float


class UnknownNameKind(StrEnum):
    """Что именно не найдено."""

    TABLE = "table"
    COLUMN = "column"


class UnknownName(BaseModel):
    """Ненайденное имя и предложенные замены.

    Сюда же попадают неоднозначные колонки: имя существует в схеме, но не в
    одной таблице запроса, поэтому СУБД откажется его выполнять. Исправление —
    квалификатор, а не замена имени, поэтому такие записи отмечены отдельно и
    в качестве кандидатов несут квалифицированные имена.
    """

    kind: UnknownNameKind
    table: str
    name: str
    candidates: list[NameCandidate] = Field(default_factory=list)
    searched_tables: list[str] = Field(
        default_factory=list,
        description="Таблицы, в которых искали колонку",
    )
    ambiguous: bool = Field(
        default=False,
        description="Колонка есть в нескольких таблицах запроса",
    )

    @property
    def is_fixable(self) -> bool:
        """Есть ли чем заменить это имя."""

        return bool(self.candidates)


class SchemaCheckResult(BaseModel):
    """Результат работы инструмента проверки имён."""

    ok: bool
    checked_tables: int = 0
    checked_columns: int = 0
    unknown: list[UnknownName] = Field(default_factory=list)
    unknown_tables: list[str] = Field(default_factory=list)

    @property
    def unfixable(self) -> list[UnknownName]:
        """Ненайденные имена, для которых замен не нашлось."""

        return [item for item in self.unknown if not item.is_fixable]

    @property
    def ambiguous(self) -> list[UnknownName]:
        """Колонки, которые есть более чем в одной таблице запроса."""

        return [item for item in self.unknown if item.ambiguous]

    @property
    def is_fixable(self) -> bool:
        """Можно ли исправить запрос: замены есть у всех ненайденных имён.

        Пустой список ненайденных имён исправлять нечего, поэтому он не
        считается исправимым случаем. Неоднозначные колонки исправимы всегда:
        их лечит квалификатор.
        """

        if self.unfixable:
            return False
        return bool(self.unknown or self.ambiguous)


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
