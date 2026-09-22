"""Тесты графа агента на подставных модели и инструментах.

Проверяются все ветвления: отсутствие опечаток, исправление с принятием и
отказом, предупреждение при пропущенном вызове инструмента, отказ от
индекса, полный цикл с ускорением и цикл без ускорения.
"""

from typing import Any

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command

from sql_query_agent.agent import nodes
from sql_query_agent.agent.graph import (
    APPLY_NODE,
    CONFIRM_FIX_NODE,
    CONFIRM_INDEX_NODE,
    EXTRACT_NODE,
    MEASURE_NODE,
    PREPARE_FIX_NODE,
    PREPARE_INDEX_NODE,
    REPORT_UNFIXABLE_NODE,
    build_graph,
)
from sql_query_agent.db.catalog import SchemaCatalog
from tests.fakes import FakeApply, FakeChecker, FakeMeasure, FakeModel

CATALOG = SchemaCatalog.from_rows(
    [
        ("brand", "brand_id"),
        ("brand", "brand_name"),
        ("product", "product_id"),
        ("product", "product_name"),
        ("product", "brand_id"),
        ("sku", "sku_id"),
        ("sku", "product_id"),
        ("sku", "product_size_id"),
        ("sku", "product_color_id"),
    ]
)
GOOD_SQL = "select * from sku where product_id = 1"
TYPO_SQL = "select * from sku where product_colr_id = 1"
FIXED_SQL = "select * from sku where product_color_id = 1"
MISSING_TABLE = "zzz_table"
MISSING_COLUMN = "qqq"


def _graph(model: FakeModel, measure: FakeMeasure, apply: FakeApply) -> Any:
    """Собрать граф с подставными зависимостями и памятью в процессе."""

    return build_graph(
        model=model,
        checker=FakeChecker(CATALOG),
        measure_tool=measure,
        apply_tool=apply,
        checkpointer=InMemorySaver(),
    )


def _config(thread_id: str) -> dict[str, Any]:
    """Конфигурация потока."""

    return {"configurable": {"thread_id": thread_id}}


async def _interrupts(graph: Any, config: dict[str, Any]) -> list[dict[str, Any]]:
    """Значения прерываний, на которых граф остановился."""

    snapshot = await graph.aget_state(config)
    return [dict(item.value) for item in (snapshot.interrupts or [])]


async def test_clean_query_skips_fix_and_asks_about_index() -> None:
    """Запрос без опечаток не идёт в исправление и доходит до предложения индекса."""

    model = FakeModel(entities=[{"table": "sku", "columns": ["product_id"]}])
    graph = _graph(model, FakeMeasure(), FakeApply())
    config = _config("clean")

    await graph.ainvoke({"current_sql": GOOD_SQL}, config)

    assert model.fix_calls == 0
    assert model.index_calls == 1
    steps = [item["step"] for item in await _interrupts(graph, config)]
    assert steps == ["index_proposal"]


async def test_typo_asks_user_to_confirm_fix() -> None:
    """При опечатке граф останавливается на подтверждении исправления."""

    model = FakeModel(fixed_sql=FIXED_SQL)
    graph = _graph(model, FakeMeasure(), FakeApply())
    config = _config("typo")

    await graph.ainvoke({"current_sql": TYPO_SQL}, config)

    stopped = await _interrupts(graph, config)
    assert stopped[0]["step"] == "schema_fix"
    assert stopped[0]["fixed_sql"] == FIXED_SQL


async def test_accepted_fix_continues_to_measure() -> None:
    """Принятое исправление продолжает обработку и меняет текущий запрос."""

    model = FakeModel(fixed_sql=FIXED_SQL)
    measure = FakeMeasure()
    graph = _graph(model, measure, FakeApply())
    config = _config("accept-fix")

    await graph.ainvoke({"current_sql": TYPO_SQL}, config)
    await graph.ainvoke(Command(resume={"accepted": True}), config)

    snapshot = await graph.aget_state(config)
    assert snapshot.values["current_sql"] == FIXED_SQL
    assert measure.calls == 1
    # После исправления граф останавливается на втором решении — по индексу.
    steps = [item["step"] for item in await _interrupts(graph, config)]
    assert steps == ["index_proposal"]


async def test_declined_fix_stops_processing() -> None:
    """Отказ от исправления завершает обработку без замера."""

    model = FakeModel(fixed_sql=FIXED_SQL)
    measure = FakeMeasure()
    graph = _graph(model, measure, FakeApply())
    config = _config("decline-fix")

    await graph.ainvoke({"current_sql": TYPO_SQL}, config)
    await graph.ainvoke(Command(resume={"accepted": False}), config)

    snapshot = await graph.aget_state(config)
    assert snapshot.values["status"] == "fix_declined"
    assert measure.calls == 0
    assert model.index_calls == 0


async def test_missing_tool_call_produces_warning_and_continues() -> None:
    """Пропущенный вызов инструмента даёт предупреждение, но не прерывает обработку."""

    model = FakeModel(tool_call=False)
    measure = FakeMeasure()
    graph = _graph(model, measure, FakeApply())
    config = _config("no-tool")

    await graph.ainvoke({"current_sql": GOOD_SQL}, config)

    snapshot = await graph.aget_state(config)
    assert snapshot.values["schema_checked"] is False
    assert any("не вызвала инструмент" in item for item in snapshot.values["warnings"])
    assert measure.calls == 1


async def test_declined_index_stops_without_applying() -> None:
    """Отказ от индекса завершает обработку: индекс не применяется."""

    model = FakeModel(entities=[{"table": "sku", "columns": ["product_id"]}])
    apply = FakeApply()
    graph = _graph(model, FakeMeasure(), apply)
    config = _config("decline-index")

    await graph.ainvoke({"current_sql": GOOD_SQL}, config)
    await graph.ainvoke(Command(resume={"accepted": False}), config)

    snapshot = await graph.aget_state(config)
    assert snapshot.values["status"] == "index_declined"
    assert apply.calls == 0


async def test_full_cycle_reports_speedup() -> None:
    """Полный цикл с принятием индекса даёт сравнение с ускорением."""

    model = FakeModel(entities=[{"table": "sku", "columns": ["product_id"]}])
    apply = FakeApply(before_ms=100.0, after_ms=10.0)
    graph = _graph(model, FakeMeasure(), apply)
    config = _config("full")

    await graph.ainvoke({"current_sql": GOOD_SQL}, config)
    await graph.ainvoke(Command(resume={"accepted": True}), config)

    snapshot = await graph.aget_state(config)
    assert snapshot.values["status"] == "compared"
    assert snapshot.values["apply_result"]["improved"] is True
    assert apply.calls == 1


async def test_cycle_without_speedup_is_a_normal_result() -> None:
    """Отсутствие ускорения — обычный результат, а не ошибка."""

    model = FakeModel(entities=[{"table": "sku", "columns": ["product_id"]}])
    apply = FakeApply(before_ms=15.0, after_ms=14.9)
    graph = _graph(model, FakeMeasure(), apply)
    config = _config("no-speedup")

    await graph.ainvoke({"current_sql": GOOD_SQL}, config)
    await graph.ainvoke(Command(resume={"accepted": True}), config)

    snapshot = await graph.aget_state(config)
    result = snapshot.values["apply_result"]
    assert result["applied"] is True
    assert result["improved"] is False
    assert result["verdict"] == "no_speedup"


async def test_measure_failure_stops_before_index_proposal() -> None:
    """Ошибка замера останавливает обработку до предложения индекса."""

    model = FakeModel(entities=[{"table": "sku", "columns": ["product_id"]}])
    graph = _graph(model, FakeMeasure(ok=False), FakeApply())
    config = _config("measure-fail")

    await graph.ainvoke({"current_sql": GOOD_SQL}, config)

    snapshot = await graph.aget_state(config)
    assert snapshot.values["status"] == "measure_failed"
    assert model.index_calls == 0


async def test_model_is_called_once_despite_resume() -> None:
    """Модель и замер вызываются по одному разу, несмотря на возобновление.

    LangGraph перезапускает узел при возобновлении после `interrupt()`,
    поэтому вызовы вынесены в отдельные узлы без `interrupt()`. Этот тест
    фиксирует, что схема действительно экономит обращения к модели.
    """

    model = FakeModel(entities=[{"table": "sku", "columns": ["product_id"]}])
    measure = FakeMeasure()
    apply = FakeApply()
    graph = _graph(model, measure, apply)
    config = _config("no-duplicates")

    await graph.ainvoke({"current_sql": GOOD_SQL}, config)
    await graph.ainvoke(Command(resume={"accepted": True}), config)

    assert model.extract_calls == 1
    assert model.index_calls == 1
    assert measure.calls == 1
    assert apply.calls == 1


async def test_typo_fix_flow_calls_model_once_per_step() -> None:
    """В ветке исправления модель вызывается по одному разу на каждый шаг."""

    model = FakeModel(fixed_sql=FIXED_SQL)
    graph = _graph(model, FakeMeasure(), FakeApply())
    config = _config("typo-counts")

    await graph.ainvoke({"current_sql": TYPO_SQL}, config)
    await graph.ainvoke(Command(resume={"accepted": True}), config)

    assert model.extract_calls == 1
    assert model.fix_calls == 1
    assert model.index_calls == 1


async def test_graph_declares_expected_nodes() -> None:
    """Граф содержит ожидаемые узлы."""

    graph = _graph(FakeModel(), FakeMeasure(), FakeApply())

    graph_nodes = set(graph.get_graph().nodes)

    assert {
        EXTRACT_NODE,
        PREPARE_FIX_NODE,
        CONFIRM_FIX_NODE,
        REPORT_UNFIXABLE_NODE,
        MEASURE_NODE,
        PREPARE_INDEX_NODE,
        CONFIRM_INDEX_NODE,
        APPLY_NODE,
    } <= graph_nodes


def test_needs_fix_routes_clean_query_to_measure() -> None:
    """Без ненайденных имён исправлять нечего."""

    route = nodes.needs_fix(
        {"schema_checked": True, "schema_result": {"unknown": []}}
    )

    assert route == "measure"


def test_needs_fix_routes_fixable_names_to_prepare_fix() -> None:
    """Имена с кандидатами идут в исправление."""

    route = nodes.needs_fix(
        {
            "schema_checked": True,
            "schema_result": {
                "unknown": [
                    {
                        "kind": "column",
                        "table": "sku",
                        "name": "product_colr_id",
                        "candidates": [{"name": "product_color_id", "score": 96.0}],
                    }
                ]
            },
        }
    )

    assert route == "prepare_fix"


def test_needs_fix_routes_unfixable_names_to_report() -> None:
    """Имя без кандидатов ведёт к терминальному сообщению."""

    route = nodes.needs_fix(
        {
            "schema_checked": True,
            "schema_result": {
                "unknown": [
                    {
                        "kind": "table",
                        "table": "zzz_table",
                        "name": "zzz_table",
                        "candidates": [],
                    }
                ]
            },
        }
    )

    assert route == "report_unfixable"


def test_needs_fix_skips_check_when_tool_was_not_called() -> None:
    """Без выполненной проверки имён маршрут идёт к замеру."""

    assert nodes.needs_fix({"schema_checked": False}) == "measure"


async def test_unfixable_names_end_session_without_model_or_measure() -> None:
    """Имя без замен завершает сессию: модель исправления и замер не вызываются."""

    model = FakeModel(
        entities=[{"table": MISSING_TABLE, "columns": []}],
        fixed_sql=FIXED_SQL,
    )
    measure = FakeMeasure()
    graph = _graph(model, measure, FakeApply())
    config = _config("unfixable-table")

    await graph.ainvoke({"current_sql": f"select * from {MISSING_TABLE}"}, config)

    snapshot = await graph.aget_state(config)
    assert snapshot.values["status"] == nodes.UNFIXABLE_STATUS
    assert model.fix_calls == 0
    assert model.index_calls == 0
    assert measure.calls == 0
    assert await _interrupts(graph, config) == []
    assert MISSING_TABLE in snapshot.values["unfixable_message"]


async def test_unfixable_column_names_table_in_message() -> None:
    """Сообщение о ненайденной колонке содержит колонку и таблицу из запроса."""

    model = FakeModel(entities=[{"table": "sku", "columns": [MISSING_COLUMN]}])
    graph = _graph(model, FakeMeasure(), FakeApply())
    config = _config("unfixable-column")

    await graph.ainvoke(
        {"current_sql": f"select * from sku where {MISSING_COLUMN} = 1"}, config
    )

    snapshot = await graph.aget_state(config)
    message = snapshot.values["unfixable_message"]
    assert MISSING_COLUMN in message
    assert "sku" in message


async def test_mixed_case_ends_session_without_partial_fix() -> None:
    """Если одно имя исправимо, а другое нет, частичная правка не выполняется."""

    model = FakeModel(
        entities=[{"table": "sku", "columns": ["product_colr_id", MISSING_COLUMN]}],
        fixed_sql=FIXED_SQL,
    )
    measure = FakeMeasure()
    graph = _graph(model, measure, FakeApply())
    config = _config("mixed-unfixable")

    await graph.ainvoke(
        {"current_sql": f"select * from sku where product_colr_id = 1 and {MISSING_COLUMN} = 2"},
        config,
    )

    snapshot = await graph.aget_state(config)
    assert snapshot.values["status"] == nodes.UNFIXABLE_STATUS
    assert model.fix_calls == 0
    assert measure.calls == 0
    assert "product_colr_id" not in snapshot.values["unfixable_message"]


async def test_prepare_fix_refuses_to_run_without_candidates() -> None:
    """Прямой вызов исправления без кандидатов не доходит до модели."""

    model = FakeModel(fixed_sql=FIXED_SQL)

    update = await nodes.prepare_fix(
        {
            "current_sql": f"select * from {MISSING_TABLE}",
            "schema_checked": True,
            "schema_result": {
                "unknown": [
                    {
                        "kind": "table",
                        "table": MISSING_TABLE,
                        "name": MISSING_TABLE,
                        "candidates": [],
                    }
                ]
            },
        },
        model=model,
    )

    assert model.fix_calls == 0
    assert update["status"] == nodes.UNFIXABLE_STATUS
    assert update["fixed_sql"] is None
