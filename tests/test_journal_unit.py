"""Тесты audit-записи: преобразование отчёта в строку журнала."""

from sql_query_agent.run.journal import RUNS_TABLE, RunJournal, RunRecord, record_from_report
from sql_query_agent.run.report import RunStatus, build_report

GOOD_SQL = "select * from sku where product_id = 42"


def test_record_from_completed_report() -> None:
    """Завершённый прогон превращается в строку со всеми величинами."""

    report = build_report(
        "t1",
        {
            "original_sql": GOOD_SQL,
            "current_sql": GOOD_SQL,
            "schema_checked": True,
            "schema_result": {"unknown": []},
            "status": "compared",
            "before_stats": {"median_ms": 8.4, "minimum_ms": 8.1, "maximum_ms": 8.6, "runs": 3},
            "apply_result": {
                "applied": True,
                "before_median_ms": 8.4,
                "after_median_ms": 0.2,
                "speedup": 42.0,
                "verdict": "speedup",
                "improved": True,
                "reason": "Bitmap Index Scan",
                "before": {"median_ms": 8.4, "minimum_ms": 8.1, "maximum_ms": 8.6, "runs": 3},
                "after": {"median_ms": 0.2, "minimum_ms": 0.1, "maximum_ms": 0.3, "runs": 3},
                "index_ddl": "CREATE INDEX i ON sku (product_id)",
            },
        },
        [],
    )

    entry = record_from_report(report)

    assert entry.status == "compared"
    assert entry.index_ddl == "CREATE INDEX i ON sku (product_id)"
    assert entry.verdict == "speedup"
    assert entry.no_speedup is False
    assert entry.before_stats["median_ms"] == 8.4
    assert entry.after_stats["median_ms"] == 0.2
    assert entry.finished_at is not None


def test_record_from_declined_report_marks_stage() -> None:
    """Прерванный прогон записывается с этапом остановки."""

    report = build_report(
        "t2",
        {
            "original_sql": GOOD_SQL,
            "current_sql": GOOD_SQL,
            "fix_declined": True,
            "status": "fix_declined",
        },
        [],
    )

    entry = record_from_report(report)

    assert entry.status == RunStatus.FIX_DECLINED.value
    assert entry.decisions["fix"] == "declined"
    assert entry.verdict is None


def test_record_serializes_to_json() -> None:
    """Строка журнала сериализуется вместе с датой завершения."""

    entry = RunRecord(thread_id="t3", status="compared", original_sql=GOOD_SQL)
    payload = entry.model_dump(mode="json")

    assert payload["thread_id"] == "t3"
    assert payload["finished_at"]
    assert RUNS_TABLE == "runs"


async def test_memory_journal_keeps_records() -> None:
    """Журнал в памяти хранит записи и отдаёт их в обратном порядке."""

    journal = RunJournal()
    await journal.record(RunRecord(thread_id="a", status="compared"))
    await journal.record(RunRecord(thread_id="b", status="compared"))

    records = await journal.history()

    assert [item.thread_id for item in records] == ["b", "a"]


async def test_memory_journal_history_honours_limit() -> None:
    """Ограничение размера истории соблюдается."""

    journal = RunJournal()
    for index in range(5):
        await journal.record(RunRecord(thread_id=f"t{index}", status="compared"))

    records = await journal.history(limit=2)

    assert [item.thread_id for item in records] == ["t4", "t3"]
