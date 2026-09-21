"""Тесты службы прогонов: сессии, решения, метрики и журнал."""

from typing import Any

import pytest
from langgraph.checkpoint.memory import InMemorySaver
from prometheus_client import CollectorRegistry, generate_latest

from sql_query_agent.agent.graph import build_graph
from sql_query_agent.db.catalog import SchemaCatalog
from sql_query_agent.observability.metrics import RunMetrics
from sql_query_agent.run.journal import RunJournal
from sql_query_agent.run.service import RunService
from sql_query_agent.run.session import SessionNotFoundError, SessionRunner
from tests.fakes import FakeApply, FakeChecker, FakeMeasure, FakeModel

CATALOG = SchemaCatalog.from_rows(
    [
        ("sku", "sku_id"),
        ("sku", "product_id"),
        ("sku", "product_color_id"),
    ]
)
TYPO_SQL = "select * from sku where product_colr_id = 1"
GOOD_SQL = "select * from sku where product_id = 1"


class _FlakyJournal:
    """Журнал, запись в который всегда срывается."""

    def __init__(self) -> None:
        self.attempts = 0

    async def record(self, entry: Any) -> None:
        """Сымитировать сбой базы."""

        self.attempts += 1
        raise RuntimeError("база журнала недоступна")

    async def history(self, limit: int = 20) -> list[Any]:
        """История недоступна."""

        raise RuntimeError("база журнала недоступна")


def _clean_model() -> FakeModel:
    """Модель, которая не находит опечаток в чистом запросе."""

    return FakeModel(entities=[{"table": "sku", "columns": ["product_id"]}])


def _typo_model() -> FakeModel:
    """Модель, которая сообщает об опечатке в колонке."""

    return FakeModel(
        entities=[{"table": "sku", "columns": ["product_colr_id"]}],
        fixed_sql="select * from sku where product_color_id = 1",
    )


def _service(
    *,
    model: FakeModel | None = None,
    measure: FakeMeasure | None = None,
    apply: FakeApply | None = None,
    journal: Any = None,
    metrics: RunMetrics | None = None,
) -> RunService:
    """Собрать службу прогонов на подставных зависимостях."""

    graph = build_graph(
        model=model or _clean_model(),
        checker=FakeChecker(CATALOG),
        measure_tool=measure or FakeMeasure(),
        apply_tool=apply or FakeApply(),
        checkpointer=InMemorySaver(),
    )
    return RunService(SessionRunner(graph), journal=journal, metrics=metrics)


async def test_start_stops_at_fix_decision() -> None:
    """Запуск с опечаткой останавливается на решении по исправлению."""

    report = await _service(model=_typo_model()).start(TYPO_SQL)

    assert report.awaiting_decision is True
    assert report.step == "schema_fix"
    assert report.fix is not None
    assert report.fix.fixed_sql.endswith("product_color_id = 1")
    assert report.thread_id


async def test_accepting_fix_continues_to_index_decision() -> None:
    """Принятие исправления продолжает прогон до решения по индексу."""

    service = _service(model=_typo_model())
    started = await service.start(TYPO_SQL)

    report = await service.decide(started.thread_id, accepted=True)

    assert report.awaiting_decision is True
    assert report.step == "index_proposal"
    assert report.current_sql.endswith("product_color_id = 1")
    assert report.index is not None
    assert report.index.ddl.startswith("CREATE INDEX")
    assert report.decisions["fix"] == "accepted"


async def test_declining_fix_ends_run() -> None:
    """Отказ от исправления завершает прогон."""

    service = _service(model=_typo_model())
    started = await service.start(TYPO_SQL)

    report = await service.decide(started.thread_id, accepted=False)

    assert report.awaiting_decision is False
    assert report.status.value == "fix_declined"


async def test_full_cycle_ends_with_comparison() -> None:
    """Полный цикл «запуск → решение → завершение» даёт сравнение."""

    service = _service(apply=FakeApply(before_ms=100.0, after_ms=10.0))
    started = await service.start(GOOD_SQL)
    report = await service.decide(started.thread_id, accepted=True)

    assert report.status.value == "compared"
    assert report.comparison is not None
    assert report.comparison.improved is True


async def test_unknown_session_is_reported() -> None:
    """Решение по неизвестной сессии отклоняется."""

    with pytest.raises(SessionNotFoundError):
        await _service().decide("no-such-session", accepted=True)


async def test_completed_run_is_recorded_in_journal_once() -> None:
    """Завершённый прогон записывается в журнал один раз."""

    journal = RunJournal()
    service = _service(journal=journal)
    started = await service.start(GOOD_SQL)
    first = await service.decide(started.thread_id, accepted=True)

    records = await journal.history()

    assert len(records) == 1
    assert records[0].thread_id == first.thread_id
    assert records[0].status == "compared"
    assert records[0].original_sql == GOOD_SQL


async def test_paused_run_is_not_recorded() -> None:
    """Прогон, ожидающий решения, в журнал не попадает."""

    journal = RunJournal()
    service = _service(model=_typo_model(), journal=journal)

    await service.start(TYPO_SQL)

    assert await journal.history() == []


async def test_declined_run_records_stage() -> None:
    """Отказ записывается с этапом остановки."""

    journal = RunJournal()
    service = _service(model=_typo_model(), journal=journal)
    started = await service.start(TYPO_SQL)
    await service.decide(started.thread_id, accepted=False)

    entries = await journal.history()

    assert len(entries) == 1
    assert entries[0].status == "fix_declined"
    assert entries[0].decisions["fix"] == "declined"


async def test_declined_index_records_stage() -> None:
    """Отказ от индекса записывается с этапом решения по индексу."""

    journal = RunJournal()
    service = _service(journal=journal)
    started = await service.start(GOOD_SQL)
    await service.decide(started.thread_id, accepted=False)

    entries = await journal.history()

    assert entries[0].status == "index_declined"
    assert entries[0].decisions["index"] == "declined"


async def test_journal_failure_does_not_break_run() -> None:
    """Сбой журнала не мешает отдать пользователю результат."""

    journal = _FlakyJournal()
    service = _service(journal=journal)
    started = await service.start(GOOD_SQL)

    report = await service.decide(started.thread_id, accepted=True)

    assert report.status.value == "compared"
    assert report.comparison is not None


async def test_metrics_reflect_completed_run() -> None:
    """Завершённый прогон с индексом увеличивает счётчики."""

    registry = CollectorRegistry()
    metrics = RunMetrics(registry)
    service = _service(metrics=metrics, apply=FakeApply(before_ms=100.0, after_ms=10.0))
    started = await service.start(GOOD_SQL)
    await service.decide(started.thread_id, accepted=True)

    rendered = generate_latest(registry).decode()
    assert 'sqa_runs_total{status="compared"} 1.0' in rendered
    assert "sqa_indexes_applied_total 1.0" in rendered
    assert 'sqa_measure_seconds_count{phase="before"} 1.0' in rendered


async def test_no_speedup_metric_is_counted() -> None:
    """Случай без ускорения считается отдельным счётчиком."""

    registry = CollectorRegistry()
    metrics = RunMetrics(registry)
    service = _service(metrics=metrics, apply=FakeApply(before_ms=15.0, after_ms=14.9))
    started = await service.start(GOOD_SQL)
    await service.decide(started.thread_id, accepted=True)

    rendered = generate_latest(registry).decode()
    assert "sqa_no_speedup_total 1.0" in rendered


async def test_paused_run_is_not_counted_in_metrics() -> None:
    """Прогон, ожидающий решения, в счётчик прогонов не попадает."""

    registry = CollectorRegistry()
    service = _service(model=_typo_model(), metrics=RunMetrics(registry))

    await service.start(TYPO_SQL)

    rendered = generate_latest(registry).decode()
    assert "sqa_runs_total{" not in rendered


async def test_history_returns_latest_first() -> None:
    """История прогонов отдаёт свежие прогоны первыми."""

    service = _service()
    first = await service.start(GOOD_SQL)
    await service.decide(first.thread_id, accepted=False)
    second = await service.start(GOOD_SQL)
    await service.decide(second.thread_id, accepted=False)

    reports = await service.history()

    assert [report.thread_id for report in reports] == [second.thread_id, first.thread_id]
