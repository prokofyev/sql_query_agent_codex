"""Интеграционные тесты проверки имён на реальной базе products."""

from collections.abc import AsyncIterator

import pytest
from psycopg_pool import AsyncConnectionPool

from sql_query_agent.config import Settings
from sql_query_agent.db.pool import create_pool, open_pool
from sql_query_agent.presets import load_presets
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
        {"tables": ["sku"], "columns": ["sku_id", "product_id", "product_color_id"]}
    )

    assert result["ok"] is True
    assert result["unknown"] == []


async def test_column_typo_has_candidates(checker: SchemaChecker) -> None:
    """Опечатка в колонке находит реальную колонку базы."""

    result = await checker.run(
        {"tables": ["sku"], "columns": ["product_colr_id"]}
    )

    assert result["ok"] is False
    unknown = result["unknown"][0]
    assert unknown["kind"] == "column"
    names = [candidate["name"] for candidate in unknown["candidates"]]
    assert "product_color_id" in names


async def test_table_typo_has_candidates_from_catalog(checker: SchemaChecker) -> None:
    """Опечатка в имени таблицы находит кандидатов без опоры на текст ошибок СУБД."""

    result = await checker.run({"tables": ["skuu"], "columns": ["sku_id"]})

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

    model = FakeModel(entities={"tables": [MISSING_TABLE], "columns": []})
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

    result = await checker.run({"tables": ["sku2"], "columns": ["product_id2"]})

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
        entities={"tables": ["sku2"], "columns": ["product_id2"]},
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

async def test_column_from_joined_table_is_not_a_typo(checker: SchemaChecker) -> None:
    """`brand_name` принадлежит подключённой таблице `brand`, а не `product`.

    Исходная ошибка: модель приписывала колонку `product`, инструмент искал её
    там и предлагал ложную замену `brand_id` вместо замера.
    """

    result = await checker.run(
        {
            "tables": ["product", "brand"],
            "aliases": ["b=brand"],
            "columns": ["brand_name", "product_name"],
        }
    )

    assert result["ok"] is True
    assert result["unknown"] == []


async def test_ambiguous_column_is_reported_with_table_candidates(checker: SchemaChecker) -> None:
    """`brand_id` есть и в `product`, и в `brand`: прогон не должен дойти до замера."""

    result = await checker.run(
        {
            "tables": ["product", "brand"],
            "columns": ["brand_id", "product_name"],
        }
    )

    assert result["ok"] is False
    ambiguous = [item for item in result["unknown"] if item["ambiguous"]]
    assert [item["name"] for item in ambiguous] == ["brand_id"]
    assert ambiguous[0]["searched_tables"] == ["product", "brand"]
    candidates = [candidate["name"] for candidate in ambiguous[0]["candidates"]]
    assert candidates == ["product.brand_id", "brand.brand_id"]

# Таблицы и колонки каждого пресета из `resources/presets.yaml` в том виде, в
# каком их должна вернуть модель: настоящие имена таблиц, алиасы и колонки как
# записаны. Список ведёт тест «пресеты проходят проверку без ложных срабатываний»:
# он ловит и потерю пресета, и ложную неоднозначность на его запросе.
PRESET_IDENTIFIERS: dict[str, dict[str, list[str]]] = {
    "count-by-product": {"tables": ["sku"], "columns": ["product_id"]},
    "page-by-product": {
        "tables": ["sku"],
        "columns": ["sku_id", "product_size_id", "product_color_id", "product_id"],
    },
    "count-by-products": {"tables": ["sku"], "columns": ["product_id"]},
    "join-product-name": {
        "tables": ["sku", "product"],
        "aliases": ["s=sku", "p=product"],
        "columns": ["p.product_name", "s.sku_id", "p.product_id", "s.product_id"],
    },
    "join-brand-count": {
        "tables": ["sku", "product", "brand"],
        "aliases": ["s=sku", "p=product", "b=brand"],
        "columns": [
            "b.brand_name",
            "p.product_id",
            "s.product_id",
            "b.brand_id",
            "p.brand_id",
        ],
    },
    "window-top-n-per-product": {"tables": ["sku"], "columns": ["product_id", "sku_id"]},
    "filter-by-size": {"tables": ["sku"], "columns": ["sku_id", "product_id", "product_size_id"]},
    "size-breakdown": {
        "tables": ["sku", "product_size"],
        "aliases": ["s=sku", "ps=product_size"],
        "columns": ["ps.product_size_name", "s.product_size_id", "ps.product_size_id"],
    },
    "recent-skus": {
        "tables": ["sku"],
        "columns": ["sku_id", "product_id", "added_at"],
    },
    "count-added-in-period": {"tables": ["sku"], "columns": ["added_at"]},
    "brand-product-count": {"tables": ["product"], "columns": ["brand_id"]},
    "brand-products-page": {
        "tables": ["product"],
        "columns": ["product_id", "product_name", "brand_id"],
    },
}


async def test_every_preset_passes_check_without_false_findings(checker: SchemaChecker) -> None:
    """Ни один пресет библиотеки не даёт ложных ненайденных имён или неоднозначностей.

    Модель ошибается в извлечении, поэтому имена заданы явно: тест проверяет
    инструмент, а не модель. Список пресетов берётся из файла, поэтому новый
    пресет без записи в `PRESET_IDENTIFIERS` уронит тест.
    """

    presets = load_presets()
    assert {preset.id for preset in presets} == set(PRESET_IDENTIFIERS)

    for preset in presets:
        result = await checker.run(PRESET_IDENTIFIERS[preset.id])
        flagged = [item for item in result["unknown"] if item.get("ambiguous")]
        assert result["ok"] is True, (preset.id, result["unknown"])
        assert flagged == [], (preset.id, flagged)


async def test_preset_brand_id_is_ambiguous_only_with_brand_joined(
    checker: SchemaChecker,
) -> None:
    """`brand_id` неоднозначен только там, где в запросе есть и `product`, и `brand`."""

    alone = await checker.run({"tables": ["product"], "columns": ["brand_id"]})
    joined = await checker.run({"tables": ["product", "brand"], "columns": ["brand_id"]})

    assert alone["ok"] is True
    assert joined["ok"] is False
