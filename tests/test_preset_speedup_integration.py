"""Проверка обещания библиотеки: каждый пресет ускоряется индексом.

Библиотека разошлась с реальностью именно потому, что её обещания никто не
проверял автоматически: два «ускоряющихся» примера соединений на демо-данных
давали около 1.0x. Этот тест закрывает корень проблемы — он не даст снова
добавить в библиотеку запрос, который индекс не ускоряет.

Замер идёт тем же путём, что и в продукте: индекс создаётся в транзакции,
запрос замеряется до и после, транзакция откатывается.
"""

from collections.abc import AsyncIterator

import pytest
from psycopg_pool import AsyncConnectionPool

from sql_query_agent.config import Settings
from sql_query_agent.db.explain import QueryMeasurer
from sql_query_agent.db.index_apply import IndexApplier
from sql_query_agent.db.pool import create_pool, open_pool
from sql_query_agent.presets import PRESET_QUERIES

pytestmark = pytest.mark.integration

PRESET_INDEX = "CREATE INDEX preset_speedup_idx ON sku (product_id)"
SIGNIFICANCE_RATIO = 1.10


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


@pytest.mark.parametrize("preset", PRESET_QUERIES, ids=lambda preset: preset.id)
async def test_every_preset_speeds_up_with_index(
    pool: AsyncConnectionPool,
    preset: object,
) -> None:
    """Каждый предустановленный запрос ускоряется индексом на реальной базе."""

    sql = preset.sql  # type: ignore[attr-defined]
    measurer = QueryMeasurer(pool, warmup_runs=1, repeat_runs=3)
    applier = IndexApplier(pool, measurer)

    outcome = await applier.apply(sql, PRESET_INDEX)

    assert outcome.applied is True
    assert outcome.after is not None
    before = outcome.before.stats
    after = outcome.after.stats
    speedup = before.median_ms / after.median_ms

    assert after.maximum_ms < before.minimum_ms, (
        f"{preset.id}: диапазоны замеров перекрываются "
        f"({before.median_ms:.3f} мс -> {after.median_ms:.3f} мс)"  # type: ignore[attr-defined]
    )
    assert speedup >= SIGNIFICANCE_RATIO, (
        f"{preset.id}: ускорение {speedup:.2f}x ниже порога {SIGNIFICANCE_RATIO}"  # type: ignore[attr-defined]
    )


async def test_preset_runs_leave_no_index_behind(pool: AsyncConnectionPool) -> None:
    """После прогонов всех пресетов индекс не остаётся в демо-схеме."""

    measurer = QueryMeasurer(pool, warmup_runs=0, repeat_runs=1)
    applier = IndexApplier(pool, measurer)
    for preset in PRESET_QUERIES:
        await applier.apply(preset.sql, PRESET_INDEX)

    async with pool.connection() as connection:
        cursor = await connection.execute(
            "SELECT count(*) FROM pg_indexes "
            "WHERE schemaname = 'public' AND indexname = 'preset_speedup_idx'"
        )
        row = await cursor.fetchone()

    assert row is not None
    assert row[0] == 0
