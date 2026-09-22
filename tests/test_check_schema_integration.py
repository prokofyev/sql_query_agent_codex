"""Интеграционные тесты проверки имён на реальной базе products."""

from collections.abc import AsyncIterator

import pytest
from psycopg_pool import AsyncConnectionPool

from sql_query_agent.config import Settings
from sql_query_agent.db.pool import create_pool, open_pool
from sql_query_agent.tools.check_schema import SchemaChecker

pytestmark = pytest.mark.integration

MISSING_TABLE = "zzz_missing_table"


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
def checker(pool: AsyncConnectionPool) -> SchemaChecker:
    """Проверка имён на схеме public."""

    return SchemaChecker(pool, schema="public", threshold=65.0, suggestion_limit=3)


async def test_existing_names_pass(checker: SchemaChecker) -> None:
    """Реальные имена демо-базы проходят проверку."""

    result = await checker.run(
        [{"table": "sku", "columns": ["sku_id", "product_id", "product_color_id"]}]
    )

    assert result["ok"] is True
    assert result["unknown"] == []


async def test_column_typo_has_candidates(checker: SchemaChecker) -> None:
    """Опечатка в колонке находит реальную колонку базы."""

    result = await checker.run(
        [{"table": "sku", "columns": ["product_colr_id"]}]
    )

    assert result["ok"] is False
    unknown = result["unknown"][0]
    assert unknown["kind"] == "column"
    names = [candidate["name"] for candidate in unknown["candidates"]]
    assert "product_color_id" in names


async def test_table_typo_has_candidates_from_catalog(checker: SchemaChecker) -> None:
    """Опечатка в имени таблицы находит кандидатов без опоры на текст ошибок СУБД."""

    result = await checker.run([{"table": "skuu", "columns": ["sku_id"]}])

    assert result["ok"] is False
    assert result["unknown_tables"] == ["skuu"]
    unknown = result["unknown"][0]
    assert unknown["kind"] == "table"
    names = [candidate["name"] for candidate in unknown["candidates"]]
    assert "sku" in names


async def test_catalog_is_cached_between_calls(checker: SchemaChecker) -> None:
    """Каталог загружается один раз и переиспользуется."""

    first = await checker.catalog()
    second = await checker.catalog()

    assert first is second


async def test_unfixable_name_ends_session_on_real_catalog(checker: SchemaChecker) -> None:
    """Имя без похожих названий завершает прогон до модели исправления и замера.

    Проверяется на реальном каталоге: имя таблицы не существует и не имеет
    кандидатов выше порога, поэтому исправлять нечем.
    """

    from langgraph.checkpoint.memory import InMemorySaver

    from sql_query_agent.agent.graph import build_graph
    from sql_query_agent.run.report import RunStatus, build_report
    from tests.fakes import FakeApply, FakeMeasure, FakeModel

    model = FakeModel(entities=[{"table": MISSING_TABLE, "columns": []}])
    measure = FakeMeasure()
    graph = build_graph(
        model=model,
        checker=checker,
        measure_tool=measure,
        apply_tool=FakeApply(),
        checkpointer=InMemorySaver(),
    )
    config = {"configurable": {"thread_id": "unfixable-real-catalog"}}

    sql = f"select * from {MISSING_TABLE}"
    await graph.ainvoke({"original_sql": sql, "current_sql": sql}, config)

    snapshot = await graph.aget_state(config)
    report = build_report("unfixable-real-catalog", dict(snapshot.values), [])

    assert report.status is RunStatus.UNKNOWN_UNFIXABLE
    assert model.fix_calls == 0
    assert measure.calls == 0
    assert MISSING_TABLE in report.unfixable_message


async def test_missing_table_and_column_are_both_checked(checker: SchemaChecker) -> None:
    """Опечатка и в таблице, и в колонке даёт оба ненайденных имени на реальной схеме."""

    result = await checker.run([{"table": "sku2", "columns": ["product_id2"]}])

    assert result["ok"] is False
    found = {(item["kind"], item["name"]) for item in result["unknown"]}
    assert found == {("table", "sku2"), ("column", "product_id2")}
    assert result["checked_columns"] == 1

    column = next(item for item in result["unknown"] if item["kind"] == "column")
    names = [candidate["name"] for candidate in column["candidates"]]
    assert names[0] == "product_id"
    sku_columns = {"sku_id", "product_id", "product_size_id", "product_color_id"}
    assert set(names) <= sku_columns


async def test_missing_table_column_typos_are_fixed_and_measured(checker: SchemaChecker) -> None:
    """Оба имени исправляются, и прогон доходит до замера без ошибок имён."""

    from langgraph.checkpoint.memory import InMemorySaver

    from sql_query_agent.agent.graph import build_graph
    from sql_query_agent.run.report import RunStatus, build_report
    from tests.fakes import FakeApply, FakeMeasure, FakeModel

    sql = "select * from sku2 where product_id2 = 42"
    fixed = "select * from sku where product_id = 42"
    model = FakeModel(
        entities=[{"table": "sku2", "columns": ["product_id2"]}],
        fixed_sql=fixed,
    )
    measure = FakeMeasure()
    graph = build_graph(
        model=model,
        checker=checker,
        measure_tool=measure,
        apply_tool=FakeApply(),
        checkpointer=InMemorySaver(),
    )
    config = {"configurable": {"thread_id": "both-typos-real-catalog"}}

    await graph.ainvoke({"original_sql": sql, "current_sql": sql}, config)
    snapshot = await graph.aget_state(config)
    report = build_report(
        "both-typos-real-catalog",
        dict(snapshot.values),
        [dict(item.value) for item in (snapshot.interrupts or [])],
    )

    assert report.status is RunStatus.AWAITING_DECISION
    assert model.fix_calls == 1
    replacements = {(item.old_name, item.new_name) for item in report.fix.replacements}
    assert replacements == {("sku2", "sku"), ("product_id2", "product_id")}
