"""Сборка реальных зависимостей приложения.

Здесь соединяются пул подключений, инструменты, модель GigaChat, граф,
checkpoint-хранилище, журнал и метрики. Отдельный модуль нужен потому, что
точка входа ASGI-приложения обязана быть синхронной, а открытие пула и
checkpoint-хранилища — асинхронным: ресурсы открываются в `lifespan`.
"""

from typing import Any

from sql_query_agent.agent.graph import build_graph
from sql_query_agent.agent.llm import GigaChatAdvisor
from sql_query_agent.api.deps import AppDeps
from sql_query_agent.config import Settings, get_settings
from sql_query_agent.db.checkpoint import open_checkpointer
from sql_query_agent.db.index_apply import IndexApplier
from sql_query_agent.db.journal_store import PostgresRunJournal
from sql_query_agent.db.pool import create_pool
from sql_query_agent.logging_setup import get_logger
from sql_query_agent.observability.metrics import RunMetrics, current_metrics
from sql_query_agent.observability.model_metrics import metered_advisor
from sql_query_agent.run.service import RunService
from sql_query_agent.run.session import SessionRunner
from sql_query_agent.tools.apply_index import ApplyIndexTool
from sql_query_agent.tools.check_schema import SchemaChecker
from sql_query_agent.tools.measure_query import QueryMeasureTool, build_measurer

logger = get_logger(__name__)


class _AsyncCloser:
    """Закрыть асинхронный контекстный менеджер."""

    def __init__(self, context: Any) -> None:
        self._context = context

    async def aclose(self) -> None:
        """Выйти из контекста."""

        await self._context.__aexit__(None, None, None)


async def open_deps(
    settings: Settings | None = None,
    *,
    metrics: RunMetrics | None = None,
    model: Any | None = None,
    journal: Any | None = None,
) -> AppDeps:
    """Открыть ресурсы и собрать зависимости приложения."""

    current = settings or get_settings()
    database = current.database
    pool = create_pool(database.dsn)
    await pool.open(wait=True)
    logger.info("пул подключений открыт")

    checkpointer_context = open_checkpointer(database.dsn, database.agent_schema)
    checkpointer = await checkpointer_context.__aenter__()

    measurer = build_measurer(
        pool,
        statement_timeout_ms=database.statement_timeout_ms,
        lock_timeout_ms=database.lock_timeout_ms,
        warmup_runs=current.measurement.warmup_runs,
        repeat_runs=current.measurement.repeat_runs,
    )
    checker = SchemaChecker(
        pool,
        schema=database.schema_name,
        threshold=current.validation.fuzzy_threshold,
        suggestion_limit=current.validation.suggestion_limit,
    )
    applier = IndexApplier(
        pool,
        measurer,
        statement_timeout_ms=database.statement_timeout_ms,
        lock_timeout_ms=database.lock_timeout_ms,
    )
    advisor = model or GigaChatAdvisor(current.gigachat)
    run_metrics = metrics or current_metrics()

    graph = build_graph(
        model=metered_advisor(advisor, run_metrics),
        checker=checker,
        measure_tool=QueryMeasureTool(measurer),
        apply_tool=ApplyIndexTool(
            applier, significance_ratio=current.measurement.significance_ratio
        ),
        checkpointer=checkpointer,
    )

    run_journal = journal or PostgresRunJournal(pool, schema=database.metrics_schema)
    service = RunService(SessionRunner(graph), journal=run_journal, metrics=run_metrics)
    return AppDeps(
        service=service,
        settings=current,
        metrics=run_metrics,
        journal=run_journal,
        closeables=[_AsyncCloser(checkpointer_context), pool],
    )


class PendingDeps(AppDeps):
    """Зависимости, которые открываются при старте ASGI-приложения.

    Точка входа `uvicorn` синхронная, поэтому здесь только настройки;
    пул, граф и журнал появляются в `lifespan` и записываются в те же поля.
    """

    def __init__(self, settings: Settings | None = None) -> None:
        super().__init__(service=None, settings=settings or get_settings())  # type: ignore[arg-type]
        self._metrics = current_metrics()
        self.metrics = self._metrics

    async def aopen(self) -> None:
        """Открыть ресурсы приложения."""

        opened = await open_deps(self.settings, metrics=self._metrics)
        self.service = opened.service
        self.journal = opened.journal
        self.closeables = opened.closeables


def build_default_deps(settings: Settings | None = None) -> AppDeps:
    """Зависимости для синхронной точки входа ASGI-приложения."""

    return PendingDeps(settings)


__all__ = ["PendingDeps", "build_default_deps", "open_deps"]
