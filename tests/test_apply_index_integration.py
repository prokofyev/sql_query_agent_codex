"""Интеграционные тесты применения индекса на реальной базе.

Ключевая проверка: после прогона индекс отсутствует в базе, а демо-данные
не изменены.
"""

from collections.abc import AsyncIterator

import pytest
from psycopg_pool import AsyncConnectionPool

from sql_query_agent.config import Settings
from sql_query_agent.db.explain import QueryMeasurer
from sql_query_agent.db.index_apply import IndexApplier
from sql_query_agent.db.pool import create_pool, open_pool
from sql_query_agent.domain_comparison import ComparisonVerdict
from sql_query_agent.tools.apply_index import ApplyIndexTool

pytestmark = pytest.mark.integration

SPEEDUP_QUERY = "select * from sku where product_id = 42"
SPEEDUP_INDEX = "CREATE INDEX sku_product_id_demo_idx ON sku (product_id)"
NO_SPEEDUP_QUERY = "select count(*) from sku where product_color_id = 1"
INDEX_NAME = "sku_product_id_demo_idx"


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


@pytest.fixture
def applier(pool: AsyncConnectionPool) -> IndexApplier:
    """Применение индекса с тремя повторами."""

    measurer = QueryMeasurer(pool, warmup_runs=1, repeat_runs=3)
    return IndexApplier(pool, measurer)


async def _index_exists(pool: AsyncConnectionPool, name: str) -> bool:
    """Существует ли индекс с таким именем."""

    async with pool.connection() as connection:
        cursor = await connection.execute(
            "SELECT 1 FROM pg_indexes WHERE schemaname = 'public' AND indexname = %s",
            (name,),
        )
        return await cursor.fetchone() is not None


async def test_index_is_rolled_back(pool: AsyncConnectionPool, applier: IndexApplier) -> None:
    """После применения индекс не остаётся в базе."""

    assert await _index_exists(pool, INDEX_NAME) is False

    outcome = await applier.apply(SPEEDUP_QUERY, SPEEDUP_INDEX)

    assert outcome.applied is True
    assert await _index_exists(pool, INDEX_NAME) is False


async def test_point_query_is_sped_up(applier: IndexApplier) -> None:
    """Точечный фильтр по внешнему ключу ускоряется заметно."""

    outcome = await applier.apply(SPEEDUP_QUERY, SPEEDUP_INDEX)

    assert outcome.applied is True
    assert outcome.after is not None
    assert outcome.speedup is not None
    assert outcome.speedup > 5.0


async def test_low_selectivity_query_reports_no_speedup(
    pool: AsyncConnectionPool,
) -> None:
    """Запрос по низкоселективной колонке не объявляется ускоренным.

    Колонка `product_color_id` содержит всего два значения, поэтому индекс по
    ней планировщик игнорирует: результат должен быть «без ускорения», а не
    «ускорилось» и не ошибка.
    """

    measurer = QueryMeasurer(pool, warmup_runs=1, repeat_runs=5)
    tool = ApplyIndexTool(IndexApplier(pool, measurer))

    result = await tool.run(NO_SPEEDUP_QUERY, SPEEDUP_INDEX)

    assert result["applied"] is True
    assert result["verdict"] != ComparisonVerdict.SPEEDUP.value
    assert result["improved"] is False
    assert await _index_exists(pool, INDEX_NAME) is False


async def test_tool_reports_comparison(pool: AsyncConnectionPool) -> None:
    """Инструмент возвращает обе величины и итог сравнения."""

    measurer = QueryMeasurer(pool, warmup_runs=1, repeat_runs=3)
    tool = ApplyIndexTool(IndexApplier(pool, measurer))

    result = await tool.run(SPEEDUP_QUERY, SPEEDUP_INDEX)

    assert result["applied"] is True
    assert result["verdict"] == ComparisonVerdict.SPEEDUP.value
    assert result["improved"] is True
    assert result["before_median_ms"] > result["after_median_ms"]


async def test_broken_ddl_reports_error_and_rolls_back(
    pool: AsyncConnectionPool, applier: IndexApplier
) -> None:
    """Ошибка создания индекса возвращается текстом, база не изменяется."""

    outcome = await applier.apply(SPEEDUP_QUERY, "CREATE INDEX ON sku (no_such_column)")

    assert outcome.applied is False
    assert outcome.error is not None
    assert await _index_exists(pool, INDEX_NAME) is False


async def test_demo_data_row_counts_are_unchanged(pool: AsyncConnectionPool) -> None:
    """Число строк демо-таблиц не меняется после прогонов."""

    expected = {"brand": 102, "product": 1000, "sku": 100000}
    async with pool.connection() as connection:
        for table, count in expected.items():
            cursor = await connection.execute(f"SELECT count(*) FROM {table}")  # noqa: S608
            row = await cursor.fetchone()
            assert row[0] == count
