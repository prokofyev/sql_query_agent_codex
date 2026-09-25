"""Инструмент проверки имён для вызова моделью.

Модель извлекает из запроса таблицы и колонки и передаёт их сюда. Инструмент
читает каталог схемы, возвращает ненайденные имена и похожие варианты замены.
Именно модель, а не инструмент, решает, как исправить запрос.
"""

from typing import Any

from langchain_core.tools import StructuredTool
from psycopg_pool import AsyncConnectionPool

from sql_query_agent.db.catalog import SchemaCatalog, load_catalog
from sql_query_agent.domain import SchemaEntities
from sql_query_agent.logging_setup import get_logger
from sql_query_agent.tools.schema_check import check_entities

logger = get_logger(__name__)

CHECK_SCHEMA_DESCRIPTION = (
    "Проверить, существуют ли таблицы и колонки из SQL-запроса в базе данных. "
    "Передай таблицы настоящими именами, алиасы парами «алиас=таблица» и колонки точно так, "
    "как они написаны в запросе, не исправляя опечатки и не решая, какой таблице принадлежит "
    "колонка. Для ненайденных имён вернутся похожие существующие названия с оценкой схожести, "
    "а для неоднозначных колонок — таблицы, в которых они есть."
)


class SchemaChecker:
    """Проверка имён со чтением каталога схемы.

    Каталог кэшируется: схема демо-базы не меняется во время прогона.
    """

    def __init__(
        self,
        pool: AsyncConnectionPool,
        *,
        schema: str = "public",
        threshold: float = 65.0,
        suggestion_limit: int = 3,
    ) -> None:
        self._pool = pool
        self._schema = schema
        self._threshold = threshold
        self._suggestion_limit = suggestion_limit
        self._catalog: SchemaCatalog | None = None

    async def catalog(self, *, refresh: bool = False) -> SchemaCatalog:
        """Вернуть каталог схемы, загрузив его при необходимости."""

        if self._catalog is None or refresh:
            self._catalog = await load_catalog(self._pool, self._schema)
        return self._catalog

    async def run(self, args: dict[str, Any]) -> dict[str, Any]:
        """Проверить переданные имена и вернуть результат словарём."""

        parsed = SchemaEntities.model_validate(args)
        catalog = await self.catalog()
        result = check_entities(
            catalog,
            parsed,
            threshold=self._threshold,
            suggestion_limit=self._suggestion_limit,
        )
        logger.info(
            "проверка имён завершена",
            tables=result.checked_tables,
            columns=result.checked_columns,
            unknown=len(result.unknown),
        )
        return result.model_dump(mode="json")

    def as_tool(self) -> StructuredTool:
        """Собрать инструмент для передачи модели."""

        return StructuredTool.from_function(
            coroutine=self.run,
            name="check_schema",
            description=CHECK_SCHEMA_DESCRIPTION,
            args_schema=SchemaEntities,
        )
