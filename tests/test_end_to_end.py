"""Сквозные тесты: соединение API, графа, журнала и метрик.

Проверяются контракт API и полные циклы: запрос с опечаткой и без неё,
ускорение и отсутствие ускорения. Часть тестов идёт против подставной модели
(маркер `llm` — только там, где нужна настоящая), а часть — против реальной
базы (маркер `integration`).
"""

from typing import Any

import httpx
import pytest
from langgraph.checkpoint.memory import InMemorySaver
from prometheus_client import CollectorRegistry

from sql_query_agent.agent.graph import build_graph
from sql_query_agent.api.app import create_app
from sql_query_agent.api.deps import AppDeps
from sql_query_agent.config import Settings
from sql_query_agent.observability.metrics import RunMetrics
from sql_query_agent.run.service import RunService
from sql_query_agent.run.session import SessionRunner
from tests.api_fakes import FakeWorld, build_test_app, build_test_client, decide
from tests.fakes import FakeApply, FakeModel

TYPO_SQL = "select * from sku where product_colr_id = 1"
FIXED_SQL = "select * from sku where product_color_id = 1"
GOOD_SQL = "select * from sku where product_id = 1"
MISSING_TABLE = "zzz_table"
UNFIXABLE_SQL = f"select * from {MISSING_TABLE}"
BOTH_TYPOS_SQL = "select * from sku2 where product_id2 = 42"
BOTH_FIXED_SQL = "select * from sku where product_id = 42"


def _typo_world(**kwargs: Any) -> FakeWorld:
    """Мир с моделью, которая находит опечатку и исправляет её."""

    model = FakeModel(
        entities={"tables": ["sku"], "columns": ["product_colr_id"]},
        fixed_sql=FIXED_SQL,
    )
    return FakeWorld(model=model, **kwargs)


def _clean_world(**kwargs: Any) -> FakeWorld:
    """Мир с моделью, которая не находит опечаток."""

    model = FakeModel(entities={"tables": ["sku"], "columns": ["product_id"]})
    return FakeWorld(model=model, **kwargs)


async def test_full_typo_cycle_writes_journal() -> None:
    """Опечатка → исправление → принятие → замер → индекс → разница → журнал."""

    world = _typo_world(apply=FakeApply(before_ms=100.0, after_ms=10.0))
    client, _ = build_test_client(world)

    started = (await client.post("/runs", json={"sql": TYPO_SQL})).json()
    assert started["step"] == "schema_fix"
    assert started["fix"]["replacements"][0]["new_name"] == "product_color_id"

    accepted = (await decide(client, started, accepted=True)).json()
    assert accepted["step"] == "index_proposal"
    assert accepted["index"]["ddl"].startswith("CREATE INDEX")
    assert accepted["comparison"] is None

    finished = (await decide(client, accepted, accepted=True)).json()

    assert finished["status"] == "compared"
    assert finished["current_sql"] == FIXED_SQL
    assert finished["comparison"]["improved"] is True
    assert finished["decisions"] == {"fix": "accepted", "index": "accepted"}

    entries = await world.journal.history()
    assert len(entries) == 1
    assert entries[0].thread_id == started["thread_id"]
    assert entries[0].fix_ddl == FIXED_SQL
    assert entries[0].no_speedup is False


async def test_clean_cycle_counts_metrics() -> None:
    """Прогон без опечаток проходит мимо исправления и считается в метриках."""

    registry = CollectorRegistry()
    world = _clean_world(apply=FakeApply(before_ms=100.0, after_ms=10.0))
    world.metrics = RunMetrics(registry)
    client, _ = build_test_client(world)

    started = (await client.post("/runs", json={"sql": GOOD_SQL})).json()
    await decide(client, started, accepted=True)

    body = (await client.get("/metrics")).text
    assert 'sqa_runs_total{status="compared"} 1.0' in body
    assert "sqa_indexes_applied_total 1.0" in body


async def test_unfixable_name_ends_session_and_writes_journal() -> None:
    """Имя без похожих названий: сообщение, конец сессии, запись в журнал."""

    world = FakeWorld(
        model=FakeModel(entities={"tables": [MISSING_TABLE], "columns": []}),
    )
    client, _ = build_test_client(world)

    started = (await client.post("/runs", json={"sql": UNFIXABLE_SQL})).json()

    assert started["status"] == "unknown_unfixable"
    assert started["awaiting_decision"] is False
    assert started["step"] is None
    assert MISSING_TABLE in started["unfixable_message"]
    assert started["fix"] is None
    assert started["index"] is None
    assert world.measure.calls == 0
    assert world.model.fix_calls == 0
    assert world.model.index_calls == 0

    entries = await world.journal.history()
    assert len(entries) == 1
    assert entries[0].status == "unknown_unfixable"
    assert entries[0].error is None


async def test_unfixable_name_is_counted_in_metrics() -> None:
    """Прогон без замен попадает в счётчик завершённых прогонов."""

    registry = CollectorRegistry()
    world = FakeWorld(
        model=FakeModel(entities={"tables": [MISSING_TABLE], "columns": []}),
    )
    world.metrics = RunMetrics(registry)
    client, _ = build_test_client(world)

    await client.post("/runs", json={"sql": UNFIXABLE_SQL})

    body = (await client.get("/metrics")).text
    assert 'sqa_runs_total{status="unknown_unfixable"} 1.0' in body


async def test_table_and_column_typos_are_both_fixed() -> None:
    """Опечатка в таблице и колонке: исправляются оба имени, замена видна и замер идёт."""

    world = FakeWorld(
        model=FakeModel(
            entities={"tables": ["sku2"], "columns": ["product_id2"]},
            fixed_sql=BOTH_FIXED_SQL,
        ),
        apply=FakeApply(before_ms=100.0, after_ms=10.0),
    )
    client, _ = build_test_client(world)

    started = (await client.post("/runs", json={"sql": BOTH_TYPOS_SQL})).json()

    assert started["step"] == "schema_fix"
    unknown = {(item["kind"], item["name"]) for item in started["unknown"]}
    assert unknown == {("table", "sku2"), ("column", "product_id2")}
    replacements = {
        (item["old_name"], item["new_name"]) for item in started["fix"]["replacements"]
    }
    assert replacements == {("sku2", "sku"), ("product_id2", "product_id")}

    accepted = (await decide(client, started, accepted=True)).json()
    assert accepted["step"] == "index_proposal"
    assert world.measure.calls == 1

    finished = (await decide(client, accepted, accepted=True)).json()
    assert finished["status"] == "compared"
    assert finished["current_sql"] == BOTH_FIXED_SQL


async def test_no_speedup_cycle_is_reported_as_result() -> None:
    """Цикл без ускорения заканчивается обычным результатом с причиной."""

    world = _clean_world(apply=FakeApply(before_ms=15.0, after_ms=14.9))
    client, _ = build_test_client(world)

    started = (await client.post("/runs", json={"sql": GOOD_SQL})).json()
    finished = (await decide(client, started, accepted=True)).json()

    assert finished["status"] == "compared"
    assert finished["comparison"]["applied"] is True
    assert finished["comparison"]["no_speedup"] is True
    assert finished["comparison"]["reason"]

    entries = await world.journal.history()
    assert entries[0].no_speedup is True


async def test_declined_fix_stops_before_measuring() -> None:
    """Отказ от исправления останавливает прогон до замера."""

    world = _typo_world()
    client, _ = build_test_client(world)

    started = (await client.post("/runs", json={"sql": TYPO_SQL})).json()
    finished = (await decide(client, started, accepted=False)).json()

    assert finished["status"] == "fix_declined"
    assert finished["comparison"] is None
    assert world.measure.calls == 0
    assert world.apply.calls == 0

    entries = await world.journal.history()
    assert entries[0].status == "fix_declined"
    assert entries[0].step is None


async def test_declined_index_leaves_database_untouched() -> None:
    """Отказ от индекса не запускает применение индекса."""

    world = _clean_world()
    client, _ = build_test_client(world)

    started = (await client.post("/runs", json={"sql": GOOD_SQL})).json()
    finished = (await decide(client, started, accepted=False)).json()

    assert finished["status"] == "index_declined"
    assert world.apply.calls == 0


async def test_missing_tool_call_is_warned_and_cycle_continues() -> None:
    """Пропущенный вызов инструмента не прерывает прогон и даёт предупреждение."""

    world = FakeWorld(model=FakeModel(tool_call=False))
    client, _ = build_test_client(world)

    started = (await client.post("/runs", json={"sql": GOOD_SQL})).json()

    assert started["schema_checked"] is False
    assert started["warnings"]
    assert started["fix"] is None
    assert started["step"] == "index_proposal"


async def test_history_endpoint_tracks_runs() -> None:
    """История прогонов видит и завершённые, и ожидающие решения прогоны."""

    world = _typo_world()
    client, _ = build_test_client(world)
    started = (await client.post("/runs", json={"sql": TYPO_SQL})).json()

    runs = (await client.get("/runs")).json()["runs"]
    assert [run["thread_id"] for run in runs] == [started["thread_id"]]

    await decide(client, started, accepted=False)
    runs = (await client.get("/runs")).json()["runs"]
    assert runs[0]["status"] == "fix_declined"


def test_app_and_ui_share_one_contract() -> None:
    """UI собран на клиенте того же приложения, что и API."""

    from sql_query_agent.ui.client import AdvisorApiClient

    world = FakeWorld()
    app = build_test_app(world)
    client = AdvisorApiClient(app)

    assert client is not None
    paths = set(app.openapi()["paths"]) | {
        getattr(route, "path", "") for route in app.routes
    }
    assert paths >= {
        "/runs",
        "/runs/{thread_id}",
        "/runs/{thread_id}/decision",
        "/presets",
        "/metrics",
    }


def test_default_app_factory_imports() -> None:
    """Точка входа приложения собирается без обращения к базе."""

    from sql_query_agent.api.app import build_default_app

    assert build_default_app is not None


__all__: list[Any] = []


@pytest.mark.integration
async def test_integration_speedup_cycle_over_real_database(
    require_postgres: None,
) -> None:
    """Полный цикл на реальной базе: опечатка → исправление → индекс → сверка.

    Модель подставная: проверяется работа с базой, а не качество ответов
    GigaChat. Проверка не закрепляет форму демо-схемы: итог замера зависит от
    данных, поэтому утверждается сам цикл (замер до и после, откат индекса,
    запись в журнале), а не конкретная величина ускорения.
    """

    model = FakeModel(
        entities={"tables": ["sku"], "columns": ["produc_id"]},
        fixed_sql="select * from sku where product_id = 42",
        ddl="CREATE INDEX e2e_sku_product_id_idx ON sku (product_id)",
    )

    run = await _run_integration_cycle("select * from sku where produc_id = 42", model)

    assert run.started["step"] == "schema_fix"
    assert run.started["fix"]["replacements"][0]["new_name"] == "product_id"
    assert run.finished["current_sql"] == "select * from sku where product_id = 42"
    assert run.finished["status"] == "compared"
    assert run.finished["comparison"]["applied"] is True
    assert run.finished["comparison"]["verdict"] in {
        "speedup",
        "slight_speedup",
        "no_speedup",
    }
    assert run.finished["comparison"]["before_median_ms"] > 0
    assert run.finished["comparison"]["after_median_ms"] > 0
    assert run.journal_entry is not None
    assert run.journal_entry.verdict == run.finished["comparison"]["verdict"]
    assert run.journal_entry.no_speedup is (run.finished["comparison"]["no_speedup"] is True)
    assert run.indexes_after == []


class _IntegrationRun:
    """Итоги одного сквозного прогона на реальной базе."""

    def __init__(
        self,
        started: dict[str, Any],
        finished: dict[str, Any],
        journal_entry: Any,
        indexes_after: list[str],
    ) -> None:
        self.started = started
        self.finished = finished
        self.journal_entry = journal_entry
        self.indexes_after = indexes_after


async def _run_integration_cycle(sql: str, model: FakeModel) -> _IntegrationRun:
    """Прогнать полный цикл на реальной базе и вернуть его итоги."""

    from psycopg_pool import AsyncConnectionPool

    from sql_query_agent.db.index_apply import IndexApplier
    from sql_query_agent.db.journal_store import PostgresRunJournal
    from sql_query_agent.db.pool import create_pool, open_pool
    from sql_query_agent.tools.apply_index import ApplyIndexTool
    from sql_query_agent.tools.check_schema import SchemaChecker
    from sql_query_agent.tools.measure_query import QueryMeasureTool, build_measurer

    settings = Settings(_env_file=None)
    pool: AsyncConnectionPool = create_pool(settings.database.dsn)
    await open_pool(pool)
    try:
        measurer = build_measurer(
            pool,
            statement_timeout_ms=settings.database.statement_timeout_ms,
            lock_timeout_ms=settings.database.lock_timeout_ms,
            warmup_runs=settings.measurement.warmup_runs,
            repeat_runs=settings.measurement.repeat_runs,
        )
        journal = PostgresRunJournal(pool, schema=settings.database.metrics_schema)
        graph = build_graph(
            model=model,
            checker=SchemaChecker(pool, schema="public"),
            measure_tool=QueryMeasureTool(measurer),
            apply_tool=ApplyIndexTool(
                IndexApplier(pool, measurer),
                significance_ratio=settings.measurement.significance_ratio,
            ),
            checkpointer=InMemorySaver(),
        )
        service = RunService(SessionRunner(graph), journal=journal)
        deps = AppDeps(
            service=service,
            settings=settings,
            metrics=RunMetrics(CollectorRegistry()),
            journal=journal,
        )
        app = create_app(deps, with_lifespan=False)
        transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            started = (await client.post("/runs", json={"sql": sql})).json()
            proposed = (await decide(client, started, accepted=True)).json()
            finished = (await decide(client, proposed, accepted=True)).json()

        history = await journal.history(50)
        entries = [item for item in history if item.thread_id == started["thread_id"]]
        assert len(entries) == 1
        return _IntegrationRun(started, finished, entries[0], await _indexes_like(pool, "e2e_%"))
    finally:
        await pool.close()


async def _indexes_like(pool: Any, pattern: str) -> list[str]:
    """Имена индексов схемы `public`, подходящие под шаблон."""

    async with pool.connection() as connection:
        cursor = await connection.execute(
            "SELECT indexname FROM pg_indexes WHERE schemaname = 'public' "
            "AND indexname LIKE %s",
            (pattern,),
        )
        return [row[0] for row in await cursor.fetchall()]
