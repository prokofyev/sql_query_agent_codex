"""Схемы служебных таблиц в целевой базе.

Демо-данные живут в схеме `public` и не изменяются. Служебные таблицы
размещаются отдельно: `agent` — checkpoint'ы LangGraph, `metrics` — журнал
прогонов. Обе схемы создаются идемпотентно.
"""

from psycopg import AsyncConnection, sql
from psycopg_pool import AsyncConnectionPool

from sql_query_agent.logging_setup import get_logger

logger = get_logger(__name__)

CHECKPOINT_SCHEMA = "agent"
METRICS_SCHEMA = "metrics"

_CREATE_SCHEMA_SQL = "CREATE SCHEMA IF NOT EXISTS {}"


async def create_schema(dsn: str, schema: str) -> None:
    """Создать схему, если её ещё нет, через отдельное подключение.

    Нужна перед открытием checkpointer: `search_path` на несуществующую
    схему не даёт создавать в ней таблицы.
    """

    connection = await AsyncConnection.connect(dsn, autocommit=True)
    try:
        await connection.execute(
            sql.SQL(_CREATE_SCHEMA_SQL).format(sql.Identifier(schema))
        )
    finally:
        await connection.close()
    logger.info("схема готова", schema=schema)


async def create_schema_via_pool(pool: AsyncConnectionPool, schema: str) -> None:
    """Создать схему через существующий пул."""

    async with pool.connection() as connection:
        await connection.execute(
            sql.SQL(_CREATE_SCHEMA_SQL).format(sql.Identifier(schema))
        )
    logger.info("схема готова", schema=schema)


async def ensure_schemas(
    dsn: str,
    *,
    agent_schema: str = CHECKPOINT_SCHEMA,
    metrics_schema: str = METRICS_SCHEMA,
) -> None:
    """Создать обе служебные схемы."""

    await create_schema(dsn, agent_schema)
    await create_schema(dsn, metrics_schema)


async def list_tables(dsn: str, schema: str) -> set[str]:
    """Вернуть имена таблиц указанной схемы."""

    connection = await AsyncConnection.connect(dsn, autocommit=True)
    try:
        cursor = await connection.execute(
            "SELECT table_name FROM information_schema.tables WHERE table_schema = %s",
            (schema,),
        )
        rows = await cursor.fetchall()
    finally:
        await connection.close()
    return {row[0] for row in rows}
