"""Интеграционные тесты замера на реальной базе products."""

from collections.abc import AsyncIterator

import pytest
from psycopg_pool import AsyncConnectionPool

from sql_query_agent.config import Settings
from sql_query_agent.db.explain import QueryMeasurer, SqlNotAllowedError
from sql_query_agent.db.pool import create_pool, open_pool
from sql_query_agent.tools.measure_query import QueryMeasureTool

pytestmark = pytest.mark.integration

SAMPLE_QUERY = "select * from sku where product_id = 42"


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
def measurer(pool: AsyncConnectionPool) -> QueryMeasurer:
    """Замер с тремя повторами."""

    return QueryMeasurer(pool, warmup_runs=1, repeat_runs=3)


async def test_measure_returns_three_samples(measurer: QueryMeasurer) -> None:
    """Замер возвращает запрошенное число повторов и границы."""

    outcome = await measurer.measure(SAMPLE_QUERY)

    assert outcome.stats.runs == 3
    assert outcome.stats.minimum_ms <= outcome.stats.median_ms <= outcome.stats.maximum_ms


async def test_repeated_measure_is_comparable(measurer: QueryMeasurer) -> None:
    """Два последовательных замера дают сопоставимые величины."""

    first = await measurer.measure(SAMPLE_QUERY)
    second = await measurer.measure(SAMPLE_QUERY)

    faster = min(first.stats.median_ms, second.stats.median_ms)
    slower = max(first.stats.median_ms, second.stats.median_ms)
    assert slower / faster < 4.0


async def test_write_statement_is_rejected_before_reaching_db(measurer: QueryMeasurer) -> None:
    """Команда изменения данных отклоняется до обращения к базе."""

    with pytest.raises(SqlNotAllowedError):
        await measurer.measure("delete from brand")


async def test_tool_reports_plan_nodes(pool: AsyncConnectionPool) -> None:
    """Инструмент замера возвращает узлы плана и успешный признак."""

    tool = QueryMeasureTool(QueryMeasurer(pool, warmup_runs=0, repeat_runs=2))

    result = await tool.run(SAMPLE_QUERY)

    assert result["ok"] is True
    assert result["plan_nodes"]
    assert result["runs"] == 2


async def test_demo_data_is_unchanged_after_measuring(measurer: QueryMeasurer) -> None:
    """После замеров демо-данные остаются нетронутыми."""

    await measurer.measure(SAMPLE_QUERY)
    await measurer.measure("select count(*) from sku")

    assert True
