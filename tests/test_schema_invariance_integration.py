"""Проверка неизменности демо-схемы `public`.

Все прогоны агента проходят с откатом индекса, поэтому после них схема
`public` обязана остаться такой же: те же таблицы, то же число строк и тот же
список индексов. Это последняя линия защиты от случайной записи в демо-данные.
"""

from collections.abc import AsyncIterator

import pytest
from psycopg_pool import AsyncConnectionPool

from sql_query_agent.config import Settings
from sql_query_agent.db.explain import QueryMeasurer
from sql_query_agent.db.index_apply import IndexApplier
from sql_query_agent.db.pool import create_pool, open_pool
from sql_query_agent.presets import load_presets

pytestmark = pytest.mark.integration

DEMO_TABLES = ("brand", "product", "sku", "product_size", "product_color")
PRESET_INDEX = "CREATE INDEX invariance_preset_idx ON sku (product_id)"


@pytest.fixture
async def pool(require_postgres: None) -> AsyncIterator[AsyncConnectionPool]:
    """Пул подключений к целевой базе."""

    settings = Settings(_env_file=None)
    connection_pool = create_pool(settings.database.dsn)
    await open_pool(connection_pool)
    try:
        yield connection_pool
    finally:
        await connection_pool.close()


async def _row_counts(pool: AsyncConnectionPool) -> dict[str, int]:
    """Число строк в демо-таблицах."""

    counts: dict[str, int] = {}
    async with pool.connection() as connection:
        for table in DEMO_TABLES:
            cursor = await connection.execute(f"SELECT count(*) FROM {table}")  # noqa: S608
            row = await cursor.fetchone()
            counts[table] = row[0]
    return counts


async def _indexes(pool: AsyncConnectionPool) -> list[str]:
    """Список индексов схемы `public`."""

    async with pool.connection() as connection:
        cursor = await connection.execute(
            "SELECT schemaname || '.' || indexname FROM pg_indexes "
            "WHERE schemaname = 'public' ORDER BY 1"
        )
        return [row[0] for row in await cursor.fetchall()]


async def test_public_schema_is_unchanged_after_all_presets(
    pool: AsyncConnectionPool,
) -> None:
    """После прогонов всех предустановленных запросов схема `public` не изменилась.

    Каждый запрос действительно исполняется с предложенным индексом внутри
    транзакции с откатом, поэтому проверка осмысленна: если откат сломан,
    индексы останутся и попадут в снимок.

    Команда индекса берётся из константы теста: в описании пресета её больше
    нет, потому что индекс на прогоне предлагает модель, а не данные пресета.
    """

    before_counts = await _row_counts(pool)
    before_indexes = await _indexes(pool)

    measurer = QueryMeasurer(pool, warmup_runs=0, repeat_runs=1)
    applier = IndexApplier(pool, measurer)
    for preset in load_presets():
        outcome = await applier.apply(preset.sql, PRESET_INDEX)
        assert outcome.applied is True, preset.id

    assert await _row_counts(pool) == before_counts
    assert await _indexes(pool) == before_indexes


async def test_no_demo_indexes_created_by_tests(pool: AsyncConnectionPool) -> None:
    """После прогонов в `public` нет индексов, созданных тестами."""

    measurer = QueryMeasurer(pool, warmup_runs=0, repeat_runs=1)
    applier = IndexApplier(pool, measurer)
    await applier.apply(
        "select * from sku where product_id = 42",
        "CREATE INDEX invariance_demo_idx ON sku (product_id)",
    )

    indexes = await _indexes(pool)

    assert all("invariance_demo_idx" not in name for name in indexes)
