"""Интеграционные тесты применения индекса на реальной базе.

Проверяется только то, что не зависит от формы конкретной схемы: после
прогона индекс отсутствует в базе, ошибка DDL возвращается текстом, а
сравнение до и после доступно инструменту. Число строк и селективность
колонок демо-схемы здесь не закрепляются — агент должен работать на любой
доступной базе.
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


async def test_tool_reports_comparison(pool: AsyncConnectionPool) -> None:
    """Инструмент возвращает обе величины и итог сравнения."""

    measurer = QueryMeasurer(pool, warmup_runs=1, repeat_runs=3)
    tool = ApplyIndexTool(IndexApplier(pool, measurer))

    result = await tool.run(SPEEDUP_QUERY, SPEEDUP_INDEX)

    assert result["applied"] is True
    assert result["verdict"] in {verdict.value for verdict in ComparisonVerdict}
    assert result["before_median_ms"] > 0
    assert result["after_median_ms"] > 0
    assert await _index_exists(pool, INDEX_NAME) is False


async def test_broken_ddl_reports_error_and_rolls_back(
    pool: AsyncConnectionPool, applier: IndexApplier
) -> None:
    """Ошибка создания индекса возвращается текстом, база не изменяется."""

    outcome = await applier.apply(SPEEDUP_QUERY, "CREATE INDEX ON sku (no_such_column)")

    assert outcome.applied is False
    assert outcome.error is not None
    assert await _index_exists(pool, INDEX_NAME) is False
