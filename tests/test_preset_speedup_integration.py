"""Условная проверка обещания библиотеки: каждый пресет ускоряется индексом.

Библиотека демонстрационная и привязана к форме локальной демо-базы
(`sku.added_at`, много значений у `sku.product_size_id`). На произвольной базе
обещание «каждый пресет ускоряется» неприменимо, поэтому тесты пропускаются,
если демо-форма не обнаружена: так поддерживается запуск на любой базе, а
обещание проверяется там, где оно осмысленно.

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
from sql_query_agent.presets import PresetQuery, load_presets

pytestmark = pytest.mark.integration

SIGNIFICANCE_RATIO = 1.10
SIZE_VALUES_THRESHOLD = 20
ADDED_AT_COLUMN = "added_at"

PRODUCT_INDEX = "CREATE INDEX preset_product_idx ON sku (product_id)"
SIZE_INDEX = "CREATE INDEX preset_size_idx ON sku (product_size_id)"
DATE_INDEX = "CREATE INDEX preset_added_at_idx ON sku (added_at)"
BRAND_INDEX = "CREATE INDEX preset_brand_idx ON product (brand_id)"

LEVER_INDEXES = {
    "product": PRODUCT_INDEX,
    "size": SIZE_INDEX,
    "date": DATE_INDEX,
    "brand": BRAND_INDEX,
}

PRESET_LEVERS = {
    "count-by-product": "product",
    "page-by-product": "product",
    "count-by-products": "product",
    "join-product-name": "product",
    "join-brand-count": "product",
    "window-top-n-per-product": "product",
    "filter-by-size": "size",
    "size-breakdown": "size",
    "recent-skus": "date",
    "count-added-in-period": "date",
    "brand-product-count": "brand",
    "brand-products-page": "brand",
}

LEVER_SAMPLES = {
    "product": ("select count(*) from sku where product_id = 42", PRODUCT_INDEX),
    "size": ("select count(*) from sku where product_size_id = 7", SIZE_INDEX),
    "date": (
        "select count(*) from sku where added_at >= now() - interval '7 days'",
        DATE_INDEX,
    ),
    "brand": ("select count(*) from product where brand_id = 7", BRAND_INDEX),
}


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


async def _demo_form_available(pool: AsyncConnectionPool) -> bool:
    """Есть ли в базе форма, на которой обещание библиотеки применимо."""

    async with pool.connection() as connection:
        cursor = await connection.execute(
            "SELECT 1 FROM information_schema.columns "
            "WHERE table_schema = 'public' AND table_name = 'sku' AND column_name = %s",
            (ADDED_AT_COLUMN,),
        )
        if await cursor.fetchone() is None:
            return False
        cursor = await connection.execute(
            "SELECT count(DISTINCT product_size_id) FROM sku"
        )
        row = await cursor.fetchone()
    return bool(row and row[0] and row[0] >= SIZE_VALUES_THRESHOLD)


@pytest.fixture
async def require_demo_form(pool: AsyncConnectionPool) -> None:
    """Пропустить тест, если демо-форма в базе не обнаружена."""

    if not await _demo_form_available(pool):
        pytest.skip(
            "демо-форма не обнаружена: нет sku.added_at или мало значений "
            "sku.product_size_id"
        )


async def _measure_speedup(
    pool: AsyncConnectionPool,
    sql: str,
    index_ddl: str,
) -> float:
    """Отношение замера до и после индекса на реальной базе."""

    measurer = QueryMeasurer(pool, warmup_runs=1, repeat_runs=3)
    applier = IndexApplier(pool, measurer)
    outcome = await applier.apply(sql, index_ddl)

    assert outcome.applied is True
    assert outcome.after is not None
    before = outcome.before.stats
    after = outcome.after.stats
    assert after.maximum_ms < before.minimum_ms, (
        f"диапазоны замеров перекрываются ({before.median_ms:.3f} мс -> "
        f"{after.median_ms:.3f} мс)"
    )
    return before.median_ms / after.median_ms


def _presets_with_levers() -> list[tuple[PresetQuery, str]]:
    """Пресеты библиотеки в паре с рычагом оптимизации."""

    pairs: list[tuple[PresetQuery, str]] = []
    for preset in load_presets():
        lever = PRESET_LEVERS.get(preset.id)
        assert lever is not None, f"для пресета `{preset.id}` не задан рычаг оптимизации"
        pairs.append((preset, lever))
    return pairs


def test_every_preset_has_a_lever() -> None:
    """У каждого пресета библиотеки задан рычаг оптимизации."""

    presets = load_presets()

    assert {preset.id for preset in presets} == set(PRESET_LEVERS)


@pytest.mark.parametrize(
    ("preset", "lever"),
    _presets_with_levers(),
    ids=lambda value: value.id if isinstance(value, PresetQuery) else value,
)
async def test_every_preset_speeds_up_with_index(
    pool: AsyncConnectionPool,
    require_demo_form: None,
    preset: PresetQuery,
    lever: str,
) -> None:
    """Каждый предустановленный запрос ускоряется индексом своего рычага."""

    speedup = await _measure_speedup(pool, preset.sql, LEVER_INDEXES[lever])

    assert speedup >= SIGNIFICANCE_RATIO, (
        f"{preset.id}: ускорение {speedup:.2f}x ниже порога {SIGNIFICANCE_RATIO}"
    )


@pytest.mark.parametrize("lever", sorted(LEVER_SAMPLES))
async def test_each_lever_speeds_up_with_its_index(
    pool: AsyncConnectionPool,
    require_demo_form: None,
    lever: str,
) -> None:
    """Пресеты разных рычагов ускоряются соответствующими им индексами."""

    sql, index_ddl = LEVER_SAMPLES[lever]
    speedup = await _measure_speedup(pool, sql, index_ddl)

    assert speedup >= SIGNIFICANCE_RATIO, (
        f"рычаг {lever}: ускорение {speedup:.2f}x ниже порога {SIGNIFICANCE_RATIO}"
    )


async def test_preset_runs_leave_no_index_behind(
    pool: AsyncConnectionPool,
    require_demo_form: None,
) -> None:
    """После прогонов всех пресетов индексы не остаются в демо-схеме."""

    measurer = QueryMeasurer(pool, warmup_runs=0, repeat_runs=1)
    applier = IndexApplier(pool, measurer)
    for preset, lever in _presets_with_levers():
        await applier.apply(preset.sql, LEVER_INDEXES[lever])

    async with pool.connection() as connection:
        cursor = await connection.execute(
            "SELECT count(*) FROM pg_indexes "
            "WHERE schemaname = 'public' AND indexname LIKE 'preset\\_%'"
        )
        row = await cursor.fetchone()

    assert row is not None
    assert row[0] == 0
