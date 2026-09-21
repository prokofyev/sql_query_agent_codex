"""Чтение схемы базы из `information_schema`.

Источник данных — только `information_schema`. Подсказки СУБД о похожих
именах, возвращаемые в тексте ошибок, не используются.
"""

from collections.abc import Iterable
from dataclasses import dataclass, field

from psycopg_pool import AsyncConnectionPool


@dataclass(slots=True)
class SchemaCatalog:
    """Таблицы схемы и их колонки."""

    tables: dict[str, set[str]] = field(default_factory=dict)

    def has_table(self, table: str) -> bool:
        """Есть ли такая таблица."""

        return table in self.tables

    def has_column(self, table: str, column: str) -> bool:
        """Есть ли такая колонка в таблице."""

        return column in self.tables.get(table, set())

    def table_names(self) -> list[str]:
        """Все имена таблиц."""

        return list(self.tables)

    def column_names(self, table: str) -> list[str]:
        """Имена колонок таблицы."""

        return list(self.tables.get(table, set()))

    @classmethod
    def from_rows(cls, rows: Iterable[tuple[str, str]]) -> "SchemaCatalog":
        """Собрать каталог из пар «таблица — колонка»."""

        tables: dict[str, set[str]] = {}
        for table_name, column_name in rows:
            tables.setdefault(table_name, set()).add(column_name)
        return cls(tables=tables)


async def load_catalog(pool: AsyncConnectionPool, schema: str = "public") -> SchemaCatalog:
    """Загрузить таблицы и колонки схемы."""

    async with pool.connection() as connection:
        cursor = await connection.execute(
            """
            SELECT table_name, column_name
            FROM information_schema.columns
            WHERE table_schema = %s
            ORDER BY table_name, ordinal_position
            """,
            (schema,),
        )
        rows = await cursor.fetchall()
    return SchemaCatalog.from_rows(rows)
