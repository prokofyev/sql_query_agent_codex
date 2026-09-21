"""Журнал прогонов: audit-записи для последующего разбора.

Журнал — отдельная сущность от метрик Prometheus: здесь сохраняется всё
содержимое прогона (текст запроса, замеры, решения), а в метках метрик
остаются только значения низкой кардинальности. Текст запроса живёт только
здесь.
"""

from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, Field

RUNS_TABLE = "runs"


class RunRecord(BaseModel):
    """Audit-строка одного прогона."""

    thread_id: str
    status: str
    step: str | None = None
    original_sql: str = ""
    current_sql: str = ""
    schema_checked: bool = False
    unknown: list[dict[str, Any]] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    fix_ddl: str | None = None
    index_ddl: str | None = None
    decisions: dict[str, str] = Field(default_factory=dict)
    before_stats: dict[str, Any] | None = None
    after_stats: dict[str, Any] | None = None
    verdict: str | None = None
    speedup: float | None = None
    no_speedup: bool = False
    reason: str = ""
    error: str | None = None
    finished_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


def record_from_report(report: Any) -> RunRecord:
    """Построить audit-строку по отчёту о прогоне."""

    comparison = report.comparison
    # Замер «до» приходит двумя путями: из состояния прогона (предложение
    # индекса) и из результата сравнения. В журнале нужна одна величина.
    before = (
        (comparison.before if comparison else None)
        or (report.index.before if report.index else None)
    )
    return RunRecord(
        thread_id=report.thread_id,
        status=report.status.value,
        step=report.step,
        original_sql=report.original_sql,
        current_sql=report.current_sql,
        schema_checked=report.schema_checked,
        unknown=list(report.unknown),
        warnings=list(report.warnings),
        fix_ddl=(report.fix.fixed_sql if report.fix else None),
        index_ddl=(comparison.index_ddl if comparison and comparison.index_ddl else None),
        decisions=dict(report.decisions),
        before_stats=before.model_dump(mode="json") if before else None,
        after_stats=(
            comparison.after.model_dump(mode="json")
            if comparison and comparison.after
            else None
        ),
        verdict=comparison.verdict.value if comparison else None,
        speedup=comparison.speedup if comparison else None,
        no_speedup=bool(comparison.no_speedup) if comparison else False,
        reason=comparison.reason if comparison else "",
        error=report.error,
    )


class RunJournal:
    """Хранилище audit-записей прогонов.

    Базовый класс даёт поведение по умолчанию для случаев, когда база
    недоступна: запись уходит в память процесса, а не теряется молча.
    """

    def __init__(self) -> None:
        self._records: list[RunRecord] = []

    async def record(self, entry: RunRecord) -> None:
        """Сохранить запись о прогоне."""

        self._records.append(entry)

    async def history(self, limit: int = 20) -> list[RunRecord]:
        """Последние записи в обратном порядке: свежие первыми."""

        return list(reversed(self._records[-limit:]))


def as_dicts(records: Sequence[RunRecord]) -> list[dict[str, Any]]:
    """Записи журнала в виде словарей для ответа API."""

    return [entry.model_dump(mode="json") for entry in records]


__all__ = ["RUNS_TABLE", "RunJournal", "RunRecord", "as_dicts", "record_from_report"]
