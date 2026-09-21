"""Интеграционные тесты audit-журнала в схеме `metrics`.

Журнал живёт в целевой базе, поэтому проверяется на ней: запись должна
содержать все поля прогона, а схема `public` при этом не меняется. Журнал не
чистится между запусками, поэтому каждый тест пишет под своим уникальным
`thread_id` и смотрит на самую свежую запись с этим идентификатором.
"""

import uuid
from collections.abc import AsyncIterator
from typing import Any

import pytest
from psycopg_pool import AsyncConnectionPool

from sql_query_agent.config import Settings
from sql_query_agent.db.journal_store import PostgresRunJournal
from sql_query_agent.db.pool import create_pool, open_pool
from sql_query_agent.run.journal import RunRecord

pytestmark = pytest.mark.integration

GOOD_SQL = "select * from sku where product_id = 42"


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
def journal(pool: AsyncConnectionPool) -> PostgresRunJournal:
    """Журнал в схеме `metrics`."""

    return PostgresRunJournal(pool, schema=Settings(_env_file=None).database.metrics_schema)


def _thread_id() -> str:
    """Уникальный идентификатор прогона для одного теста."""

    return f"journal-{uuid.uuid4().hex}"


def _record(thread_id: str, **overrides: Any) -> RunRecord:
    """Audit-строка с заполненными полями."""

    values: dict[str, Any] = {
        "thread_id": thread_id,
        "status": "compared",
        "step": None,
        "original_sql": GOOD_SQL,
        "current_sql": GOOD_SQL,
        "schema_checked": True,
        "unknown": [],
        "warnings": [],
        "fix_ddl": None,
        "index_ddl": "CREATE INDEX demo_idx ON sku (product_id)",
        "decisions": {"fix": "not_required", "index": "accepted"},
        "before_stats": {"median_ms": 8.4, "minimum_ms": 8.1, "maximum_ms": 8.6, "runs": 3},
        "after_stats": {"median_ms": 0.2, "minimum_ms": 0.1, "maximum_ms": 0.3, "runs": 3},
        "verdict": "speedup",
        "speedup": 42.0,
        "no_speedup": False,
        "reason": "Запрос ускорился",
    }
    values.update(overrides)
    return RunRecord(**values)


async def _latest(journal: PostgresRunJournal, thread_id: str) -> RunRecord:
    """Самая свежая запись с указанным идентификатором."""

    stored = [item for item in await journal.history(200) if item.thread_id == thread_id]
    assert stored, f"запись {thread_id} не найдена"
    return stored[0]


async def test_record_is_stored_with_all_fields(journal: PostgresRunJournal) -> None:
    """Запись журнала содержит все поля прогона."""

    thread_id = _thread_id()
    await journal.record(_record(thread_id))

    record = await _latest(journal, thread_id)

    assert record.original_sql == GOOD_SQL
    assert record.schema_checked is True
    assert record.index_ddl.startswith("CREATE INDEX")
    assert record.decisions["index"] == "accepted"
    assert record.before_stats["median_ms"] == 8.4
    assert record.after_stats["median_ms"] == 0.2
    assert record.verdict == "speedup"
    assert record.speedup == 42.0
    assert record.no_speedup is False
    assert record.finished_at is not None


async def test_no_speedup_run_is_stored_as_normal_result(
    journal: PostgresRunJournal,
) -> None:
    """Прогон без ускорения сохраняется как обычный результат."""

    thread_id = _thread_id()
    await journal.record(
        _record(
            thread_id,
            verdict="no_speedup",
            speedup=1.0,
            no_speedup=True,
            reason="низкая селективность",
        )
    )

    record = await _latest(journal, thread_id)

    assert record.status == "compared"
    assert record.no_speedup is True
    assert record.verdict == "no_speedup"
    assert record.reason == "низкая селективность"


async def test_declined_run_is_stored_with_stage(journal: PostgresRunJournal) -> None:
    """Прерванный прогон сохраняется с этапом остановки."""

    thread_id = _thread_id()
    await journal.record(
        _record(
            thread_id,
            status="index_declined",
            step="index_proposal",
            decisions={"fix": "accepted", "index": "declined"},
            before_stats={"median_ms": 8.4, "minimum_ms": 8.1, "maximum_ms": 8.6, "runs": 3},
            after_stats=None,
            verdict=None,
            speedup=None,
            no_speedup=False,
        )
    )

    record = await _latest(journal, thread_id)

    assert record.status == "index_declined"
    assert record.step == "index_proposal"
    assert record.decisions["index"] == "declined"
    assert record.after_stats is None


async def test_history_is_ordered_newest_first(journal: PostgresRunJournal) -> None:
    """Свежие записи идут первыми."""

    first = _thread_id()
    second = _thread_id()
    await journal.record(_record(first))
    await journal.record(_record(second))

    records = await journal.history(200)
    order = [item.thread_id for item in records]

    assert order.index(second) < order.index(first)


async def test_public_schema_is_not_touched_by_journal(journal: PostgresRunJournal) -> None:
    """Журнал не создаёт таблиц в схеме `public`."""

    await journal.record(_record(_thread_id()))

    from sql_query_agent.db.pool import create_pool, open_pool

    settings = Settings(_env_file=None)
    pool = create_pool(settings.database.dsn)
    await open_pool(pool)
    try:
        async with pool.connection() as connection:
            cursor = await connection.execute(
                "SELECT table_name FROM information_schema.tables "
                "WHERE table_schema = 'public'"
            )
            rows = await cursor.fetchall()
    finally:
        await pool.close()

    assert all(row[0] != "runs" for row in rows)
