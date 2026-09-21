"""Хранилище checkpoint'ов LangGraph в отдельной схеме.

`AsyncPostgresSaver` создаёт таблицы без квалификатора схемы и использует
`CREATE INDEX CONCURRENTLY`, поэтому:

1. целевая схема задаётся через `search_path` в строке подключения;
2. `setup()` выполняется на autocommit-соединении.
"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any
from urllib.parse import urlencode, urlsplit, urlunsplit

from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from psycopg import AsyncConnection
from psycopg.rows import dict_row

from sql_query_agent.db.schemas import create_schema
from sql_query_agent.logging_setup import get_logger

logger = get_logger(__name__)


def with_search_path(dsn: str, schema: str) -> str:
    """Добавить `search_path` к строке подключения.

    Существующие параметры сохраняются; `options` перезаписывается, потому
    что важен итоговый `search_path`.
    """

    parts = urlsplit(dsn)
    query = dict(
        pair.split("=", 1) for pair in parts.query.split("&") if "=" in pair
    )
    query["options"] = f"-csearch_path={schema}"
    return urlunsplit(
        (parts.scheme, parts.netloc, parts.path, urlencode(query), parts.fragment)
    )


@asynccontextmanager
async def open_checkpointer(dsn: str, schema: str) -> AsyncIterator[AsyncPostgresSaver]:
    """Открыть checkpointer, работающий в указанной схеме.

    Соединение — autocommit: иначе `setup()` не сможет выполнить
    `CREATE INDEX CONCURRENTLY`.
    """

    await create_schema(dsn, schema)
    schema_dsn = with_search_path(dsn, schema)
    connection = await AsyncConnection.connect(
        schema_dsn,
        autocommit=True,
        prepare_threshold=0,
        row_factory=dict_row,
    )
    try:
        saver = AsyncPostgresSaver(conn=connection)
        await saver.setup()
        logger.info("checkpointer готов", schema=schema)
        yield saver
    finally:
        await connection.close()


async def checkpoint_tables(dsn: str, schema: str) -> set[str]:
    """Вернуть имена таблиц checkpoint'ов, созданных в схеме."""

    connection: AsyncConnection[Any] = await AsyncConnection.connect(
        dsn, autocommit=True, prepare_threshold=0, row_factory=dict_row
    )
    try:
        cursor = await connection.execute(
            "SELECT table_name FROM information_schema.tables WHERE table_schema = %s",
            (schema,),
        )
        rows = await cursor.fetchall()
    finally:
        await connection.close()
    return {row["table_name"] for row in rows}
