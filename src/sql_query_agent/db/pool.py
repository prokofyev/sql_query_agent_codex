"""Пул асинхронных подключений к целевой базе."""

from psycopg_pool import AsyncConnectionPool

from sql_query_agent.logging_setup import get_logger

logger = get_logger(__name__)


def create_pool(dsn: str, *, min_size: int = 1, max_size: int = 4) -> AsyncConnectionPool:
    """Создать пул подключений (без открытия)."""

    return AsyncConnectionPool(
        conninfo=dsn,
        min_size=min_size,
        max_size=max_size,
        open=False,
    )


async def open_pool(pool: AsyncConnectionPool) -> AsyncConnectionPool:
    """Открыть пул и дождаться готовности соединений."""

    await pool.open(wait=True)
    logger.info("пул подключений открыт")
    return pool
